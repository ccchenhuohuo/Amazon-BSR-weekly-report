import json
import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "sync_report_to_base.py"
spec = importlib.util.spec_from_file_location("sync_report_to_base", SCRIPT_PATH)
sync_report_to_base = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = sync_report_to_base
spec.loader.exec_module(sync_report_to_base)

RUNNER_PATH = Path(__file__).resolve().parents[3] / "workflows" / "run_sorftime_weekly_workflow.py"
runner_spec = importlib.util.spec_from_file_location("run_sorftime_weekly_workflow", RUNNER_PATH)
run_sorftime_weekly_workflow = importlib.util.module_from_spec(runner_spec)
assert runner_spec.loader is not None
sys.modules[runner_spec.name] = run_sorftime_weekly_workflow
runner_spec.loader.exec_module(run_sorftime_weekly_workflow)


def fields(names):
    return {name: {"name": name} for name in names}


def block_fixture(first="{类目1}", second="{类目2}"):
    blocks = [
        {"id": "f1", "name": first, "parent_id": None, "type": "folder"},
        {"id": "f2", "name": second, "parent_id": None, "type": "folder"},
        {"id": "flow", "name": "低分高销", "parent_id": None, "type": "folder"},
        {"id": "fown", "name": "本品", "parent_id": None, "type": "folder"},
    ]
    parent_by_table = {
        "异动数据": None,
        "低分高销数据": None,
        "本品数据": None,
        "2.1.1": "f1",
        "2.2": "f1",
        "2.3": "f1",
        "2.4": "f1",
        "3.1.1": "f2",
        "3.2": "f2",
        "3.3": "f2",
        "3.4": "f2",
        "2.5.2": "flow",
        "3.5.2": "flow",
        "4.1.1": "fown",
        "4.1.2": "fown",
    }
    for table_name in sync_report_to_base.REQUIRED_TABLES:
        blocks.append(
            {
                "id": f"tbl-{table_name}",
                "name": table_name,
                "parent_id": parent_by_table[table_name],
                "type": "table",
            }
        )
    return blocks


def table_ids():
    return {name: f"tbl-{name}" for name in sync_report_to_base.REQUIRED_TABLES}


def test_adapts_own_table_template_rank_placeholders_to_report_dates():
    template_visible = [
        "ASIN",
        "产品名称",
        "{XXXX-XX-XX}排名",
        "{YYYY-YY-YY}排名",
        "排名变化",
        "价格",
        "评分",
        "月销",
        "上架天数",
        "商品图片",
        "附图",
        "报告日期",
        "类目",
    ]
    target = fields(
        [
            "ASIN",
            "产品名称",
            "2026-05-13排名",
            "2026-05-20排名",
            "排名变化",
            "价格",
            "评分",
            "月销",
            "上架天数",
            "商品图片",
            "附图",
            "报告日期",
            "类目",
        ]
    )

    assert sync_report_to_base.adapt_template_visible_fields(
        "本品数据",
        template_visible,
        target,
        "2026-05-13",
        "2026-05-20",
    ) == [
        "ASIN",
        "产品名称",
        "2026-05-13排名",
        "2026-05-20排名",
        "排名变化",
        "价格",
        "评分",
        "月销",
        "上架天数",
        "商品图片",
        "附图",
        "报告日期",
        "类目",
    ]


def test_non_own_table_requires_exact_template_visible_fields():
    template_visible = ["排名", "品牌", "产品名称"]
    target = fields(["排名", "品牌", "产品名称"])

    assert sync_report_to_base.adapt_template_visible_fields(
        "异动数据",
        template_visible,
        target,
        "2026-05-13",
        "2026-05-20",
    ) == template_visible


def test_adapts_own_table_template_date_rank_fields_by_position():
    template_visible = [
        "ASIN",
        "产品名称",
        "2026-04-29排名",
        "2026-05-06排名",
        "排名变化",
    ]
    target = fields(["ASIN", "产品名称", "2026-05-06排名", "2026-05-13排名", "排名变化"])

    assert sync_report_to_base.adapt_template_visible_fields(
        "4.1.1",
        template_visible,
        target,
        "2026-05-06",
        "2026-05-13",
    ) == ["ASIN", "产品名称", "2026-05-06排名", "2026-05-13排名", "排名变化"]


