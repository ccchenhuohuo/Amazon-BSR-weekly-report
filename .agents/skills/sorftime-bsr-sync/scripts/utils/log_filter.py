#!/usr/bin/env python3
"""
日志过滤器模块

提供敏感信息过滤功能，防止 API Key、密码等出现在日志中
"""

import logging
import re
from typing import Any, List, Union


class SensitiveDataFilter(logging.Filter):
    """敏感数据日志过滤器"""

    # 敏感信息匹配模式
    SENSITIVE_PATTERNS = [
        # API Key 相关（带引号匹配）
        (r'(api[_-]?key\s*[:=]\s*)(["\']?)(\S+?)(\2)', r'\1\2***\4'),
        (r'(authorization\s*[:=]\s*basicauth\s+)(["\']?)(\S+?)(\2)', r'\1\2***\4'),
        (r'(authorization\s*[:=]\s*)(["\']?)(\S+?)(\2)', r'\1\2***\4'),
        (r'(token\s*[:=]\s*)(["\']?)(\S+?)(\2)', r'\1\2***\4'),
        (r'(secret\s*[:=]\s*)(["\']?)(\S+?)(\2)', r'\1\2***\4'),
        # 密码相关（带引号匹配）
        (r'(password\s*[:=]\s*)(["\']?)(\S+?)(\2)', r'\1\2***\4'),
        (r'(passwd\s*[:=]\s*)(["\']?)(\S+?)(\2)', r'\1\2***\4'),
        # JSON 格式的敏感信息（双引号）
        (r'("(?:api[_-]?key|password|passwd|secret|token|authorization)")\s*:\s*"[^"]*"', r'\1:"***"'),
        # JSON 格式的敏感信息（单引号）
        (r"('(?:api[_-]?key|password|passwd|secret|token|authorization)')\s*:\s*'[^']*'", r"\1:'***'"),
        # URL 查询参数中的敏感信息
        (r'([?&](?:api[_-]?key|password|passwd|secret|token|authorization)=)[^&\s]+', r'\1***'),
        # 数据库连接字符串
        (r'(mysql://\S+?:)(\S+?)(@\S+)', r'\1***\3'),
        (r'(postgresql://\S+?:)(\S+?)(@\S+)', r'\1***\3'),
        (r'(doris://\S+?:)(\S+?)(@\S+)', r'\1***\3'),
        (r'(jdbc:[^:]+://\S+?:)(\S+?)(@\S+)', r'\1***\3'),
    ]

    # 预编译正则表达式
    _COMPILED_PATTERNS = [(re.compile(p, re.IGNORECASE), r) for p, r in SENSITIVE_PATTERNS]

    def filter(self, record: logging.LogRecord) -> bool:
        """
        过滤日志记录中的敏感信息

        Args:
            record: 日志记录

        Returns:
            True（总是记录，只是修改内容）
        """
        # 过滤消息
        record.msg = self._sanitize_message(str(record.msg))

        # 过滤参数
        if hasattr(record, 'args') and record.args:
            record.args = self._sanitize_args(record.args)

        return True

    @classmethod
    def _sanitize_message(cls, msg: str) -> str:
        """
        清理消息中的敏感信息

        Args:
            msg: 原始消息

        Returns:
            清理后的消息
        """
        for pattern, replacement in cls._COMPILED_PATTERNS:
            msg = pattern.sub(replacement, msg)
        return msg

    @classmethod
    def _sanitize_args(cls, args: Any) -> Any:
        """
        清理参数中的敏感信息

        Args:
            args: 原始参数

        Returns:
            清理后的参数
        """
        if isinstance(args, tuple):
            return tuple(cls._sanitize_single(arg) for arg in args)
        elif isinstance(args, dict):
            return {k: cls._sanitize_single(v) for k, v in args.items()}
        else:
            return cls._sanitize_single(args)

    @classmethod
    def _sanitize_single(cls, value: Any) -> Any:
        """
        清理单个值中的敏感信息

        Args:
            value: 原始值

        Returns:
            清理后的值
        """
        if isinstance(value, str):
            for pattern, replacement in cls._COMPILED_PATTERNS:
                value = pattern.sub(replacement, value)
        return value


def sanitize_message(msg: str) -> str:
    """
    清理消息中的敏感信息（独立函数，不依赖 logging）

    Args:
        msg: 原始消息

    Returns:
        清理后的消息
    """
    return SensitiveDataFilter._sanitize_message(msg)


def setup_sensitive_filter(logger_or_handler: Union[logging.Logger, logging.Handler]) -> Union[logging.Logger, logging.Handler]:
    """
    为 logger 或 handler 设置敏感信息过滤器

    Args:
        logger_or_handler: 要设置的 logger 或 handler

    Returns:
        设置后的 logger 或 handler
    """
    sensitive_filter = SensitiveDataFilter()

    if isinstance(logger_or_handler, logging.Logger):
        # 添加过滤器到 logger 的所有 handler
        for handler in logger_or_handler.handlers:
            # 避免重复添加
            if not any(isinstance(f, SensitiveDataFilter) for f in handler.filters):
                handler.addFilter(sensitive_filter)
        return logger_or_handler
    else:
        # 直接添加过滤器到单个 handler
        handler = logger_or_handler
        # 避免重复添加
        if not any(isinstance(f, SensitiveDataFilter) for f in handler.filters):
            handler.addFilter(sensitive_filter)
        return handler


__all__ = [
    "SensitiveDataFilter",
    "sanitize_message",
    "setup_sensitive_filter"
]
