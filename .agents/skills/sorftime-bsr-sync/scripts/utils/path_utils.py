#!/usr/bin/env python3
"""
统一路径管理模块

提供跨脚本一致的路径计算功能
"""

from pathlib import Path
from typing import Optional


def get_skill_dir() -> Path:
    """
    获取 skill 根目录（绝对路径）

    Returns:
        Path: skill 根目录
    """
    # 当前文件位置: {skill}/scripts/utils/path_utils.py
    return Path(__file__).resolve().parent.parent.parent


def get_scripts_dir() -> Path:
    """获取 scripts 目录"""
    return get_skill_dir() / "scripts"


def get_references_dir() -> Path:
    """获取 references 目录"""
    return get_skill_dir() / "references"


def get_logs_dir() -> Path:
    """获取 logs 目录"""
    from utils.constants import LogConfig
    env_log_dir = get_env_log_dir()
    if env_log_dir:
        return Path(env_log_dir)
    return get_skill_dir() / LogConfig.LOG_DIR_NAME


def get_env_log_dir() -> Optional[str]:
    """从环境变量获取日志目录"""
    import os
    return os.environ.get("BSR_LOG_DIR")


def get_env_file() -> Path:
    """获取 .env 文件路径"""
    return get_skill_dir() / ".env"


def get_stream_load_dir() -> Path:
    """
    获取项目内或显式配置的 stream-load scripts 目录

    Returns:
        Path: stream-load scripts 目录路径
    """
    import os

    skill_dir = get_skill_dir()
    configured = os.environ.get("STREAM_LOAD_SCRIPT_DIR")
    candidates = [
        Path(configured).expanduser() if configured else None,
        skill_dir.parent / "stream-load" / "scripts",
    ]
    for candidate in [item for item in candidates if item is not None]:
        if (candidate / "stream_load.py").exists():
            return candidate
    return skill_dir.parent / "stream-load" / "scripts"


def get_stream_load_script() -> Path:
    """获取 stream_load.py 路径"""
    return get_stream_load_dir() / "stream_load.py"


def add_scripts_to_path() -> None:
    """将 scripts 目录添加到 sys.path（一次性调用）"""
    import sys
    scripts_dir = str(get_scripts_dir())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)


__all__ = [
    "get_skill_dir",
    "get_scripts_dir",
    "get_references_dir",
    "get_logs_dir",
    "get_env_log_dir",
    "get_env_file",
    "get_stream_load_dir",
    "get_stream_load_script",
    "add_scripts_to_path",
]
