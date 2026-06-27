import importlib.util
import io
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = (
    ROOT
    / ".agents"
    / "skills"
    / "sorftime-bsr-sync"
    / "scripts"
    / "sorftime_api"
    / "category"
    / "CategoryRequest"
    / "backfill"
    / "pipeline.py"
)


def load_pipeline_module():
    spec = importlib.util.spec_from_file_location("bsr_pipeline", PIPELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_fetch_and_transform_uses_current_python_executable(monkeypatch):
    pipeline = load_pipeline_module()
    commands = []

    class Proc:
        def __init__(self, command, **kwargs):
            self.command = command
            self.returncode = 0
            self.stdout = io.StringIO("")
            commands.append(command)

        def communicate(self, timeout=None):
            return ("[]", "")

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(pipeline.subprocess, "Popen", Proc)

    rows = pipeline._fetch_and_transform(
        "2026-06-24",
        "499310",
        Path("fetch_bsr.py"),
        Path("transform_bsr.py"),
        logger=None,
    )

    assert rows == []
    assert [command[0] for command in commands] == [sys.executable, sys.executable]
