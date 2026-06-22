#!/usr/bin/env python3
"""
统一路径初始化模块

替代多处 sys.path.insert(0, ...) 的混乱方式
支持从任意目录执行脚本
"""

import sys
import os
from pathlib import Path


def init_path() -> Path:
    """
    初始化模块导入路径，将项目根目录添加到 sys.path

    支持多种方式定位项目根目录：
    1. 通过 __file__ 相对位置（scripts/utils/path_init.py）
    2. 通过环境变量 SORFTIME_BSR_SYNC_ROOT
    3. 通过查找 .env 文件或 references 目录
    4. 通过当前工作目录向上查找

    Returns:
        项目根目录路径
    """
    project_root = None

    # 方式1: 通过环境变量
    env_root = os.environ.get('SORFTIME_BSR_SYNC_ROOT')
    if env_root:
        env_path = Path(env_root).resolve()
        if env_path.exists():
            project_root = env_path

    # 方式2: 通过 __file__ 位置
    if project_root is None:
        try:
            script_path = Path(__file__).resolve()
            # 尝试向上查找包含特定标识的目录
            for parent in [script_path] + list(script_path.parents):
                # 检查是否是项目根目录（包含 scripts、references 等标识）
                if (parent / 'scripts').exists() and (parent / 'scripts' / 'utils').exists():
                    project_root = parent
                    break
                # 或者检查是否有 .env.example 文件
                if (parent / '.env.example').exists():
                    project_root = parent
                    break
        except (NameError, RuntimeError):
            # __file__ 不可用（比如在交互式环境）
            pass

    # 方式3: 从当前工作目录向上查找
    if project_root is None:
        try:
            cwd = Path.cwd()
            for parent in [cwd] + list(cwd.parents):
                if (parent / 'scripts' / 'utils').exists():
                    project_root = parent
                    break
                if (parent / '.env.example').exists():
                    project_root = parent
                    break
        except:
            pass

    # 方式4: 最后尝试，假设当前文件在 scripts/utils/ 下
    if project_root is None:
        try:
            script_path = Path(__file__).resolve()
            project_root = script_path.parent.parent.parent
        except (NameError, RuntimeError):
            # 如果所有方式都失败，使用当前工作目录
            project_root = Path.cwd()

    # 确保项目根目录存在
    if not project_root.exists():
        project_root = Path.cwd()

    # 添加到 sys.path（如果尚未存在）
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

    # 同时添加 scripts 目录到 sys.path
    scripts_dir = project_root / 'scripts'
    scripts_dir_str = str(scripts_dir)
    if scripts_dir.exists() and scripts_dir_str not in sys.path:
        sys.path.insert(0, scripts_dir_str)

    return project_root


def get_project_root() -> Path:
    """
    获取项目根目录

    Returns:
        项目根目录路径
    """
    return init_path()


__all__ = [
    "init_path",
    "get_project_root"
]