#!/usr/bin/env python3
"""
Sorftime CategoryRequest API 调用脚本

使用统一的 BaseAPIClient，消除全局状态污染
"""

import json
import argparse
import sys
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional

SCRIPTS_DIR = Path(__file__).resolve().parents[3]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 统一路径初始化
from utils.path_init import init_path
init_path()

from utils.api_base import BaseAPIClient, APIError, FETCH_EXCEPTIONS
from utils.date_utils import validate_date
from utils.base_config import APIConfig, ConfigError
from utils.common import load_env_safe
from utils.retry import retry
from utils.constants import APIConfig as APIConstants


# 加载 .env 配置
load_env_safe()


class CategoryAPIClient(BaseAPIClient):
    """类目 BSR API 客户端"""

    def __init__(self, config: Optional[APIConfig] = None):
        super().__init__(APIConstants.CATEGORY_REQUEST_PATH, config)

    def _parse_data(self, data: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        """
        解析 CategoryRequest 响应数据

        Args:
            data: API 响应数据

        Returns:
            Products 列表
        """
        return data.get("Data", {}).get("Products", [])

    def fetch_bsr(self, date: str, node_id: str, timeout: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        调用 CategoryRequest API，获取指定日期的 Top 100 BSR 产品列表

        Args:
            date: 格式 YYYY-MM-DD
            node_id: 类目 NodeId
            timeout: 请求超时时间（秒），None 则使用配置中的值

        Returns:
            API 返回的 Products 列表（原始字段）

        Raises:
            requests.RequestException: 请求失败
            json.JSONDecodeError: JSON 解析失败
            APIError: API 返回非零 code
        """
        payload = {"NodeId": node_id, "QueryDate": date, "QueryDays": 7}
        return self._post(payload, timeout=timeout)


# 线程局部存储 - 避免全局状态污染
_local = threading.local()


def get_client(config: Optional[APIConfig] = None) -> CategoryAPIClient:
    """
    获取或创建 CategoryAPIClient 实例

    Args:
        config: 如果提供了配置，总是创建新实例；
                否则返回线程本地缓存的实例（如果存在）

    Returns:
        CategoryAPIClient 实例
    """
    if config is not None:
        return CategoryAPIClient(config)
    if not hasattr(_local, 'client'):
        _local.client = CategoryAPIClient(None)
    return _local.client


def _reset_client() -> None:
    """
    重置 client 缓存（用于测试）
    """
    if hasattr(_local, 'client'):
        delattr(_local, 'client')


# 保持向后兼容的函数签名
@retry(
    exceptions=FETCH_EXCEPTIONS,
    max_retries=APIConstants.MAX_RETRIES,
    base_delay=APIConstants.BASE_RETRY_DELAY,
    max_delay=APIConstants.MAX_RETRY_DELAY
)
def fetch_bsr(date: str, node_id: str, timeout: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    调用 CategoryRequest API，获取指定日期的 Top 100 BSR 产品列表

    保持向后兼容的函数接口

    Args:
        date: 格式 YYYY-MM-DD
        node_id: 类目 NodeId
        timeout: 请求超时时间（秒），None 则使用配置中的值

    Returns:
        API 返回的 Products 列表（原始字段）

    Raises:
        requests.RequestException: 请求失败
        json.JSONDecodeError: JSON 解析失败
        APIError: API 返回非零 code
    """
    client = get_client()
    return client.fetch_bsr(date, node_id, timeout)


# 保持向后兼容的别名
def get_api_config() -> APIConfig:
    """
    获取 API 配置（向后兼容）

    Returns:
        APIConfig 实例
    """
    return get_client().get_config()


def _reset_api_config() -> None:
    """
    重置配置缓存（向后兼容，用于测试）
    """
    _reset_client()


def main():
    parser = argparse.ArgumentParser(description="拉取 Sorftime 类目 BSR Top100 数据")
    parser.add_argument("--date", "-d", required=True,
                        help="查询日期，格式 YYYY-MM-DD")
    parser.add_argument("--node-id", "-n", default="11139610011",
                        help="类目 NodeId（默认 11139610011 Tripods）")
    parser.add_argument("--output", "-o",
                        help="输出 JSON 文件路径（可选，默认打印到 stdout）")
    parser.add_argument("--timeout", type=int, default=None,
                        help="请求超时时间（秒），默认使用配置值")
    args = parser.parse_args()

    date = args.date.strip()
    node_id = args.node_id.strip()

    # 验证日期格式
    if not date:
        print("Error: date 不能为空", file=sys.stderr)
        sys.exit(1)
    if not validate_date(date):
        print(f"Error: date 格式无效，期望 YYYY-MM-DD，实际: {date}", file=sys.stderr)
        sys.exit(1)

    if not node_id:
        print("Error: node-id 不能为空", file=sys.stderr)
        sys.exit(1)

    print(f"[fetch_bsr] 日期={date}, node_id={node_id} ...", file=sys.stderr, end=" ", flush=True)
    try:
        products = fetch_bsr(date, node_id, timeout=args.timeout)
    except Exception as e:
        print(f"\n[ERROR] 拉取失败: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"获取到 {len(products)} 条产品", file=sys.stderr)

    # API 返回空数组时告警并退出
    if len(products) == 0:
        print(f"[ERROR] API 返回空列表，日期={date}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False)
        print(f"[fetch_bsr] 数据已写入: {args.output}", file=sys.stderr)
    else:
        # 打印到 stdout（供管道使用）
        print(json.dumps(products, ensure_ascii=False))


__all__ = [
    "fetch_bsr",
    "APIError",
    "get_api_config",
    "_reset_api_config",
    "CategoryAPIClient",
    "get_client",
]

if __name__ == "__main__":
    main()
