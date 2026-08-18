#!/usr/bin/env python3
"""Generate Sorftime weekly trend reports through the default production path.

The conversational subagent workflow remains supported only when the user
explicitly asks for parallel/manual agent analysis.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from time import strftime
from typing import Any

import pymysql

from category_config import load_category_mapping
from validate_report import validate


SKILL_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(__file__).resolve().parents[4]
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"
DEFAULT_DORIS_MYSQL_PORT = "30930"
DEFAULT_IMAGE_WIDTH = 150
IMAGE_WIDTH = DEFAULT_IMAGE_WIDTH
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def die(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def load_dotenv(path: Path | None = None) -> None:
    paths = [path] if path is not None else [PROJECT_ROOT / ".env", SKILL_DIR / ".env"]
    for env_path in paths:
        if env_path is None or not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def default_report_dir() -> Path:
    configured = os.environ.get("SORFTIME_REPORT_OUTPUT_DIR")
    if configured:
        return Path(configured).expanduser()
    return DEFAULT_REPORT_DIR


def read(path: Path) -> str:
    if not path.exists():
        die(f"missing file or broken symlink: {path}")
    return path.read_text(encoding="utf-8")


def parse_mapping() -> dict[str, list[dict[str, str]]]:
    """Backward-compatible entrypoint for tests and callers."""
    return load_category_mapping(SKILL_DIR / "references/category-mapping.md")


CHINESE_NUMBERS = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七"}


def chinese_number(value: int) -> str:
    try:
        return CHINESE_NUMBERS[value]
    except KeyError as exc:
        raise ValueError(f"unsupported chapter number: {value}") from exc


def parse_date(value: str, today: datetime | None = None) -> str:
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    today = today or datetime.now()
    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    match = re.fullmatch(r"(上上|上|本)?周([一二三四五六日天])", value)
    if not match:
        die(f"cannot parse date: {value}")
    prefix, weekday_text = match.groups()
    week_offset = {"本": 0, None: 0, "上": -1, "上上": -2}[prefix]
    monday = today - timedelta(days=today.weekday())
    target = monday + timedelta(weeks=week_offset, days=weekday_map[weekday_text])
    return target.strftime("%Y-%m-%d")


def validate_doris_identifier(value: str | None, env_name: str) -> str:
    value = (value or "").strip()
    if not value:
        die(f"{env_name} is required. Set it in the shell or in a local ignored .env file.")
    if not IDENTIFIER_RE.fullmatch(value):
        die(f"{env_name} must be a simple Doris identifier, got: {value!r}")
    return value


def doris_table_ref() -> str:
    database = validate_doris_identifier(os.environ.get("DORIS_DATABASE"), "DORIS_DATABASE")
    table_name = validate_doris_identifier(os.environ.get("DORIS_TABLE"), "DORIS_TABLE")
    return f"{database}.{table_name}"


def data_source_label() -> str:
    return "Doris BSR configured table"


def db_connect():
    load_dotenv()
    host = os.environ.get("DORIS_HOST")
    user = os.environ.get("DORIS_USER")
    password = os.environ.get("DORIS_PASSWORD")
    database = os.environ.get("DORIS_DATABASE")
    table_name = os.environ.get("DORIS_TABLE")
    if not host or not user or not password or not database or not table_name:
        die(
            "DORIS_HOST, DORIS_USER, DORIS_PASSWORD, DORIS_DATABASE, and DORIS_TABLE are required. "
            "Set them in the shell or in a local ignored .env file."
        )
    database = validate_doris_identifier(database, "DORIS_DATABASE")
    validate_doris_identifier(table_name, "DORIS_TABLE")
    return pymysql.connect(
        host=host,
        port=int(os.environ.get("DORIS_MYSQL_PORT", DEFAULT_DORIS_MYSQL_PORT)),
        user=user,
        password=password,
        database=database,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=20,
        read_timeout=180,
        write_timeout=180,
    )


def render_sql(path: Path, **values: str) -> str:
    sql = read(path)
    for key, value in values.items():
        sql = sql.replace("{" + key + "}", value)
    leftovers = re.findall(r"\{[^{}\n]+\}", sql)
    if leftovers:
        die(f"unreplaced SQL placeholders in {path}: {leftovers}")
    return sql


def query(conn, sql: str) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql)
        return list(cur.fetchall())


def qfile(conn, path: Path, **values: str) -> list[dict[str, Any]]:
    values.setdefault("table", doris_table_ref())
    return query(conn, render_sql(path, **values))


def check_env() -> dict[str, Any]:
    load_dotenv()
    env = {
        "DORIS_HOST": os.environ.get("DORIS_HOST"),
        "DORIS_MYSQL_PORT": os.environ.get("DORIS_MYSQL_PORT", DEFAULT_DORIS_MYSQL_PORT),
        "DORIS_USER": os.environ.get("DORIS_USER"),
        "DORIS_PASSWORD": os.environ.get("DORIS_PASSWORD"),
        "DORIS_DATABASE": os.environ.get("DORIS_DATABASE"),
        "DORIS_TABLE": os.environ.get("DORIS_TABLE"),
    }
    missing = [key for key in ["DORIS_HOST", "DORIS_USER", "DORIS_PASSWORD", "DORIS_DATABASE", "DORIS_TABLE"] if not env[key]]
    if not missing:
        validate_doris_identifier(env["DORIS_DATABASE"], "DORIS_DATABASE")
        validate_doris_identifier(env["DORIS_TABLE"], "DORIS_TABLE")
    summary = {
        "status": "ENV_OK" if not missing else "ENV_MISSING",
        "missing": missing,
        "resolved": {
            "DORIS_HOST": bool(env["DORIS_HOST"]),
            "DORIS_MYSQL_PORT": env["DORIS_MYSQL_PORT"],
            "DORIS_USER": bool(env["DORIS_USER"]),
            "DORIS_PASSWORD": bool(env["DORIS_PASSWORD"]),
            "DORIS_DATABASE": bool(env["DORIS_DATABASE"]),
            "DORIS_TABLE": bool(env["DORIS_TABLE"]),
        },
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if missing:
        die(
            "missing required Doris environment variables: "
            + ", ".join(missing)
            + ". DORIS_MYSQL_PORT defaults to 30930; host/user/password/database/table do not."
        )
    return summary


def money(value: Any) -> str:
    return "-" if value is None else f"${float(value):.2f}"


def integer(value: Any) -> str:
    return "-" if value is None else f"{int(float(value)):,}"


def cell(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value).replace("\n", " ").replace("|", "\\|")


def asin(asin_value: Any) -> str:
    return "-" if not asin_value else f"[{asin_value}](https://www.amazon.com/dp/{asin_value})"


def change(value: Any) -> str:
    if value is None:
        return "-"
    value = int(value)
    return f"+{value}" if value > 0 else str(value)


def photo(value: Any) -> str:
    if not value:
        return "-"
    try:
        parsed = json.loads(value)
        if parsed:
            return f'<img src="{parsed[0]}" width="{IMAGE_WIDTH}" />'
    except Exception:
        if isinstance(value, str) and value.startswith("http"):
            return f'<img src="{value}" width="{IMAGE_WIDTH}" />'
    return "-"


def table(headers: list[str], rows: list[list[str]]) -> str:
    output = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    output += ["| " + " | ".join(row) + " |" for row in rows]
    return "\n".join(output)


def product_rows(rows: list[dict[str, Any]], mode: str) -> list[list[str]]:
    out: list[list[str]] = []
    for row in rows:
        if mode in {"top10", "low"}:
            out.append([
                integer(row["bsr_rank"]),
                cell(row["brand"]),
                cell(row["title"]),
                asin(row["asin"]),
                money(row["price"]),
                cell(row["ratings"]),
                integer(row["monthly_sales"]),
                integer(row["online_days"]),
                integer(row["last_rank"]),
                change(row["rank_change"]),
                photo(row["photo"]),
            ])
        elif mode in {"rising", "falling"}:
            out.append([
                change(row["rank_change"]),
                cell(row["brand"]),
                cell(row["title"]),
                asin(row["asin"]),
                money(row["price"]),
                cell(row["ratings"]),
                integer(row["monthly_sales"]),
                integer(row["online_days"]),
                integer(row["this_rank"]),
                integer(row["last_rank"]),
                photo(row["photo"]),
            ])
        elif mode == "new":
            out.append([
                integer(row["this_rank"]),
                cell(row["brand"]),
                cell(row["title"]),
                asin(row["asin"]),
                money(row["price"]),
                cell(row["ratings"]),
                integer(row["monthly_sales"]),
                integer(row["online_days"]),
                cell(row["is_new_product"]),
                photo(row["photo"]),
            ])
    return out


def fetch_category(conn, category: dict[str, str], start_date: str, end_date: str) -> dict[str, Any]:
    base = SKILL_DIR / "agents/02-categories/queries"
    values = {"node_id": category["node"], "start_date": start_date, "end_date": end_date}
    data = {
        "top10": qfile(conn, base / "top10.sql", **values),
        "rising": qfile(conn, base / "rising.sql", **values),
        "falling": qfile(conn, base / "falling.sql", **values),
        "new": qfile(conn, base / "new-entries.sql", **values),
        "low": qfile(conn, base / "low-rating-high-sales.sql", **values),
        "ratings": qfile(conn, base / "rating-distribution.sql", **values),
    }
    if len(data["top10"]) != 10:
        die(f"{category['name']} TOP10 expected 10 rows, got {len(data['top10'])}")
    if not data["low"] or len(data["low"]) > 10:
        die(f"{category['name']} low-rating expected 1-10 rows, got {len(data['low'])}")
    if len(data["ratings"]) != 3:
        die(f"{category['name']} rating distribution expected 3 rows, got {len(data['ratings'])}")
    if len(data["rising"]) > 10 or len(data["falling"]) > 3:
        die(f"{category['name']} movement row limit exceeded")
    return data


def metric_for(metrics: list[dict[str, Any]], date: str) -> dict[str, Any]:
    for row in metrics:
        if str(row["bsr_date"]) == date:
            return row
    die(f"missing metric row for {date}")


def top_product(row: dict[str, Any]) -> str:
    return f"{cell(row['brand'])} ({asin(row['asin'])})"


def render_overview(
    conn,
    board_name: str,
    categories: list[dict[str, str]],
    data_by_node: dict[str, dict[str, Any]],
    start_date: str,
    end_date: str,
) -> str:
    base = SKILL_DIR / "agents/01-overview/queries"
    metrics: dict[str, list[dict[str, Any]]] = {}
    top: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for category in categories:
        metrics[category["node"]] = qfile(conn, base / "core-metrics.sql", node_id=category["node"], start_date=start_date, end_date=end_date)
        for date in [start_date, end_date]:
            top[(category["node"], date)] = qfile(conn, base / "top3.sql", node_id=category["node"], date=date)
            if len(top[(category["node"], date)]) != 3:
                die(f"{category['name']} {date} TOP3 expected 3 rows")

    header = read(SKILL_DIR / "agents/01-overview/templates/00-header.md")
    replacements = {
        "{板块名称}": board_name,
        "{报告日期}": end_date,
        "{上周日期}": start_date,
        "{本周日期}": end_date,
        "{类目映射说明}": "\n".join(
            f"> - **类目{chr(65 + index)} ({category['name']})**：{category['path']}（Node ID: {category['node']}）"
            for index, category in enumerate(categories)
        ),
        "{数据来源}": data_source_label(),
    }
    for key, value in replacements.items():
        header = header.replace(key, value)

    rows: list[list[str]] = []
    for idx in range(3):
        row = [f"**TOP{idx + 1}产品**"]
        for category in categories:
            row.extend([
                top_product(top[(category["node"], start_date)][idx]),
                top_product(top[(category["node"], end_date)][idx]),
            ])
        rows.append(row)

    metric_fields = [
        ("**独立品牌数**", "unique_brands", integer),
        ("**类目月销总量**", "total_monthly_sales", integer),
        ("**类目均价（按销量加权）**", "weighted_avg_price", money),
    ]
    for label, field, formatter in metric_fields:
        row = [label]
        for category in categories:
            start = metric_for(metrics[category["node"]], start_date)
            end = metric_for(metrics[category["node"]], end_date)
            row.extend([formatter(start[field]), formatter(end[field])])
        rows.append(row)

    def delta(start: dict[str, Any], end: dict[str, Any], field: str) -> float:
        return float(end[field]) - float(start[field])

    headers = ["指标"]
    for category in categories:
        headers.extend([f"{category['name']} ({start_date})", f"{category['name']} ({end_date})"])

    insights: list[str] = []
    for category in categories:
        start = metric_for(metrics[category["node"]], start_date)
        end = metric_for(metrics[category["node"]], end_date)
        category_data = data_by_node[category["node"]]
        insights.extend([
            f"**{category['name']}类目关键洞察**：",
            f"1. 月销总量较上周变化{delta(start, end, 'total_monthly_sales'):+,.0f}件，独立品牌数变化{delta(start, end, 'unique_brands'):+.0f}个。",
            f"2. 加权均价较上周变化{delta(start, end, 'weighted_avg_price'):+.2f}美元。",
            f"3. TOP10头部月销最高产品为{cell(category_data['top10'][0]['brand'])} {asin(category_data['top10'][0]['asin'])}，月销{integer(category_data['top10'][0]['monthly_sales'])}件。",
            "",
        ])

    return "\n".join([
        header.rstrip(),
        "",
        "## 一、数据概览",
        "",
        "### 1.1 核心指标对比",
        "",
        table(headers, rows),
        "",
        "### 1.2 核心结论",
        "",
        *insights,
    ])


def overview_counts(conn, categories: list[dict[str, str]], start_date: str, end_date: str) -> dict[str, Any]:
    base = SKILL_DIR / "agents/01-overview/queries"
    counts: dict[str, Any] = {}
    for category in categories:
        metrics = qfile(conn, base / "core-metrics.sql", node_id=category["node"], start_date=start_date, end_date=end_date)
        if len(metrics) != 2:
            die(f"{category['name']} core metrics expected 2 rows, got {len(metrics)}")
        counts[f"{category['name']}.core_metrics"] = len(metrics)
        for date in [start_date, end_date]:
            top3 = qfile(conn, base / "top3.sql", node_id=category["node"], date=date)
            if len(top3) != 3:
                die(f"{category['name']} {date} TOP3 expected 3 rows, got {len(top3)}")
            counts[f"{category['name']}.{date}.top3"] = len(top3)
    return counts


def render_category(chapter: str, section: str, category: dict[str, str], data: dict[str, Any], end_date: str) -> str:
    top10 = data["top10"]
    total_top10_sales = sum(int(row["monthly_sales"]) for row in top10)
    new_sales = sum(int(row["monthly_sales"]) for row in top10 if int(row["online_days"]) <= 180)
    avg_top10_price = sum(float(row["price"]) for row in top10) / len(top10)
    brands = sorted({cell(row["brand"]) for row in top10})
    rating_rows = [[cell(r["rating_range"]), integer(r["product_count"]), f"{float(r['percentage']):.1f}%", integer(r["avg_rank"]), integer(r["total_monthly_sales"])] for r in data["ratings"]]
    strongest = data["rising"][0] if data["rising"] else None
    weakest = data["falling"][0] if data["falling"] else None
    low = data["low"][0] if data["low"] else None

    return "\n".join([
        f"## {chapter}、{category['name']}产品分析",
        "",
        f"### {section}.1 TOP10产品",
        "",
        f"#### {section}.1.1 TOP10产品（{end_date}）",
        "",
        table(["排名", "品牌", "产品名称", "ASIN", "价格($)", "评分", "月销", "上架天数", "上周排名", "排名变化", "商品图片"], product_rows(data["top10"], "top10")),
        "",
        f"#### {section}.1.2 TOP10整体分析",
        "",
        "**TOP10整体分析**：",
        f"- **头部产品特征**：TOP3月销合计{integer(sum(int(row['monthly_sales']) for row in top10[:3]))}件。",
        f"- **价格区间分布**：TOP10价格覆盖{money(min(float(row['price']) for row in top10))}至{money(max(float(row['price']) for row in top10))}，均价约{money(avg_top10_price)}。",
        f"- **品牌集中度**：TOP10共有{len(brands)}个品牌，主要品牌包括{'、'.join(brands[:6])}。",
        f"- **新品/老品比例（按销量）**：TOP10中180天内新品月销占比约{(new_sales / total_top10_sales * 100 if total_top10_sales else 0):.1f}%。",
        "",
        f"### {section}.2 强势上升产品",
        "",
        "筛选标准：本周排名变化≥10位（升序）",
        "",
        table(["排名变化", "品牌", "产品名称", "ASIN", "价格($)", "评分", "月销", "上架天数", "本周排名", "上周排名", "商品图片"], product_rows(data["rising"], "rising") or [["-", "无满足条件产品", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]),
        "",
        f"### {section}.3 强势下降产品",
        "",
        "筛选标准：本周排名变化≥10位（降序）",
        "",
        table(["排名变化", "品牌", "产品名称", "ASIN", "价格($)", "评分", "月销", "上架天数", "本周排名", "上周排名", "商品图片"], product_rows(data["falling"], "falling") or [["-", "无满足条件产品", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]),
        "",
        f"### {section}.4 新上榜产品追踪",
        "",
        "新上榜定义：上周不在TOP100，本周新进入TOP100的产品",
        "",
        table(["本周排名", "品牌", "产品名称", "ASIN", "价格($)", "评分", "月销", "上架天数", "是否新品", "商品图片"], product_rows(data["new"], "new") or [["-", "无新上榜产品", "-", "-", "-", "-", "-", "-", "-", "-"]]),
        "",
        f"### {section}.5 {category['name']}低分高销洞察",
        "",
        f"#### {section}.5.1 评分分布与排名关系",
        "",
        f"基于{end_date}{category['name']}类目TOP100产品数据分析，各评分区间分布情况如下：",
        "",
        table(["评分区间", "产品数", "占比", "平均排名", "月销总额"], rating_rows),
        "",
        "**关键发现**：",
        f"- 月销贡献最高评分段为{cell(max(data['ratings'], key=lambda r: r['total_monthly_sales'])['rating_range'])}。",
        f"- 最大上升产品为{cell(strongest['brand']) if strongest else '-'}，最大下降产品为{cell(weakest['brand']) if weakest else '-'}。",
        f"- 低分高销头部产品为{cell(low['brand']) if low else '-'}，月销{integer(low['monthly_sales']) if low else '-'}件。",
        "",
        f"#### {section}.5.2 低分高销产品明细",
        "",
        "筛选标准：评分<4.3分（低分），但月销表现突出",
        "",
        table(["排名", "品牌", "产品名称", "ASIN", "价格($)", "评分", "月销", "上架天数", "上周排名", "排名变化", "商品图片"], product_rows(data["low"], "low")),
        "",
    ])


def fetch_ulanzi(conn, category: dict[str, str], start_date: str, end_date: str) -> dict[str, Any]:
    base = SKILL_DIR / "agents/03-ulanzi/queries"
    values = {"node_id": category["node"], "start_date": start_date, "end_date": end_date}
    table_ref = doris_table_ref()
    threshold = query(conn, f"SELECT MIN(listing_sales_volume_of_month) AS top100_threshold FROM {table_ref} WHERE bsr_date = '{end_date}' AND bsr_category_node = '{category['node']}'")[0]["top100_threshold"]
    avg_sales = query(conn, f"SELECT ROUND(AVG(listing_sales_volume_of_month), 0) AS category_avg_sales FROM {table_ref} WHERE bsr_date = '{end_date}' AND bsr_category_node = '{category['node']}'")[0]["category_avg_sales"]
    brand_stats = query(conn, f"SELECT COUNT(DISTINCT brand) AS brand_count, SUM(listing_sales_volume_of_month) AS total_sales FROM {table_ref} WHERE bsr_date = '{end_date}' AND bsr_category_node = '{category['node']}'")[0]
    products = qfile(conn, base / "ulanzi-products.sql", **values)
    brands = qfile(conn, base / "brand-efficiency.sql", **values)
    internal = qfile(conn, base / "ulanzi-internal-efficiency.sql", **values)
    if len(brands) != 15:
        die(f"{category['name']} brand efficiency expected 15 rows, got {len(brands)}")
    if len(products) != len(internal):
        die(f"{category['name']} ULANZI product/internal row mismatch: {len(products)} vs {len(internal)}")
    return {
        "products": products,
        "brands": brands,
        "internal": internal,
        "threshold": threshold,
        "avg_sales": avg_sales,
        "brand_stats": brand_stats,
    }


def rank_ulanzi(brands: list[dict[str, Any]]) -> tuple[int | None, dict[str, Any] | None]:
    for idx, row in enumerate(brands, 1):
        if "ulanzi" in str(row["brand"]).lower():
            return idx, row
    return None, None


def render_ulanzi(
    categories: list[dict[str, str]],
    ulanzi_by_node: dict[str, dict[str, Any]],
    start_date: str,
    end_date: str,
    chapter_number: int,
) -> str:
    def products(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return table(["ASIN", "产品名称", f"{start_date}排名", f"{end_date}排名", "排名变化", "价格($)", "评分", "月销(件)", "上架天数", "商品图片"], [["-", "无 ULANZI 产品进入本周 TOP100", "-", "-", "-", "-", "-", "-", "-", "-"]])
        return table(["ASIN", "产品名称", f"{start_date}排名", f"{end_date}排名", "排名变化", "价格($)", "评分", "月销(件)", "上架天数", "商品图片"], [[asin(r["asin"]), cell(r["title"]), integer(r["last_rank"]), integer(r["this_rank"]), change(r["rank_change"]), money(r["price"]), cell(r["ratings"]), integer(r["monthly_sales"]), integer(r["online_days"]), photo(r["photo"])] for r in rows])

    def brand_table(rows: list[dict[str, Any]]) -> str:
        return table(["排名", "品牌", "SKU数", "月销总额(件)", "月销/SKU", "均价($)"], [[str(i + 1), cell(r["brand"]), integer(r["sku_count"]), integer(r["total_monthly_sales"]), integer(r["sales_per_sku"]), money(r["avg_price"])] for i, r in enumerate(rows)])

    def avg_line(data: dict[str, Any]) -> str:
        mean = int(data["brand_stats"]["total_sales"] / max(int(data["brand_stats"]["brand_count"]), 1))
        rank, row = rank_ulanzi(data["brands"])
        if row:
            relation = "高于" if float(row["sales_per_sku"]) >= mean else "低于"
            return f"> **类目均值**：约{integer(mean)}件/SKU（ULANZI排名第{rank}，{relation}类目均值）"
        return f"> **类目均值**：约{integer(mean)}件/SKU（ULANZI未进入品牌效率TOP15）"

    def internal(rows: list[dict[str, Any]]) -> str:
        if not rows:
            return table(["ASIN", "产品名称", f"{end_date}排名", "上架天数", "月销/SKU", "商品图片", "效率评级"], [["-", "无 ULANZI 产品进入本周 TOP100", "-", "-", "-", "-", "-"]])
        return table(["ASIN", "产品名称", f"{end_date}排名", "上架天数", "月销/SKU", "商品图片", "效率评级"], [[asin(r["asin"]), cell(r["title"]), integer(r["bsr_rank"]), integer(r["online_days"]), integer(r["monthly_sales"]), photo(r["photo"]), cell(r["efficiency_rating"])] for r in rows])

    def product_summary(rows: list[dict[str, Any]]) -> list[str]:
        if not rows:
            return ["- **SKU数量**：0", "- **最高排名**：-", "- **整体趋势**：本周未进入 TOP100", "- **表现突出产品**：-"]
        best = min(rows, key=lambda r: r["this_rank"])
        up = sum(1 for r in rows if r["rank_change"] is not None and int(r["rank_change"]) > 0)
        down = sum(1 for r in rows if r["rank_change"] is not None and int(r["rank_change"]) < 0)
        return [f"- **SKU数量**：{len(rows)}", f"- **最高排名**：{best['this_rank']}", f"- **整体趋势**：{up}个SKU上升，{down}个SKU下降", f"- **表现突出产品**：{asin(best['asin'])}，月销{integer(best['monthly_sales'])}件"]

    def summary_values(data: dict[str, Any]) -> dict[str, Any]:
        rows = data["products"]
        sales = sum(int(row.get("monthly_sales") or 0) for row in rows)
        ranks = [int(row["this_rank"]) for row in rows if row.get("this_rank") is not None]
        prices = [float(row["price"]) for row in rows if row.get("price") is not None]
        return {
            "sku_count": len(rows),
            "total_monthly_sales": sales,
            "top10_sku_count": sum(1 for rank in ranks if rank <= 10),
            "avg_rank": (sum(ranks) / len(ranks)) if ranks else None,
            "avg_price": (sum(prices) / len(prices)) if prices else None,
            "new_sku_count": sum(1 for row in rows if int(row.get("online_days") or 0) <= 180),
        }

    summaries = {category["node"]: summary_values(ulanzi_by_node[category["node"]]) for category in categories}
    total_sku = sum(summary["sku_count"] for summary in summaries.values())
    total_sales = sum(summary["total_monthly_sales"] for summary in summaries.values())
    chapter = str(chapter_number)
    output: list[str] = [
        f"## {chinese_number(chapter_number)}、ULANZI本品专题分析",
        "",
        f"数据来源：{data_source_label()}\n分析周期：{start_date} ~ {end_date}",
        "",
        "> **数据口径说明**：",
        "> - **TOP100门槛**：" + "；".join(
            f"{category['name']}约{integer(ulanzi_by_node[category['node']]['threshold'])}件"
            for category in categories
        ),
        "> - **ULANZI状态**：" + "；".join(
            f"{category['name']}有{len(ulanzi_by_node[category['node']]['products'])}个产品进入TOP100"
            for category in categories
        ),
        "",
        f"### {chapter}.1 周度产品线明细",
        "",
    ]
    for index, category in enumerate(categories, 1):
        data = ulanzi_by_node[category["node"]]
        output.extend([
            f"#### {chapter}.1.{index} {category['name']}类目ULANZI产品",
            "",
            products(data["products"]),
            "",
            f"**{category['name']}类目ULANZI表现总结**：",
            *product_summary(data["products"]),
            "",
        ])

    output.extend([
        f"### {chapter}.2 品牌销售效率全面对比分析",
        "",
        f"#### {chapter}.2.1 TOP品牌单品效率排名 ({end_date})",
        "",
    ])
    for category in categories:
        data = ulanzi_by_node[category["node"]]
        output.extend([
            f"**{category['name']}**：",
            "",
            brand_table(data["brands"]),
            "",
            avg_line(data),
            "",
        ])

    output.extend([
        f"#### {chapter}.2.2 ULANZI内部效率分析",
        "",
    ])
    for category in categories:
        data = ulanzi_by_node[category["node"]]
        output.extend([
            f"**{category['name']}（类目均值：{integer(data['avg_sales'])}件/SKU）**：",
            "",
            internal(data["internal"]),
            "",
        ])

    summary_headers = ["指标", *[category["name"] for category in categories], "合计/均值"]
    summary_rows = [
        ["**SKU数**", *[integer(summaries[category["node"]]["sku_count"]) for category in categories], integer(total_sku)],
        ["**月销总额**", *[integer(summaries[category["node"]]["total_monthly_sales"]) for category in categories], integer(total_sales)],
        ["**TOP10 SKU数**", *[integer(summaries[category["node"]]["top10_sku_count"]) for category in categories], "-"],
        ["**平均排名**", *[f"{summaries[category['node']]['avg_rank']:.1f}" if summaries[category["node"]]["avg_rank"] is not None else "-" for category in categories], "-"],
        ["**均价**", *[money(summaries[category["node"]]["avg_price"]) for category in categories], "-"],
    ]
    competition_headers = ["对比维度", "ULANZI", *[category["name"] for category in categories]]
    competition_rows = [
        ["**单品效率**", "按月销/SKU评估", *[avg_line(ulanzi_by_node[category["node"]]).replace('> **类目均值**：', '') for category in categories]],
        ["**价格策略**", "以进入TOP100产品均价评估", *[money(summaries[category["node"]]["avg_price"]) for category in categories]],
        ["**TOP10占比**", "以TOP10 SKU数衡量", *[integer(summaries[category["node"]]["top10_sku_count"]) for category in categories]],
        ["**新品表现**", "以180天内SKU数量观察", *[integer(summaries[category["node"]]["new_sku_count"]) for category in categories]],
    ]

    output.extend([
        f"#### {chapter}.2.3 跨类目战略洞察",
        "",
        "**一、ULANZI品牌整体表现**",
        "",
        table(summary_headers, summary_rows),
        "",
        "**二、跨类目竞争格局对比**",
        "",
        table(competition_headers, competition_rows),
        "",
        "**三、战略洞察与建议**",
        "",
        "1. **优先级策略**：优先关注已进入TOP100且效率高于类目均值的SKU。",
        "2. **SKU精简计划**：复盘低于类目均值且排名靠后的SKU，保留具备差异化卖点的产品。",
        "3. **产品策略**：继续产品创新，关注消费者反馈，优化产品迭代速度。",
        "4. **价格策略**：保持中高端定位，灵活应对竞品价格战。",
        "5. **新品策略**：保持稳定的新品上市节奏，加强新品上市前的测试和准备。",
        "",
    ])
    return "\n".join(output)


def render_summary(
    categories: list[dict[str, str]],
    data_by_node: dict[str, dict[str, Any]],
    chapter_number: int,
) -> str:
    def leader(data: dict[str, Any]) -> str:
        return cell(data["top10"][0]["brand"])

    def top3(data: dict[str, Any]) -> str:
        return "、".join(cell(row["brand"]) for row in data["top10"][:3])

    def price_success(data: dict[str, Any]) -> str:
        best = max(data["top10"], key=lambda row: int(row["monthly_sales"]))
        return f"{cell(best['brand'])}（{money(best['price'])}，月销{integer(best['monthly_sales'])}）"

    def first_or_dash(data: dict[str, Any], key: str) -> str:
        return cell(data[key][0]["brand"]) if data[key] else "-"

    rows: list[list[str]] = []
    metrics = [
        ("**市场领导者**", leader),
        ("**TOP3稳定组合**", top3),
        ("**价格策略成功者**", price_success),
        ("**表现亮眼品牌**", lambda data: first_or_dash(data, "rising")),
        ("**失意品牌**", lambda data: first_or_dash(data, "falling")),
        ("**跌幅最大品牌**", lambda data: first_or_dash(data, "falling")),
        ("**新晋品牌**", lambda data: first_or_dash(data, "new")),
    ]
    for label, renderer in metrics:
        rows.append([label, *[renderer(data_by_node[category["node"]]) for category in categories]])
    rows.append([
        "**品类趋势**",
        *["头部产品稳定，中后段排名波动需持续观察" for _ in categories],
    ])

    return "\n".join([
        f"## {chinese_number(chapter_number)}、本周市场格局总结",
        "",
        table(["格局类型", *[category["name"] for category in categories]], rows),
        "",
    ])


def preflight() -> None:
    required = [
        SKILL_DIR / "references/category-mapping.md",
        SKILL_DIR / "references/04-summary.md",
        SKILL_DIR / "agents/01-overview/templates/00-header.md",
        SKILL_DIR / "agents/01-overview/templates/01-overview.md",
        SKILL_DIR / "agents/02-categories/templates/02-category.md",
        SKILL_DIR / "agents/03-ulanzi/templates/03-ulanzi.md",
    ]
    for path in required:
        read(path)
    print("PREFLIGHT_OK")


def query_counts(
    overview: dict[str, Any],
    categories: list[dict[str, str]],
    data_by_node: dict[str, dict[str, Any]],
    ulanzi_by_node: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    counts = dict(overview)
    for category in categories:
        data = data_by_node[category["node"]]
        prefix = category["name"]
        counts[f"{prefix}.top10"] = len(data["top10"])
        counts[f"{prefix}.rising"] = len(data["rising"])
        counts[f"{prefix}.falling"] = len(data["falling"])
        counts[f"{prefix}.new_entries"] = len(data["new"])
        counts[f"{prefix}.low_rating_high_sales"] = len(data["low"])
        counts[f"{prefix}.rating_distribution"] = len(data["ratings"])
    for category in categories:
        data = ulanzi_by_node[category["node"]]
        prefix = category["name"]
        counts[f"{prefix}.ulanzi_products"] = len(data["products"])
        counts[f"{prefix}.ulanzi_internal"] = len(data["internal"])
        counts[f"{prefix}.brand_efficiency"] = len(data["brands"])
    return counts


def print_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=False, help="报告方向，例如灯光类、支架类、脚架类、音视频类、智能工作室类")
    parser.add_argument("--date", required=False, help="YYYY-MM-DD or 本周三/上周三/上上周三")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--check-env", action="store_true", help="Check Doris connection environment variables and exit")
    parser.add_argument("--dry-run", action="store_true", help="Run parsing, SQL, rendering, and validation without writing the final Obsidian report")
    parser.add_argument("--image-width", type=int, default=DEFAULT_IMAGE_WIDTH, help="Product image width in markdown tables")
    overwrite = parser.add_mutually_exclusive_group()
    overwrite.add_argument("--overwrite", action="store_true", help="Overwrite the target report when it already exists")
    overwrite.add_argument("--no-overwrite", action="store_true", help="Do not overwrite existing reports; this is the default")
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=default_report_dir(),
        help="Directory for generated reports when --out is not provided.",
    )
    args = parser.parse_args()

    if args.preflight:
        preflight()
        return
    load_dotenv()
    if args.check_env:
        check_env()
        return
    if not args.category or not args.date:
        die("--category and --date are required unless --preflight or --check-env is used")
    if args.image_width < 40 or args.image_width > 400:
        die("--image-width must be between 40 and 400")

    global IMAGE_WIDTH
    IMAGE_WIDTH = args.image_width

    mapping = parse_mapping()
    if args.category not in mapping:
        die(f"unknown category {args.category}; available: {', '.join(mapping)}")
    categories = mapping[args.category]
    if not 1 <= len(categories) <= 4:
        die(f"{args.category} must map to 1-4 Sorftime categories")

    end_date = parse_date(args.date)
    start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    with db_connect() as conn:
        overview = overview_counts(conn, categories, start_date, end_date)
        data_by_node = {
            category["node"]: fetch_category(conn, category, start_date, end_date)
            for category in categories
        }
        ulanzi_by_node = {
            category["node"]: fetch_ulanzi(conn, category, start_date, end_date)
            for category in categories
        }
        ulanzi_chapter = len(categories) + 2
        summary_chapter = ulanzi_chapter + 1
        report = "\n".join([
            render_overview(conn, args.category, categories, data_by_node, start_date, end_date),
            *[
                render_category(
                    chinese_number(index + 2),
                    str(index + 2),
                    category,
                    data_by_node[category["node"]],
                    end_date,
                )
                for index, category in enumerate(categories)
            ],
            render_ulanzi(categories, ulanzi_by_node, start_date, end_date, ulanzi_chapter),
            render_summary(categories, data_by_node, summary_chapter),
        ])
    counts = query_counts(overview, categories, data_by_node, ulanzi_by_node)

    compact_date = end_date.replace("-", "")
    out_path = args.out or args.out_dir / f"{compact_date}{args.category}周趋势监测报告.md"
    overwrite_enabled = bool(args.overwrite)

    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="sorftime-weekly-report-") as tmpdir:
            validation_path = Path(tmpdir) / out_path.name
            validation_path.write_text(report, encoding="utf-8")
            validate(validation_path, args.category, [category["name"] for category in categories], image_width=IMAGE_WIDTH)
        print_summary({
            "status": "DRY_RUN_OK",
            "category": args.category,
            "start_date": start_date,
            "end_date": end_date,
            "categories": [{"name": category["name"], "node_id": category["node"]} for category in categories],
            "target_path": str(out_path),
            "output_path": None,
            "dry_run": True,
            "overwrite": overwrite_enabled,
            "image_width": IMAGE_WIDTH,
            "query_counts": counts,
            "validation": "VALIDATION_OK",
        })
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    target_path = out_path
    conflict = out_path.exists() and not overwrite_enabled
    if conflict:
        timestamp = strftime("%Y%m%d-%H%M%S")
        out_path = args.out_dir / "_tmp" / f"{target_path.stem}-{timestamp}{target_path.suffix}"
        out_path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=out_path.parent,
            prefix=f".{out_path.stem}.",
            suffix=f"{out_path.suffix}.tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(report)
            temp_path = Path(temp_file.name)
        validate(temp_path, args.category, [category["name"] for category in categories], image_width=IMAGE_WIDTH)
        os.replace(temp_path, out_path)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    print_summary({
        "status": "REPORT_CONFLICT_TEMP_OK" if conflict else "REPORT_OK",
        "category": args.category,
        "start_date": start_date,
        "end_date": end_date,
        "categories": [{"name": category["name"], "node_id": category["node"]} for category in categories],
        "target_path": str(target_path),
        "output_path": str(out_path),
        "dry_run": False,
        "overwrite": overwrite_enabled,
        "conflict": conflict,
        "image_width": IMAGE_WIDTH,
        "query_counts": counts,
        "validation": "VALIDATION_OK",
    })


if __name__ == "__main__":
    main()
