import importlib.util
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_SCRIPT = ROOT / ".agents" / "skills" / "sorftime-weekly-report" / "scripts" / "generate_weekly_report.py"
BSR_HELP_SCRIPT = (
    ROOT
    / ".agents"
    / "skills"
    / "sorftime-bsr-sync"
    / "scripts"
    / "sorftime_api"
    / "category"
    / "CategoryRequest"
    / "fill_missing.py"
)


def load_report_module():
    scripts_dir = REPORT_SCRIPT.parent
    sys.path.insert(0, str(scripts_dir))
    try:
        spec = importlib.util.spec_from_file_location("generate_weekly_report", REPORT_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        try:
            sys.path.remove(str(scripts_dir))
        except ValueError:
            pass


def test_doris_table_ref_uses_environment(monkeypatch):
    module = load_report_module()
    monkeypatch.setenv("DORIS_DATABASE", "analytics")
    monkeypatch.setenv("DORIS_TABLE", "weekly_bsr")

    assert module.doris_table_ref() == "analytics.weekly_bsr"
    assert module.render_sql(Path(__file__), table=module.doris_table_ref()).startswith("import ")


def test_check_env_redacts_doris_host(monkeypatch, capsys):
    module = load_report_module()
    monkeypatch.setenv("DORIS_HOST", "sensitive-host.example")
    monkeypatch.setenv("DORIS_MYSQL_PORT", "9030")
    monkeypatch.setenv("DORIS_USER", "sensitive-user")
    monkeypatch.setenv("DORIS_PASSWORD", "sensitive-password")
    monkeypatch.setenv("DORIS_DATABASE", "analytics")
    monkeypatch.setenv("DORIS_TABLE", "weekly_bsr")

    summary = module.check_env()
    captured = capsys.readouterr().out

    assert summary["status"] == "ENV_OK"
    assert "sensitive-host.example" not in captured
    assert "sensitive-password" not in captured
    assert '"DORIS_HOST": true' in captured


def test_bsr_help_does_not_require_business_environment():
    env = os.environ.copy()
    for key in list(env):
        if key.startswith(("SORFTIME_", "DORIS_")):
            env.pop(key)

    proc = subprocess.run(
        [sys.executable, str(BSR_HELP_SCRIPT), "--help"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert proc.returncode == 0
    assert "--dates" in proc.stdout
    assert "默认周三" in proc.stdout
    assert "配置加载失败" not in proc.stderr


def test_generate_weekly_report_validation_failure_keeps_existing_report(tmp_path, monkeypatch):
    module = load_report_module()
    target = tmp_path / "20260617灯光类周趋势监测报告.md"
    target.write_text("old report\n", encoding="utf-8")

    class Conn:
        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(module, "load_dotenv", lambda: None)
    monkeypatch.setattr(module, "db_connect", lambda: Conn())
    def fake_mapping():
        mapping = {}
        mapping["灯光类"] = [dict(name="A", node="1"), dict(name="B", node="2")]
        return mapping

    monkeypatch.setattr(module, "parse_mapping", fake_mapping)
    monkeypatch.setattr(module, "overview_counts", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "fetch_category", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "fetch_ulanzi", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "render_overview", lambda *args, **kwargs: "# report")
    monkeypatch.setattr(module, "render_category", lambda *args, **kwargs: "category")
    monkeypatch.setattr(module, "render_ulanzi", lambda *args, **kwargs: "ulanzi")
    monkeypatch.setattr(module, "render_summary", lambda *args, **kwargs: "summary")
    monkeypatch.setattr(module, "query_counts", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "validate", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bad report")))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_weekly_report.py",
            "--category",
            "灯光类",
            "--date",
            "2026-06-17",
            "--out-dir",
            str(tmp_path),
            "--overwrite",
        ],
    )

    try:
        module.main()
    except RuntimeError as exc:
        assert "bad report" in str(exc)
    else:
        raise AssertionError("expected validation failure")

    assert target.read_text(encoding="utf-8") == "old report\n"
    assert not list(tmp_path.glob("*.tmp"))