def test_field_order_mismatch_is_a_hard_failure():
    template_visible = ["排名", "品牌", "产品名称"]
    target = fields(["排名", "品牌", "产品名称", "额外字段"])

    try:
        sync_report_to_base.adapt_template_visible_fields(
            "异动数据",
            template_visible,
            target,
            "2026-05-13",
            "2026-05-20",
        )
    except sync_report_to_base.SyncError as exc:
        assert "extra target fields" in str(exc)
    else:
        raise AssertionError("Expected SyncError for target/template field mismatch")


def test_parse_cli_json_tolerates_lark_cli_prefix_lines():
    stdout = '[lark-cli] token already refreshed by another process\n{"ok": true, "data": {"value": 1}}\n'

    assert sync_report_to_base.parse_cli_json(stdout, ["lark-cli"]) == {
        "ok": True,
        "data": {"value": 1},
    }


def test_run_cli_dry_run_redacts_token_arguments(capsys):
    sync_report_to_base.run_cli(
        [
            "base",
            "+table-list",
            "--base-token",
            "sample-value-123",
            "--folder-token",
            "sample-folder-123",
        ],
        dry_run=True,
    )

    stderr = capsys.readouterr().err
    assert "sample-value-123" not in stderr
    assert "sample-folder-123" not in stderr
    assert "[REDACTED]" in stderr


def test_run_cli_allow_failure_redacts_failed_json(monkeypatch):
    class Proc:
        returncode = 0
        stdout = (
            '{"ok": false, "error": {"message": "--base-token sample-sensitive-123", '
            '"base_token": "sample-sensitive-123", "url": "https://redacted.invalid/app/abc"}}'
        )
        stderr = ""

    monkeypatch.setattr(sync_report_to_base.subprocess, "run", lambda *args, **kwargs: Proc())

    data = sync_report_to_base.run_cli(
        ["base", "+base-block-rename", "--base-token", "sample-sensitive-123"],
        allow_failure=True,
    )
    text = json.dumps(data, ensure_ascii=False)

    assert "sample-sensitive-123" not in text
    assert "redacted.invalid" not in text
    assert "[REDACTED]" in text


def test_blocked_folder_rename_is_blocking_result():
    assert sync_report_to_base.has_blocking_result(
        {"status": "blocked_missing_scope"},
        {"status": "ok"},
    )
    assert sync_report_to_base.has_blocking_result(
        {"status": "ok"},
        {"status": "failed"},
    )
    assert not sync_report_to_base.has_blocking_result(
        {"status": "ok"},
        {"status": "ok"},
    )


def test_rename_date_fields_uses_chronological_rank_order(monkeypatch):
    calls = []
    fields_by_table = {
        "tbl": {
            "2026-05-06排名": {"id": "old2", "name": "2026-05-06排名", "type": "number"},
            "2026-04-29排名": {"id": "old1", "name": "2026-04-29排名", "type": "number"},
        }
    }

    def fake_run_cli(args, dry_run=False):
        field_id = args[args.index("--field-id") + 1]
        body = __import__("json").loads(args[args.index("--json") + 1])
        calls.append((field_id, body["name"]))
        current = fields_by_table["tbl"]
        old_name = next(name for name, field in current.items() if field["id"] == field_id)
        current[body["name"]] = {**current.pop(old_name), "name": body["name"]}
        return {"ok": True}

    monkeypatch.setattr(sync_report_to_base, "run_cli", fake_run_cli)
    monkeypatch.setattr(sync_report_to_base, "field_map", lambda base_token, table_id: dict(fields_by_table[table_id]))

    all_fields = {
        "本品数据": {},
        "4.1.1": dict(fields_by_table["tbl"]),
        "4.1.2": {},
    }
    sync_report_to_base.rename_date_fields_if_needed(
        "base",
        {"本品数据": "missing1", "4.1.1": "tbl", "4.1.2": "missing2"},
        all_fields,
        "2026-05-13",
        "2026-05-20",
        False,
    )

    assert calls == [("old1", "2026-05-13排名"), ("old2", "2026-05-20排名")]


def test_validate_block_layout_accepts_template_category_folders():
    result = sync_report_to_base.validate_block_layout(
        block_fixture(),
        table_ids(),
        ("Continuous Output Lighting", "Selfie Lights"),
    )

    assert result["status"] == "ok"
    assert result["folders"]["category_folders"][0]["current_name"] == "{类目1}"
    assert result["table_parent_checks"]["2.1.1"]["expected_parent_id"] == "f1"


