import json
import importlib.util
import os
import sys
from datetime import date
from pathlib import Path


RUNNER_PATH = Path(__file__).resolve().parents[1] / ".agents" / "workflows" / "run_sorftime_weekly_workflow.py"
spec = importlib.util.spec_from_file_location("run_sorftime_weekly_workflow", RUNNER_PATH)
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def test_report_path_uses_configured_report_dir(tmp_path):
    assert runner.report_path(date(2026, 6, 17), "灯光类", tmp_path) == tmp_path / "20260617灯光类周趋势监测报告.md"


def test_runner_loads_root_dotenv(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("FEISHU_TEMPLATE_BASE_TOKEN", raising=False)
    monkeypatch.delenv("LARK_CLI_BIN", raising=False)
    (tmp_path / ".env").write_text(
        "FEISHU_TEMPLATE_BASE_TOKEN=template-token\nLARK_CLI_BIN=/usr/local/bin/lark-cli\n",
        encoding="utf-8",
    )

    runner.load_dotenv()

    assert os.environ["FEISHU_TEMPLATE_BASE_TOKEN"] == "template-token"
    assert os.environ["LARK_CLI_BIN"] == "/usr/local/bin/lark-cli"


def test_redact_command_hides_base_tokens():
    command = [
        "python3",
        "sync_report_to_base.py",
        "--base-token",
        "sample-value-123",
        "--template-base-token",
        "sample-template-123",
        "--folder-token",
        "sample-folder-123",
        "--user-id",
        "ou_sample",
        "--markdown",
        "contains https://ulanzichina.feishu.cn/base/base_sample",
    ]

    assert runner.redact_command(command) == [
        "python3",
        "sync_report_to_base.py",
        "--base-token",
        "[REDACTED]",
        "--template-base-token",
        "[REDACTED]",
        "--folder-token",
        "[REDACTED]",
        "--user-id",
        "[REDACTED]",
        "--markdown",
        "[REDACTED]",
    ]


def test_redact_detail_hides_sensitive_string_values():
    detail = {
        "base_token": "sample-value-123",
        "copied_base_url": "https://example.invalid/app/abc",
        "counts": {"异动数据": 10},
    }

    assert runner.redact_detail(detail) == {
        "base_token": "[REDACTED]",
        "copied_base_url": "[REDACTED]",
        "counts": {"异动数据": 10},
    }


def test_redact_output_text_hides_lark_tokens_and_urls():
    text = (
        '{"app_token": "sample-value-123", "url": "https://redacted.invalid/app/abc", '
        '"chat_id": "oc_1234567890abcdef", "message_id": "om_1234567890abcdef"} '
        "--base-token sample-value-123 ou_1234567890abcdef"
    )

    redacted = runner.redact_output_text(text)

    assert "sample-value-123" not in redacted
    assert "redacted.invalid" not in redacted
    assert "oc_1234567890abcdef" not in redacted
    assert "om_1234567890abcdef" not in redacted
    assert "ou_1234567890abcdef" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_output_text_hides_project_absolute_paths():
    text = f"report={runner.PROJECT_ROOT / 'reports' / 'sample.md'}"

    redacted = runner.redact_output_text(text)

    assert str(runner.PROJECT_ROOT) not in redacted
    assert "$PROJECT_ROOT/reports/sample.md" in redacted


def test_runner_dry_run_writes_summary_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_sorftime_weekly_workflow.py",
            "--date",
            "2026-06-17",
            "--dry-run",
            "--skip-bsr",
            "--skip-report",
            "--skip-base-sync",
        ],
    )

    assert runner.main() == 0
    run_dirs = list((tmp_path / "logs" / "sorftime-weekly-workflow").iterdir())
    assert len(run_dirs) == 1
    summary_path = run_dirs[0] / "summary.json"
    run_report_path = run_dirs[0] / "run-report.md"
    assert summary_path.exists()
    assert run_report_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["dry_run"] is True
    assert [item["status"] for item in summary["results"]] == ["skipped", "skipped", "skipped", "skipped"]
    assert summary["results"][-1]["name"] == "notify:feishu"


