#!/usr/bin/env python3
"""
项目常量配置模块

统一管理所有魔法数字和字符串常量
"""


class APIConfig:
    """API 相关常量"""
    DEFAULT_DOMAIN = 1
    DEFAULT_TIMEOUT = 120
    MAX_RETRIES = 3
    BASE_RETRY_DELAY = 2.0
    MAX_RETRY_DELAY = 15.0
    BASE_URL = "https://standardapi.sorftime.com/api"
    CATEGORY_REQUEST_PATH = "CategoryRequest"
    PRODUCT_REQUEST_PATH = "ProductRequest"


class BSRConfig:
    """BSR 数据相关常量"""
    MAX_RANK = 100
    EXPECTED_RECORDS_PER_CATEGORY = 100
    TREND_ENABLED = 1


class DatabaseConfig:
    """数据库相关常量"""
    DEFAULT_PORT = 33060
    DEFAULT_MYSQL_PORT = 30930
    CONNECT_TIMEOUT = 30
    DEFAULT_USER = ""
    DEFAULT_DATABASE = ""
    DEFAULT_TABLE = ""


class BackfillConfig:
    """数据回填相关常量"""
    DEFAULT_MAX_WORKERS = 4
    DEFAULT_START_DATE = "2026-01-01"
    # 0=周一, 1=周二, ..., 4=周五, ..., 6=周日
    DEFAULT_TARGET_WEEKDAY = 4


class LogConfig:
    """日志相关常量"""
    LOG_MAX_LENGTH = 500
    PIPELINE_TIMEOUT_SECONDS = 300
    LOG_DIR_NAME = "logs"
    LOG_FILE_NAME = "bsr_sync.log"
    LOG_BACKUP_COUNT = 30


class ValidationConfig:
    """数据验证相关常量"""
    ASIN_PATTERN = r'^B[0-9A-Z]{9}$'
    MIN_ONLINE_DAYS = 0
    MAX_ONLINE_DAYS = 36500  # 约100年


__all__ = [
    "APIConfig",
    "BSRConfig",
    "DatabaseConfig",
    "BackfillConfig",
    "LogConfig",
    "ValidationConfig"
]