def test_rename_category_folders_updates_placeholder_names(monkeypatch):
    state = {"blocks": block_fixture()}

    def fake_run_cli(args, dry_run=False, allow_failure=False):
        if args[:2] == ["base", "+base-block-list"]:
            return {"ok": True, "data": {"blocks": state["blocks"]}}
        if args[:2] == ["base", "+base-block-rename"] and "--dry-run" in args:
            return {"ok": True, "data": {}}
        if args[:2] == ["base", "+base-block-rename"]:
            block_id = args[args.index("--block-id") + 1]
            name = args[args.index("--name") + 1]
            for block in state["blocks"]:
                if block["id"] == block_id:
                    block["name"] = name
                    break
            return {"ok": True, "data": {}}
        raise AssertionError(args)

    monkeypatch.setattr(sync_report_to_base, "run_cli", fake_run_cli)

    result, blocks = sync_report_to_base.rename_category_folders("base", "灯光类", dry_run=False)

    assert result["status"] == "ok"
    assert [op["status"] for op in result["operations"]] == ["renamed", "renamed"]
    assert [block["name"] for block in blocks[:2]] == ["Continuous Output Lighting", "Selfie Lights"]


def test_rename_category_folders_is_idempotent(monkeypatch):
    state = {"blocks": block_fixture("Continuous Output Lighting", "Selfie Lights")}

    def fake_run_cli(args, dry_run=False, allow_failure=False):
        if args[:2] == ["base", "+base-block-list"]:
            return {"ok": True, "data": {"blocks": state["blocks"]}}
        raise AssertionError("rename should not be called for already-renamed folders")

    monkeypatch.setattr(sync_report_to_base, "run_cli", fake_run_cli)

    result, _ = sync_report_to_base.rename_category_folders("base", "灯光类", dry_run=False)

    assert result["status"] == "ok"
    assert result["mode"] == "already_renamed"


def test_rename_category_folders_dry_run_reports_planned(monkeypatch):
    state = {"blocks": block_fixture()}
    rename_calls = []

    def fake_run_cli(args, dry_run=False, allow_failure=False):
        if args[:2] == ["base", "+base-block-list"]:
            return {"ok": True, "data": {"blocks": state["blocks"]}}
        if args[:2] == ["base", "+base-block-rename"] and "--dry-run" in args:
            return {"ok": True, "data": {}}
        if args[:2] == ["base", "+base-block-rename"]:
            rename_calls.append(args)
            return {"ok": True, "data": {}}
        raise AssertionError(args)

    monkeypatch.setattr(sync_report_to_base, "run_cli", fake_run_cli)

    result, _ = sync_report_to_base.rename_category_folders("base", "灯光类", dry_run=True)

    assert result["status"] == "planned"
    assert [op["status"] for op in result["operations"]] == ["planned", "planned"]
    assert rename_calls == []


def test_rename_category_folders_marks_missing_scope(monkeypatch):
    state = {"blocks": block_fixture()}

    def fake_run_cli(args, dry_run=False, allow_failure=False):
        if args[:2] == ["base", "+base-block-list"]:
            return {"ok": True, "data": {"blocks": state["blocks"]}}
        if args[:2] == ["base", "+base-block-rename"] and "--dry-run" in args:
            return {
                "ok": False,
                "error": {
                    "subtype": "missing_scope",
                    "missing_scopes": ["base:block:update"],
                    "hint": "auth required",
                },
            }
        raise AssertionError(args)

    monkeypatch.setattr(sync_report_to_base, "run_cli", fake_run_cli)

    result, _ = sync_report_to_base.rename_category_folders("base", "灯光类", dry_run=False)

    assert result["status"] == "blocked_missing_scope"
    assert result["scope"]["missing_scopes"] == ["base:block:update"]
    assert [op["status"] for op in result["operations"]] == ["blocked_missing_scope", "blocked_missing_scope"]