def test_runner_argument_failure_still_writes_summary_and_report(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_sorftime_weekly_workflow.py",
            "--date",
            "not-a-date",
            "--dry-run",
        ],
    )

    assert runner.main() == 1
    run_dirs = list((tmp_path / "logs" / "sorftime-weekly-workflow").iterdir())
    assert len(run_dirs) == 1
    summary_path = run_dirs[0] / "summary.json"
    run_report_path = run_dirs[0] / "run-report.md"
    assert summary_path.exists()
    assert run_report_path.exists()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["report_date"] == "not-a-date"
    assert summary["results"][0]["name"] == "argument-validation"
    assert summary["results"][0]["status"] == "failed"


def test_runner_main_full_publish_path_sends_notification(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_sorftime_weekly_workflow.py",
            "--date",
            "2026-06-17",
            "--skip-bsr",
            "--report-dir",
            str(tmp_path / "reports"),
            "--base-token",
            "灯光类=base-light",
            "--base-token",
            "支架类=base-mount",
            "--base-token",
            "脚架类=base-tripod",
            "--notify-user-id",
            "ou_sample",
            "--require-notify",
        ],
    )
    calls = []

    def fake_run_command(name, command, timeout_seconds):
        calls.append(name)
        if name.startswith("weekly-report:"):
            category = name.split(":", 1)[1]
            report = tmp_path / "reports" / f"20260617{category}周趋势监测报告.md"
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(f"# {category}周趋势监测报告\n", encoding="utf-8")
            return runner.StepResult(name=name, status="ok", returncode=0, stdout="")
        if name.startswith("base-sync:"):
            category = name.split(":", 1)[1]
            return runner.StepResult(
                name=name,
                status="ok",
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "category": category,
                        "counts": {"异动数据": 0, "低分高销数据": 0, "本品数据": 0},
                        "server_verification": {"status": "ok"},
                    }
                ),
            )
        if name.startswith("base-doc-create:"):
            category = name.split(":", 1)[1]
            return runner.StepResult(
                name=name,
                status="ok",
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "block": {
                                "type": "docx",
                                "docx_token": f"docx-{category}",
                            }
                        },
                    }
                ),
            )
        if name.startswith("base-doc-update:"):
            return runner.StepResult(
                name=name,
                status="ok",
                returncode=0,
                stdout=json.dumps({"ok": True, "data": {"result": "success", "updated_blocks_count": 1}}),
            )
        if name == "notify:feishu":
            return runner.StepResult(
                name=name,
                status="ok",
                returncode=0,
                stdout=json.dumps({"ok": True, "data": {"chat_id": "oc_sample", "message_id": "om_sample"}}),
            )
        raise AssertionError(f"unexpected command {name}")

    monkeypatch.setattr(runner, "run_command", fake_run_command)

    assert runner.main() == 0
    assert calls.count("notify:feishu") == 1
    for category in runner.CATEGORIES:
        assert f"weekly-report:{category}" in calls
        assert f"base-sync:{category}" in calls
        assert f"base-doc-create:{category}" in calls
        assert f"base-doc-update:{category}" in calls

    run_dir = next((tmp_path / "logs" / "sorftime-weekly-workflow").iterdir())
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["results"][-1]["name"] == "notify:feishu"
    assert summary["results"][-1]["status"] == "ok"


def test_runner_base_token_format_failure_does_not_echo_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_sorftime_weekly_workflow.py",
            "--date",
            "2026-06-17",
            "--base-token",
            "sample-sensitive-123",
        ],
    )

    assert runner.main() == 1
    run_dir = next((tmp_path / "logs" / "sorftime-weekly-workflow").iterdir())
    summary_text = (run_dir / "summary.json").read_text(encoding="utf-8")
    report_text = (run_dir / "run-report.md").read_text(encoding="utf-8")
    assert "sample-sensitive-123" not in summary_text
    assert "sample-sensitive-123" not in report_text


