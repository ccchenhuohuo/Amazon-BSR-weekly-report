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
WORKFLOW_DIR = PIPELINE_PATH.parent


def load_pipeline_module():
    spec = importlib.util.spec_from_file_location("bsr_pipeline", PIPELINE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_workflow_module():
    package_root = WORKFLOW_DIR.parent
    sys.path.insert(0, str(package_root))
    try:
        import backfill.workflow as workflow

        return workflow
    finally:
        try:
            sys.path.remove(str(package_root))
        except ValueError:
            pass


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


def test_bsr_task_restores_backup_when_load_fails(monkeypatch):
    workflow_module = load_workflow_module()
    workflow = workflow_module.CategoryBSRWorkflow.__new__(workflow_module.CategoryBSRWorkflow)
    workflow.MAX_RETRY = 0
    workflow._fetch_script = Path("fetch.py")
    workflow._transform_script = Path("transform.py")
    workflow._columns = "asin,bsr_date,bsr_category_node"
    workflow._db_config = type(
        "DbConfig",
        (),
        {
            "stream_load_host": "host",
            "stream_load_port": 8030,
            "stream_load_fallback_host": "",
            "stream_load_fallback_port": 0,
            "user": "user",
            "password": "password",
            "database": "db",
            "table": "tbl",
        },
    )()
    workflow.logger = type(
        "Logger",
        (),
        {
            "error": lambda *args, **kwargs: None,
            "warning": lambda *args, **kwargs: None,
            "info": lambda *args, **kwargs: None,
        },
    )()
    restored = []

    monkeypatch.setattr(workflow, "_needs_backfill", lambda date_str, node_id: True)
    monkeypatch.setattr(
        workflow,
        "_backup_existing_rows",
        lambda date_str, node_id: ([{"asin": "A", "bsr_date": date_str, "bsr_category_node": node_id}], Path("backup.json")),
    )
    monkeypatch.setattr(workflow, "_delete_old_data", lambda date_str, node_id: True)
    monkeypatch.setattr(workflow, "_batch_insert_rows", lambda rows: False)
    monkeypatch.setattr(
        workflow,
        "_restore_backup_rows",
        lambda date_str, node_id, rows, path: restored.append((date_str, node_id, rows, path)) or True,
    )
    monkeypatch.setattr(workflow_module, "_fetch_and_transform", lambda *args, **kwargs: [{"asin": str(i)} for i in range(100)])
    monkeypatch.setattr(workflow_module, "_call_stream_load_direct", lambda *args, **kwargs: False)

    assert workflow._process_single_task(("2026-06-17", "499310"), force=True) is False
    assert restored
    assert restored[0][0] == "2026-06-17"
    assert restored[0][1] == "499310"


def test_bsr_task_does_not_delete_when_backup_fails(monkeypatch):
    workflow_module = load_workflow_module()
    workflow = workflow_module.CategoryBSRWorkflow.__new__(workflow_module.CategoryBSRWorkflow)
    workflow.MAX_RETRY = 0
    workflow._fetch_script = Path("fetch.py")
    workflow._transform_script = Path("transform.py")
    workflow.logger = type(
        "Logger",
        (),
        {
            "error": lambda *args, **kwargs: None,
            "warning": lambda *args, **kwargs: None,
            "info": lambda *args, **kwargs: None,
        },
    )()
    deletes = []

    monkeypatch.setattr(workflow, "_needs_backfill", lambda date_str, node_id: True)
    monkeypatch.setattr(workflow, "_backup_existing_rows", lambda date_str, node_id: (_ for _ in ()).throw(RuntimeError("backup failed")))
    monkeypatch.setattr(workflow, "_delete_old_data", lambda date_str, node_id: deletes.append((date_str, node_id)) or True)
    monkeypatch.setattr(workflow_module, "_fetch_and_transform", lambda *args, **kwargs: [{"asin": str(i)} for i in range(100)])

    assert workflow._process_single_task(("2026-06-17", "499310"), force=True) is False
    assert deletes == []
    assert workflow._task_status_summary()["backups"]["2026-06-17+499310"]["status"] == "failed"
