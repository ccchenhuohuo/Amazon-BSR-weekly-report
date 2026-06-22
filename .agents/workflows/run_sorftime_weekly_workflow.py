#!/usr/bin/env python3
"""Project-level runner for the Sorftime weekly report workflow.

This script orchestrates the three project-scoped skills. It intentionally keeps
domain logic inside the skills and only handles date calculation, command order,
and summary collection.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"
CATEGORIES = ("灯光类", "支架类", "脚架类")
SECRET_FLAGS = {"--base-token", "--template-base-token", "--folder-token"}
SECRET_KEY_PARTS = ("token", "password", "secret", "api_key", "url")
SECRET_OUTPUT_KEYS = (
    "app_token",
    "base_token",
    "template_base_token",
    "folder_token",
    "token",
    "url",
)
LARK_CLI_BIN = "lark-cli"


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


def most_recent_finished_wednesday(now: datetime | None = None) -> date:
    current = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    # Monday=0, Wednesday=2. On Friday 17:00 this resolves to the current week.
    days_since_wednesday = (current.weekday() - 2) % 7
    if days_since_wednesday == 0 and current.hour < 23:
        days_since_wednesday = 7
    return (current.date() - timedelta(days=days_since_wednesday))


def default_report_dir() -> Path:
    configured = os.environ.get("SORFTIME_REPORT_OUTPUT_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_REPORT_DIR


def load_dotenv(path: Path | None = None) -> None:
    paths = [path] if path is not None else [PROJECT_ROOT / ".env"]
    for env_path in paths:
        if env_path is None or not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def report_path(report_date: date, category: str, report_dir: Path) -> Path:
    return report_dir / f"{report_date:%Y%m%d}{category}周趋势监测报告.md"


def redact_command(command: list[str] | None) -> list[str] | None:
    if command is None:
        return None
    redacted = list(command)
    for idx, part in enumerate(redacted[:-1]):
        if part in SECRET_FLAGS:
            redacted[idx + 1] = "[REDACTED]"
    return [redact_output_text(part) for part in redacted]


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
    return redact_local_paths(redacted)


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


def parse_copied_base(data: dict, source_token: str) -> dict[str, str | None]:
    base = data.get("base")
    if not isinstance(base, dict):
        data_field = data.get("data")
        if isinstance(data_field, dict):
            base = data_field.get("base")
    if not isinstance(base, dict):
        base = {}

    token = (
        base.get("app_token")
        or base.get("base_token")
        or base.get("token")
        or base.get("id")
        or find_nested_value(data, {"app_token", "base_token"})
    )
    url = base.get("url")
    name = base.get("name") or base.get("title") or find_nested_value(data, {"name", "title"})

    # Dry-run output contains the source token; never treat that as a copied Base.
    if token == source_token:
        token = None
    return {"token": token, "url": url, "name": name}


def parse_base_sync_result(result: StepResult) -> dict:
    data = extract_json_object(result.stdout)
    if not data:
        return {}
    counts = data.get("counts") if isinstance(data.get("counts"), dict) else {}
    mother_names = {"异动数据", "低分高销数据", "本品数据"}
    mother_counts = {k: v for k, v in counts.items() if k in mother_names}
    child_counts = {k: v for k, v in counts.items() if k not in mother_names}
    return {
        "report": data.get("report"),
        "base_token_present": bool(data.get("base_token") or data.get("base_token_present")),
        "category": data.get("category"),
        "date": data.get("date"),
        "previous_date": data.get("previous_date"),
        "mother_counts": mother_counts,
        "child_counts": child_counts,
        "duplicates": data.get("duplicates"),
        "field_order": data.get("field_order"),
        "folder_rename": data.get("folder_rename"),
        "block_layout": data.get("block_layout"),
        "server_verification": data.get("server_verification"),
        "base_sync_log_dir": data.get("log_dir"),
    }


def copy_base_for_category(
    category: str,
    report_date: date,
    template_base_token: str,
    folder_token: str | None,
    dry_run: bool,
    timeout_seconds: int,
) -> tuple[StepResult, str | None]:
    name = f"{report_date:%Y%m%d}{category}周趋势监测报告数据"
    if not template_base_token:
        return (
            StepResult(
                name=f"base-copy:{category}",
                status="failed",
                detail={
                    "reason": "missing template base token",
                    "required": "pass --template-base-token or set FEISHU_TEMPLATE_BASE_TOKEN",
                    "requested_name": name,
                },
            ),
            None,
        )
    command = [
        LARK_CLI_BIN,
        "base",
        "+base-copy",
        "--base-token",
        template_base_token,
        "--name",
        name,
        "--time-zone",
        "Asia/Shanghai",
        "--without-content",
        "--as",
        "user",
    ]
    if folder_token:
        command.extend(["--folder-token", folder_token])
    if dry_run:
        command.append("--dry-run")

    result = run_command(f"base-copy:{category}", command, timeout_seconds=timeout_seconds)
    data = extract_json_object(result.stdout)
    copied = parse_copied_base(data, source_token=template_base_token)
    copied_token = copied.get("token")
    copy_ok = data.get("ok") if data else None
    if result.status == "ok" and not dry_run and (copy_ok is False or not copied_token):
        result.status = "failed"
        result.stderr = (result.stderr + "\nBase copy did not return a new Base token.").strip()
    result.detail = {
        "template_base_token_present": bool(template_base_token),
        "requested_name": name,
        "copied_base_token_present": bool(copied_token),
        "copied_base_url_present": bool(copied.get("url")),
        "copy_response_ok": copy_ok,
        "dry_run": dry_run,
    }
    return result, copied_token


def print_result(result: StepResult) -> None:
    if result.streamed:
        return
    print(f"\n## {result.name}: {result.status}")
    if result.command:
        print("$ " + " ".join(result.command))
    if result.stdout.strip():
        print(redact_output_text(result.stdout.strip()))
    if result.stderr.strip():
        print(redact_output_text(result.stderr.strip()), file=sys.stderr)


def command_text(command: list[str] | None) -> str:
    if not command:
        return ""
    return " ".join(shlex.quote(part) for part in command)


def report_date_text(report_date: date | str) -> str:
    return report_date.isoformat() if isinstance(report_date, date) else str(report_date)


def write_run_report(
    path: Path,
    report_date: date | str,
    dry_run: bool,
    log_dir: Path,
    results: list[StepResult],
) -> None:
    status_icon = {"ok": "OK", "failed": "FAILED", "skipped": "SKIPPED"}
    lines = [
        f"# Sorftime 周度自动化运行报告 {report_date_text(report_date)}",
        "",
        f"- 运行目录：`{redact_output_text(str(log_dir))}`",
        f"- dry-run：`{str(dry_run).lower()}`",
        f"- 生成时间：`{datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds')}`",
        "",
        "## 步骤状态",
        "",
        "| 步骤 | 状态 | 返回码 |",
        "| --- | --- | --- |",
    ]
    for result in results:
        returncode = "" if result.returncode is None else str(result.returncode)
        lines.append(f"| `{result.name}` | {status_icon.get(result.status, result.status)} | {returncode} |")

    lines.extend(["", "## Base 复制结果", ""])
    copy_results = [item for item in results if item.name.startswith("base-copy:")]
    if copy_results:
        lines.extend(["| 类目 | 请求名称 | 新 Base token | 新 Base URL |", "| --- | --- | --- | --- |"])
        for item in copy_results:
            detail = item.detail or {}
            category = item.name.split(":", 1)[1]
            lines.append(
                "| {category} | `{name}` | `{token}` | {url} |".format(
                    category=category,
                    name=detail.get("requested_name") or "",
                    token="[REDACTED]" if detail.get("copied_base_token_present") else "",
                    url="[REDACTED]" if detail.get("copied_base_url_present") else "",
                )
            )
    else:
        lines.append("本次没有执行 Base 复制，可能使用了显式 `--base-token` 或跳过了 Base sync。")

    lines.extend(["", "## Base Sync 结构化汇总", ""])
    sync_results = [item for item in results if item.name.startswith("base-sync:") and item.detail]
    if sync_results:
        for item in sync_results:
            detail = item.detail or {}
            if "mother_counts" not in detail:
                continue
            category = item.name.split(":", 1)[1]
            lines.extend(
                [
                    f"### {category}",
                    "",
                    f"- 报告：`{redact_output_text(str(detail.get('report') or ''))}`",
                    f"- Base token：`{'[REDACTED]' if detail.get('base_token_present') else ''}`",
                    f"- Base sync 日志：`{redact_output_text(str(detail.get('base_sync_log_dir') or ''))}`",
                    "",
                    "| 表类型 | 表名 | 记录数 |",
                    "| --- | --- | --- |",
                ]
            )
            for table_name, count in (detail.get("mother_counts") or {}).items():
                lines.append(f"| 母表 | `{table_name}` | {count} |")
            for table_name, count in (detail.get("child_counts") or {}).items():
                lines.append(f"| 子表 | `{table_name}` | {count} |")
            duplicates = detail.get("duplicates") or {}
            duplicate_tables = [name for name, values in duplicates.items() if values]
            duplicate_text = "无" if not duplicate_tables else "、".join(duplicate_tables)
            field_order = detail.get("field_order") or {}
            changed_views = field_order.get("changed_views") or {}
            changed_count = sum(len(views) for views in changed_views.values())
            folder_rename = detail.get("folder_rename") or {}
            block_layout = detail.get("block_layout") or {}
            server_verification = detail.get("server_verification") or {}
            lines.extend(
                [
                    "",
                    f"- 重复检查：{duplicate_text}",
                    f"- 字段顺序修正视图数：{changed_count}",
                    f"- 左侧分组 CLI 重命名：{folder_rename.get('status') or '未执行'}",
                    f"- Base block 层级校验：{block_layout.get('status') or '未执行'}",
                    f"- 服务端回读校验：{server_verification.get('status') or '未执行'}",
                    "",
                ]
            )
    else:
        lines.append("本次没有可解析的 Base sync JSON 汇总。")

    lines.extend(
        [
            "",
            "## Base 左侧分组 CLI 状态",
            "",
            "Base 左侧 `{类目1}` / `{类目2}` 通过 `lark-cli base +base-block-list/+base-block-rename` 处理；Chrome/Computer Use 仅作为人工排障 fallback。",
            "",
            "| 报告类目 | CLI 状态 | `{类目1}` | `{类目2}` | 说明 |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    if sync_results:
        for item in sync_results:
            detail = item.detail or {}
            category = item.name.split(":", 1)[1]
            folder_rename = detail.get("folder_rename") or {}
            operations = folder_rename.get("operations") or []
            op_by_placeholder = {operation.get("placeholder"): operation for operation in operations}

            def operation_text(placeholder: str) -> str:
                operation = op_by_placeholder.get(placeholder) or {}
                if not operation:
                    return ""
                return "{old} -> {new} ({status})".format(
                    old=operation.get("from") or "",
                    new=operation.get("to") or "",
                    status=operation.get("status") or "",
                )

            scope = folder_rename.get("scope") or {}
            note = scope.get("hint") if folder_rename.get("status") == "blocked_missing_scope" else ""
            lines.append(
                "| {category} | `{status}` | {folder1} | {folder2} | {note} |".format(
                    category=category,
                    status=folder_rename.get("status") or "未执行",
                    folder1=operation_text("{类目1}"),
                    folder2=operation_text("{类目2}"),
                    note=note or "",
                )
            )
    else:
        lines.append("| - | `未执行` |  |  | 本次没有 Base sync 结果 |")
    lines.extend(
        [
            "",
            "## 命令明细",
            "",
        ]
    )
    for result in results:
        lines.extend([f"### {result.name}", "", "```bash", command_text(result.command), "```", ""])
        if result.detail:
            lines.extend(["```json", json.dumps(redact_detail(result.detail), ensure_ascii=False, indent=2), "```", ""])

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def write_workflow_outputs(
    log_dir: Path,
    report_date: date | str,
    dry_run: bool,
    results: list[StepResult],
) -> tuple[Path, Path]:
    summary = {
        "report_date": report_date_text(report_date),
        "dry_run": dry_run,
        "log_dir": redact_output_text(str(log_dir)),
        "results": [
            {
                "name": item.name,
                "status": item.status,
                "returncode": item.returncode,
                "command": item.command,
                "detail": redact_detail(item.detail),
            }
            for item in results
        ],
    }
    summary_path = log_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path_out = log_dir / "run-report.md"
    write_run_report(report_path_out, report_date, dry_run, log_dir, results)
    return summary_path, report_path_out


def parse_base_tokens(values: list[str]) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--base-token must use CATEGORY=TOKEN format")
        category, token = value.split("=", 1)
        category = category.strip()
        token = token.strip()
        if category not in CATEGORIES:
            raise ValueError("--base-token category must be one of the configured report categories")
        if not token:
            raise ValueError("--base-token value cannot be empty")
        tokens[category] = token
    return tokens


def load_base_token_json(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("--base-token-json must contain an object")
    return {str(k): str(v) for k, v in data.items()}


def main() -> int:
    load_dotenv()
    global LARK_CLI_BIN
    LARK_CLI_BIN = os.environ.get("LARK_CLI_BIN", "lark-cli")

    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Report date, YYYY-MM-DD. Defaults to recent finished Wednesday.")
    parser.add_argument("--dry-run", action="store_true", help="Do not write Doris/Base or final reports.")
    parser.add_argument("--skip-bsr", action="store_true")
    parser.add_argument("--skip-report", action="store_true")
    parser.add_argument("--skip-base-sync", action="store_true")
    parser.add_argument("--overwrite-reports", action="store_true")
    parser.add_argument("--no-overwrite-reports", action="store_true", help="Do not overwrite existing markdown reports.")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--command-timeout-seconds", type=int, default=1800)
    parser.add_argument("--lark-cli-timeout-seconds", type=int, default=env_int("LARK_CLI_TIMEOUT_SECONDS", 240))
    parser.add_argument(
        "--template-base-token",
        default=os.environ.get("FEISHU_TEMPLATE_BASE_TOKEN", ""),
        help="Template Base token used when a category has no explicit Base token.",
    )
    parser.add_argument("--report-dir", type=Path, default=default_report_dir(), help="Directory for generated markdown reports.")
    parser.add_argument("--folder-token", help="Optional destination Drive folder token for copied Bases.")
    parser.add_argument("--no-copy-bases", action="store_true", help="Do not copy template Bases when token is missing.")
    parser.add_argument("--base-token", action="append", default=[], help="Optional override: CATEGORY=BASE_TOKEN.")
    parser.add_argument("--base-token-json", type=Path, help="Optional JSON object mapping category to Base token.")
    parser.add_argument("--keep-going", action="store_true", help="Continue after a failed category step.")
    args = parser.parse_args()

    run_id = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S-%f")
    log_dir = PROJECT_ROOT / "logs" / "sorftime-weekly-workflow" / run_id
    log_dir.mkdir(parents=True, exist_ok=True)
    results: list[StepResult] = []

    try:
        report_date = (
            datetime.strptime(args.date, "%Y-%m-%d").date()
            if args.date
            else most_recent_finished_wednesday()
        )

        base_tokens = load_base_token_json(args.base_token_json)
        base_tokens.update(parse_base_tokens(args.base_token))
        if args.no_overwrite_reports and not args.skip_base_sync:
            raise ValueError("--no-overwrite-reports cannot be used while Base sync is enabled")
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        results.append(
            StepResult(
                "argument-validation",
                "failed",
                detail={"reason": str(exc), "date": args.date or "auto"},
            )
        )
        summary_path, report_path_out = write_workflow_outputs(
            log_dir,
            args.date or "auto",
            args.dry_run,
            results,
        )
        print(f"dry_run={args.dry_run}")
        print(f"log_dir={redact_output_text(str(log_dir))}")
        print(f"\nsummary={redact_output_text(str(summary_path))}")
        print(f"run_report={redact_output_text(str(report_path_out))}")
        return 1

    print(f"report_date={report_date.isoformat()}")
    print(f"dry_run={args.dry_run}")
    print(f"log_dir={redact_output_text(str(log_dir))}")

    stop_on_failure = not args.keep_going
    abort_remaining = False

    if not args.skip_bsr:
        bsr_cmd = [
            sys.executable,
            ".agents/skills/sorftime-bsr-sync/scripts/sorftime_api/category/CategoryRequest/fill_missing.py",
            "--dates",
            report_date.isoformat(),
            "--weekday",
            "wednesday",
            "--force",
            "--parallel",
            "--max-workers",
            str(args.max_workers),
        ]
        if args.dry_run:
            bsr_cmd.append("--dry-run")
        result = run_command("bsr-sync", bsr_cmd, timeout_seconds=args.command_timeout_seconds)
        results.append(result)
        print_result(result)
        abort_remaining = stop_on_failure and result.status == "failed"

    report_failed: dict[str, str] = {}

    # Stage 1: generate all category reports before any Base sync starts.
    if not args.skip_report:
        for category in CATEGORIES:
            if abort_remaining:
                report_failed[category] = "previous step failed"
                results.append(
                    StepResult(f"weekly-report:{category}", "skipped", detail={"reason": "previous step failed"})
                )
                continue

            report_cmd = [
                sys.executable,
                ".agents/skills/sorftime-weekly-report/scripts/generate_weekly_report.py",
                "--category",
                category,
                "--date",
                report_date.isoformat(),
                "--out-dir",
                str(args.report_dir),
            ]
            if args.dry_run:
                report_cmd.append("--dry-run")
            elif not args.no_overwrite_reports:
                report_cmd.append("--overwrite")
            result = run_command(f"weekly-report:{category}", report_cmd, timeout_seconds=args.command_timeout_seconds)
            results.append(result)
            print_result(result)
            if stop_on_failure and result.status == "failed":
                report_failed[category] = "weekly report failed"
                abort_remaining = True
            elif result.status == "failed":
                report_failed[category] = "weekly report failed"

    # Stage 2: sync reports to Base only after report generation is complete.
    for category in CATEGORIES:
        if abort_remaining:
            results.append(StepResult(f"base-sync:{category}", "skipped", detail={"reason": "previous step failed"}))
            continue
        if category in report_failed:
            results.append(StepResult(f"base-sync:{category}", "skipped", detail={"reason": report_failed[category]}))
            continue
        if args.skip_base_sync:
            results.append(StepResult(f"base-sync:{category}", "skipped", detail={"reason": "--skip-base-sync"}))
            continue

        token = base_tokens.get(category)
        path = report_path(report_date, category, args.report_dir)
        if not token:
            if args.no_copy_bases:
                results.append(
                    StepResult(
                        f"base-sync:{category}",
                        "skipped",
                        detail={"reason": "missing base token and --no-copy-bases", "report": str(path)},
                    )
                )
                print(f"\n## base-sync:{category}: skipped (missing base token and --no-copy-bases)")
                continue
            copy_result, token = copy_base_for_category(
                category=category,
                report_date=report_date,
                template_base_token=args.template_base_token,
                folder_token=args.folder_token,
                dry_run=args.dry_run,
                timeout_seconds=args.command_timeout_seconds,
            )
            results.append(copy_result)
            print_result(copy_result)
            if stop_on_failure and copy_result.status == "failed":
                results.append(StepResult(f"base-sync:{category}", "skipped", detail={"reason": "base copy failed"}))
                abort_remaining = True
                continue
            if token:
                base_tokens[category] = token
            else:
                results.append(
                    StepResult(
                        f"base-sync:{category}",
                        "skipped",
                        detail={
                            "reason": "base copy dry-run or copy result did not include a new token",
                            "report": str(path),
                        },
                    )
                )
                print(f"\n## base-sync:{category}: skipped (base copy did not produce a usable token)")
                continue
        if not path.exists():
            results.append(
                StepResult(
                    f"base-sync:{category}",
                    "failed",
                    detail={"reason": "report file does not exist", "report": str(path)},
                )
            )
            print(f"\n## base-sync:{category}: failed (report file does not exist: {redact_output_text(str(path))})")
            if stop_on_failure:
                abort_remaining = True
            continue

        base_cmd = [
            sys.executable,
            ".agents/skills/sorftime-report-base-sync/scripts/sync_report_to_base.py",
            "--report",
            str(path),
            "--base-token",
            token,
            "--category",
            category,
            "--date",
            report_date.isoformat(),
            "--overwrite",
            "--rename-folders",
            "--lark-cli-timeout-seconds",
            str(args.lark_cli_timeout_seconds),
            "--template-base-token",
            args.template_base_token,
        ]
        if args.dry_run:
            base_cmd.append("--dry-run")
        result = run_command(f"base-sync:{category}", base_cmd, timeout_seconds=args.command_timeout_seconds)
        result.detail = parse_base_sync_result(result)
        results.append(result)
        print_result(result)
        if stop_on_failure and result.status == "failed":
            abort_remaining = True

    summary_path, report_path_out = write_workflow_outputs(log_dir, report_date, args.dry_run, results)
    print(f"\nsummary={redact_output_text(str(summary_path))}")
    print(f"run_report={redact_output_text(str(report_path_out))}")

    failed = [item for item in results if item.status == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
