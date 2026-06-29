#!/usr/bin/env python3
"""Sync image-bearing Sorftime weekly report tables to a Feishu Base."""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from report_parser import (
    CATEGORY_MAP,
    LOW_SALES_SECTIONS,
    MOVEMENT_SECTIONS,
    OWN_SECTIONS,
    OWN_TABLES,
    REQUIRED_TABLES,
    SyncError,
    asin_link,
    asin_plain,
    build_records,
    cell_text,
    common_product_fields,
    image_url,
    infer_report_category,
    low_sales_record,
    movement_record,
    normalize_row,
    number_value,
    own_record,
    parse_tables,
    previous_week,
    record_fields,
    split_md_row,
    validate_records,
)

ATTACHMENT_FIELDS = {"附件", "附图"}
DEFAULT_TEMPLATE_BASE_TOKEN = os.environ.get("FEISHU_TEMPLATE_BASE_TOKEN", "")
LARK_CLI_BIN = os.environ.get("LARK_CLI_BIN", "lark-cli")
LARK_CLI_TIMEOUT_SECONDS = int(os.environ.get("LARK_CLI_TIMEOUT_SECONDS", "240"))
BASE_VERIFY_MAX_ATTEMPTS = int(os.environ.get("BASE_VERIFY_MAX_ATTEMPTS", "6"))
BASE_VERIFY_RETRY_DELAY_SECONDS = int(os.environ.get("BASE_VERIFY_RETRY_DELAY_SECONDS", "15"))
SECRET_FLAGS = {"--base-token", "--template-base-token", "--folder-token"}
SECRET_OUTPUT_KEYS = (
    "app_token",
    "base_token",
    "template_base_token",
    "folder_token",
    "token",
    "url",
)
CATEGORY_FOLDER_PLACEHOLDERS = ("{类目1}", "{类目2}")
CATEGORY_FOLDER_TABLES = {
    0: ("2.1.1", "2.2", "2.3", "2.4"),
    1: ("3.1.1", "3.2", "3.3", "3.4"),
}
STATIC_FOLDER_TABLES = {
    "低分高销": ("2.5.2", "3.5.2"),
    "本品": ("4.1.1", "4.1.2"),
}


def redact_cli_args(args: list[str]) -> list[str]:
    redacted = list(args)
    for idx, value in enumerate(redacted[:-1]):
        if value in SECRET_FLAGS:
            redacted[idx + 1] = "[REDACTED]"
    return redacted


def command_for_log(args: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in redact_cli_args(args))


def redact_local_paths(text: str) -> str:
    try:
        cwd = str(Path.cwd())
    except OSError:
        cwd = ""
    redacted = text.replace(cwd, "$PWD") if cwd else text
    return re.sub(r"/Users/[^\s\"'`]+", "[LOCAL_PATH]", redacted)


def display_path(path: Path | str | None) -> str | None:
    if path is None:
        return None
    text = str(path)
    if not text:
        return text
    return redact_local_paths(text)


def redact_text_for_log(text: str | None) -> str:
    if not text:
        return ""
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
    return redact_local_paths(redacted)


