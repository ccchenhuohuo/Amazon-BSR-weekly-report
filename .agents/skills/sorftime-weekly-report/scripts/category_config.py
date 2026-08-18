#!/usr/bin/env python3
"""Load and validate the project-wide Sorftime report category mapping."""

from __future__ import annotations

from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MAPPING_PATH = SKILL_DIR / "references/category-mapping.md"
MIN_CATEGORIES_PER_REPORT = 1
MAX_CATEGORIES_PER_REPORT = 4


class CategoryConfigError(ValueError):
    """Raised when the category mapping is missing or internally inconsistent."""


def load_category_mapping(path: Path = DEFAULT_MAPPING_PATH) -> dict[str, list[dict[str, str]]]:
    if not path.exists():
        raise CategoryConfigError(f"category mapping not found: {path}")

    mapping: dict[str, list[dict[str, str]]] = {}
    current: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("## "):
            current = line[3:].strip()
            if current == "关联笔记":
                current = None
                continue
            if not current:
                raise CategoryConfigError("report group heading cannot be empty")
            if current in mapping:
                raise CategoryConfigError(f"duplicate report group: {current}")
            mapping[current] = []
            continue
        if not current or not line.startswith("|") or "node_id" in line.lower() or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2 or not cells[0] or not cells[1]:
            continue
        path_text, node_id = cells[:2]
        mapping[current].append({
            "name": path_text.split(">")[-1].strip(),
            "path": path_text,
            "node": node_id,
        })

    if not mapping:
        raise CategoryConfigError(f"no report groups found in {path}")

    seen_nodes: dict[str, str] = {}
    for group, categories in mapping.items():
        if not MIN_CATEGORIES_PER_REPORT <= len(categories) <= MAX_CATEGORIES_PER_REPORT:
            raise CategoryConfigError(
                f"{group} must contain {MIN_CATEGORIES_PER_REPORT}-{MAX_CATEGORIES_PER_REPORT} "
                f"leaf categories, got {len(categories)}"
            )
        seen_names: set[str] = set()
        for category in categories:
            node_id = category["node"]
            name = category["name"]
            if not node_id.isdigit():
                raise CategoryConfigError(f"{group}/{name} has invalid node_id: {node_id!r}")
            if name in seen_names:
                raise CategoryConfigError(f"{group} contains duplicate leaf category name: {name}")
            seen_names.add(name)
            if node_id in seen_nodes:
                raise CategoryConfigError(
                    f"node_id {node_id} is shared by {seen_nodes[node_id]} and {group}/{name}"
                )
            seen_nodes[node_id] = f"{group}/{name}"

    return mapping


def report_groups(path: Path = DEFAULT_MAPPING_PATH) -> tuple[str, ...]:
    return tuple(load_category_mapping(path))


def all_leaf_categories(path: Path = DEFAULT_MAPPING_PATH) -> list[dict[str, str]]:
    return [category for categories in load_category_mapping(path).values() for category in categories]


__all__ = [
    "CategoryConfigError",
    "DEFAULT_MAPPING_PATH",
    "MAX_CATEGORIES_PER_REPORT",
    "MIN_CATEGORIES_PER_REPORT",
    "all_leaf_categories",
    "load_category_mapping",
    "report_groups",
]