def test_base_copy_without_new_token_fails_in_production(monkeypatch):
    def fake_run_command(name, command, timeout_seconds):
        return runner.StepResult(name=name, status="ok", returncode=0, stdout='{"ok": true, "data": {}}')

    monkeypatch.setattr(runner, "run_command", fake_run_command)

    result, token = runner.copy_base_for_category(
        "灯光类",
        date(2026, 6, 17),
        "template-token",
        None,
        False,
        1,
    )

    assert token is None
    assert result.status == "failed"
    assert result.detail["copied_base_token_present"] is False


def test_parse_base_doc_block_from_create_output():
    data = {
        "ok": True,
        "data": {
            "block": {
                "block_id": "blk_123",
                "type": "docx",
                "name": "20260617灯光类周趋势监测报告",
                "docx_token": "docx_123",
                "url": "https://example.feishu.cn/docx/docx_123",
            }
        },
    }

    parsed = runner.parse_base_doc_block(data)

    assert parsed == {
        "block_id": "blk_123",
        "docx_token": "docx_123",
        "url": "https://example.feishu.cn/docx/docx_123",
        "name": "20260617灯光类周趋势监测报告",
    }


def test_lark_cli_content_argument_requires_project_root(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    report = tmp_path / "reports" / "sample.md"
    report.parent.mkdir()
    report.write_text("# sample\n", encoding="utf-8")

    assert runner.lark_cli_content_argument(report) == "@reports/sample.md"

    outside = tmp_path.parent / "outside-report.md"
    outside.write_text("# outside\n", encoding="utf-8")
    try:
        runner.lark_cli_content_argument(outside)
    except ValueError as exc:
        assert "project root" in str(exc)
    else:
        raise AssertionError("expected ValueError for report outside project root")


def test_prepare_base_doc_markdown_removes_image_tags(tmp_path):
    report = tmp_path / "report.md"
    report.write_text(
        (
            "---\n"
            'title: "sample"\n'
            "---\n"
            "# Report\n\n"
            '| 商品图片 |\n| --- |\n| <img src="https://example.invalid/a.jpg" width="150" /> |\n'
        ),
        encoding="utf-8",
    )
    content_dir = tmp_path / "content"

    output, image_count = runner.prepare_base_doc_markdown(report, content_dir)

    assert image_count == 1
    assert output == content_dir / "report.base-doc.md"
    text = output.read_text(encoding="utf-8")
    assert text.startswith("# Report")
    assert 'title: "sample"' not in text
    assert "<img" not in text
    assert "图片见 Base 数据表" in text


def test_prepare_base_doc_markdown_removes_varied_image_tags(tmp_path):
    report = tmp_path / "report.md"
    report.write_text(
        (
            "# Report\n\n"
            "<IMG SRC='https://example.invalid/a.jpg' width='150'>\n"
            '<img\n  alt="sample"\n  src="https://example.invalid/b.jpg"\n/>\n'
        ),
        encoding="utf-8",
    )

    output, image_count = runner.prepare_base_doc_markdown(report, tmp_path / "content")

    assert image_count == 2
    text = output.read_text(encoding="utf-8")
    assert "<IMG" not in text
    assert "<img" not in text
    assert text.count("图片见 Base 数据表") == 2


def test_publish_report_doc_to_base_creates_and_updates_doc(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    report = tmp_path / "reports" / "20260617灯光类周趋势监测报告.md"
    report.parent.mkdir()
    report.write_text(
        '# 灯光类周趋势监测报告\n\n<img src="https://example.invalid/a.jpg" width="150" />\n',
        encoding="utf-8",
    )
    calls = []

    def fake_run_command(name, command, timeout_seconds):
        calls.append((name, command, timeout_seconds))
        if name == "base-doc-create:灯光类":
            return runner.StepResult(
                name=name,
                status="ok",
                returncode=0,
                stdout=json.dumps(
                    {
                        "ok": True,
                        "data": {
                            "block": {
                                "block_id": "blk_123",
                                "type": "docx",
                                "docx_token": "docx_123",
                                "url": "https://example.feishu.cn/docx/docx_123",
                            }
                        },
                    }
                ),
            )
        return runner.StepResult(
            name=name,
            status="ok",
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "data": {"document": {"revision_id": 7}, "result": "success", "updated_blocks_count": 3},
                }
            ),
        )

    monkeypatch.setattr(runner, "run_command", fake_run_command)

    results = runner.publish_report_doc_to_base(
        "灯光类",
        date(2026, 6, 17),
        "base_123",
        report,
        dry_run=False,
        timeout_seconds=99,
        doc_content_dir=tmp_path / "doc-content",
    )

    assert [item.name for item in results] == ["base-doc-create:灯光类", "base-doc-update:灯光类"]
    assert all(item.status == "ok" for item in results)
    create_cmd = calls[0][1]
    update_cmd = calls[1][1]
    assert create_cmd[:3] == [runner.LARK_CLI_BIN, "base", "+base-block-create"]
    assert "--type" in create_cmd
    assert "docx" in create_cmd
    assert update_cmd[:3] == [runner.LARK_CLI_BIN, "docs", "+update"]
    assert update_cmd[update_cmd.index("--doc") + 1] == "docx_123"
    assert update_cmd[update_cmd.index("--content") + 1] == "@doc-content/20260617灯光类周趋势监测报告.base-doc.md"
    assert results[1].detail["revision_id"] == 7
    assert results[1].detail["image_tags_removed"] == 1
    assert results[1].detail["docx_url"] == "https://example.feishu.cn/docx/docx_123"
    assert results[1].detail["base_url"] == "https://ulanzichina.feishu.cn/base/base_123"


