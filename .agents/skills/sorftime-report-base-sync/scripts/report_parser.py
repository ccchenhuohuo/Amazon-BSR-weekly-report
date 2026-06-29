"""Parse Sorftime weekly Markdown reports into Base records."""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any


REQUIRED_TABLES = [
    "异动数据",
    "低分高销数据",
    "本品数据",
    "2.1.1",
    "2.2",
    "2.3",
    "2.4",
    "2.5.2",
    "3.1.1",
    "3.2",
    "3.3",
    "3.4",
    "3.5.2",
    "4.1.1",
    "4.1.2",
]

CATEGORY_MAP = {
    "灯光类": ("Continuous Output Lighting", "Selfie Lights"),
    "支架类": ("Cradles", "Grips"),
    "脚架类": ("Complete Tripods", "Tripods"),
}

MOVEMENT_SECTIONS = {
    "2.1.1": ("TOP10产品", 0),
    "2.2": ("强势上升产品", 0),
    "2.3": ("强势下降产品", 0),
    "2.4": ("新上榜产品", 0),
    "3.1.1": ("TOP10产品", 1),
    "3.2": ("强势上升产品", 1),
    "3.3": ("强势下降产品", 1),
    "3.4": ("新上榜产品", 1),
}

LOW_SALES_SECTIONS = {
    "2.5.2": 0,
    "3.5.2": 1,
}

OWN_SECTIONS = {
    "4.1.1": 0,
    "4.1.2": 1,
}

OWN_TABLES = {"本品数据", "4.1.1", "4.1.2"}


class SyncError(RuntimeError):
    pass


def split_md_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    cells: list[str] = []
    buf: list[str] = []
    escaped = False
    for ch in line:
        if escaped:
            if ch == "|":
                buf.append("|")
            else:
                buf.append("\\")
                buf.append(ch)
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == "|":
            cells.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if escaped:
        buf.append("\\")
    cells.append("".join(buf).strip())
    return cells


def is_separator(line: str) -> bool:
    cells = split_md_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", c.strip()) for c in cells)


def parse_tables(report_path: Path) -> dict[str, list[dict[str, str]]]:
    lines = report_path.read_text(encoding="utf-8").splitlines()
    current_section: str | None = None
    wanted = set(MOVEMENT_SECTIONS) | set(LOW_SALES_SECTIONS) | set(OWN_SECTIONS)
    tables: dict[str, list[dict[str, str]]] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        heading = re.match(r"^#{2,6}\s+(\d+(?:\.\d+)+)\b", line)
        if heading:
            section = heading.group(1)
            current_section = section if section in wanted else None
        if (
            current_section
            and current_section not in tables
            and line.lstrip().startswith("|")
            and i + 1 < len(lines)
            and is_separator(lines[i + 1])
        ):
            headers = split_md_row(line)
            rows: list[dict[str, str]] = []
            i += 2
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                cells = split_md_row(lines[i])
                if len(cells) < len(headers):
                    cells += [""] * (len(headers) - len(cells))
                row = dict(zip(headers, cells[: len(headers)]))
                if any(v.strip() for v in row.values()):
                    rows.append(row)
                i += 1
            tables[current_section] = rows
            continue
        i += 1
    return tables


def clean_key(key: str) -> str:
    return key.strip().replace("($)", "").replace("(件)", "")


def normalize_row(row: dict[str, str]) -> dict[str, str]:
    return {clean_key(k): v.strip() for k, v in row.items()}


def number_value(value: str | None) -> int | float | None:
    if value is None:
        return None
    text = str(value).strip()
    if text in {"", "-", "—", "None", "none", "null"}:
        return None
    text = text.replace("$", "").replace(",", "").replace("+", "").strip()
    if not text:
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    return int(num) if num.is_integer() else num


