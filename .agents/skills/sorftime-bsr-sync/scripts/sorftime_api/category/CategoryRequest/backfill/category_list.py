#!/usr/bin/env python3
"""
类目列表加载模块
"""
from pathlib import Path
from typing import List
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[4]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from utils.path_utils import get_references_dir


def load_category_list() -> List[str]:
    """
    从 bsr-category-list.md 解析所有 node_id（去重）

    Returns:
        List[str]: node_id 列表

    Raises:
        FileNotFoundError: 类目清单文件不存在
        ValueError: 未解析到任何 node_id
    """
    category_list_path = get_references_dir() / "bsr-category-list.md"

    if not category_list_path.exists():
        raise FileNotFoundError(f"类目清单文件不存在: {category_list_path}")

    with open(category_list_path, "r", encoding="utf-8") as f:
        content = f.read()

    node_ids = []
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("| --"):
            continue
        parts = [p.strip() for p in line.split("|")]
        for p in parts:
            if p.isdigit() and len(p) >= 3:  # 假设 node_id 至少 3 位
                node_ids.append(p)

    if not node_ids:
        raise ValueError(f"未从 {category_list_path} 解析到任何 node_id")

    # 去重，保持顺序（dict.fromkeys 保留插入顺序）
    return list(dict.fromkeys(node_ids))


__all__ = ['load_category_list']
