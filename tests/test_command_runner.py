import importlib.util
import io
import subprocess
import sys
from pathlib import Path


COMMAND_RUNNER_PATH = Path(__file__).resolve().parents[1] / ".agents" / "workflows" / "command_runner.py"
spec = importlib.util.spec_from_file_location("command_runner", COMMAND_RUNNER_PATH)
command_runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
sys.modules[spec.name] = command_runner
spec.loader.exec_module(command_runner)


def test_run_command_timeout_kills_process_group(monkeypatch):
    popen_kwargs = {}
    killed_groups = []

    class Proc:
        pid = 4321

        def __init__(self, command, **kwargs):
            popen_kwargs.update(kwargs)
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")
            self._waits = 0

        def wait(self, timeout=None):
            self._waits += 1
            if self._waits == 1:
                raise subprocess.TimeoutExpired(["sample"], timeout)
            return -9

        def kill(self):
            raise AssertionError("direct process kill should not be used while killpg succeeds")

    monkeypatch.setattr(command_runner.subprocess, "Popen", Proc)
    monkeypatch.setattr(command_runner.os, "killpg", lambda pid, sig: killed_groups.append((pid, sig)))

    result = command_runner.run_command("sample", ["sample"], timeout_seconds=1)

    assert popen_kwargs["start_new_session"] is True
    assert killed_groups == [(4321, command_runner.signal.SIGKILL)]
    assert result.status == "failed"
    assert "timed out" in result.stderr