def test_resolve_category_folders_rejects_missing_or_duplicate():
    try:
        sync_report_to_base.resolve_category_folders(block_fixture(first="Wrong"), ("Continuous Output Lighting", "Selfie Lights"))
    except sync_report_to_base.SyncError as exc:
        assert "Missing root folder" in str(exc)
    else:
        raise AssertionError("Expected missing folder to fail")

    blocks = block_fixture()
    blocks.append({"id": "f-conflict", "name": "Continuous Output Lighting", "parent_id": None, "type": "folder"})
    try:
        sync_report_to_base.resolve_category_folders(blocks, ("Continuous Output Lighting", "Selfie Lights"))
    except sync_report_to_base.SyncError as exc:
        assert "Ambiguous root folder" in str(exc)
    else:
        raise AssertionError("Expected duplicate folder candidates to fail")


def test_build_records_from_sanitized_markdown_fixture():
    fixture = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "minimal_report.md"

    mother, child = sync_report_to_base.build_records(fixture, "灯光类", "2026-06-17", "2026-06-10")

    assert len(mother["异动数据"]) == 8
    assert len(mother["低分高销数据"]) == 2
    assert len(mother["本品数据"]) == 2
    assert len(child["2.1.1"]) == 1
    assert child["2.1.1"][0]["数据类型"] == "TOP10产品"
    assert child["2.1.1"][0]["ASIN"] == "[X000000001](https://www.amazon.com/dp/X000000001)"
    assert child["2.1.1"][0]["商品图片"] == "https://example.invalid/images/p01.jpg"
    assert mother["本品数据"][0]["2026-06-10排名"] == 20
    assert mother["本品数据"][0]["2026-06-17排名"] == 18


def test_write_records_creates_base_batch_payload_from_fixture(tmp_path, monkeypatch):
    fixture = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "minimal_report.md"
    mother, _ = sync_report_to_base.build_records(fixture, "灯光类", "2026-06-17", "2026-06-10")
    records = mother["异动数据"][:1]
    fields = {
        "报告日期": {"name": "报告日期", "type": "datetime"},
        "类目": {"name": "类目", "type": "select"},
        "品牌": {"name": "品牌", "type": "text"},
        "产品名称": {"name": "产品名称", "type": "text"},
        "ASIN": {"name": "ASIN", "type": "text"},
        "价格": {"name": "价格", "type": "number"},
        "评分": {"name": "评分", "type": "number"},
        "月销": {"name": "月销", "type": "number"},
        "上架天数": {"name": "上架天数", "type": "number"},
        "商品图片": {"name": "商品图片", "type": "text"},
        "数据类型": {"name": "数据类型", "type": "select"},
        "排名": {"name": "排名", "type": "number"},
        "上周排名": {"name": "上周排名", "type": "number"},
        "排名变化": {"name": "排名变化", "type": "number"},
        "是否新品": {"name": "是否新品", "type": "select"},
        "附图": {"name": "附图", "type": "attachment"},
    }
    calls = []

    def fake_run_cli(args, dry_run=False, allow_failure=False):
        calls.append((args, dry_run))
        return {"ok": True}

    monkeypatch.setattr(sync_report_to_base, "run_cli", fake_run_cli)

    sync_report_to_base.write_records(
        "base-token",
        "异动数据",
        "tbl-1",
        fields,
        records,
        False,
        tmp_path,
    )

    assert calls
    args, dry_run = calls[0]
    assert dry_run is False
    assert args[:2] == ["base", "+record-batch-create"]
    payload_arg = args[args.index("--json") + 1]
    assert payload_arg.startswith("@")
    payload = json.loads(Path(payload_arg[1:]).read_text(encoding="utf-8"))
    assert payload["fields"][:5] == ["报告日期", "类目", "品牌", "产品名称", "ASIN"]
    first_row = payload["rows"][0]
    assert first_row[payload["fields"].index("ASIN")] == "[X000000001](https://www.amazon.com/dp/X000000001)"
    assert first_row[payload["fields"].index("商品图片")] == "https://example.invalid/images/p01.jpg"
    assert "附图" not in payload["fields"]


