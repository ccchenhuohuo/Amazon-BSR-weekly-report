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
    assert "配置加载失败" not in proc.stderr
