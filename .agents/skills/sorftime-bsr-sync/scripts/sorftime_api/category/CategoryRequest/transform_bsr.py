#!/usr/bin/env python3
"""
Sorftime BSR API 原始字段 → Doris BSR 目标表字段映射脚本
"""

import json
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

SCRIPTS_DIR = Path(__file__).resolve().parents[3]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 统一路径初始化
from utils.path_init import init_path
init_path()

from utils.date_utils import validate_date
from utils.common import safe_float, safe_int, read_json_input, write_json_output
from utils.constants import BSRConfig


def transform_product(p: Dict[str, Any], bsr_date: str, max_rank: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    将单个 API 产品字段映射为 Doris 表字段

    Args:
        p: API 返回的单个产品字典
        bsr_date: BSR 日期（来自 QueryDate）
        max_rank: 最大 BSR 排名，超过的过滤掉，None 表示不过滤

    Returns:
        映射后的字典；若 bsr_rank > max_rank 则返回 None（表示过滤）
    """
    bsr_cats: List[Any] = p.get("BsrCategory") or []
    if not bsr_cats:
        return None

    # BsrCategory[0] = [类目名称, NodeId, 细分类目排名]
    cat_info: List[Any] = bsr_cats[0]
    if len(cat_info) < 3:
        return None

    bsr_category_name: Any = cat_info[0] or ""
    bsr_category_node: Any = cat_info[1] or ""
    bsr_rank: Any = cat_info[2]

    # 过滤：仅保留 bsr_rank <= max_rank 的记录
    bsr_rank_int = safe_int(bsr_rank)
    if max_rank is not None and (bsr_rank is None or bsr_rank_int > max_rank):
        return None

    return {
        # 基础字段 - 确保 NOT NULL 字段有默认值
        "asin": p.get("Asin") or "",
        "bsr_date": bsr_date,
        "title": p.get("Title") or "",
        "photo": json.dumps(p.get("Photo") or []),
        "ebc_photo": json.dumps(p.get("EbcPhoto") or []),
        "store_name": p.get("StoreName") or "",
        # 销量字段
        "listing_sales_volume_of_daily": safe_int(p.get("ListingSalesVolumeOfDaily")),
        "listing_sales_of_daily": safe_int(p.get("ListingSalesOfDaily")),
        "listing_sales_volume_of_month": safe_int(p.get("ListingSalesVolumeOfMonth")),
        "listing_sales_of_month": safe_int(p.get("ListingSalesOfMonth")),
        # ASIN 信息
        "parent_asin": p.get("ParentAsin") or "",
        "price": safe_int(p.get("Price")),
        "list_price": safe_int(p.get("ListPrice")),
        "product_type": p.get("ProductType") or "",
        "sales_price": safe_int(p.get("SalesPrice")),
        "brand": p.get("Brand") or "",
        # 利润
        "profit": safe_int(p.get("Profit")),
        "profit_rate": safe_float(p.get("ProfitRate")),
        # 上架信息
        "online_date": p.get("OnlineDate") or "",
        "online_days": safe_int(p.get("OnlineDays")),
        "ratings_count": safe_int(p.get("RatingsCount")),
        # 类目
        "category": json.dumps(p.get("Category") or []),
        "bsr_category_name": bsr_category_name,
        "bsr_category_node": bsr_category_node,
        # 排名（核心）
        "bsr_rank": safe_int(bsr_rank),
        "rank": safe_int(p.get("Rank")),
        # 评分
        "ratings": safe_float(p.get("Ratings")),
        # 尺寸
        "size": json.dumps(p.get("Size") or []),
        # 入库时间
        "insert_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def main():
    parser = argparse.ArgumentParser(description="将 API 原始字段映射为 Doris BSR 表格式")
    parser.add_argument("--date", "-d", required=True,
                        help="BSR 日期，格式 YYYY-MM-DD（对应 bsr_date 字段）")
    parser.add_argument("--input", "-i",
                        help="输入 JSON 文件路径（若不指定则从 stdin 读取）")
    parser.add_argument("--output", "-o",
                        help="输出 JSON 文件路径")
    parser.add_argument("--no-filter", action="store_true",
                        help=f"不过滤 bsr_rank > {BSRConfig.MAX_RANK} 的记录（默认会过滤）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印统计信息，不写文件")
    args = parser.parse_args()

    # 验证日期格式
    bsr_date = args.date.strip()
    if not validate_date(bsr_date):
        print(f"[ERROR] date 格式无效，期望 YYYY-MM-DD，实际: {bsr_date}", file=sys.stderr)
        sys.exit(1)

    # 读取输入
    products = read_json_input(args.input, "transform_bsr")

    # 验证 products 类型
    if not isinstance(products, list):
        print(f"[ERROR] 输入数据格式错误，期望列表，实际: {type(products)}", file=sys.stderr)
        sys.exit(1)

    # 输入为空时告警但不退出
    if len(products) == 0:
        print(f"[WARNING] 输入产品列表为空，日期={bsr_date}", file=sys.stderr)

    max_rank = None if args.no_filter else BSRConfig.MAX_RANK

    # 转换
    rows = []
    skipped = 0
    for p in products:
        row = transform_product(p, bsr_date, max_rank)
        if row is None:
            if max_rank is not None:
                skipped += 1
            continue
        rows.append(row)

    # 按 bsr_rank 排序
    rows.sort(key=lambda x: safe_int(x["bsr_rank"]))

    # 校验逻辑：生产要求必须刚好 Top100，否则终止，避免写入不完整快照。
    if max_rank is not None and len(rows) != BSRConfig.MAX_RANK:
        msg = f"[ERROR] 期望 {BSRConfig.MAX_RANK} 条记录，实际 {len(rows)} 条，日期={bsr_date}"
        print(msg, file=sys.stderr)
        sys.exit(1)

    # 打印统计信息
    print(f"[transform_bsr] 日期={bsr_date}, 总记录={len(products)}, "
          f"保留={len(rows)}, 过滤={skipped}bsr_rank>{max_rank})",
          file=sys.stderr)

    # dry-run 模式
    if args.dry_run:
        print(f"[transform_bsr] [dry-run] 跳过文件写入", file=sys.stderr)
        sys.exit(0)

    # 输出
    write_json_output(rows, args.output, "transform_bsr")


__all__ = [
    "transform_product",
]

if __name__ == "__main__":
    main()