def asin_link(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip()
    match = re.search(r"\[([A-Z0-9]{10})\]\((https://www\.amazon\.com/dp/[A-Z0-9]{10})\)", text)
    if match:
        return f"[{match.group(1)}]({match.group(2)})"
    match = re.search(r"\b([A-Z0-9]{10})\b", text)
    if match:
        asin = match.group(1)
        return f"[{asin}](https://www.amazon.com/dp/{asin})"
    return None


def asin_plain(value: str | None) -> str | None:
    link = asin_link(value)
    if not link:
        return None
    match = re.search(r"\[([A-Z0-9]{10})\]", link)
    return match.group(1) if match else None


def image_url(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r'src="([^"]+)"', value)
    url = match.group(1) if match else value.strip()
    return url if url.startswith("https://") else None


def common_product_fields(row: dict[str, str], report_date: str, category: str) -> dict[str, Any] | None:
    norm = normalize_row(row)
    asin = asin_link(norm.get("ASIN"))
    img = image_url(norm.get("商品图片"))
    if not asin or not img:
        return None
    return {
        "报告日期": f"{report_date} 00:00:00",
        "类目": category,
        "品牌": norm.get("品牌"),
        "产品名称": norm.get("产品名称"),
        "ASIN": asin,
        "价格": number_value(norm.get("价格")),
        "评分": number_value(norm.get("评分")),
        "月销": number_value(norm.get("月销")),
        "上架天数": number_value(norm.get("上架天数")),
        "商品图片": img,
    }


def movement_record(
    section: str, row: dict[str, str], report_date: str, categories: tuple[str, str]
) -> dict[str, Any] | None:
    data_type, category_idx = MOVEMENT_SECTIONS[section]
    base = common_product_fields(row, report_date, categories[category_idx])
    if not base:
        return None
    norm = normalize_row(row)
    base["数据类型"] = data_type
    if data_type == "TOP10产品":
        base["排名"] = number_value(norm.get("排名"))
        base["上周排名"] = number_value(norm.get("上周排名"))
        base["排名变化"] = number_value(norm.get("排名变化"))
        base["是否新品"] = None
    elif data_type in {"强势上升产品", "强势下降产品"}:
        base["排名"] = number_value(norm.get("本周排名"))
        base["上周排名"] = number_value(norm.get("上周排名"))
        base["排名变化"] = number_value(norm.get("排名变化"))
        base["是否新品"] = None
    else:
        base["排名"] = number_value(norm.get("本周排名"))
        base["上周排名"] = None
        base["排名变化"] = None
        base["是否新品"] = norm.get("是否新品") or None
    return base


def low_sales_record(
    section: str, row: dict[str, str], report_date: str, categories: tuple[str, str]
) -> dict[str, Any] | None:
    category = categories[LOW_SALES_SECTIONS[section]]
    base = common_product_fields(row, report_date, category)
    if not base:
        return None
    norm = normalize_row(row)
    base["排名"] = number_value(norm.get("排名"))
    base["上周排名"] = number_value(norm.get("上周排名"))
    base["排名变化"] = number_value(norm.get("排名变化"))
    return base


def own_record(
    section: str,
    row: dict[str, str],
    report_date: str,
    previous_date: str,
    categories: tuple[str, str],
) -> dict[str, Any] | None:
    norm = normalize_row(row)
    asin = asin_link(norm.get("ASIN"))
    img = image_url(norm.get("商品图片"))
    if not asin or not img:
        return None
    return {
        "报告日期": f"{report_date} 00:00:00",
        "类目": categories[OWN_SECTIONS[section]],
        "产品名称": norm.get("产品名称"),
        "ASIN": asin,
        f"{previous_date}排名": number_value(norm.get(f"{previous_date}排名")),
        f"{report_date}排名": number_value(norm.get(f"{report_date}排名")),
        "排名变化": number_value(norm.get("排名变化")),
        "价格": number_value(norm.get("价格")),
        "评分": number_value(norm.get("评分")),
        "月销": number_value(norm.get("月销")),
        "上架天数": number_value(norm.get("上架天数")),
        "商品图片": img,
    }


def infer_report_category(report_path: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    for category in CATEGORY_MAP:
        if category in report_path.name:
            return category
    raise SyncError(f"Cannot infer report category from path: {report_path}")


def previous_week(report_date: str) -> str:
    return (dt.date.fromisoformat(report_date) - dt.timedelta(days=7)).isoformat()


def build_records(
    report_path: Path, report_category: str, report_date: str, previous_date: str
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    categories = CATEGORY_MAP[report_category]
    tables = parse_tables(report_path)
    missing = [s for s in (set(MOVEMENT_SECTIONS) | set(LOW_SALES_SECTIONS) | set(OWN_SECTIONS)) if s not in tables]
    if missing:
        raise SyncError(f"Missing target Markdown tables: {', '.join(sorted(missing))}")

    mother = {"异动数据": [], "低分高销数据": [], "本品数据": []}
    child: dict[str, list[dict[str, Any]]] = {section: [] for section in REQUIRED_TABLES if re.match(r"^[234]\.", section)}

    for section in MOVEMENT_SECTIONS:
        rows = [r for r in (movement_record(section, row, report_date, categories) for row in tables[section]) if r]
        mother["异动数据"].extend(rows)
        child[section] = rows

    for section in LOW_SALES_SECTIONS:
        rows = [r for r in (low_sales_record(section, row, report_date, categories) for row in tables[section]) if r]
        mother["低分高销数据"].extend(rows)
        child[section] = rows

    for section in OWN_SECTIONS:
        rows = [
            r
            for r in (own_record(section, row, report_date, previous_date, categories) for row in tables[section])
            if r
        ]
        mother["本品数据"].extend(rows)
        child[section] = rows

    return mother, child


def validate_records(
    mother: dict[str, list[dict[str, Any]]],
    child: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    issues: list[str] = []
    counts = {**{k: len(v) for k, v in mother.items()}, **{k: len(v) for k, v in child.items()}}
    for table_name, records in {**mother, **child}.items():
        for idx, record in enumerate(records, start=1):
            img = record.get("商品图片")
            asin = record.get("ASIN")
            if img and (not str(img).startswith("https://") or "<img" in str(img)):
                issues.append(f"{table_name} row {idx}: invalid image {img}")
            if asin and not re.fullmatch(r"\[[A-Z0-9]{10}\]\(https://www\.amazon\.com/dp/[A-Z0-9]{10}\)", str(asin)):
                issues.append(f"{table_name} row {idx}: invalid ASIN link {asin}")
            if record.get("数据类型") == "新上榜产品":
                if record.get("上周排名") is not None or record.get("排名变化") is not None:
                    issues.append(f"{table_name} row {idx}: new listing has previous/rank change value")
            elif "数据类型" in record and record.get("是否新品") is not None:
                issues.append(f"{table_name} row {idx}: non-new-listing has 是否新品")

    duplicate_specs = {
        "异动数据": ["ASIN", "类目", "数据类型"],
        "低分高销数据": ["ASIN", "类目"],
        "本品数据": ["ASIN", "类目"],
    }
    duplicates: dict[str, list[tuple[Any, ...]]] = {}
    for table_name, keys in duplicate_specs.items():
        seen: set[tuple[Any, ...]] = set()
        dup: list[tuple[Any, ...]] = []
        for record in mother[table_name]:
            key = tuple(record.get(k) for k in keys)
            if key in seen:
                dup.append(key)
            seen.add(key)
        if dup:
            duplicates[table_name] = dup
    return {"counts": counts, "issues": issues, "duplicates": duplicates}


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "".join(cell_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "link", "url", "value", "name"):
            if key in value:
                return cell_text(value[key])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def record_fields(record: dict[str, Any]) -> dict[str, Any]:
    fields = record.get("fields")
    if isinstance(fields, dict):
        return fields
    return {key: value for key, value in record.items() if key not in {"id", "record_id", "recordId"}}