def test_runner_parses_folder_rename_and_writes_cli_report(tmp_path):
    stdout = """
[progress] writing
{
  "report": "/tmp/report.md",
  "base_token": "base",
  "category": "灯光类",
  "date": "2026-06-17",
  "previous_date": "2026-06-10",
  "counts": {"异动数据": 1, "2.1.1": 1},
  "duplicates": {},
  "folder_rename": {
    "status": "ok",
    "operations": [
      {"placeholder": "{类目1}", "from": "{类目1}", "to": "Continuous Output Lighting", "status": "renamed"},
      {"placeholder": "{类目2}", "from": "{类目2}", "to": "Selfie Lights", "status": "renamed"}
    ],
    "scope": {"status": "ok"}
  },
  "block_layout": {"status": "ok"},
  "field_order": {"changed_views": {}},
  "server_verification": {"status": "ok"},
  "log_dir": "logs/sync"
}
"""
    result = run_sorftime_weekly_workflow.StepResult(
        name="base-sync:灯光类",
        status="ok",
        returncode=0,
        stdout=stdout,
    )

    detail = run_sorftime_weekly_workflow.parse_base_sync_result(result)
    assert detail["folder_rename"]["status"] == "ok"

    result.detail = detail
    report_path = tmp_path / "run-report.md"
    run_sorftime_weekly_workflow.write_run_report(
        report_path,
        __import__("datetime").date(2026, 6, 17),
        False,
        tmp_path,
        [result],
    )
    text = report_path.read_text(encoding="utf-8")
    assert "Base 左侧分组 CLI 状态" in text
    assert "待 UI 视觉确认" not in text
    assert "Continuous Output Lighting" in text


def test_verify_written_records_reads_back_counts_and_cells(monkeypatch):
    rows = {
        "异动数据": [
            {
                "fields": {
                    "ASIN": "[X012345678](https://www.amazon.com/dp/X012345678)",
                    "类目": "Continuous Output Lighting",
                    "数据类型": "TOP10产品",
                    "商品图片": "https://example.com/a.jpg",
                }
            }
        ],
        "低分高销数据": [],
        "本品数据": [],
    }
    for table_name in sync_report_to_base.REQUIRED_TABLES:
        rows.setdefault(table_name, [])

    tables = table_ids()
    id_to_name = {table_id: table_name for table_name, table_id in tables.items()}

    def fake_run_cli(args, dry_run=False, allow_failure=False):
        table_id = args[args.index("--table-id") + 1]
        table_name = id_to_name[table_id]
        return {"ok": True, "data": {"data": rows[table_name], "has_more": False}}

    monkeypatch.setattr(sync_report_to_base, "run_cli", fake_run_cli)
    expected = {name: len(rows[name]) for name in sync_report_to_base.REQUIRED_TABLES}

    result = sync_report_to_base.verify_written_records(
        "base",
        tables,
        expected,
        dry_run=False,
        prepare_only=False,
        overwrite=True,
    )

    assert result["status"] == "ok"
    assert result["actual_counts"]["异动数据"] == 1
    assert result["issues"] == []


