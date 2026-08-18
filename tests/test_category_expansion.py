import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_SCRIPTS = ROOT / ".agents" / "skills" / "sorftime-weekly-report" / "scripts"
BSR_SCRIPTS = ROOT / ".agents" / "skills" / "sorftime-bsr-sync" / "scripts"
BASE_SCRIPTS = ROOT / ".agents" / "skills" / "sorftime-report-base-sync" / "scripts"
WORKFLOW_SCRIPTS = ROOT / ".agents" / "workflows"


def load_module(name: str, path: Path, search_paths: tuple[Path, ...] = ()):
    inserted: list[str] = []
    for search_path in search_paths:
        value = str(search_path)
        if value not in sys.path:
            sys.path.insert(0, value)
            inserted.append(value)
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for value in inserted:
            sys.path.remove(value)


def test_shared_mapping_contains_five_groups_and_twelve_unique_leaves():
    category_config = load_module(
        "expanded_category_config",
        REPORT_SCRIPTS / "category_config.py",
    )

    mapping = category_config.load_category_mapping()

    assert tuple(mapping) == ("灯光类", "支架类", "脚架类", "音视频类", "智能工作室类")
    assert {group: len(categories) for group, categories in mapping.items()} == {
        "灯光类": 3,
        "支架类": 4,
        "脚架类": 3,
        "音视频类": 1,
        "智能工作室类": 1,
    }
    leaves = [category for categories in mapping.values() for category in categories]
    assert len(leaves) == 12
    assert len({category["node"] for category in leaves}) == 12
    assert (leaves[-1]["name"], leaves[-1]["node"]) == (
        "Digital Audio Workstation Controllers",
        "11973701",
    )


def test_bsr_sync_category_list_matches_shared_mapping():
    category_config = load_module(
        "expanded_category_config_for_bsr",
        REPORT_SCRIPTS / "category_config.py",
    )
    category_list = load_module(
        "expanded_bsr_category_list",
        BSR_SCRIPTS
        / "sorftime_api"
        / "category"
        / "CategoryRequest"
        / "backfill"
        / "category_list.py",
        (BSR_SCRIPTS,),
    )

    expected_nodes = [
        category["node"]
        for categories in category_config.load_category_mapping().values()
        for category in categories
    ]

    assert category_list.load_category_list() == expected_nodes


def test_runner_loads_all_groups_and_blocks_incompatible_base_template():
    runner = load_module(
        "expanded_weekly_runner",
        WORKFLOW_SCRIPTS / "run_sorftime_weekly_workflow.py",
        (WORKFLOW_SCRIPTS, REPORT_SCRIPTS),
    )

    assert runner.CATEGORIES == ("灯光类", "支架类", "脚架类", "音视频类", "智能工作室类")
    assert runner.base_template_incompatibilities() == {
        "灯光类": 3,
        "支架类": 4,
        "脚架类": 3,
        "音视频类": 1,
        "智能工作室类": 1,
    }


def test_base_sync_fails_closed_before_using_two_category_template():
    base_sync = load_module(
        "expanded_sync_report_to_base",
        BASE_SCRIPTS / "sync_report_to_base.py",
        (BASE_SCRIPTS, REPORT_SCRIPTS),
    )

    for group in ("灯光类", "支架类", "脚架类", "音视频类", "智能工作室类"):
        error = base_sync.base_template_shape_error(group)
        assert error is not None
        assert "supports exactly 2" in error
