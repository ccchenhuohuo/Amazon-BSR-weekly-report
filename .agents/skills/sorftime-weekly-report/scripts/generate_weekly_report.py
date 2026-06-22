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
    text = read(SKILL_DIR / "references/category-mapping.md")
    mapping: dict[str, list[dict[str, str]]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("## "):
            current = line[3:].strip()
            mapping[current] = []
            continue
        if not current or not line.startswith("|") or "node_id" in line or "---" in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 2 and cells[0] and cells[1]:
            name = cells[0].split(">")[-1].strip()
            mapping[current].append({"name": name, "path": cells[0], "node": cells[1]})
    return mapping


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


def render_overview(conn, board_name: str, a: dict[str, str], b: dict[str, str], a_data: dict[str, Any], b_data: dict[str, Any], start_date: str, end_date: str) -> str:
    base = SKILL_DIR / "agents/01-overview/queries"
    metrics = {}
    top = {}
    for category in [a, b]:
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
        "{类目A名称}": a["name"],
        "{类目A完整路径}": a["path"],
        "{类目A节点ID}": a["node"],
        "{类目B名称}": b["name"],
        "{类目B完整路径}": b["path"],
        "{类目B节点ID}": b["node"],
        "{数据来源}": data_source_label(),
    }
    for key, value in replacements.items():
        header = header.replace(key, value)

    rows = []
    for idx in range(3):
        rows.append([
            f"**TOP{idx + 1}产品**",
            top_product(top[(a["node"], start_date)][idx]),
            top_product(top[(a["node"], end_date)][idx]),
            top_product(top[(b["node"], start_date)][idx]),
            top_product(top[(b["node"], end_date)][idx]),
        ])
    a_start = metric_for(metrics[a["node"]], start_date)
    a_end = metric_for(metrics[a["node"]], end_date)
    b_start = metric_for(metrics[b["node"]], start_date)
    b_end = metric_for(metrics[b["node"]], end_date)
    rows += [
        ["**独立品牌数**", integer(a_start["unique_brands"]), integer(a_end["unique_brands"]), integer(b_start["unique_brands"]), integer(b_end["unique_brands"])],
        ["**类目月销总量**", integer(a_start["total_monthly_sales"]), integer(a_end["total_monthly_sales"]), integer(b_start["total_monthly_sales"]), integer(b_end["total_monthly_sales"])],
        ["**类目均价（按销量加权）**", money(a_start["weighted_avg_price"]), money(a_end["weighted_avg_price"]), money(b_start["weighted_avg_price"]), money(b_end["weighted_avg_price"])],
    ]

    def delta(start: dict[str, Any], end: dict[str, Any], field: str) -> float:
        return float(end[field]) - float(start[field])

    return "\n".join([
        header.rstrip(),
        "",
        "## 一、数据概览",
        "",
        "### 1.1 核心指标对比",
        "",
        table(["指标", f"{a['name']} ({start_date})", f"{a['name']} ({end_date})", f"{b['name']} ({start_date})", f"{b['name']} ({end_date})"], rows),
        "",
        "### 1.2 核心结论",
        "",
        f"**{a['name']}类目关键洞察**：",
        f"1. 月销总量较上周变化{delta(a_start, a_end, 'total_monthly_sales'):+,.0f}件，独立品牌数变化{delta(a_start, a_end, 'unique_brands'):+.0f}个。",
        f"2. 加权均价较上周变化{delta(a_start, a_end, 'weighted_avg_price'):+.2f}美元。",
        f"3. TOP10头部月销最高产品为{cell(a_data['top10'][0]['brand'])} {asin(a_data['top10'][0]['asin'])}，月销{integer(a_data['top10'][0]['monthly_sales'])}件。",
        "",
        f"**{b['name']}类目关键洞察**：",
        f"1. 月销总量较上周变化{delta(b_start, b_end, 'total_monthly_sales'):+,.0f}件，独立品牌数变化{delta(b_start, b_end, 'unique_brands'):+.0f}个。",
        f"2. 加权均价较上周变化{delta(b_start, b_end, 'weighted_avg_price'):+.2f}美元。",
        f"3. TOP10头部月销最高产品为{cell(b_data['top10'][0]['brand'])} {asin(b_data['top10'][0]['asin'])}，月销{integer(b_data['top10'][0]['monthly_sales'])}件。",
        "",
    ])


def overview_counts(conn, a: dict[str, str], b: dict[str, str], start_date: str, end_date: str) -> dict[str, Any]:
    base = SKILL_DIR / "agents/01-overview/queries"
    counts: dict[str, Any] = {}
    for category in [a, b]:
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