def redact_json_for_log(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if isinstance(value, dict):
        return {item_key: redact_json_for_log(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact_json_for_log(item) for item in value]
    if isinstance(value, str):
        if any(part in lowered for part in ("token", "password", "secret", "api_key", "url")):
            return "[REDACTED]" if value else value
        return redact_text_for_log(value)
    return value


def log_progress(message: str) -> None:
    timestamp = dt.datetime.now().isoformat(timespec="seconds")
    print(f"[progress] {timestamp} {message}", file=sys.stderr, flush=True)


def parse_cli_json(stdout: str, cmd: list[str]) -> dict[str, Any]:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        start = stdout.find("{")
        end = stdout.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(stdout[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise SyncError(f"Invalid JSON from {command_for_log(cmd)}:\n{redact_text_for_log(stdout)}")


def run_cli(args: list[str], *, dry_run: bool = False, allow_failure: bool = False) -> dict[str, Any]:
    cmd = [LARK_CLI_BIN, *args, "--as", "user"]
    safe_cmd = command_for_log(cmd)
    if dry_run:
        log_progress("[dry-run] " + safe_cmd)
        return {"ok": True, "dry_run": True, "data": {}}
    proc = None
    for attempt in range(1, 4):
        started_at = time.monotonic()
        log_progress(f"lark-cli attempt={attempt} command={safe_cmd}")
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True, timeout=LARK_CLI_TIMEOUT_SECONDS)
        except FileNotFoundError as exc:
            raise SyncError(f"Command not found: {cmd[0]}. Install lark-cli and retry. Command: {safe_cmd}") from exc
        except subprocess.TimeoutExpired as exc:
            elapsed = time.monotonic() - started_at
            log_progress(f"lark-cli timeout after {elapsed:.1f}s command={safe_cmd}")
            if attempt == 3:
                raise SyncError(
                    f"Command timed out after {LARK_CLI_TIMEOUT_SECONDS}s: {safe_cmd}\n"
                    f"STDOUT:\n{redact_text_for_log(exc.stdout)}\nSTDERR:\n{redact_text_for_log(exc.stderr)}"
                ) from exc
            time.sleep(attempt * 2)
            continue
        elapsed = time.monotonic() - started_at
        log_progress(f"lark-cli returncode={proc.returncode} elapsed={elapsed:.1f}s command={safe_cmd}")
        transient = proc.returncode != 0 and any(
            marker in f"{proc.stdout}\n{proc.stderr}".lower()
            for marker in ["timeout", "temporarily", "connection reset", "eof", "retryable\": true", "base is copying"]
        )
        if proc.returncode == 0 or not transient or attempt == 3:
            break
        time.sleep(attempt * 2)
    assert proc is not None
    if proc.returncode != 0 and allow_failure:
        try:
            return parse_cli_json(proc.stdout or proc.stderr, cmd)
        except SyncError:
            return {
                "ok": False,
                "error": {
                    "message": f"Command failed ({proc.returncode}): {safe_cmd}",
                    "stdout": redact_text_for_log(proc.stdout),
                    "stderr": redact_text_for_log(proc.stderr),
                },
            }
    if proc.returncode != 0:
        raise SyncError(
            f"Command failed ({proc.returncode}): {safe_cmd}\n"
            f"STDOUT:\n{redact_text_for_log(proc.stdout)}\nSTDERR:\n{redact_text_for_log(proc.stderr)}"
        )
    data = parse_cli_json(proc.stdout, cmd)
    if data.get("ok") is False and allow_failure:
        return redact_json_for_log(data)
    if data.get("ok") is False:
        raise SyncError(f"CLI returned ok=false for {safe_cmd}:\n{redact_text_for_log(proc.stdout)}")
    return data


def table_map(base_token: str) -> dict[str, str]:
    data = run_cli(["base", "+table-list", "--base-token", base_token, "--limit", "100"])
    tables = {item["name"]: item["id"] for item in data["data"]["tables"]}
    missing = [name for name in REQUIRED_TABLES if name not in tables]
    if missing:
        raise SyncError(f"Base is missing required tables: {', '.join(missing)}")
    return tables


def field_map(base_token: str, table_id: str) -> dict[str, dict[str, Any]]:
    data = run_cli(["base", "+field-list", "--base-token", base_token, "--table-id", table_id, "--limit", "200"])
    return {item["name"]: item for item in data["data"]["fields"]}


def list_base_blocks(base_token: str, block_type: str | None = None) -> list[dict[str, Any]]:
    args = ["base", "+base-block-list", "--base-token", base_token]
    if block_type:
        args.extend(["--type", block_type])
    data = run_cli(args)
    blocks = data.get("data", {}).get("blocks") or []
    return [block for block in blocks if isinstance(block, dict)]


def root_blocks_by_name(blocks: list[dict[str, Any]], block_type: str | None = None) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for block in blocks:
        if block.get("parent_id") is not None:
            continue
        if block_type and block.get("type") != block_type:
            continue
        name = str(block.get("name") or "")
        if name:
            grouped[name].append(block)
    return dict(grouped)


def unique_root_block(
    grouped: dict[str, list[dict[str, Any]]],
    names: list[str],
    *,
    label: str,
) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    for name in names:
        matches.extend(grouped.get(name, []))
    if not matches:
        raise SyncError(f"Missing root folder for {label}: expected one of {names}")
    ids = {str(block.get("id")) for block in matches}
    if len(ids) != 1:
        raise SyncError(f"Ambiguous root folder for {label}: expected one of {names}, got {matches}")
    return matches[0]


def resolve_category_folders(blocks: list[dict[str, Any]], categories: tuple[str, str]) -> list[dict[str, Any]]:
    grouped = root_blocks_by_name(blocks, "folder")
    resolved: list[dict[str, Any]] = []
    for idx, target_name in enumerate(categories):
        placeholder = CATEGORY_FOLDER_PLACEHOLDERS[idx]
        block = unique_root_block(grouped, [placeholder, target_name], label=placeholder)
        current_name = str(block.get("name") or "")
        if current_name not in {placeholder, target_name}:
            raise SyncError(f"Unexpected folder name for {placeholder}: {current_name}")
        if current_name != target_name and grouped.get(target_name):
            raise SyncError(f"Cannot rename {placeholder} to {target_name}: target folder name already exists")
        resolved.append(
            {
                "placeholder": placeholder,
                "target_name": target_name,
                "block_id": str(block.get("id") or ""),
                "current_name": current_name,
                "status": "already_renamed" if current_name == target_name else "needs_rename",
            }
        )
    return resolved


def validate_block_layout(
    blocks: list[dict[str, Any]],
    tables: dict[str, str],
    categories: tuple[str, str],
) -> dict[str, Any]:
    category_folders = resolve_category_folders(blocks, categories)
    grouped_folders = root_blocks_by_name(blocks, "folder")
    low_sales_folder = unique_root_block(grouped_folders, ["低分高销"], label="低分高销")
    own_folder = unique_root_block(grouped_folders, ["本品"], label="本品")
    table_blocks = {str(block.get("id") or ""): block for block in blocks if block.get("type") == "table"}

    folder_by_table: dict[str, str | None] = {
        "异动数据": None,
        "低分高销数据": None,
        "本品数据": None,
    }
    for idx, section_names in CATEGORY_FOLDER_TABLES.items():
        for table_name in section_names:
            folder_by_table[table_name] = category_folders[idx]["block_id"]
    for table_name in STATIC_FOLDER_TABLES["低分高销"]:
        folder_by_table[table_name] = str(low_sales_folder.get("id") or "")
    for table_name in STATIC_FOLDER_TABLES["本品"]:
        folder_by_table[table_name] = str(own_folder.get("id") or "")

    issues: list[str] = []
    table_checks: dict[str, dict[str, Any]] = {}
    for table_name in REQUIRED_TABLES:
        table_id = tables.get(table_name)
        block = table_blocks.get(str(table_id))
        expected_parent = folder_by_table.get(table_name)
        actual_parent = block.get("parent_id") if block else None
        ok = block is not None and actual_parent == expected_parent
        if not ok:
            issues.append(
                f"{table_name}: expected parent {expected_parent or 'root'}, got {actual_parent or 'missing/root'}"
            )
        table_checks[table_name] = {
            "table_id": table_id,
            "expected_parent_id": expected_parent,
            "actual_parent_id": actual_parent,
            "ok": ok,
        }

    root_folder_names = [str(block.get("name") or "") for block in blocks if block.get("type") == "folder" and block.get("parent_id") is None]
    folder_summary = {
        "category_folders": category_folders,
        "low_rating_high_sales": {
            "name": str(low_sales_folder.get("name") or ""),
            "block_id": str(low_sales_folder.get("id") or ""),
        },
        "own_products": {
            "name": str(own_folder.get("name") or ""),
            "block_id": str(own_folder.get("id") or ""),
        },
        "root_folder_names": root_folder_names,
    }
    if issues:
        raise SyncError("Base block layout mismatch:\n" + "\n".join(issues))
    return {
        "status": "ok",
        "folders": folder_summary,
        "table_parent_checks": table_checks,
        "issues": [],
    }


def check_block_update_scope(base_token: str, probe_block_id: str) -> dict[str, Any]:
    probe_name = f"__codex_scope_check_{int(time.time())}"
    data = run_cli(
        [
            "base",
            "+base-block-rename",
            "--base-token",
            base_token,
            "--block-id",
            probe_block_id,
            "--name",
            probe_name,
            "--dry-run",
        ],
        allow_failure=True,
    )
    if data.get("ok") is not False:
        return {"status": "ok", "missing_scopes": [], "hint": None}
    error = data.get("error") if isinstance(data.get("error"), dict) else {}
    missing_scopes = error.get("missing_scopes") or []
    if error.get("subtype") == "missing_scope" or "base:block:update" in missing_scopes:
        return {
            "status": "blocked_missing_scope",
            "missing_scopes": missing_scopes,
            "hint": error.get("hint") or 'run `lark-cli auth login --scope "base:block:update"`',
        }
    raise SyncError(f"Base block rename dry-run failed: {json.dumps(redact_json_for_log(data), ensure_ascii=False)}")


def rename_category_folders(
    base_token: str,
    report_category: str,
    *,
    dry_run: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    categories = CATEGORY_MAP[report_category]
    blocks = list_base_blocks(base_token)
    folders = resolve_category_folders(blocks, categories)
    operations = [
        {
            "placeholder": folder["placeholder"],
            "block_id": folder["block_id"],
            "from": folder["current_name"],
            "to": folder["target_name"],
            "status": folder["status"],
        }
        for folder in folders
    ]
    needs_rename = [operation for operation in operations if operation["from"] != operation["to"]]
    if not needs_rename:
        return {
            "status": "ok",
            "mode": "already_renamed",
            "operations": operations,
            "scope": {"status": "not_needed", "missing_scopes": [], "hint": None},
        }, blocks

    scope = check_block_update_scope(base_token, needs_rename[0]["block_id"])
    if scope["status"] != "ok":
        for operation in needs_rename:
            operation["status"] = "blocked_missing_scope"
        return {
            "status": "blocked_missing_scope",
            "mode": "dry_run" if dry_run else "execute",
            "operations": operations,
            "scope": scope,
        }, blocks

    if dry_run:
        for operation in needs_rename:
            operation["status"] = "planned"
        return {
            "status": "planned",
            "mode": "dry_run",
            "operations": operations,
            "scope": scope,
        }, blocks

    for operation in needs_rename:
        run_cli(
            [
                "base",
                "+base-block-rename",
                "--base-token",
                base_token,
                "--block-id",
                operation["block_id"],
                "--name",
                operation["to"],
            ]
        )
        operation["status"] = "renamed"

    blocks_after = list_base_blocks(base_token)
    verified_folders = resolve_category_folders(blocks_after, categories)
    verified_names = [folder["current_name"] for folder in verified_folders]
    if verified_names != list(categories):
        raise SyncError(f"Folder rename verification failed: expected {categories}, got {verified_names}")
    return {
        "status": "ok",
        "mode": "execute",
        "operations": operations,
        "scope": scope,
    }, blocks_after


def rename_date_fields_if_needed(
    base_token: str,
    tables: dict[str, str],
    all_fields: dict[str, dict[str, dict[str, Any]]],
    previous_date: str,
    report_date: str,
    dry_run: bool,
) -> None:
    for table_name in ["本品数据", "4.1.1", "4.1.2"]:
        targets = [
            (previous_date, ["{XXXX-XX-XX}排名", "上周排名"]),
            (report_date, ["{YYYY-YY-YY}排名", "本周排名"]),
        ]
        for target_idx, (target_name, placeholder_candidates) in enumerate(targets):
            fields = all_fields[table_name]
            final_name = f"{target_name}排名"
            if final_name in fields:
                continue
            date_rank_fields = sorted(
                [
                    name
                    for name in fields
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}排名", name)
                    and name not in {f"{previous_date}排名", f"{report_date}排名"}
                ]
            )
            if target_idx == 0:
                date_candidates = date_rank_fields
            elif f"{previous_date}排名" in fields:
                date_candidates = date_rank_fields
            else:
                date_candidates = date_rank_fields[1:] + date_rank_fields[:1]
            candidates = [*placeholder_candidates, *date_candidates]
            for candidate in candidates:
                if candidate in fields:
                    field = fields[candidate]
                    body = {"name": final_name, "type": field["type"]}
                    if "style" in field:
                        body["style"] = field["style"]
                    if "description" in field:
                        body["description"] = field["description"]
                    run_cli(
                        [
                            "base",
                            "+field-update",
                            "--base-token",
                            base_token,
                            "--table-id",
                            tables[table_name],
                            "--field-id",
                            field["id"],
                            "--json",
                            json.dumps(body, ensure_ascii=False),
                            "--yes",
                        ],
                        dry_run=dry_run,
                    )
                    if dry_run:
                        fields[final_name] = {**field, "name": final_name}
                        fields.pop(candidate, None)
                    else:
                        all_fields[table_name] = field_map(base_token, tables[table_name])
                    break


def ensure_select_options(
    base_token: str,
    tables: dict[str, str],
    all_fields: dict[str, dict[str, dict[str, Any]]],
    field_name: str,
    option_names: list[str],
    dry_run: bool,
) -> None:
    hues = ["Blue", "Orange", "Green", "Purple", "Wathet", "Yellow"]
    for table_name in REQUIRED_TABLES:
        fields = all_fields[table_name]
        field = fields.get(field_name)
        if not field or field.get("type") != "select":
            continue
        existing_options = field.get("options") or []
        existing_names = {option.get("name") for option in existing_options}
        missing = [name for name in option_names if name not in existing_names]
        if not missing:
            continue
        options = [
            {
                "name": option["name"],
                "hue": option.get("hue", hues[idx % len(hues)]),
                "lightness": option.get("lightness", "Lighter"),
            }
            for idx, option in enumerate(existing_options)
            if option.get("name")
        ]
        for idx, name in enumerate(missing, start=len(options)):
            options.append({"name": name, "hue": hues[idx % len(hues)], "lightness": "Lighter"})
        body = {
            "name": field["name"],
            "type": "select",
            "multiple": field.get("multiple", False),
            "options": options,
        }
        if "description" in field:
            body["description"] = field["description"]
        run_cli(
            [
                "base",
                "+field-update",
                "--base-token",
                base_token,
                "--table-id",
                tables[table_name],
                "--field-id",
                field["id"],
                "--json",
                json.dumps(body, ensure_ascii=False),
                "--yes",
            ],
            dry_run=dry_run,
        )
        if dry_run:
            field["options"] = options
    if not dry_run:
        for table_name in REQUIRED_TABLES:
            if field_name in all_fields[table_name]:
                all_fields[table_name] = field_map(base_token, tables[table_name])


def convert_for_field(value: Any, field: dict[str, Any]) -> Any:
    if value is None:
        return None
    field_type = field.get("type")
    if field_type in {"number"}:
        return number_value(str(value))
    if field_type == "text":
        return str(value)
    if field_type == "datetime":
        return str(value)
    if field_type == "select":
        return str(value) if value is not None else None
    return value


def write_records(
    base_token: str,
    table_name: str,
    table_id: str,
    fields: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    dry_run: bool,
    log_dir: Path,
) -> None:
    writable = [name for name in fields if name not in ATTACHMENT_FIELDS and fields[name].get("type") != "attachment"]
    record_keys = collections.OrderedDict()
    for record in records:
        for key in record:
            if key in fields and key in writable:
                record_keys[key] = None
    ordered_fields = [name for name in writable if name in record_keys]
    missing_keys = sorted({key for record in records for key in record if key not in fields})
    if missing_keys:
        raise SyncError(f"Table {table_name} is missing fields needed by parsed records: {missing_keys}")
    if not records:
        return
    for start in range(0, len(records), 200):
        chunk = records[start : start + 200]
        rows = [
            [convert_for_field(record.get(field_name), fields[field_name]) for field_name in ordered_fields]
            for record in chunk
        ]
        payload = {"fields": ordered_fields, "rows": rows}
        payload_path = log_dir / f"payload-{table_name}-{start // 200 + 1}.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(payload_path, 0o600)
        run_cli(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--json",
                f"@{payload_path}",
            ],
            dry_run=dry_run,
        )


def list_record_ids(base_token: str, table_id: str) -> list[str]:
    record_ids: list[str] = []
    offset = 0
    while True:
        data = run_cli(
            [
                "base",
                "+record-list",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--offset",
                str(offset),
                "--limit",
                "200",
                "--format",
                "json",
            ]
        )
        payload = data.get("data", {})
        ids = payload.get("record_id_list") or []
        record_ids.extend(ids)
        if not payload.get("has_more"):
            break
        offset += 200
    return record_ids


def clear_table(base_token: str, table_id: str, dry_run: bool) -> int:
    if dry_run:
        return 0
    record_ids = list_record_ids(base_token, table_id)
    for start in range(0, len(record_ids), 200):
        args = ["base", "+record-delete", "--base-token", base_token, "--table-id", table_id]
        for rid in record_ids[start : start + 200]:
            args.extend(["--record-id", rid])
        args.append("--yes")
        run_cli(args)
    return len(record_ids)


def snapshot_table_records(
    base_token: str,
    table_name: str,
    table_id: str,
    snapshot_dir: Path,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {"table": table_name, "count": 0, "path": None, "status": "dry_run"}
    records = list_records(base_token, table_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(snapshot_dir, 0o700)
    snapshot_path = snapshot_dir / f"{table_name}.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "table": table_name,
                "table_id": table_id,
                "record_count": len(records),
                "records": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    os.chmod(snapshot_path, 0o600)
    return {"table": table_name, "count": len(records), "path": display_path(snapshot_path), "status": "ok"}


def restore_snapshot_records(
    base_token: str,
    table_name: str,
    table_id: str,
    snapshot: dict[str, Any],
    dry_run: bool,
    log_dir: Path,
) -> dict[str, Any]:
    if dry_run:
        return {"table": table_name, "status": "dry_run", "restored": 0}
    snapshot_path = snapshot.get("path")
    if not snapshot_path:
        return {"table": table_name, "status": "skipped_empty_snapshot", "restored": 0}
    raw_path = Path(str(snapshot_path).replace("$PWD", str(Path.cwd())))
    try:
        data = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"table": table_name, "status": "failed", "reason": str(exc), "restored": 0}
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, list) or not records:
        clear_table(base_token, table_id, dry_run=False)
        return {"table": table_name, "status": "ok", "restored": 0}

    field_order = collections.OrderedDict()
    row_fields: list[dict[str, Any]] = []
    for record in records:
        fields = record_fields(record) if isinstance(record, dict) else {}
        row_fields.append(fields)
        for field_name in fields:
            field_order[field_name] = None
    fields = list(field_order)
    if not fields:
        return {"table": table_name, "status": "failed", "reason": "snapshot records have no fields", "restored": 0}

    clear_table(base_token, table_id, dry_run=False)
    for start in range(0, len(row_fields), 200):
        chunk = row_fields[start : start + 200]
        payload = {"fields": fields, "rows": [[row.get(field) for field in fields] for row in chunk]}
        payload_path = log_dir / f"restore-{table_name}-{start // 200 + 1}.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(payload_path, 0o600)
        run_cli(
            [
                "base",
                "+record-batch-create",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--json",
                f"@{payload_path}",
            ],
        )
    actual_count = len(list_records(base_token, table_id))
    if actual_count != len(row_fields):
        return {
            "table": table_name,
            "status": "failed",
            "reason": "restore readback count mismatch",
            "expected": len(row_fields),
            "actual": actual_count,
            "restored": len(row_fields),
        }
    return {"table": table_name, "status": "ok", "restored": len(row_fields), "actual": actual_count}


def restore_overwrite_snapshots(
    base_token: str,
    tables: dict[str, str],
    snapshots: dict[str, dict[str, Any]],
    dry_run: bool,
    log_dir: Path,
) -> dict[str, Any]:
    restored: dict[str, Any] = {}
    failures: dict[str, Any] = {}
    for table_name, snapshot in snapshots.items():
        try:
            result = restore_snapshot_records(
                base_token,
                table_name,
                tables[table_name],
                snapshot,
                dry_run,
                log_dir,
            )
        except Exception as exc:
            result = {"table": table_name, "status": "failed", "reason": str(exc), "restored": 0}
        restored[table_name] = result
        if result.get("status") not in {"ok", "dry_run", "skipped_empty_snapshot"}:
            failures[table_name] = result
    return {
        "status": "failed" if failures else "ok",
        "restored_tables": restored,
        "restore_failures": failures,
    }


def list_views(base_token: str, table_id: str) -> list[dict[str, Any]]:
    data = run_cli(["base", "+view-list", "--base-token", base_token, "--table-id", table_id, "--limit", "100"])
    views = data.get("data", {}).get("views") or data.get("data", {}).get("items") or []
    return [view for view in views if isinstance(view, dict)]


def get_visible_fields(base_token: str, table_id: str, view_id: str) -> list[str]:
    data = run_cli(
        [
            "base",
            "+view-get-visible-fields",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--view-id",
            view_id,
        ]
    )
    visible_fields = data.get("data", {}).get("visible_fields")
    if not isinstance(visible_fields, list):
        raise SyncError(f"Cannot read visible fields for table {table_id} view {view_id}")
    return [str(name) for name in visible_fields]


def template_grid_view_specs(base_token: str) -> dict[str, list[dict[str, Any]]]:
    template_tables = table_map(base_token)
    specs: dict[str, list[dict[str, Any]]] = {}
    for table_name in REQUIRED_TABLES:
        table_id = template_tables[table_name]
        table_specs: list[dict[str, Any]] = []
        for view in list_views(base_token, table_id):
            if view.get("type") != "grid":
                continue
            view_id = str(view.get("id") or view.get("view_id"))
            table_specs.append(
                {
                    "name": str(view.get("name") or view_id),
                    "type": "grid",
                    "visible_fields": get_visible_fields(base_token, table_id, view_id),
                }
            )
        if not table_specs:
            raise SyncError(f"Template table {table_name} has no grid view")
        specs[table_name] = table_specs
    return specs


def adapt_template_visible_fields(
    table_name: str,
    visible_fields: list[str],
    target_fields: dict[str, dict[str, Any]],
    previous_date: str,
    report_date: str,
) -> list[str]:
    target_names = set(target_fields)
    adapted: list[str] = []
    template_date_rank_fields = [
        name for name in visible_fields if re.fullmatch(r"\d{4}-\d{2}-\d{2}排名", name)
    ]
    previous_rank_template = template_date_rank_fields[0] if template_date_rank_fields else None
    report_rank_template = template_date_rank_fields[1] if len(template_date_rank_fields) > 1 else None

    def candidates_for(name: str) -> list[str]:
        if table_name not in OWN_TABLES:
            return [name]
        if name in {"{XXXX-XX-XX}排名", "上周排名"} or name == previous_rank_template:
            return [f"{previous_date}排名", name]
        if name in {"{YYYY-YY-YY}排名", "本周排名"} or name == report_rank_template:
            return [f"{report_date}排名", name]
        return [name]

    missing: list[str] = []
    for field_name in visible_fields:
        matched = next((candidate for candidate in candidates_for(field_name) if candidate in target_names), None)
        if matched:
            if matched not in adapted:
                adapted.append(matched)
        else:
            missing.append(field_name)

    extra = [name for name in target_fields if name not in adapted]
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing from target after template adaptation: {missing}")
        if extra:
            details.append(f"extra target fields not present in template visible order: {extra}")
        raise SyncError(f"Table {table_name} field order does not match template: {'; '.join(details)}")
    return adapted


def set_view_visible_fields(
    base_token: str,
    table_id: str,
    view_id: str,
    visible_fields: list[str],
    dry_run: bool,
) -> None:
    run_cli(
        [
            "base",
            "+view-set-visible-fields",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--view-id",
            view_id,
            "--json",
            json.dumps({"visible_fields": visible_fields}, ensure_ascii=False),
        ],
        dry_run=dry_run,
    )


def ensure_template_view_layout(
    base_token: str,
    tables: dict[str, str],
    all_fields: dict[str, dict[str, dict[str, Any]]],
    template_views: dict[str, list[dict[str, Any]]],
    previous_date: str,
    report_date: str,
    dry_run: bool,
) -> dict[str, list[str]]:
    changed: dict[str, list[str]] = {}
    for table_name in REQUIRED_TABLES:
        table_id = tables[table_name]
        target_views = [view for view in list_views(base_token, table_id) if view.get("type") == "grid"]
        target_by_name = {str(view.get("name") or view.get("id") or view.get("view_id")): view for view in target_views}
        template_by_name = {str(view["name"]): view for view in template_views[table_name]}
        missing_views = [name for name in template_by_name if name not in target_by_name]
        extra_views = [name for name in target_by_name if name not in template_by_name]
        if missing_views or extra_views:
            details = []
            if missing_views:
                details.append(f"missing template views: {missing_views}")
            if extra_views:
                details.append(f"extra target views not in template: {extra_views}")
            raise SyncError(f"Table {table_name} views do not match template: {'; '.join(details)}")

        for view_name, template_view in template_by_name.items():
            view = target_by_name[view_name]
            view_id = str(view.get("id") or view.get("view_id"))
            expected = adapt_template_visible_fields(
                table_name,
                template_view["visible_fields"],
                all_fields[table_name],
                previous_date,
                report_date,
            )
            current = get_visible_fields(base_token, table_id, view_id)
            if current == expected:
                continue
            set_view_visible_fields(base_token, table_id, view_id, expected, dry_run)
            changed.setdefault(table_name, []).append(view_name)
    return changed


def list_records(base_token: str, table_id: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        data = run_cli(
            [
                "base",
                "+record-list",
                "--base-token",
                base_token,
                "--table-id",
                table_id,
                "--offset",
                str(offset),
                "--limit",
                "200",
                "--format",
                "json",
            ]
        )
        payload = data.get("data", {})
        rows = payload.get("data") or payload.get("records") or payload.get("items") or []
        field_names = payload.get("fields") or []
        record_ids = payload.get("record_id_list") or []
        if isinstance(rows, list):
            for idx, row in enumerate(rows):
                if isinstance(row, dict):
                    records.append(row)
                elif isinstance(row, list) and field_names:
                    fields = {
                        field_name: row[field_idx]
                        for field_idx, field_name in enumerate(field_names[: len(row)])
                    }
                    record: dict[str, Any] = {"fields": fields}
                    if idx < len(record_ids):
                        record["record_id"] = record_ids[idx]
                    records.append(record)
        if not payload.get("has_more"):
            break
        offset += 200
    return records


def verify_written_records(
    base_token: str,
    tables: dict[str, str],
    expected_counts: dict[str, int],
    *,
    dry_run: bool,
    prepare_only: bool,
    overwrite: bool,
) -> dict[str, Any]:
    if dry_run:
        return {"status": "dry_run", "expected_counts": expected_counts, "actual_counts": {}, "issues": [], "duplicates": {}}
    if prepare_only:
        return {"status": "skipped_prepare_only", "expected_counts": expected_counts, "actual_counts": {}, "issues": [], "duplicates": {}}
    if not overwrite:
        return {"status": "skipped_non_overwrite", "expected_counts": expected_counts, "actual_counts": {}, "issues": [], "duplicates": {}}

    duplicate_specs = {
        "异动数据": ["ASIN", "类目", "数据类型"],
        "低分高销数据": ["ASIN", "类目"],
        "本品数据": ["ASIN", "类目"],
    }

    attempts = max(1, BASE_VERIFY_MAX_ATTEMPTS)
    actual_counts: dict[str, int] = {}
    issues: list[str] = []
    duplicates: dict[str, list[tuple[Any, ...]]] = {}

    for attempt in range(1, attempts + 1):
        actual_counts = {}
        issues = []
        duplicates = {}

        for table_name in REQUIRED_TABLES:
            records = list_records(base_token, tables[table_name])
            actual_counts[table_name] = len(records)
            expected = expected_counts.get(table_name, 0)
            if len(records) != expected:
                issues.append(f"{table_name}: expected {expected} records, got {len(records)}")

            seen: set[tuple[Any, ...]] = set()
            table_duplicates: list[tuple[Any, ...]] = []
            duplicate_keys = duplicate_specs.get(table_name)
            for idx, record in enumerate(records, start=1):
                fields = record_fields(record)
                img = cell_text(fields.get("商品图片"))
                asin = cell_text(fields.get("ASIN"))
                if img and (not img.startswith("https://") or "<img" in img):
                    issues.append(f"{table_name} row {idx}: invalid image {img}")
                if asin and not re.fullmatch(r"\[[A-Z0-9]{10}\]\(https://www\.amazon\.com/dp/[A-Z0-9]{10}\)", asin):
                    issues.append(f"{table_name} row {idx}: invalid ASIN link {asin}")
                if duplicate_keys:
                    key = tuple(cell_text(fields.get(name)) for name in duplicate_keys)
                    if key in seen:
                        table_duplicates.append(key)
                    seen.add(key)
            if table_duplicates:
                duplicates[table_name] = table_duplicates

        if duplicates:
            issues.append("Duplicate business keys found in " + ", ".join(sorted(duplicates)))
        if not issues or attempt == attempts:
            break

        log_progress(
            "record verification mismatch "
            f"(attempt {attempt}/{attempts}); retrying in {BASE_VERIFY_RETRY_DELAY_SECONDS}s"
        )
        time.sleep(max(0, BASE_VERIFY_RETRY_DELAY_SECONDS))

    return {
        "status": "ok" if not issues else "failed",
        "expected_counts": expected_counts,
        "actual_counts": actual_counts,
        "issues": issues,
        "duplicates": duplicates,
    }


def has_blocking_result(folder_rename: dict[str, Any], server_verification: dict[str, Any]) -> bool:
    return (
        server_verification.get("status") == "failed"
        or folder_rename.get("status") == "blocked_missing_scope"
    )


def main() -> int:
    global LARK_CLI_TIMEOUT_SECONDS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--base-token", required=True)
    parser.add_argument("--category", choices=sorted(CATEGORY_MAP))
    parser.add_argument("--date", required=True, help="Report date, e.g. 2026-05-13")
    parser.add_argument("--previous-date", help="Previous report date; defaults to date - 7 days")
    parser.add_argument("--overwrite", action="store_true", help="Delete all records in target tables before writing")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Prepare Base schema, select options, and view field order without parsing a report or writing records.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--log-dir", type=Path, default=Path("logs/sorftime-report-base-sync"))
    parser.add_argument("--rename-folders", action="store_true", help="Rename Base root folders {类目1}/{类目2} by CLI.")
    parser.add_argument(
        "--lark-cli-timeout-seconds",
        type=int,
        default=LARK_CLI_TIMEOUT_SECONDS,
        help="Timeout for each lark-cli subprocess call.",
    )
    parser.add_argument(
        "--template-base-token",
        default=DEFAULT_TEMPLATE_BASE_TOKEN,
        help="Template Base token used to enforce target view field order.",
    )
    args = parser.parse_args()

    LARK_CLI_TIMEOUT_SECONDS = args.lark_cli_timeout_seconds
    if not args.template_base_token:
        raise SyncError("--template-base-token or FEISHU_TEMPLATE_BASE_TOKEN is required")

    report_date = dt.date.fromisoformat(args.date).isoformat()
    previous_date = dt.date.fromisoformat(args.previous_date).isoformat() if args.previous_date else previous_week(report_date)
    if args.prepare_only:
        if not args.category:
            raise SyncError("--category is required with --prepare-only")
        report_category = args.category
    else:
        if not args.report:
            raise SyncError("--report is required unless --prepare-only is set")
        if not args.report.exists():
            raise SyncError(f"Report not found: {args.report}")
        report_category = infer_report_category(args.report, args.category)
    categories = CATEGORY_MAP[report_category]

    run_id = f"{report_date}-{report_category}-{int(time.time())}"
    log_dir = args.log_dir / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)

    log_progress(f"start category={report_category} date={report_date} dry_run={args.dry_run}")
    if args.prepare_only:
        mother = {"异动数据": [], "低分高销数据": [], "本品数据": []}
        child = {section: [] for section in REQUIRED_TABLES if re.match(r"^[234]\.", section)}
        validation = validate_records(mother, child)
    else:
        assert args.report is not None
        mother, child = build_records(args.report, report_category, report_date, previous_date)
        validation = validate_records(mother, child)
    if validation["issues"]:
        raise SyncError("Pre-write validation failed:\n" + "\n".join(validation["issues"]))

    summary_path = log_dir / "parsed-summary.json"
    summary_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(summary_path, 0o600)

    log_progress("reading target Base tables and fields")
    tables = table_map(args.base_token)
    all_fields = {name: field_map(args.base_token, table_id) for name, table_id in tables.items() if name in REQUIRED_TABLES}
    log_progress("validating initial Base block layout")
    initial_blocks = list_base_blocks(args.base_token)
    block_layout = validate_block_layout(initial_blocks, tables, categories)
    folder_rename = {
        "status": "not_requested",
        "mode": "not_requested",
        "operations": [],
        "scope": {"status": "not_checked", "missing_scopes": [], "hint": None},
    }
    if args.rename_folders:
        log_progress("renaming Base category folders by CLI")
        folder_rename, renamed_blocks = rename_category_folders(
            args.base_token,
            report_category,
            dry_run=args.dry_run,
        )
        if folder_rename["status"] == "ok":
            block_layout = validate_block_layout(renamed_blocks, tables, categories)
        elif folder_rename["status"] == "planned":
            block_layout = validate_block_layout(initial_blocks, tables, categories)

    log_progress("preparing fields and select options")
    rename_date_fields_if_needed(args.base_token, tables, all_fields, previous_date, report_date, args.dry_run)
    ensure_select_options(args.base_token, tables, all_fields, "类目", list(categories), args.dry_run)
    ensure_select_options(
        args.base_token,
        tables,
        all_fields,
        "数据类型",
        ["TOP10产品", "强势上升产品", "强势下降产品", "新上榜产品"],
        args.dry_run,
    )
    ensure_select_options(args.base_token, tables, all_fields, "是否新品", ["是", "否"], args.dry_run)

    log_progress("checking template view layouts")
    template_views = template_grid_view_specs(args.template_base_token)
    field_order_changes = ensure_template_view_layout(
        args.base_token,
        tables,
        all_fields,
        template_views,
        previous_date,
        report_date,
        args.dry_run,
    )

    overwrite_recovery: dict[str, Any] = {
        "status": "not_applicable",
        "snapshots": {},
        "cleared_tables": {},
        "written_tables": [],
        "failed_tables": [],
        "snapshot_failures": {},
        "clear_failures": {},
        "write_failures": [],
        "restored_tables": {},
        "restore_failures": {},
    }
    write_error: str | None = None
    server_verification: dict[str, Any]
    current_write_table: str | None = None

    try:
        if args.overwrite and not args.prepare_only:
            log_progress("snapshotting existing records")
            snapshot_dir = log_dir / "snapshots"
            for table_name in REQUIRED_TABLES:
                try:
                    overwrite_recovery["snapshots"][table_name] = snapshot_table_records(
                        args.base_token,
                        table_name,
                        tables[table_name],
                        snapshot_dir,
                        args.dry_run,
                    )
                except Exception as exc:
                    overwrite_recovery["snapshot_failures"][table_name] = str(exc)
                    raise
            overwrite_recovery["status"] = "snapshotted"

            log_progress("clearing existing records")
            for table_name in REQUIRED_TABLES:
                try:
                    overwrite_recovery["cleared_tables"][table_name] = clear_table(
                        args.base_token,
                        tables[table_name],
                        args.dry_run,
                    )
                except Exception as exc:
                    overwrite_recovery["clear_failures"][table_name] = str(exc)
                    raise
            overwrite_recovery.update(
                {
                    "status": "cleared",
                }
            )
            (log_dir / "deleted-counts.json").write_text(
                json.dumps(overwrite_recovery["cleared_tables"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.chmod(log_dir / "deleted-counts.json", 0o600)

        if not args.prepare_only:
            log_progress("writing mother tables")
            for table_name, records in mother.items():
                current_write_table = table_name
                write_records(
                    args.base_token,
                    table_name,
                    tables[table_name],
                    all_fields[table_name],
                    records,
                    args.dry_run,
                    log_dir,
                )
                overwrite_recovery["written_tables"].append(table_name)
            log_progress("writing child tables")
            for table_name, records in child.items():
                current_write_table = table_name
                write_records(
                    args.base_token,
                    table_name,
                    tables[table_name],
                    all_fields[table_name],
                    records,
                    args.dry_run,
                    log_dir,
                )
                overwrite_recovery["written_tables"].append(table_name)
            current_write_table = None

        log_progress("verifying records from Base")
        server_verification = verify_written_records(
            args.base_token,
            tables,
            validation["counts"],
            dry_run=args.dry_run,
            prepare_only=args.prepare_only,
            overwrite=args.overwrite,
        )
        if (
            args.overwrite
            and not args.prepare_only
            and not args.dry_run
            and server_verification.get("status") == "failed"
        ):
            overwrite_recovery["failed_tables"] = [
                issue.split(":", 1)[0]
                for issue in server_verification.get("issues", [])
                if isinstance(issue, str) and ":" in issue
            ]
            recovery = restore_overwrite_snapshots(
                args.base_token,
                tables,
                overwrite_recovery["snapshots"],
                args.dry_run,
                log_dir,
            )
            overwrite_recovery.update(recovery)
    except Exception as exc:
        write_error = str(exc)
        if current_write_table:
            overwrite_recovery["failed_tables"].append(current_write_table)
            overwrite_recovery["write_failures"].append(
                {"table": current_write_table, "error": write_error}
            )
        server_verification = {
            "status": "failed",
            "expected_counts": validation["counts"],
            "actual_counts": {},
            "issues": [f"write failed: {write_error}"],
            "duplicates": {},
        }
        if args.overwrite and not args.prepare_only:
            recovery = restore_overwrite_snapshots(
                args.base_token,
                tables,
                overwrite_recovery["snapshots"],
                args.dry_run,
                log_dir,
            )
            overwrite_recovery.update(recovery)

    result = {
        "report": display_path(args.report) if args.report else None,
        "base_token_present": bool(args.base_token),
        "category": report_category,
        "date": report_date,
        "previous_date": previous_date,
        "prepare_only": args.prepare_only,
        "counts": validation["counts"],
        "duplicates": validation["duplicates"],
        "folder_rename": folder_rename,
        "block_layout": block_layout,
        "field_order": {
            "template_base_token_present": bool(args.template_base_token),
            "changed_views": field_order_changes,
            "checked_tables": REQUIRED_TABLES,
            "template_views": {table_name: [view["name"] for view in views] for table_name, views in template_views.items()},
        },
        "server_verification": server_verification,
        "overwrite_recovery": overwrite_recovery,
        "log_dir": display_path(log_dir),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if write_error or overwrite_recovery.get("restore_failures"):
        return 1
    if has_blocking_result(folder_rename, server_verification):
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SyncError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