def test_publish_report_doc_to_base_fails_without_docx_token_in_production(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    report = tmp_path / "reports" / "20260617灯光类周趋势监测报告.md"
    report.parent.mkdir()
    report.write_text("# report\n", encoding="utf-8")
    calls = []

    def fake_run_command(name, command, timeout_seconds):
        calls.append(name)
        return runner.StepResult(
            name=name,
            status="ok",
            returncode=0,
            stdout=json.dumps({"ok": True, "data": {"block": {"type": "docx", "block_id": "blk_123"}}}),
        )

    monkeypatch.setattr(runner, "run_command", fake_run_command)

    results = runner.publish_report_doc_to_base(
        "灯光类",
        date(2026, 6, 17),
        "base_123",
        report,
        dry_run=False,
        timeout_seconds=99,
        doc_content_dir=tmp_path / "doc-content",
    )

    assert calls == ["base-doc-create:灯光类"]
    assert [item.status for item in results] == ["ok", "failed"]
    assert results[1].name == "base-doc-update:灯光类"


def test_publish_report_doc_to_base_fails_on_partial_doc_update(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    report = tmp_path / "reports" / "20260617灯光类周趋势监测报告.md"
    report.parent.mkdir()
    report.write_text("# report\n", encoding="utf-8")

    def fake_run_command(name, command, timeout_seconds):
        if name == "base-doc-create:灯光类":
            return runner.StepResult(
                name=name,
                status="ok",
                returncode=0,
                stdout=json.dumps({"ok": True, "data": {"block": {"type": "docx", "docx_token": "docx_123"}}}),
            )
        return runner.StepResult(
            name=name,
            status="ok",
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "data": {
                        "result": "partial_success",
                        "warnings": [{"message": "some blocks were skipped"}],
                    },
                }
            ),
        )

    monkeypatch.setattr(runner, "run_command", fake_run_command)

    results = runner.publish_report_doc_to_base(
        "灯光类",
        date(2026, 6, 17),
        "base_123",
        report,
        dry_run=False,
        timeout_seconds=99,
        doc_content_dir=tmp_path / "doc-content",
    )

    assert results[1].status == "failed"
    assert results[1].detail["api_response_ok"] is True
    assert results[1].detail["result"] == "partial_success"
    assert "Doc update warnings" in results[1].stderr


