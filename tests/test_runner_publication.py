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
        '{"app_token": "sample-value-123", "url": "https://redacted.invalid/app/abc"} '
        "--base-token sample-value-123"
    )

    redacted = runner.redact_output_text(text)

    assert "sample-value-123" not in redacted
    assert "redacted.invalid" not in redacted
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
    assert [item["status"] for item in summary["results"]] == ["skipped", "skipped", "skipped"]


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
