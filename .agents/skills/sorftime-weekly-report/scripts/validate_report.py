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

CHINESE_NUMBERS = {1: "一", 2: "二", 3: "三", 4: "四", 5: "五", 6: "六", 7: "七"}


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


def assert_overview_top3(text: str, category_count: int) -> None:
    rows = first_table_rows(section_after(text, "### 1.1 核心指标对比"))
    top_rows = [row for row in rows if re.match(r"\|\s*\*\*TOP[123]产品\*\*", row)]
    if len(top_rows) != 3:
        fail(f"overview TOP rows expected 3, got {len(top_rows)}")
    for row in top_rows:
        expected_links = category_count * 2
        if row.count("https://www.amazon.com/dp/") != expected_links:
            fail(f"each overview TOP row must include {expected_links} ASIN links")


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


def validate(
    path: Path,
    category: str,
    categories: list[str] | str,
    category_b: str | None = None,
    image_width: int = 150,
) -> None:
    if isinstance(categories, str):
        category_names = [categories]
        if category_b:
            category_names.append(category_b)
    else:
        category_names = list(categories)
    if not 1 <= len(category_names) <= 4:
        fail(f"expected 1-4 report categories, got {len(category_names)}")

    text = path.read_text(encoding="utf-8")

    for pattern in FORBIDDEN_PATTERNS:
        match = re.search(pattern, text)
        if match:
            fail(f"forbidden pattern {pattern!r} at character {match.start()}")

    required_headings = [
        f"# {category}周趋势监测报告",
        "## 一、数据概览",
        "### 1.1 核心指标对比",
        "### 1.2 核心结论",
    ]
    for index, category_name in enumerate(category_names, 2):
        required_headings.extend([
            f"## {CHINESE_NUMBERS[index]}、{category_name}产品分析",
            f"### {index}.1 TOP10产品",
            f"### {index}.2 强势上升产品",
            f"### {index}.3 强势下降产品",
            f"### {index}.4 新上榜产品追踪",
            f"### {index}.5 {category_name}低分高销洞察",
        ])
    ulanzi_chapter = len(category_names) + 2
    summary_chapter = ulanzi_chapter + 1
    required_headings.extend([
        f"## {CHINESE_NUMBERS[ulanzi_chapter]}、ULANZI本品专题分析",
        f"### {ulanzi_chapter}.1 周度产品线明细",
        f"### {ulanzi_chapter}.2 品牌销售效率全面对比分析",
        f"## {CHINESE_NUMBERS[summary_chapter]}、本周市场格局总结",
    ])
    for expected in required_headings:
        if expected not in text:
            fail(f"missing required heading: {expected}")

    assert_prices_and_links(text)
    assert_photo_format(text, image_width=image_width)
    assert_overview_top3(text, len(category_names))

    for chapter, category_name in enumerate(category_names, 2):
        assert_rows(text, f"#### {chapter}.1.1 TOP10产品", expected=10)
        assert_rows(text, f"### {chapter}.2 强势上升产品", minimum=1, maximum=10)
        assert_rows(text, f"### {chapter}.3 强势下降产品", minimum=1, maximum=3)
        assert_rows(text, f"### {chapter}.4 新上榜产品追踪", minimum=1)
        assert_rows(text, f"#### {chapter}.5.1 评分分布与排名关系", expected=3)
        assert_rows(text, f"#### {chapter}.5.2 低分高销产品明细", minimum=1, maximum=10)

    for index, category_name in enumerate(category_names, 1):
        marker = f"#### {ulanzi_chapter}.1.{index} {category_name}类目ULANZI产品"
        assert_marker_table(text, marker, minimum=1)
        assert_marker_table(text, f"**{category_name}**：", expected=15)
        assert_marker_table(text, f"**{category_name}（类目均值：", minimum=1)
        section = section_after(text, marker)
        rows = first_table_rows(section)
        if any("无 ULANZI 产品进入本周 TOP100" in row for row in rows):
            if len(rows) != 1:
                fail(f"{category_name} ULANZI empty-state table must contain exactly one row")
        elif not any("https://www.amazon.com/dp/" in row for row in rows):
            fail(f"{category_name} ULANZI table has neither product links nor empty-state text")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("--category", required=True)
    parser.add_argument("--category-name", action="append", default=[], help="Leaf category name; repeat 1-4 times")
    parser.add_argument("--category-a", help="Deprecated two-category compatibility option")
    parser.add_argument("--category-b", help="Deprecated two-category compatibility option")
    parser.add_argument("--image-width", type=int, default=150)
    args = parser.parse_args()
    category_names = args.category_name or [name for name in [args.category_a, args.category_b] if name]
    validate(args.report, args.category, category_names, image_width=args.image_width)
    print(f"VALIDATION_OK: {args.report}")


if __name__ == "__main__":
    main()
