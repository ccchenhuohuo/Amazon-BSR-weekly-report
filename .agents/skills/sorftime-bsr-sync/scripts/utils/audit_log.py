#!/usr/bin/env python3
"""
共享审计日志模块

所有 bsr-sync 脚本调用此模块记录操作日志
- 线程安全的初始化
- 敏感信息自动过滤
- 按天轮转日志
"""

import os
import sys
import logging
import threading
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

# 导入路径初始化
from utils.path_init import init_path
init_path()

from utils.path_utils import (
    get_skill_dir as _get_skill_dir,
    get_logs_dir as _get_logs_dir,
)
from utils.constants import LogConfig
from utils.log_filter import setup_sensitive_filter


# 线程锁，防止并发初始化
_logger_init_lock = threading.Lock()


def get_skill_dir() -> Path:
    """获取 skill 根目录（向后兼容）"""
    return _get_skill_dir()


def get_log_dir() -> Path:
    """
    获取日志目录

    优先级：
        1. 环境变量 BSR_LOG_DIR
        2. {skill_dir}/logs/
    """
    from utils.path_utils import get_env_log_dir
    env_log_dir = get_env_log_dir()
    if env_log_dir:
        return Path(env_log_dir)
    return _get_logs_dir()


def get_log_file() -> Path:
    """获取日志文件路径"""
    return get_log_dir() / LogConfig.LOG_FILE_NAME


def get_logger(name: str, log_level: int = logging.INFO) -> logging.Logger:
    """
    获取指定名称的 logger（线程安全，单例模式）

    Args:
        name: logger 名称，通常传入 __name__
        log_level: 日志级别，默认 INFO

    Returns:
        配置好的 logger 实例
    """
    logger = logging.getLogger(name)

    # 双重检查锁定
    if logger.handlers:
        logger.setLevel(log_level)
        return logger

    with _logger_init_lock:
        # 再次检查，防止竞态条件
        if logger.handlers:
            logger.setLevel(log_level)
            return logger

        # 创建日志目录
        log_dir = get_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)

        log_file = get_log_file()

        logger.setLevel(log_level)
        logger.propagate = False  # 防止重复输出到 root logger

        # 文件 handler（按天轮转，保留 30 天）
        fh = TimedRotatingFileHandler(
            log_file,
            when="midnight",
            interval=1,
            backupCount=LogConfig.LOG_BACKUP_COUNT,
            encoding="utf-8"
        )
        fh.setLevel(log_level)
        file_formatter = logging.Formatter(
            '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        fh.setFormatter(file_formatter)

        # 控制台 handler（stderr）
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(log_level)
        sh.setFormatter(file_formatter)

        # 添加敏感信息过滤器
        fh = setup_sensitive_filter(fh)
        sh = setup_sensitive_filter(sh)

        logger.addHandler(fh)
        logger.addHandler(sh)

        return logger


def log_sync_event(
    script: str,
    action: str,
    date_or_task: str,
    detail: str = "",
    log_level: int = logging.INFO
) -> None:
    """
    记录同步事件快捷函数

    Args:
        script: 脚本名称
        action: 操作动作
        date_or_task: BSR 日期或任务标识
        detail: 额外详情
        log_level: 日志级别
    """
    logger = get_logger(script)
    msg = f"action={action} date={date_or_task}"
    if detail:
        msg += f" detail={detail}"

    if action in ("SUCCESS", "SKIP"):
        logger.info(msg)
    elif action == "FAIL":
        logger.error(msg)
    else:
        logger.log(log_level, msg)


__all__ = [
    "get_logger",
    "log_sync_event",
    "get_skill_dir",
    "get_log_dir",
    "get_log_file"
]