#!/usr/bin/env python3
"""
重试机制模块
提供指数退避重试装饰器和同步重试函数
"""

import time
import functools
import logging
import random  # 提前导入，避免在装饰器内部重复导入
import json
from typing import Callable, Type, Tuple, Optional, Any


logger = logging.getLogger(__name__)


def retry(
    exceptions: Tuple[Type[Exception], ...],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    logger_instance: Optional[logging.Logger] = None,
    on_retry: Optional[Callable[[int, Exception], None]] = None
):
    """
    重试装饰器，使用指数退避算法

    Args:
        exceptions: 需要重试的异常类型元组
        max_retries: 最大重试次数，默认 3
        base_delay: 基础延迟时间（秒），默认 1.0
        max_delay: 最大延迟时间（秒），默认 10.0
        logger_instance: 日志记录器实例，可选
        on_retry: 重试时的回调函数，参数为 (attempt, exception)

    Returns:
        装饰器函数

    Example:
        @retry((ConnectionError, TimeoutError), max_retries=3)
        def fetch_data():
            ...
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            log = logger_instance or logger
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e
                    if attempt >= max_retries:
                        log.error(f"[retry] 已达到最大重试次数 {max_retries}，放弃: {e}")
                        raise

                    # 计算延迟：指数退避，带抖动
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    # 添加 ±50% 的抖动避免雪崩
                    jitter = random.uniform(0.5, 1.5)
                    delay *= jitter

                    log.warning(
                        f"[retry] 第 {attempt + 1}/{max_retries} 次尝试失败: {e}, "
                        f"{delay:.2f}s 后重试..."
                    )

                    if on_retry:
                        on_retry(attempt, e)

                    time.sleep(delay)

            raise last_exception  # should never reach here

        return wrapper
    return decorator


def retry_sync(
    func: Callable,
    exceptions: Tuple[Type[Exception], ...],
    *args,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    logger_instance: Optional[logging.Logger] = None,
    **kwargs
) -> Any:
    """
    同步函数重试（非装饰器方式）

    Args:
        func: 要执行的函数
        exceptions: 需要重试的异常类型元组
        *args: 函数位置参数
        max_retries: 最大重试次数，默认 3
        base_delay: 基础延迟时间（秒），默认 1.0
        max_delay: 最大延迟时间（秒），默认 10.0
        logger_instance: 日志记录器实例，可选
        **kwargs: 函数关键字参数

    Returns:
        函数执行结果

    Example:
        result = retry_sync(fetch_data, (ConnectionError,), url="...")
    """
    @retry(
        exceptions=exceptions,
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        logger_instance=logger_instance
    )
    def _wrapped():
        return func(*args, **kwargs)

    return _wrapped()


# requests 库相关异常（如果 requests 可用）
try:
    import requests
    REQUESTS_EXCEPTIONS = (
        requests.RequestException,
        TimeoutError,
        ConnectionError,
        json.JSONDecodeError
    )
except ImportError:
    REQUESTS_EXCEPTIONS = (
        TimeoutError,
        ConnectionError,
        json.JSONDecodeError
    )

# 数据库异常
try:
    import pymysql
    DATABASE_EXCEPTIONS = (
        TimeoutError,
        ConnectionError,
        pymysql.Error
    )
except ImportError:
    DATABASE_EXCEPTIONS = (
        TimeoutError,
        ConnectionError
    )


__all__ = [
    'retry',
    'retry_sync',
    'REQUESTS_EXCEPTIONS',
    'DATABASE_EXCEPTIONS'
]
