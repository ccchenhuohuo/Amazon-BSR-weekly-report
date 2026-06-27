#!/usr/bin/env python3
"""Project-level runner for the Sorftime weekly report workflow.

This script orchestrates the three project-scoped skills. It intentionally keeps
domain logic inside the skills and only handles date calculation, command order,
and summary collection.
"""

from __future__ import annotations

import argparse
import hashlib
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
DEFAULT_FEISHU_WEB_ORIGIN = "https://ulanzichina.feishu.cn"
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
LARK_CLI_BIN = "lark-cli"
FRONT_MATTER_PATTERN = re.compile(r"\A---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
IMAGE_TAG_PATTERN = re.compile(r"<img\b[^>]*>", re.IGNORECASE)
IMAGE_PLACEHOLDER = "图片见 Base 数据表"


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


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


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
    redacted = re.sub(r"\b(?:ou|oc|om|omt)_[A-Za-z0-9_-]{8,}\b", "[REDACTED_ID]", redacted)
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


def iter_dicts(data: object):
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from iter_dicts(value)
    elif isinstance(data, list):
        for item in data:
            yield from iter_dicts(item)


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


def report_doc_name(report_date: date, category: str) -> str:
    return f"{report_date:%Y%m%d}{category}周趋势监测报告"


def feishu_web_origin() -> str:
    return os.environ.get("FEISHU_WEB_ORIGIN", DEFAULT_FEISHU_WEB_ORIGIN).rstrip("/")


def feishu_resource_url(resource: str, token: str | None, fallback_url: str | None = None) -> str | None:
    if fallback_url:
        return fallback_url
    if not token:
        return None
    return f"{feishu_web_origin()}/{resource}/{token}"


def parse_base_doc_block(data: dict) -> dict[str, str | None]:
    doc_block: dict[str, object] = {}
    for item in iter_dicts(data):
        if item.get("type") == "docx" or item.get("docx_token"):
            doc_block = item
            break

    docx_token = (
        doc_block.get("docx_token")
        or doc_block.get("document_id")
        or doc_block.get("doc_token")
        or find_nested_value(data, {"docx_token", "document_id", "doc_token"})
    )
    if not docx_token and doc_block.get("type") == "docx":
        docx_token = doc_block.get("token")

    return {
        "block_id": (
            doc_block.get("block_id")
            or doc_block.get("id")
            or find_nested_value(data, {"block_id"})
        ),
        "docx_token": docx_token if isinstance(docx_token, str) else None,
        "url": (
            doc_block.get("url")
            or find_nested_value(data, {"url"})
        ),
        "name": (
            doc_block.get("name")
            or doc_block.get("title")
            or find_nested_value(data, {"name", "title"})
        ),
    }


def lark_cli_content_argument(path: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError as exc:
        raise ValueError("report file must be under the project root for lark-cli @file content") from exc
    return "@" + relative_path.as_posix()


def prepare_base_doc_markdown(report_file: Path, content_dir: Path) -> tuple[Path, int]:
    text = report_file.read_text(encoding="utf-8")
    text = FRONT_MATTER_PATTERN.sub("", text, count=1)
    image_count = len(IMAGE_TAG_PATTERN.findall(text))
    if image_count:
        text = IMAGE_TAG_PATTERN.sub(IMAGE_PLACEHOLDER, text)
    content_dir.mkdir(parents=True, exist_ok=True)
    output_path = content_dir / f"{report_file.stem}.base-doc.md"
    output_path.write_text(text, encoding="utf-8")
    return output_path, image_count


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


def parse_doc_update_result(result: StepResult) -> dict:
    data = extract_json_object(result.stdout)
    if not data:
        return {}
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    document = payload.get("document") if isinstance(payload.get("document"), dict) else {}
    return {
        "api_response_ok": data.get("ok"),
        "document_id_present": bool(data.get("document_id") or find_nested_value(data, {"document_id"})),
        "result": payload.get("result") or data.get("result"),
        "updated_blocks_count": payload.get("updated_blocks_count"),
        "revision_id": document.get("revision_id"),
        "warnings": payload.get("warnings"),
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
        "copied_base_url": feishu_resource_url("base", copied_token, copied.get("url")),
        "copy_response_ok": copy_ok,
        "dry_run": dry_run,
    }
    return result, copied_token


def publish_report_doc_to_base(
    category: str,
    report_date: date,
    base_token: str,
    report_file: Path,
    dry_run: bool,
    timeout_seconds: int,
    doc_content_dir: Path | None = None,
) -> list[StepResult]:
    name = report_doc_name(report_date, category)
    if not base_token:
        return [
            StepResult(
                f"base-doc-create:{category}",
                "skipped",
                detail={"reason": "missing base token", "requested_name": name, "report": str(report_file)},
            )
        ]

    create_cmd = [
        LARK_CLI_BIN,
        "base",
        "+base-block-create",
        "--base-token",
        base_token,
        "--type",
        "docx",
        "--name",
        name,
        "--as",
        "user",
    ]
    if dry_run:
        create_cmd.append("--dry-run")

    create_result = run_command(f"base-doc-create:{category}", create_cmd, timeout_seconds=timeout_seconds)
    create_data = extract_json_object(create_result.stdout)
    created_doc = parse_base_doc_block(create_data)
    create_ok = create_data.get("ok") if create_data else None
    if create_result.status == "ok" and not dry_run and create_ok is False:
        create_result.status = "failed"
        create_result.stderr = (create_result.stderr + "\nBase doc create returned ok=false.").strip()
    create_result.detail = {
        "report": str(report_file),
        "requested_name": name,
        "base_token_present": bool(base_token),
        "base_url": feishu_resource_url("base", base_token),
        "docx_token_present": bool(created_doc.get("docx_token")),
        "docx_url_present": bool(created_doc.get("url") or created_doc.get("docx_token")),
        "docx_url": feishu_resource_url("docx", created_doc.get("docx_token"), created_doc.get("url")),
        "block_id_present": bool(created_doc.get("block_id")),
        "create_response_ok": create_ok,
        "dry_run": dry_run,
    }

    results = [create_result]
    if create_result.status != "ok":
        return results

    docx_token = created_doc.get("docx_token")
    if not docx_token:
        status = "skipped" if dry_run else "failed"
        results.append(
            StepResult(
                f"base-doc-update:{category}",
                status,
                detail={
                    "reason": "base doc create dry-run or create result did not include a docx token",
                    "report": str(report_file),
                    "requested_name": name,
                    "dry_run": dry_run,
                },
            )
        )
        return results

    content_dir = doc_content_dir or PROJECT_ROOT / "logs" / "base-doc-content"
    try:
        doc_content_file, image_tags_removed = prepare_base_doc_markdown(report_file, content_dir)
        content_arg = lark_cli_content_argument(doc_content_file)
    except (OSError, ValueError) as exc:
        results.append(
            StepResult(
                f"base-doc-update:{category}",
                "failed",
                detail={"reason": str(exc), "report": str(report_file), "requested_name": name},
            )
        )
        return results

    update_cmd = [
        LARK_CLI_BIN,
        "docs",
        "+update",
        "--api-version",
        "v2",
        "--doc",
        docx_token,
        "--command",
        "overwrite",
        "--doc-format",
        "markdown",
        "--content",
        content_arg,
        "--as",
        "user",
    ]
    if dry_run:
        update_cmd.append("--dry-run")

    update_result = run_command(f"base-doc-update:{category}", update_cmd, timeout_seconds=timeout_seconds)
    update_detail = parse_doc_update_result(update_result)
    update_detail.update(
        {
            "report": str(report_file),
            "requested_name": name,
            "doc_content": str(doc_content_file),
            "image_tags_removed": image_tags_removed,
            "docx_token_present": True,
            "docx_url_present": bool(created_doc.get("url") or docx_token),
            "docx_url": feishu_resource_url("docx", docx_token, created_doc.get("url")),
            "base_url": feishu_resource_url("base", base_token),
            "dry_run": dry_run,
        }
    )
    update_result.detail = update_detail
    warnings = update_detail.get("warnings")
    if warnings:
        update_result.stderr = (
            update_result.stderr + "\nDoc update warnings: " + json.dumps(warnings, ensure_ascii=False)
        ).strip()
    if update_result.status == "ok" and not dry_run:
        update_ok = update_detail.get("api_response_ok")
        update_status = update_detail.get("result")
        if update_ok is not True or update_status != "success":
            update_result.status = "failed"
            update_result.stderr = (
                update_result.stderr
                + f"\nDoc update did not return ok=true and result=success (ok={update_ok!r}, result={update_status!r})."
            ).strip()
    results.append(update_result)
    return results


def category_step(results: list[StepResult], prefix: str, category: str) -> StepResult | None:
    name = f"{prefix}:{category}"
    return next((item for item in results if item.name == name), None)


def successful_category_count(results: list[StepResult]) -> int:
    count = 0
    for category in CATEGORIES:
        required = [
            category_step(results, "weekly-report", category),
            category_step(results, "base-sync", category),
            category_step(results, "base-doc-update", category),
        ]
        if all(item is not None and item.status == "ok" for item in required):
            count += 1
    return count


def workflow_complete(results: list[StepResult]) -> bool:
    return not any(item.status == "failed" for item in results) and successful_category_count(results) == len(CATEGORIES)


def category_publication_links(results: list[StepResult]) -> dict[str, dict[str, str | None]]:
    links: dict[str, dict[str, str | None]] = {}
    for category in CATEGORIES:
        base_url = None
        doc_url = None
        for item in results:
            if not item.name.endswith(f":{category}") or not item.detail:
                continue
            detail = item.detail
            base_url = base_url or detail.get("base_url") or detail.get("copied_base_url")
            doc_url = doc_url or detail.get("docx_url") or detail.get("document_url")
        links[category] = {
            "base_url": base_url if isinstance(base_url, str) else None,
            "doc_url": doc_url if isinstance(doc_url, str) else None,
        }
    return links


def markdown_link(label: str, url: str | None) -> str:
    if not url:
        return f"{label}未生成"
    return f"[{label}]({url})"


def notification_link_lines(results: list[StepResult]) -> list[str]:
    links = category_publication_links(results)
    lines = []
    for category in CATEGORIES:
        category_links = links[category]
        lines.append(
            "{category}：{base} ｜ {doc}".format(
                category=category,
                base=markdown_link("多维表格", category_links["base_url"]),
                doc=markdown_link("周报文档", category_links["doc_url"]),
            )
        )
    return lines


def problem_step_text(results: list[StepResult], limit: int = 8) -> str:
    failed_names = [item.name for item in results if item.status == "failed"]
    skipped_names = [
        item.name
        for item in results
        if item.status == "skipped" and not item.name.startswith("notify:")
    ]
    problem_names = failed_names or skipped_names
    if not problem_names:
        return "无"
    displayed = problem_names[:limit]
    suffix = "" if len(problem_names) <= limit else f" 等 {len(problem_names)} 个"
    return "、".join(displayed) + suffix


def build_workflow_notification_message(
    report_date: date | str,
    results: list[StepResult],
    run_report_path: Path,
    finished_at: str | None = None,
) -> str:
    finished_at = finished_at or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S CST")
    success_count = successful_category_count(results)
    link_lines = "\n".join(notification_link_lines(results))
    complete = workflow_complete(results)

    if complete:
        return "\n".join(
            [
                "【Amazon BSR 战略周报】已完成",
                "",
                f"报告日期：{report_date_text(report_date)}",
                f"运行结果：{success_count}/{len(CATEGORIES)} 类目成功",
                f"完成时间：{finished_at}",
                "",
                "链接：",
                link_lines,
                "",
                "说明：周报文档已新增到对应多维表格左侧栏；商品图片见 Base 数据表。",
            ]
        )

    return "\n".join(
        [
            "【Amazon BSR 战略周报】运行异常",
            "",
            f"报告日期：{report_date_text(report_date)}",
            f"运行结果：{success_count}/{len(CATEGORIES)} 类目成功",
            f"失败环节：{problem_step_text(results)}",
            f"完成时间：{finished_at}",
            "",
            "已生成链接：",
            link_lines,
            "",
            f"排查日志：{display_local_path(run_report_path)}",
        ]
    )


def notification_idempotency_key(
    report_date: date | str,
    results: list[StepResult],
    recipient: str,
    run_report_path: Path,
) -> str:
    status = "ok" if workflow_complete(results) else "fail"
    report_date_part = report_date_text(report_date).replace("-", "")
    digest = hashlib.sha1(
        f"{report_date_part}:{status}:{recipient}:{problem_step_text(results)}".encode("utf-8")
    ).hexdigest()[:8]
    return f"bsr-{report_date_part}-{status}-{digest}"


def send_workflow_notification(
    report_date: date | str,
    dry_run: bool,
    notify_dry_run: bool,
    require_notify: bool,
    notify_user_id: str,
    notify_chat_id: str,
    notify_as: str,
    results: list[StepResult],
    run_report_path: Path,
    timeout_seconds: int,
) -> StepResult:
    if dry_run and not notify_dry_run:
        return StepResult(
            "notify:feishu",
            "skipped",
            detail={"reason": "--dry-run", "notify_dry_run": notify_dry_run},
        )
    if not notify_user_id and not notify_chat_id:
        status = "failed" if require_notify else "skipped"
        return StepResult(
            "notify:feishu",
            status,
            detail={
                "reason": "missing FEISHU_NOTIFY_USER_ID or FEISHU_NOTIFY_CHAT_ID",
                "required": require_notify,
            },
        )

    recipient_flag = "--chat-id" if notify_chat_id else "--user-id"
    recipient = notify_chat_id or notify_user_id
    message = build_workflow_notification_message(report_date, results, run_report_path)
    command = [
        LARK_CLI_BIN,
        "im",
        "+messages-send",
        "--as",
        notify_as,
        recipient_flag,
        recipient,
        "--idempotency-key",
        notification_idempotency_key(report_date, results, recipient, run_report_path),
        "--markdown",
        message,
        "--json",
    ]
    result = run_command("notify:feishu", command, timeout_seconds=timeout_seconds)
    data = extract_json_object(result.stdout)
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    api_ok = data.get("ok") if data else None
    message_id = payload.get("message_id")
    if result.status == "ok" and (api_ok is not True or not message_id):
        result.status = "failed"
        result.stderr = (
            result.stderr
            + f"\nFeishu notification did not confirm delivery (ok={api_ok!r}, message_id_present={bool(message_id)})."
        ).strip()
    result.detail = {
        "recipient_type": "chat_id" if notify_chat_id else "user_id",
        "recipient_present": bool(recipient),
        "identity": notify_as,
        "api_response_ok": api_ok,
        "chat_id_present": bool(payload.get("chat_id")),
        "message_id_present": bool(message_id),
        "create_time": payload.get("create_time"),
        "dry_run": dry_run,
    }
    return result


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


def display_local_path(path: Path) -> str:
    try:
        relative_path = path.resolve().relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        if path.is_absolute():
            return f"[LOCAL_PATH]/{path.name}"
        return redact_output_text(str(path))
    return f"$PROJECT_ROOT/{relative_path.as_posix()}"


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

    lines.extend(["", "## Base 内文档结果", ""])
    doc_results = [
        item
        for item in results
        if item.name.startswith("base-doc-create:") or item.name.startswith("base-doc-update:")
    ]
    if doc_results:
        lines.extend(["| 类目 | 创建状态 | 写入状态 | 文档 token | 文档 URL |", "| --- | --- | --- | --- | --- |"])
        for category in CATEGORIES:
            create_item = next((item for item in doc_results if item.name == f"base-doc-create:{category}"), None)
            update_item = next((item for item in doc_results if item.name == f"base-doc-update:{category}"), None)
            detail = (create_item.detail if create_item else {}) or {}
            lines.append(
                "| {category} | `{create_status}` | `{update_status}` | `{token}` | {url} |".format(
                    category=category,
                    create_status=create_item.status if create_item else "未执行",
                    update_status=update_item.status if update_item else "未执行",
                    token="[REDACTED]" if detail.get("docx_token_present") else "",
                    url="[REDACTED]" if detail.get("docx_url_present") else "",
                )
            )
    else:
        lines.append("本次没有执行 Base 内文档创建。")

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
    parser.add_argument("--skip-base-doc", action="store_true", help="Do not create/update the weekly report docx inside the Base sidebar.")
    parser.add_argument("--skip-notify", action="store_true", help="Do not send the final Feishu notification.")
    parser.add_argument("--notify-dry-run", action="store_true", help="Send the final Feishu notification even when --dry-run is set.")
    parser.add_argument(
        "--require-notify",
        action="store_true",
        default=env_bool("FEISHU_REQUIRE_NOTIFY", False),
        help="Fail the workflow if the final Feishu notification cannot be sent or no recipient is configured.",
    )
    parser.add_argument(
        "--notify-user-id",
        default=os.environ.get("FEISHU_NOTIFY_USER_ID", ""),
        help="Feishu open_id (ou_xxx) that receives the final notification.",
    )
    parser.add_argument(
        "--notify-chat-id",
        default=os.environ.get("FEISHU_NOTIFY_CHAT_ID", ""),
        help="Feishu chat_id (oc_xxx) that receives the final notification. Takes precedence over --notify-user-id.",
    )
    parser.add_argument(
        "--notify-as",
        choices=("bot", "user"),
        default=os.environ.get("FEISHU_NOTIFY_AS", "bot"),
        help="Identity used by lark-cli to send the final notification.",
    )
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
        if not args.skip_notify:
            notification_result = send_workflow_notification(
                report_date=args.date or "auto",
                dry_run=args.dry_run,
                notify_dry_run=args.notify_dry_run,
                require_notify=args.require_notify,
                notify_user_id=args.notify_user_id,
                notify_chat_id=args.notify_chat_id,
                notify_as=args.notify_as,
                results=results,
                run_report_path=report_path_out,
                timeout_seconds=args.lark_cli_timeout_seconds,
            )
            results.append(notification_result)
            print_result(notification_result)
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
        result.detail["base_url"] = feishu_resource_url("base", token)
        results.append(result)
        print_result(result)
        if stop_on_failure and result.status == "failed":
            abort_remaining = True
            continue
        if result.status != "ok":
            continue
        if args.skip_base_doc:
            results.append(
                StepResult(
                    f"base-doc-create:{category}",
                    "skipped",
                    detail={"reason": "--skip-base-doc", "report": str(path)},
                )
            )
            continue
        doc_results = publish_report_doc_to_base(
            category=category,
            report_date=report_date,
            base_token=token,
            report_file=path,
            dry_run=args.dry_run,
            timeout_seconds=args.command_timeout_seconds,
            doc_content_dir=log_dir / "base-doc-content",
        )
        results.extend(doc_results)
        for doc_result in doc_results:
            print_result(doc_result)
        if stop_on_failure and any(item.status == "failed" for item in doc_results):
            abort_remaining = True

    summary_path, report_path_out = write_workflow_outputs(log_dir, report_date, args.dry_run, results)
    if not args.skip_notify:
        notification_result = send_workflow_notification(
            report_date=report_date,
            dry_run=args.dry_run,
            notify_dry_run=args.notify_dry_run,
            require_notify=args.require_notify,
            notify_user_id=args.notify_user_id,
            notify_chat_id=args.notify_chat_id,
            notify_as=args.notify_as,
            results=results,
            run_report_path=report_path_out,
            timeout_seconds=args.lark_cli_timeout_seconds,
        )
        results.append(notification_result)
        print_result(notification_result)
        summary_path, report_path_out = write_workflow_outputs(log_dir, report_date, args.dry_run, results)
    print(f"\nsummary={redact_output_text(str(summary_path))}")
    print(f"run_report={redact_output_text(str(report_path_out))}")

    failed = [item for item in results if item.status == "failed"]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
