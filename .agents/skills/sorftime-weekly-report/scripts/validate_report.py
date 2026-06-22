#!/usr/bin/env python3
"""Validate generated Sorftime weekly report markdown."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


FORBIDDEN_PATTERNS = [
    r"\{[^{}\n]+\}",
    r"数据待补充",
    r"XXXX",
    r"\|\s*\|\s*\|",
]

REQUIRED_HEADINGS = [
    "# {category}周趋势监测报告",
    "## 一、数据概览",
    "### 1.1 核心指标对比",
    "### 1.2 核心结论",
    "## 二、{category_a}产品分析",
    "### 2.1 TOP10产品",
    "### 2.2 强势上升产品",
    "### 2.3 强势下降产品",
    "### 2.4 新上榜产品追踪",
    "### 2.5 {category_a}低分高销洞察",
    "## 三、{category_b}产品分析",
    "### 3.1 TOP10产品",
    "### 3.2 强势上升产品",
    "### 3.3 强势下降产品",
    "### 3.4 新上榜产品追踪",
    "### 3.5 {category_b}低分高销洞察",
    "## 四、ULANZI本品专题分析",
    "### 4.1 周度产品线明细",
    "### 4.2 品牌销售效率全面对比分析",
    "## 五、本周市场格局总结",
]


def fail(message: str) -> None:
    raise SystemExit(f"VALIDATION_FAILED: {message}")


def count_table_rows_after(text: str, heading: str) -> int:
    start = text.find(heading)
    if start < 0:
        fail(f"missing heading: {heading}")
    lines = text[start:].splitlines()
    in_table = False
    rows = 0
    for line in lines[1:]:
        if line.startswith("### ") or line.startswith("## "):
            if in_table:
                break
        if line.startswith("|"):
            in_table = True
            if "---" not in line:
                rows += 1
        elif in_table and line.strip():
            break
    return max(rows - 1, 0)


def section_after(text: str, marker: str, stop_pattern: str | None = None) -> str:
    start = text.find(marker)
    if start < 0:
        fail(f"missing marker: {marker}")
    section = text[start + len(marker):]
    if stop_pattern:
        match = re.search(stop_pattern, section, flags=re.MULTILINE)
        if match:
            section = section[: match.start()]
    return section


def first_table_rows(section: str) -> list[str]:
    lines = section.splitlines()
    rows: list[str] = []
    in_table = False
    for line in lines:
        if line.startswith("|"):
            in_table = True
            rows.append(line)
            continue
        if in_table and line.strip():
            break
    if not rows:
        fail("expected table after marker")
    data_rows = [row for row in rows if "---" not in row]
    return data_rows[1:]


def assert_rows(text: str, marker: str, expected: int | None = None, minimum: int | None = None, maximum: int | None = None) -> None:
    rows = count_table_rows_after(text, marker)
    if expected is not None and rows != expected:
        fail(f"{marker} has {rows} rows, expected {expected}")
    if minimum is not None and rows < minimum:
        fail(f"{marker} has {rows} rows, expected at least {minimum}")
    if maximum is not None and rows > maximum:
        fail(f"{marker} has {rows} rows, expected at most {maximum}")


def assert_first_table_rows(text: str, marker: str, expected: int | None = None, minimum: int | None = None, maximum: int | None = None) -> None:
    rows = first_table_rows(section_after(text, marker))
    count = len(rows)
    if expected is not None and count != expected:
        fail(f"{marker} has {count} rows, expected {expected}")
    if minimum is not None and count < minimum:
        fail(f"{marker} has {count} rows, expected at least {minimum}")
    if maximum is not None and count > maximum:
        fail(f"{marker} has {count} rows, expected at most {maximum}")


def assert_overview_top3(text: str) -> None:
    rows = first_table_rows(section_after(text, "### 1.1 核心指标对比"))
    top_rows = [row for row in rows if re.match(r"\|\s*\*\*TOP[123]产品\*\*", row)]
    if len(top_rows) != 3:
        fail(f"overview TOP rows expected 3, got {len(top_rows)}")
    for row in top_rows:
        if row.count("https://www.amazon.com/dp/") != 4:
            fail("each overview TOP row must include 4 ASIN links")


def assert_marker_table(text: str, marker: str, expected: int | None = None, minimum: int | None = None) -> None:
    rows = first_table_rows(section_after(text, marker))
    if expected is not None and len(rows) != expected:
        fail(f"{marker} has {len(rows)} rows, expected {expected}")
    if minimum is not None and len(rows) < minimum:
        fail(f"{marker} has {len(rows)} rows, expected at least {minimum}")


def assert_photo_format(text: str, image_width: int = 150) -> None:
    for match in re.finditer(r"<img\s+[^>]*>", text):
        tag = match.group(0)
        if 'src="' not in tag or f'width="{image_width}"' not in tag:
            fail(f"invalid image tag at character {match.start()}: {tag}")


def assert_prices_and_links(text: str) -> None:
    if not re.search(r"\|\s*\$\d+(?:\.\d{2})?\s*\|", text):
        fail("missing table price cells with $ prefix")
    if not re.search(r"\[B0[A-Z0-9]+\]\(https://www\.amazon\.com/dp/B0[A-Z0-9]+\)", text):
        fail("missing valid ASIN Amazon links")


def validate(path: Path, category: str, category_a: str, category_b: str, image_width: int = 150) -> None:
    text = path.read_text(encoding="utf-8")

    for pattern in FORBIDDEN_PATTERNS:
        match = re.search(pattern, text)
        if match:
            fail(f"forbidden pattern {pattern!r} at character {match.start()}")

    for heading in REQUIRED_HEADINGS:
        expected = heading.format(category=category, category_a=category_a, category_b=category_b)
        if expected not in text:
            fail(f"missing required heading: {expected}")

    assert_prices_and_links(text)
    assert_photo_format(text, image_width=image_width)
    assert_overview_top3(text)

    assert_rows(text, "#### 2.1.1 TOP10产品", expected=10)
    assert_rows(text, "### 2.2 强势上升产品", minimum=1, maximum=10)
    assert_rows(text, "### 2.3 强势下降产品", minimum=1, maximum=3)
    assert_rows(text, "### 2.4 新上榜产品追踪", minimum=1)
    assert_rows(text, "#### 2.5.1 评分分布与排名关系", expected=3)
    assert_rows(text, "#### 2.5.2 低分高销产品明细", minimum=1, maximum=10)

    assert_rows(text, "#### 3.1.1 TOP10产品", expected=10)
    assert_rows(text, "### 3.2 强势上升产品", minimum=1, maximum=10)
    assert_rows(text, "### 3.3 强势下降产品", minimum=1, maximum=3)
    assert_rows(text, "### 3.4 新上榜产品追踪", minimum=1)
    assert_rows(text, "#### 3.5.1 评分分布与排名关系", expected=3)
    assert_rows(text, "#### 3.5.2 低分高销产品明细", minimum=1, maximum=10)

    assert_marker_table(text, f"#### 4.1.1 {category_a}类目ULANZI产品", minimum=1)
    assert_marker_table(text, f"#### 4.1.2 {category_b}类目ULANZI产品", minimum=1)
    assert_marker_table(text, f"**{category_a}**：", expected=15)
    assert_marker_table(text, f"**{category_b}**：", expected=15)
    assert_marker_table(text, f"**{category_a}（类目均值：", minimum=1)
    assert_marker_table(text, f"**{category_b}（类目均值：", minimum=1)

    for category_name in [category_a, category_b]:
        section = section_after(text, f"#### 4.1.1 {category_name}类目ULANZI产品") if category_name == category_a else section_after(text, f"#### 4.1.2 {category_name}类目ULANZI产品")
        rows = first_table_rows(section)
        if any("无 ULANZI 产品进入本周 TOP100" in row for row in rows):
            if len(rows) != 1:
                fail(f"{category_name} ULANZI empty-state table must contain exactly one row")
        elif not any("ULANZI" in row.upper() for row in rows):
            fail(f"{category_name} ULANZI table has neither products nor empty-state text")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--category", required=True)
    parser.add_argument("--category-a", required=True)
    parser.add_argument("--category-b", required=True)
    parser.add_argument("--image-width", type=int, default=150)
    args = parser.parse_args()
    validate(args.report, args.category, args.category_a, args.category_b, image_width=args.image_width)
    print(f"VALIDATION_OK: {args.report}")


if __name__ == "__main__":
    main()
