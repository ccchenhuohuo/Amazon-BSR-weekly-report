#!/usr/bin/env python3
"""
Sorftime ProductRequest 数据转换脚本
将 API 返回的数据转换为符合 Doris 数据库表结构的 JSON 格式（供 Stream Load 使用）

功能:
    1. 接收 fetch_product.py 的 JSON 输出（或指定 JSON 文件）
    2. 字段映射：API 字段 → Doris 表字段
    3. 输出 Doris Stream Load 可用的 JSON 文件

用法:
    # 管道方式（从 fetch_product.py 接收）
    python3 fetch_product.py --asins B0XXX,B0YYY | python3 transform_product.py --date 2026-04-19 -o /tmp/product_doris.json

    # 直接文件方式
    python3 transform_product.py --input /tmp/products_raw.json --date 2026-04-19 -o /tmp/product_doris.json
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
from datetime import datetime
from typing import Optional, List, Dict, Any

# 统一路径初始化
from utils.path_init import init_path
init_path()

from utils.date_utils import validate_date
from utils.common import safe_float, safe_int, read_json_input, write_json_output

# ============ 字段映射 ============
# API字段名 -> 数据库字段名（snake_case）
FIELD_MAPPING = {
    "ListingSalesVolumeOfDailyTrend": "listing_sales_volume_daily_trend",
    "ListingSalesOfDailyTrend": "listing_sales_daily_trend",
    "ListingSalesVolumeOfMonthTrend": "listing_sales_volume_month_trend",
    "ListingSalesOfMonthTrend": "listing_sales_month_trend",
    "RankTrend": "rank_trend",
    "BsrRankTrend": "bsr_rank_trend",
    "PriceTrend": "price_trend",
    "ListPriceTrend": "list_price_trend",
    "AsinSalesCount": "asin_sales_count",
    "OneStarRatings": "one_star_ratings",
    "TwoStarRatings": "two_star_ratings",
    "ThreeStarRatings": "three_star_ratings",
    "FourStarRatings": "four_star_ratings",
    "FiveStarRatings": "five_star_ratings",
    "ProductInfo": "product_info",
    "Property": "property",
    "Attribute": "attribute",
    # ProductRequest Stream Load columns do not include Photo. Weekly report
    # image URLs come from the configured CategoryRequest target table photo field instead.
    "Description": "description"
}


def transform_product(api_product: Dict[str, Any], query_date: str) -> Optional[Dict[str, Any]]:
    """
    将单个 API 产品字段映射为 Doris 表字段

    Args:
        api_product: API 返回的单个产品字典
        query_date: 查询日期（YYYY-MM-DD）

    Returns:
        映射后的字典；若无 ASIN 则返回 None
    """
    asin = api_product.get("Asin") or api_product.get("ASIN")
    if not asin:
        return None

    processed = {
        "asin": asin,
        "query_date": query_date,
        "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    for api_field, db_field in FIELD_MAPPING.items():
        api_value = api_product.get(api_field)

        if api_value is not None:
            if isinstance(api_value, (list, dict)):
                processed[db_field] = json.dumps(api_value, ensure_ascii=False)
            elif isinstance(api_value, str):
                stripped = api_value.strip()
                if len(stripped) > 0:
                    processed[db_field] = stripped
                else:
                    # 空白字符串统一处理为 None
                    processed[db_field] = None
            else:
                processed[db_field] = api_value
        else:
            processed[db_field] = None

    return processed


def main():
    parser = argparse.ArgumentParser(description="将 API 原始字段映射为 Doris ProductRequest 表格式")
    parser.add_argument("--date", "-d", dest="query_date", required=True,
                        help="查询日期，格式 YYYY-MM-DD（对应 query_date 字段）")
    parser.add_argument("--input", "-i",
                        help="输入 JSON 文件路径（若不指定则从 stdin 读取）")
    parser.add_argument("--output", "-o",
                        help="输出 JSON 文件路径（若不指定则输出到 stdout）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印统计信息，不写文件")
    args = parser.parse_args()

    # 验证日期格式
    query_date = args.query_date.strip()
    if not validate_date(query_date):
        print(f"[ERROR] date 格式无效，期望 YYYY-MM-DD，实际: {query_date}", file=sys.stderr)
        sys.exit(1)

    # 读取输入
    products = read_json_input(args.input, "transform_product")

    # 验证 products 类型
    if not isinstance(products, list):
        print(f"[ERROR] 输入数据格式错误，期望列表，实际: {type(products)}", file=sys.stderr)
        sys.exit(1)

    if len(products) == 0:
        print(f"[WARNING] 输入产品列表为空，query_date={query_date}", file=sys.stderr)

    # 转换
    rows = []
    skipped = 0
    for p in products:
        row = transform_product(p, query_date)
        if row is None:
            skipped += 1
            continue
        rows.append(row)

    # 打印统计信息
    print(f"[transform_product] query_date={query_date}, 总记录={len(products)}, "
          f"保留={len(rows)}, 跳过={skipped}(无ASIN)",
          file=sys.stderr)

    # dry-run 模式：只打印统计，不写文件
    if args.dry_run:
        print(f"[transform_product] [dry-run] 跳过文件写入", file=sys.stderr)
        sys.exit(0)

    # 输出
    write_json_output(rows, args.output, "transform_product")


__all__ = [
    'transform_product',
    'FIELD_MAPPING'
]

if __name__ == "__main__":
    main()