def test_restore_snapshot_records_recreates_old_rows(tmp_path, monkeypatch):
    snapshot_path = tmp_path / "snapshots" / "异动数据.json"
    snapshot_path.parent.mkdir()
    snapshot_path.write_text(
        json.dumps(
            {
                "table": "异动数据",
                "table_id": "tbl-1",
                "record_count": 1,
                "records": [
                    {
                        "record_id": "rec-1",
                        "fields": {
                            "ASIN": "[X012345678](https://www.amazon.com/dp/X012345678)",
                            "类目": "Continuous Output Lighting",
                            "商品图片": "https://example.com/a.jpg",
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_clear_table(base_token, table_id, dry_run):
        calls.append(("clear", base_token, table_id, dry_run))
        return 1

    def fake_run_cli(args, dry_run=False, allow_failure=False):
        calls.append(("run_cli", args, dry_run))
        return {"ok": True}

    monkeypatch.setattr(sync_report_to_base, "clear_table", fake_clear_table)
    monkeypatch.setattr(sync_report_to_base, "run_cli", fake_run_cli)
    monkeypatch.setattr(sync_report_to_base, "list_records", lambda base_token, table_id: [{"fields": {"ASIN": "x"}}])

    result = sync_report_to_base.restore_snapshot_records(
        "base-token",
        "异动数据",
        "tbl-1",
        {"path": str(snapshot_path)},
        dry_run=False,
        log_dir=tmp_path,
    )

    assert result == {"table": "异动数据", "status": "ok", "restored": 1, "actual": 1}
    assert calls[0] == ("clear", "base-token", "tbl-1", False)
    batch_call = calls[1][1]
    assert batch_call[:2] == ["base", "+record-batch-create"]
    payload = json.loads(Path(batch_call[batch_call.index("--json") + 1][1:]).read_text(encoding="utf-8"))
    assert payload["fields"] == ["ASIN", "类目", "商品图片"]
    assert payload["rows"][0][0] == "[X012345678](https://www.amazon.com/dp/X012345678)"


def test_restore_overwrite_snapshots_captures_restore_exceptions(tmp_path, monkeypatch):
    def fake_restore(*args, **kwargs):
        raise sync_report_to_base.SyncError("restore exploded")

    monkeypatch.setattr(sync_report_to_base, "restore_snapshot_records", fake_restore)

    result = sync_report_to_base.restore_overwrite_snapshots(
        "base-token",
        {"异动数据": "tbl-1"},
        {"异动数据": {"path": str(tmp_path / "snapshot.json")}},
        dry_run=False,
        log_dir=tmp_path,
    )

    assert result["status"] == "failed"
    assert result["restore_failures"]["异动数据"]["status"] == "failed"
    assert "restore exploded" in result["restore_failures"]["异动数据"]["reason"]


def test_verify_written_records_retries_until_server_counts_match(monkeypatch):
    rows = {
        "异动数据": [
            {
                "fields": {
                    "ASIN": "[X012345678](https://www.amazon.com/dp/X012345678)",
                    "类目": "Continuous Output Lighting",
                    "数据类型": "TOP10产品",
                    "商品图片": "https://example.com/a.jpg",
                }
            }
        ],
        "低分高销数据": [],
        "本品数据": [],
    }
    for table_name in sync_report_to_base.REQUIRED_TABLES:
        rows.setdefault(table_name, [])

    tables = table_ids()
    id_to_name = {table_id: table_name for table_name, table_id in tables.items()}
    calls = []

    def fake_run_cli(args, dry_run=False, allow_failure=False):
        table_id = args[args.index("--table-id") + 1]
        table_name = id_to_name[table_id]
        calls.append(table_name)
        if len(calls) <= len(sync_report_to_base.REQUIRED_TABLES):
            return {"ok": True, "data": {"data": [], "has_more": False}}
        return {"ok": True, "data": {"data": rows[table_name], "has_more": False}}

    monkeypatch.setattr(sync_report_to_base, "run_cli", fake_run_cli)
    monkeypatch.setattr(sync_report_to_base, "BASE_VERIFY_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(sync_report_to_base, "BASE_VERIFY_RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(sync_report_to_base.time, "sleep", lambda seconds: None)
    expected = {name: len(rows[name]) for name in sync_report_to_base.REQUIRED_TABLES}

    result = sync_report_to_base.verify_written_records(
        "base",
        tables,
        expected,
        dry_run=False,
        prepare_only=False,
        overwrite=True,
    )

    assert result["status"] == "ok"
    assert result["actual_counts"]["异动数据"] == 1
    assert len(calls) == len(sync_report_to_base.REQUIRED_TABLES) * 2


def test_list_records_converts_lark_cli_array_rows_to_field_dicts(monkeypatch):
    def fake_run_cli(args, dry_run=False, allow_failure=False):
        assert args[:2] == ["base", "+record-list"]
        return {
            "ok": True,
            "data": {
                "data": [
                    [
                        1,
                        "https://example.com/a.jpg",
                        "[X012345678](https://www.amazon.com/dp/X012345678)",
                        ["Continuous Output Lighting"],
                    ],
                    [
                        2,
                        "https://example.com/b.jpg",
                        "[X087654321](https://www.amazon.com/dp/X087654321)",
                        ["Selfie Lights"],
                    ],
                ],
                "fields": ["排名", "商品图片", "ASIN", "类目"],
                "record_id_list": ["rec-a", "rec-b"],
                "has_more": False,
            },
        }

    monkeypatch.setattr(sync_report_to_base, "run_cli", fake_run_cli)

    records = sync_report_to_base.list_records("base", "tbl")

    assert records == [
        {
            "record_id": "rec-a",
            "fields": {
                "排名": 1,
                "商品图片": "https://example.com/a.jpg",
                "ASIN": "[X012345678](https://www.amazon.com/dp/X012345678)",
                "类目": ["Continuous Output Lighting"],
            },
        },
        {
            "record_id": "rec-b",
            "fields": {
                "排名": 2,
                "商品图片": "https://example.com/b.jpg",
                "ASIN": "[X087654321](https://www.amazon.com/dp/X087654321)",
                "类目": ["Selfie Lights"],
            },
        },
    ]