def render_ulanzi(conn, a: dict[str, str], b: dict[str, str], a_u: dict[str, Any], b_u: dict[str, Any], start_date: str, end_date: str) -> str:
    summary = qfile(conn, SKILL_DIR / "agents/03-ulanzi/queries/ulanzi-summary.sql", end_date=end_date, category_a_node=a["node"], category_b_node=b["node"])
    by_node = {row["bsr_category_node"]: row for row in summary}

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

    def summary_values(category: dict[str, str]) -> list[str]:
        row = by_node.get(category["node"], {})
        return [integer(row.get("sku_count", 0)), integer(row.get("total_monthly_sales", 0)), integer(row.get("top10_sku_count", 0)), cell(row.get("avg_rank", "-")), money(row.get("avg_price")) if row else "-"]

    a_sum = summary_values(a)
    b_sum = summary_values(b)
    total_sku = int(by_node.get(a["node"], {}).get("sku_count", 0) or 0) + int(by_node.get(b["node"], {}).get("sku_count", 0) or 0)
    total_sales = int(by_node.get(a["node"], {}).get("total_monthly_sales", 0) or 0) + int(by_node.get(b["node"], {}).get("total_monthly_sales", 0) or 0)

    return "\n".join([
        "## 四、ULANZI本品专题分析",
        "",
        f"数据来源：{data_source_label()}\n分析周期：{start_date} ~ {end_date}",
        "",
        "> **数据口径说明**：",
        f"> - **TOP100门槛**：{a['name']}最低月销约{integer(a_u['threshold'])}件，{b['name']}最低月销约{integer(b_u['threshold'])}件",
        f"> - **ULANZI状态**：{a['name']}有{len(a_u['products'])}个产品进入TOP100，{b['name']}有{len(b_u['products'])}个产品进入TOP100",
        "",
        "### 4.1 周度产品线明细",
        "",
        f"#### 4.1.1 {a['name']}类目ULANZI产品",
        "",
        products(a_u["products"]),
        "",
        f"**{a['name']}类目ULANZI表现总结**：",
        *product_summary(a_u["products"]),
        "",
        f"#### 4.1.2 {b['name']}类目ULANZI产品",
        "",
        products(b_u["products"]),
        "",
        f"**{b['name']}类目ULANZI表现总结**：",
        *product_summary(b_u["products"]),
        "",
        "### 4.2 品牌销售效率全面对比分析",
        "",
        f"#### 4.2.1 TOP品牌单品效率排名 ({end_date})",
        "",
        f"**{a['name']}**：",
        "",
        brand_table(a_u["brands"]),
        "",
        avg_line(a_u),
        "",
        f"**{b['name']}**：",
        "",
        brand_table(b_u["brands"]),
        "",
        avg_line(b_u),
        "",
        "#### 4.2.2 ULANZI内部效率分析",
        "",
        f"**{a['name']}（类目均值：{integer(a_u['avg_sales'])}件/SKU）**：",
        "",
        internal(a_u["internal"]),
        "",
        f"**{b['name']}（类目均值：{integer(b_u['avg_sales'])}件/SKU）**：",
        "",
        internal(b_u["internal"]),
        "",
        "#### 4.2.3 跨类目战略洞察",
        "",
        "**一、ULANZI品牌整体表现**",
        "",
        table(["指标", a["name"], b["name"], "合计/均值"], [
            ["**SKU数**", a_sum[0], b_sum[0], integer(total_sku)],
            ["**月销总额**", a_sum[1], b_sum[1], integer(total_sales)],
            ["**TOP10 SKU数**", a_sum[2], b_sum[2], "-"],
            ["**平均排名**", a_sum[3], b_sum[3], "-"],
            ["**均价**", a_sum[4], b_sum[4], "-"],
        ]),
        "",
        "**二、跨类目竞争格局对比**",
        "",
        table(["对比维度", "ULANZI", a["name"], b["name"]], [
            ["**单品效率**", "按月销/SKU评估", avg_line(a_u).replace('> **类目均值**：', ''), avg_line(b_u).replace('> **类目均值**：', '')],
            ["**价格策略**", "以进入TOP100产品均价评估", a_sum[4], b_sum[4]],
            ["**TOP10占比**", "以TOP10 SKU数衡量", a_sum[2], b_sum[2]],
            ["**新品表现**", "以180天内SKU数量观察", integer(sum(1 for r in a_u["products"] if int(r["online_days"]) <= 180)), integer(sum(1 for r in b_u["products"] if int(r["online_days"]) <= 180))],
        ]),
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


def render_summary(a: dict[str, str], b: dict[str, str], a_data: dict[str, Any], b_data: dict[str, Any]) -> str:
    def leader(data: dict[str, Any]) -> str:
        return cell(data["top10"][0]["brand"])

    def top3(data: dict[str, Any]) -> str:
        return "、".join(cell(row["brand"]) for row in data["top10"][:3])

    def price_success(data: dict[str, Any]) -> str:
        best = max(data["top10"], key=lambda row: int(row["monthly_sales"]))
        return f"{cell(best['brand'])}（{money(best['price'])}，月销{integer(best['monthly_sales'])}）"

    def first_or_dash(data: dict[str, Any], key: str) -> str:
        return cell(data[key][0]["brand"]) if data[key] else "-"

    return "\n".join([
        "## 五、本周市场格局总结",
        "",
        table(["格局类型", a["name"], b["name"]], [
            ["**市场领导者**", leader(a_data), leader(b_data)],
            ["**TOP3稳定组合**", top3(a_data), top3(b_data)],
            ["**价格策略成功者**", price_success(a_data), price_success(b_data)],
            ["**表现亮眼品牌**", first_or_dash(a_data, "rising"), first_or_dash(b_data, "rising")],
            ["**失意品牌**", first_or_dash(a_data, "falling"), first_or_dash(b_data, "falling")],
            ["**跌幅最大品牌**", first_or_dash(a_data, "falling"), first_or_dash(b_data, "falling")],
            ["**新晋品牌**", first_or_dash(a_data, "new"), first_or_dash(b_data, "new")],
            ["**品类趋势**", "头部产品稳定，中后段排名波动明显", "多品牌竞争，低价高销产品密集"],
        ]),
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
    a: dict[str, str],
    b: dict[str, str],
    a_data: dict[str, Any],
    b_data: dict[str, Any],
    a_u: dict[str, Any],
    b_u: dict[str, Any],
) -> dict[str, Any]:
    counts = dict(overview)
    for category, data in [(a, a_data), (b, b_data)]:
        prefix = category["name"]
        counts[f"{prefix}.top10"] = len(data["top10"])
        counts[f"{prefix}.rising"] = len(data["rising"])
        counts[f"{prefix}.falling"] = len(data["falling"])
        counts[f"{prefix}.new_entries"] = len(data["new"])
        counts[f"{prefix}.low_rating_high_sales"] = len(data["low"])
        counts[f"{prefix}.rating_distribution"] = len(data["ratings"])
    for category, data in [(a, a_u), (b, b_u)]:
        prefix = category["name"]
        counts[f"{prefix}.ulanzi_products"] = len(data["products"])
        counts[f"{prefix}.ulanzi_internal"] = len(data["internal"])
        counts[f"{prefix}.brand_efficiency"] = len(data["brands"])
    return counts


def print_summary(summary: dict[str, Any]) -> None:
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", required=False, help="支架类、脚架类、灯光类")
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
    if len(mapping[args.category]) != 2:
        die(f"{args.category} must map to exactly 2 Sorftime categories")

    end_date = parse_date(args.date)
    start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
    a, b = mapping[args.category]

    with db_connect() as conn:
        overview = overview_counts(conn, a, b, start_date, end_date)
        a_data = fetch_category(conn, a, start_date, end_date)
        b_data = fetch_category(conn, b, start_date, end_date)
        a_u = fetch_ulanzi(conn, a, start_date, end_date)
        b_u = fetch_ulanzi(conn, b, start_date, end_date)
        report = "\n".join([
            render_overview(conn, args.category, a, b, a_data, b_data, start_date, end_date),
            render_category("二", "2", a, a_data, end_date),
            render_category("三", "3", b, b_data, end_date),
            render_ulanzi(conn, a, b, a_u, b_u, start_date, end_date),
            render_summary(a, b, a_data, b_data),
        ])
    counts = query_counts(overview, a, b, a_data, b_data, a_u, b_u)

    compact_date = end_date.replace("-", "")
    out_path = args.out or args.out_dir / f"{compact_date}{args.category}周趋势监测报告.md"
    overwrite_enabled = bool(args.overwrite)

    if args.dry_run:
        with tempfile.TemporaryDirectory(prefix="sorftime-weekly-report-") as tmpdir:
            validation_path = Path(tmpdir) / out_path.name
            validation_path.write_text(report, encoding="utf-8")
            validate(validation_path, args.category, a["name"], b["name"], image_width=IMAGE_WIDTH)
        print_summary({
            "status": "DRY_RUN_OK",
            "category": args.category,
            "start_date": start_date,
            "end_date": end_date,
            "category_a": {"name": a["name"], "node_id": a["node"]},
            "category_b": {"name": b["name"], "node_id": b["node"]},
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

    out_path.write_text(report, encoding="utf-8")
    validate(out_path, args.category, a["name"], b["name"], image_width=IMAGE_WIDTH)
    print_summary({
        "status": "REPORT_CONFLICT_TEMP_OK" if conflict else "REPORT_OK",
        "category": args.category,
        "start_date": start_date,
        "end_date": end_date,
        "category_a": {"name": a["name"], "node_id": a["node"]},
        "category_b": {"name": b["name"], "node_id": b["node"]},
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