def test_publish_report_doc_to_base_builds_doc_url_from_token_without_url(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    report = tmp_path / "reports" / "20260617灯光类周趋势监测报告.md"
    report.parent.mkdir()
    report.write_text("# report\n", encoding="utf-8")

    def fake_run_command(name, command, timeout_seconds):
        if name == "base-doc-create:灯光类":
            return runner.StepResult(
                name=name,
                status="ok",
                returncode=0,
                stdout=json.dumps({"ok": True, "data": {"block": {"type": "docx", "docx_token": "docx_123"}}}),
            )
        return runner.StepResult(
            name=name,
            status="ok",
            returncode=0,
            stdout=json.dumps({"ok": True, "data": {"result": "success", "updated_blocks_count": 1}}),
        )

    monkeypatch.setattr(runner, "run_command", fake_run_command)

    results = runner.publish_report_doc_to_base(
        "灯光类",
        date(2026, 6, 17),
        "base_123",
        report,
        dry_run=False,
        timeout_seconds=99,
        doc_content_dir=tmp_path / "doc-content",
    )

    assert results[0].detail["docx_url_present"] is True
    assert results[0].detail["docx_url"] == "https://ulanzichina.feishu.cn/docx/docx_123"
    assert results[1].status == "ok"
    assert results[1].detail["docx_url_present"] is True
    assert results[1].detail["docx_url"] == "https://ulanzichina.feishu.cn/docx/docx_123"


def notification_results() -> list:
    results = []
    for category in runner.CATEGORIES:
        results.extend(
            [
                runner.StepResult(f"weekly-report:{category}", "ok"),
                runner.StepResult(
                    f"base-sync:{category}",
                    "ok",
                    detail={"base_url": f"https://ulanzichina.feishu.cn/base/{category}-base"},
                ),
                runner.StepResult(f"base-doc-create:{category}", "ok"),
                runner.StepResult(
                    f"base-doc-update:{category}",
                    "ok",
                    detail={"docx_url": f"https://ulanzichina.feishu.cn/docx/{category}-doc"},
                ),
            ]
        )
    return results


def test_build_success_notification_message_uses_settled_template(tmp_path):
    message = runner.build_workflow_notification_message(
        date(2026, 6, 24),
        notification_results(),
        tmp_path / "run-report.md",
        finished_at="2026-06-27 20:14:57 CST",
    )

    assert "【Amazon BSR 战略周报】已完成" in message
    assert "报告日期：2026-06-24" in message
    assert "运行结果：3/3 类目成功" in message
    assert "完成时间：2026-06-27 20:14:57 CST" in message
    assert "灯光类：[多维表格](https://ulanzichina.feishu.cn/base/灯光类-base)" in message
    assert "[周报文档](https://ulanzichina.feishu.cn/docx/灯光类-doc)" in message
    assert "说明：周报文档已新增到对应多维表格左侧栏；商品图片见 Base 数据表。" in message


def test_build_failure_notification_message_lists_failed_steps_and_log(tmp_path):
    results = notification_results()
    for item in results:
        if item.name == "base-doc-update:脚架类":
            item.status = "failed"
    report_path = tmp_path / "run-report.md"

    message = runner.build_workflow_notification_message(
        date(2026, 6, 24),
        results,
        report_path,
        finished_at="2026-06-27 20:14:57 CST",
    )

    assert "【Amazon BSR 战略周报】运行异常" in message
    assert "失败环节：base-doc-update:脚架类" in message
    assert str(tmp_path) not in message
    assert "排查日志：[LOCAL_PATH]/run-report.md" in message


def test_skipped_category_step_uses_abnormal_notification(tmp_path):
    results = notification_results()
    for item in results:
        if item.name == "base-doc-update:脚架类":
            item.status = "skipped"
            item.detail = {"reason": "missing docx token"}

    message = runner.build_workflow_notification_message(
        date(2026, 6, 24),
        results,
        tmp_path / "run-report.md",
        finished_at="2026-06-27 20:14:57 CST",
    )

    assert "【Amazon BSR 战略周报】运行异常" in message
    assert "运行结果：2/3 类目成功" in message
    assert "失败环节：base-doc-update:脚架类" in message


def test_notification_idempotency_key_is_stable_without_run_dir(tmp_path):
    results = notification_results()
    key1 = runner.notification_idempotency_key(
        date(2026, 6, 24),
        results,
        "ou_sample",
        tmp_path / "run-a" / "run-report.md",
    )
    key2 = runner.notification_idempotency_key(
        date(2026, 6, 24),
        results,
        "ou_sample",
        tmp_path / "run-b" / "run-report.md",
    )

    assert key1 == key2
    assert key1.startswith("bsr-20260624-ok-")

    for item in results:
        if item.name == "base-doc-update:脚架类":
            item.status = "skipped"

    failed_key = runner.notification_idempotency_key(
        date(2026, 6, 24),
        results,
        "ou_sample",
        tmp_path / "run-c" / "run-report.md",
    )
    assert failed_key.startswith("bsr-20260624-fail-")
    assert failed_key != key1


def test_send_workflow_notification_uses_short_idempotency_key(monkeypatch, tmp_path):
    calls = []

    def fake_run_command(name, command, timeout_seconds):
        calls.append((name, command, timeout_seconds))
        return runner.StepResult(
            name,
            "ok",
            returncode=0,
            stdout=json.dumps(
                {
                    "ok": True,
                    "data": {
                        "chat_id": "oc_sample",
                        "message_id": "om_sample",
                        "create_time": "2026-06-27 20:15:32",
                    },
                }
            ),
        )

    monkeypatch.setattr(runner, "run_command", fake_run_command)

    result = runner.send_workflow_notification(
        report_date=date(2026, 6, 24),
        dry_run=False,
        notify_dry_run=False,
        require_notify=False,
        notify_user_id="ou_sample",
        notify_chat_id="",
        notify_as="bot",
        results=notification_results(),
        run_report_path=tmp_path / "run-report.md",
        timeout_seconds=7,
    )

    assert result.status == "ok"
    command = calls[0][1]
    assert command[:3] == [runner.LARK_CLI_BIN, "im", "+messages-send"]
    assert command[command.index("--user-id") + 1] == "ou_sample"
    idempotency_key = command[command.index("--idempotency-key") + 1]
    assert idempotency_key.startswith("bsr-20260624-ok-")
    assert len(idempotency_key) < 64
    assert "【Amazon BSR 战略周报】已完成" in command[command.index("--markdown") + 1]
    assert result.detail["message_id_present"] is True


def test_send_workflow_notification_fails_without_message_id(monkeypatch, tmp_path):
    def fake_run_command(name, command, timeout_seconds):
        return runner.StepResult(
            name,
            "ok",
            returncode=0,
            stdout=json.dumps({"ok": True, "data": {"chat_id": "oc_sample"}}),
        )

    monkeypatch.setattr(runner, "run_command", fake_run_command)

    result = runner.send_workflow_notification(
        report_date=date(2026, 6, 24),
        dry_run=False,
        notify_dry_run=False,
        require_notify=False,
        notify_user_id="ou_sample",
        notify_chat_id="",
        notify_as="bot",
        results=notification_results(),
        run_report_path=tmp_path / "run-report.md",
        timeout_seconds=7,
    )

    assert result.status == "failed"
    assert result.detail["api_response_ok"] is True
    assert result.detail["message_id_present"] is False
    assert "did not confirm delivery" in result.stderr


def test_send_workflow_notification_can_require_recipient(tmp_path):
    result = runner.send_workflow_notification(
        report_date=date(2026, 6, 24),
        dry_run=False,
        notify_dry_run=False,
        require_notify=True,
        notify_user_id="",
        notify_chat_id="",
        notify_as="bot",
        results=notification_results(),
        run_report_path=tmp_path / "run-report.md",
        timeout_seconds=7,
    )

    assert result.status == "failed"
    assert result.detail["required"] is True


def test_missing_report_file_makes_base_sync_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_sorftime_weekly_workflow.py",
            "--date",
            "2026-06-17",
            "--skip-bsr",
            "--skip-report",
            "--base-token",
            "灯光类=sample-value-123",
            "--base-token",
            "支架类=sample-value-456",
            "--base-token",
            "脚架类=sample-value-789",
        ],
    )

    assert runner.main() == 1
    run_dir = next((tmp_path / "logs" / "sorftime-weekly-workflow").iterdir())
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["results"][0]["name"] == "base-sync:灯光类"
    assert summary["results"][0]["status"] == "failed"
    assert str(tmp_path) not in (run_dir / "run-report.md").read_text(encoding="utf-8")
