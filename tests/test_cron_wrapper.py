import fcntl
import os
import stat
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = PROJECT_ROOT / ".agents" / "workflows" / "run_sorftime_weekly_cron.sh"


def make_cron_project(tmp_path):
    project = tmp_path / "project"
    python_bin = project / ".venv" / "bin" / "python"
    python_bin.parent.mkdir(parents=True)
    python_bin.write_text(
        "#!/usr/bin/env sh\n"
        "printf '%s\\n' \"$*\" > \"$SORFTIME_FAKE_RUNNER_ARGS\"\n"
        "exit \"${FAKE_RUNNER_EXIT:-0}\"\n",
        encoding="utf-8",
    )
    python_bin.chmod(0o755)
    return project


def run_wrapper(project, lock_file, *, args=(), extra_env=None):
    arg_log = project / "runner-args.txt"
    env = os.environ.copy()
    env.update(
        {
            "SORFTIME_PROJECT_ROOT": str(project),
            "SORFTIME_WEEKLY_LOCK_FILE": str(lock_file),
            "SORFTIME_FAKE_RUNNER_ARGS": str(arg_log),
        }
    )
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(WRAPPER), *args],
        cwd=PROJECT_ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
    )


def assert_cron_permissions(project):
    cron_dir = project / "logs" / "cron"
    cron_log = cron_dir / "cron.log"
    assert stat.S_IMODE(cron_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(cron_log.stat().st_mode) == 0o600


def test_cron_wrapper_preflight_success_and_permissions(tmp_path):
    project = make_cron_project(tmp_path)
    result = run_wrapper(project, tmp_path / "weekly.lock", args=("--preflight",))

    assert result.returncode == 0
    assert_cron_permissions(project)
    assert "--preflight" in (project / "runner-args.txt").read_text(encoding="utf-8")
    assert "amazon-bsr weekly cron finished" in (project / "logs" / "cron" / "cron.log").read_text(
        encoding="utf-8"
    )


def test_cron_wrapper_lock_busy_returns_zero(tmp_path):
    project = make_cron_project(tmp_path)
    lock_file = tmp_path / "weekly.lock"
    lock_file.touch()
    with lock_file.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_wrapper(project, lock_file)

    assert result.returncode == 0
    assert "lock busy, skipped" in (project / "logs" / "cron" / "cron.log").read_text(encoding="utf-8")


def test_cron_wrapper_child_failure_returns_nonzero(tmp_path):
    project = make_cron_project(tmp_path)
    result = run_wrapper(project, tmp_path / "weekly.lock", extra_env={"FAKE_RUNNER_EXIT": "13"})

    assert result.returncode == 13
    assert "failed exit_code=13" in (project / "logs" / "cron" / "cron.log").read_text(encoding="utf-8")
