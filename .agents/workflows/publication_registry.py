"""Local state for idempotent Feishu publication resources."""

from __future__ import annotations

import json
import os
import atexit
import fcntl
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo


class PublicationRegistryError(RuntimeError):
    """Raised when publication state cannot be trusted."""


def _date_key(report_date: date | str) -> str:
    return report_date.isoformat() if isinstance(report_date, date) else str(report_date)


def _now_text() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


class PublicationRegistry:
    """A chmod-600 JSON registry keyed by report date and category."""

    def __init__(self, path: Path):
        self.path = path
        self._data: dict[str, dict[str, dict]] | None = None
        self._lock_file = None

    def acquire_lock(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        self._lock_file = lock_path.open("a+", encoding="utf-8")
        os.chmod(lock_path, 0o600)
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)
        atexit.register(self.release_lock)

    def release_lock(self) -> None:
        if self._lock_file is None:
            return
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
        self._lock_file.close()
        self._lock_file = None

    def load(self) -> dict[str, dict[str, dict]]:
        if self._data is not None:
            return self._data
        if not self.path.exists():
            self._data = {}
            return self._data
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PublicationRegistryError(f"publication registry is not valid JSON: {self.path}") from exc
        if not isinstance(data, dict):
            raise PublicationRegistryError(f"publication registry root must be an object: {self.path}")
        for date_value, categories in data.items():
            if not isinstance(date_value, str) or not isinstance(categories, dict):
                raise PublicationRegistryError(f"publication registry has invalid entry: {date_value!r}")
            for category, entry in categories.items():
                if not isinstance(category, str) or not isinstance(entry, dict):
                    raise PublicationRegistryError(
                        f"publication registry has invalid category entry: {date_value!r}/{category!r}"
                    )
        self._data = data
        return self._data

    def get(self, report_date: date | str, category: str) -> dict:
        data = self.load()
        entry = data.get(_date_key(report_date), {}).get(category, {})
        return deepcopy(entry) if isinstance(entry, dict) else {}

    def update(self, report_date: date | str, category: str, values: dict) -> dict:
        data = self.load()
        date_entry = data.setdefault(_date_key(report_date), {})
        current = date_entry.setdefault(category, {})
        if not current.get("created_at"):
            current["created_at"] = values.get("created_at") or _now_text()
        current.update({key: value for key, value in values.items() if value is not None})
        current["last_updated_at"] = values.get("last_updated_at") or _now_text()
        self._write()
        return deepcopy(current)

    def _write(self) -> None:
        assert self._data is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        tmp_path = self.path.with_name(f".{self.path.name}.tmp")
        tmp_path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self.path)
        os.chmod(self.path, 0o600)
