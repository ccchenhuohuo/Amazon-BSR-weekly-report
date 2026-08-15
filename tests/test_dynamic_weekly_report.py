import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / ".agents" / "skills" / "sorftime-weekly-report" / "scripts"
REPORT_SCRIPT = SCRIPTS_DIR / "generate_weekly_report.py"


def load_report_module():
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec = importlib.util.spec_from_file_location("dynamic_generate_weekly_report", REPORT_SCRIPT)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(SCRIPTS_DIR))


def product(index: int) -> dict[str, object]:
    return {
        "bsr_rank": index,
        "brand": f"Brand{index}",
        "title": f"Product {index}",
        "asin": f"B0TEST{index:04d}",
        "price": 10 + index,
        "ratings": 4.5,
        "monthly_sales": 1000 - index,
        "online_days": 100 + index,
        "last_rank": index + 1,
        "rank_change": 1,
        "this_rank": index,
        "is_new_product": "是",
        "photo": '["https://example.com/product.jpg"]',
    }


def category_data() -> dict[str, object]:
    rows = [product(index) for index in range(1, 11)]
    return {
        "top10": rows,
        "rising": rows[:1],
        "falling": rows[1:2],
        "new": rows[2:3],
        "low": rows[3:4],
        "ratings": [
            {"rating_range": "4.5-5.0", "product_count": 40, "percentage": 40, "avg_rank": 20, "total_monthly_sales": 4000},
            {"rating_range": "4.0-4.4", "product_count": 35, "percentage": 35, "avg_rank": 50, "total_monthly_sales": 3000},
            {"rating_range": "<4.0", "product_count": 25, "percentage": 25, "avg_rank": 80, "total_monthly_sales": 2000},
        ],
    }


def ulanzi_data() -> dict[str, object]:
    row = product(1)
    products = [{
        "asin": row["asin"],
        "title": row["title"],
        "last_rank": 2,
        "this_rank": 1,
        "rank_change": 1,
        "price": row["price"],
        "ratings": row["ratings"],
        "monthly_sales": row["monthly_sales"],
        "online_days": row["online_days"],
        "photo": row["photo"],
    }]
    brands = [
        {
            "brand": "ULANZI" if index == 0 else f"Brand{index}",
            "sku_count": 2,
            "total_monthly_sales": 1000,
            "sales_per_sku": 500,
            "avg_price": 50,
        }
        for index in range(15)
    ]
    internal = [{
        "asin": row["asin"],
        "title": row["title"],
        "bsr_rank": 1,
        "online_days": row["online_days"],
        "monthly_sales": row["monthly_sales"],
        "photo": row["photo"],
        "efficiency_rating": "高效",
    }]
    return {
        "products": products,
        "brands": brands,
        "internal": internal,
        "threshold": 100,
        "avg_sales": 500,
        "brand_stats": {"brand_count": 10, "total_sales": 10000},
    }


def overview(module, group: str, categories: list[dict[str, str]]) -> str:
    headers = ["指标"]
    for category in categories:
        headers.extend([f"{category['name']} (上周)", f"{category['name']} (本周)"])
    rows = []
    for rank in range(1, 4):
        values = [f"**TOP{rank}产品**"]
        for index, _category in enumerate(categories, 1):
            link = module.asin(f"B0OV{index}{rank}TEST")
            values.extend([f"Brand ({link})", f"Brand ({link})"])
        rows.append(values)
    rows.append(["**独立品牌数**", *["10" for _ in range(len(categories) * 2)]])
    return "\n".join([
        f"# {group}周趋势监测报告",
        "",
        "## 一、数据概览",
        "",
        "### 1.1 核心指标对比",
        "",
        module.table(headers, rows),
        "",
        "### 1.2 核心结论",
        "",
        "本周保持观察。",
        "",
    ])


@pytest.mark.parametrize("category_count", [1, 2, 3, 4])
def test_render_and_validate_supports_one_to_four_leaf_categories(tmp_path, category_count):
    module = load_report_module()
    categories = [
        {"name": f"Category{index}", "node": str(index), "path": f"Root > Category{index}"}
        for index in range(1, category_count + 1)
    ]
    data_by_node = {category["node"]: category_data() for category in categories}
    ulanzi_by_node = {category["node"]: ulanzi_data() for category in categories}
    ulanzi_chapter = category_count + 2
    summary_chapter = ulanzi_chapter + 1

    report = "\n".join([
        overview(module, "测试类", categories),
        *[
            module.render_category(
                module.chinese_number(index + 2),
                str(index + 2),
                category,
                data_by_node[category["node"]],
                "2026-07-29",
            )
            for index, category in enumerate(categories)
        ],
        module.render_ulanzi(categories, ulanzi_by_node, "2026-07-22", "2026-07-29", ulanzi_chapter),
        module.render_summary(categories, data_by_node, summary_chapter),
    ])
    path = tmp_path / f"report-{category_count}.md"
    path.write_text(report, encoding="utf-8")

    module.validate(path, "测试类", [category["name"] for category in categories])
