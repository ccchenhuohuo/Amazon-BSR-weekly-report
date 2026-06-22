#!/usr/bin/env python3
"""
Sorftime ProductRequest API 调用脚本

使用统一的 BaseAPIClient，消除全局状态污染
"""

# 首先设置正确的导入路径 - 在任何其他导入之前
import sys
from pathlib import Path

script_path = Path(__file__).resolve()
# 向上查找直到找到 scripts/utils 目录
project_root = None
for parent in [script_path] + list(script_path.parents):
    if (parent / 'scripts' / 'utils').exists():
        project_root = parent
        break
    if (parent / '.env.example').exists():
        project_root = parent
        break

if project_root:
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    scripts_dir = project_root / 'scripts'
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

# 现在导入其他模块
import json
import argparse
import os
import threading
from typing import List, Dict, Any, Optional

# 统一路径初始化
from utils.path_init import init_path
init_path()

from utils.api_base import BaseAPIClient, APIError, FETCH_EXCEPTIONS
from utils.base_config import APIConfig, ConfigError
from utils.common import load_env_safe
from utils.retry import retry
from utils.constants import APIConfig as APIConstants, BSRConfig


# 加载 .env 配置
load_env_safe()


class ProductAPIClient(BaseAPIClient):
    """产品详情 API 客户端"""

    def __init__(self, config: Optional[APIConfig] = None):
        super().__init__(APIConstants.PRODUCT_REQUEST_PATH, config)

    def _parse_data(self, data: Dict[str, Any], **kwargs) -> Optional[Dict[str, Any]]:
        """
        解析 ProductRequest 响应数据

        Args:
            data: API 响应数据
            **kwargs: 需要包含 'asin' 关键字参数

        Returns:
            Product 数据字典
        """
        product_data = data.get("Data") or data.get("data")
        asin = kwargs.get("asin", "")
        if product_data:
            product_data["ASIN"] = asin  # 确保返回的数据包含 ASIN 字段
        return product_data

    def fetch_single_product(self, asin: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """
        调用 ProductRequest API，获取单个 ASIN 的产品详情数据

        Args:
            asin: ASIN
            timeout: 请求超时时间（秒），None 则使用配置中的值

        Returns:
            API 返回的产品数据（原始字段）

        Raises:
            requests.RequestException: 请求失败
            json.JSONDecodeError: JSON 解析失败
            APIError: API 返回非零 code
        """
        payload = {"ASIN": asin, "Trend": BSRConfig.TREND_ENABLED}
        return self._post(payload, timeout=timeout, asin=asin)


# 线程局部存储 - 避免全局状态污染
_local = threading.local()


def get_client(config: Optional[APIConfig] = None) -> ProductAPIClient:
    """
    获取或创建 ProductAPIClient 实例

    Args:
        config: 如果提供了配置，总是创建新实例；
                否则返回线程本地缓存的实例（如果存在）

    Returns:
        ProductAPIClient 实例
    """
    if config is not None:
        return ProductAPIClient(config)
    if not hasattr(_local, 'client'):
        _local.client = ProductAPIClient(None)
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
def fetch_single_product(asin: str, timeout: Optional[int] = None) -> Dict[str, Any]:
    """
    调用 ProductRequest API，获取单个 ASIN 的产品详情数据

    保持向后兼容的函数接口

    Args:
        asin: ASIN
        timeout: 请求超时时间（秒），None 则使用配置中的值

    Returns:
        API 返回的产品数据（原始字段）

    Raises:
        requests.RequestException: 请求失败
        json.JSONDecodeError: JSON 解析失败
        APIError: API 返回非零 code
    """
    client = get_client()
    return client.fetch_single_product(asin, timeout)


def fetch_products(asins: List[str], timeout: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    调用 ProductRequest API，获取多个 ASIN 的产品详情数据

    Args:
        asins: ASIN 列表
        timeout: 请求超时时间（秒），None 则使用配置中的值

    Returns:
        API 返回的产品数据列表（原始字段）
    """
    products = []
    for asin in asins:
        print(f"正在拉取 ASIN: {asin} ...", file=sys.stderr)
        try:
            product = fetch_single_product(asin, timeout)
            if product:
                products.append(product)
                print(f"成功拉取 ASIN: {asin}", file=sys.stderr)
        except Exception as e:
            print(f"未能拉取 ASIN: {asin}, error: {e}", file=sys.stderr)
    return products


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
    parser = argparse.ArgumentParser(description="拉取 Sorftime ProductRequest 产品详情数据")
    parser.add_argument("--asins", "-a", required=True,
                        help="ASIN 列表，逗号分隔")
    parser.add_argument("--output", "-o",
                        help="输出 JSON 文件路径（可选，默认打印到 stdout）")
    parser.add_argument("--timeout", type=int, default=None,
                        help="请求超时时间（秒），默认使用配置值")
    args = parser.parse_args()

    asins = args.asins.strip().split(",")
    asins = [asin.strip() for asin in asins if asin.strip()]
    if not asins:
        print("Error: asins 不能为空", file=sys.stderr)
        sys.exit(1)

    print(f"[fetch_product] 正在拉取 {len(asins)} 个 ASIN 的数据: {', '.join(asins)} ...", file=sys.stderr)
    try:
        products = fetch_products(asins, timeout=args.timeout)
    except Exception as e:
        print(f"\n[ERROR] 拉取失败: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"共成功拉取 {len(products)} 条产品数据", file=sys.stderr)

    if len(products) == 0:
        print(f"[WARNING] 未拉取到任何产品数据", file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(products, f, ensure_ascii=False)
        print(f"[fetch_product] 数据已写入: {args.output}", file=sys.stderr)
    else:
        # 打印到 stdout（供管道使用）
        print(json.dumps(products, ensure_ascii=False))


__all__ = [
    "fetch_single_product",
    "fetch_products",
    "APIError",
    "get_api_config",
    "_reset_api_config",
    "ProductAPIClient",
    "get_client",
]

if __name__ == "__main__":
    main()