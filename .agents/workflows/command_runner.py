"""Command execution, JSON extraction, and redaction helpers for workflows."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET_FLAGS = {
    "--base-token",
    "--template-base-token",
    "--folder-token",
    "--doc",
    "--chat-id",
    "--user-id",
    "--content",
    "--markdown",
    "--text",
}
SECRET_KEY_PARTS = ("token", "password", "secret", "api_key", "url")
SECRET_OUTPUT_KEYS = (
    "app_token",
    "base_token",
    "template_base_token",
    "folder_token",
    "docx_token",
    "doc_token",
    "document_id",
    "block_token",
    "token",
    "url",
    "chat_id",
    "open_chat_id",
    "open_id",
    "user_id",
    "user_open_id",
    "userOpenId",
    "openId",
    "message_id",
    "receive_id",
)


@dataclass
class StepResult:
    name: str
    status: str
    command: list[str] | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    detail: dict | None = None
    streamed: bool = False


def redact_local_paths(text: str) -> str:
    redacted = text.replace(str(PROJECT_ROOT), "$PROJECT_ROOT")
    return re.sub(r"/Users/[^\s\"'`]+", "[LOCAL_PATH]", redacted)


def redact_output_text(text: str) -> str:
    redacted = text
    for flag in SECRET_FLAGS:
        redacted = re.sub(rf"({re.escape(flag)}\s+)\S+", rf"\1[REDACTED]", redacted)
    key_pattern = "|".join(re.escape(key) for key in SECRET_OUTPUT_KEYS)
    redacted = re.sub(
        rf'("?({key_pattern})"?\s*:\s*)"[^"]*"',
        r'\1"[REDACTED]"',
        redacted,
        flags=re.IGNORECASE,
    )
    redacted = re.sub(
        r"https?://[^\s\"']*(?:feishu\.cn|larksuite\.com|/base/)[^\s\"']*",
        "[REDACTED_URL]",
        redacted,
        flags=re.IGNORECASE,
    )
    redacted = re.sub(r"\b(?:ou|oc|om|omt)_[A-Za-z0-9_-]{8,}\b", "[REDACTED_ID]", redacted)
    return redact_local_paths(redacted)


def redact_command(command: list[str] | None) -> list[str] | None:
    if command is None:
        return None
    redacted = list(command)
    for idx, part in enumerate(redacted[:-1]):
        if part in SECRET_FLAGS:
            redacted[idx + 1] = "[REDACTED]"
    return [redact_output_text(part) for part in redacted]


def redact_detail(value: object, key: str = "") -> object:
    lowered = key.lower()
    if isinstance(value, dict):
        return {item_key: redact_detail(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_detail(item) for item in value]
    if isinstance(value, str):
        if any(part in lowered for part in SECRET_KEY_PARTS):
            return "[REDACTED]" if value else value
        return redact_output_text(value)
    return value


def run_command(name: str, command: list[str], timeout_seconds: int) -> StepResult:
    print(f"\n## {name}: running", flush=True)
    print("$ " + " ".join(redact_command(command) or []), flush=True)
    try:
        proc = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        message = f"command not found: {command[0]}"
        print(message, file=sys.stderr, flush=True)
        return StepResult(
            name=name,
            status="failed",
            command=redact_command(command),
            returncode=127,
            stderr=f"{message}: {exc}",
        )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []

    def pump(stream, chunks: list[str], target) -> None:
        assert stream is not None
        for line in iter(stream.readline, ""):
            chunks.append(line)
            print(redact_output_text(line), end="", file=target, flush=True)
        stream.close()

    stdout_thread = threading.Thread(target=pump, args=(proc.stdout, stdout_chunks, sys.stdout), daemon=True)
    stderr_thread = threading.Thread(target=pump, args=(proc.stderr, stderr_chunks, sys.stderr), daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    timed_out = False
    try:
        returncode = proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            proc.kill()
        returncode = proc.wait()
    stdout_thread.join()
    stderr_thread.join()
    stdout = "".join(stdout_chunks)
    stderr = "".join(stderr_chunks)
    if timed_out:
        stderr += f"\nCommand timed out after {timeout_seconds} seconds."
    status = "ok" if returncode == 0 else "failed"
    print(f"\n## {name}: {status} (returncode={returncode})", flush=True)
    return StepResult(
        name=name,
        status=status,
        command=redact_command(command),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        streamed=True,
    )


def extract_json_object(text: str) -> dict:
    end = text.rfind("}")
    if end < 0:
        return {}
    for start in reversed([idx for idx, char in enumerate(text[: end + 1]) if char == "{"]):
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def find_nested_value(data: object, keys: set[str]) -> str | None:
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        for value in data.values():
            found = find_nested_value(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_nested_value(item, keys)
            if found:
                return found
    return None


def iter_dicts(data: object):
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from iter_dicts(value)
    elif isinstance(data, list):
        for item in data:
            yield from iter_dicts(item)


def command_text(command: list[str] | None) -> str:
    if not command:
        return ""
    return " ".join(shlex.quote(part) for part in command)


def report_date_text(report_date: date | str) -> str:
    return report_date.isoformat() if isinstance(report_date, date) else str(report_date)
