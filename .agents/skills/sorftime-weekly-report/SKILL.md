---
name: sorftime-weekly-report
description: 生成 Sorftime 周趋势监测报告
version: 1.0.0
author: chenyu
tags: [电商, BSR, Sorftime, 周报, 数据分析]
platforms:
  - Codex
---

# Sorftime 周报生成

## 目录结构

```
sorftime-weekly-report/
├── SKILL.md
├── scripts/
│   ├── generate_weekly_report.py
│   └── validate_report.py
├── agents/
│   ├── 01-overview/
│   │   ├── agent.md
│   │   ├── templates/
│   │   │   ├── 00-header.md
│   │   │   └── 01-overview.md
│   │   └── queries/
│   │       ├── top3.sql
│   │       ├── core-metrics.sql
│   │       ├── rating-distribution.sql
│   │       └── brand-efficiency.sql
│   ├── 02-categories/
│   │   ├── agent.md
│   │   ├── templates/
│   │   │   └── 02-category.md
│   │   └── queries/
│   │       ├── top10.sql
│   │       ├── rising.sql
│   │       ├── falling.sql
│   │       ├── new-entries.sql
│   │       ├── low-rating-high-sales.sql
│   │       └── rating-distribution.sql
│   └── 03-ulanzi/
│       ├── agent.md
│       ├── templates/
│       │   └── 03-ulanzi.md
│       └── queries/
│           ├── ulanzi-products.sql
│           ├── brand-efficiency.sql
│           ├── ulanzi-internal-efficiency.sql
│           └── ulanzi-summary.sql
└── references/
    ├── doris-execution.md
    ├── 04-summary.md
    └── category-mapping.md
```

## 重要数据规范

### 价格单位
数据库中 `price` 字段单位是**美分（cents）**，需要除以100转换为**美元**，并在数值前加 `$` 符号。
- 例如：数据库值 `5992.27` → 显示为 `$59.92`

### 排名字段
数据库中排名字段是 `bsr_rank`，**不是** `rank`。所有SQL查询必须使用 `bsr_rank`。

### 类目完整路径
模板中的类目路径占位符（如 `{类目A完整路径}`）需要使用 `references/category-mapping.md` 中的完整路径，而不是简称。

### ULANZI 空数据
ULANZI 在某个类目的本周 TOP100 中可能为 0 个 SKU，这是合法状态。此时报告必须明确写出“无 ULANZI 产品进入本周 TOP100”，不能当成查询失败。

## 工作流

### 0. Codex 默认生产路径

Codex 侧默认且优先使用本 skill 自带脚本生成报告，不主动启动 subagent：

```bash
python3 scripts/generate_weekly_report.py --category 支架类 --date 2026-04-22
```

数据库连接配置见 `references/doris-execution.md`。脚本会执行：
- 模板/项目内资源 preflight
- Doris 环境检查（可通过 `--check-env` 单独执行）
- 类目与日期解析
- SQL 查询
- 规则化洞察生成
- 完整性校验
- 拼接并保存报告

脚本会自动读取 skill 根目录下的本地 `.env` 文件作为 Doris 连接配置；如果 shell 中已经导出同名环境变量，则优先使用 shell 环境变量。

如果用户**明确要求并行 agent / subagent**，再使用下面的“可选 Subagent 工作流”。否则不要为了执行本 skill 主动 spawn subagent。

默认输出路径：
`{PROJECT_ROOT}/reports/{YYYYMMDD}{类目}周趋势监测报告.md`

生产 Obsidian 输出路径：
通过 `SORFTIME_REPORT_OUTPUT_DIR` 或 `--out-dir` 配置，不写入仓库。

默认覆盖策略：
- 默认等价于 `--no-overwrite`，目标文件已存在时不会静默覆盖，会写入 `_tmp/` 下的带时间戳临时报告并在 JSON 摘要中提示冲突。
- 需要覆盖历史报告时必须显式传入 `--overwrite`。
- 旧命名 `{类目}周趋势监测报告-{YYYY-MM-DD}.md` 和 `YYYYMMDD-xxx.md` 不再作为默认产物，避免双命名并存造成误判。

商品图片默认宽度：
- 默认使用 `<img ... width="150" />`。
- 需要临时对比效果时可传入 `--image-width` 参数覆盖默认值。

### 1. 解析请求

首先尝试从用户输入中解析类目和日期。

**类目识别关键词：**
- "支架" → 支架类
- "脚架" → 脚架类
- "灯光" → 灯光类

**日期识别：**
- "2026-04-22" → 直接使用
- "*周*" → 将星期解析为具体日期

**如果解析失败或不确定：**
直接向用户追问缺失信息：
- 类目：支架类、脚架类、灯光类
- 日期：支持“上周三”“上上周三”或 `YYYY-MM-DD`

### 2. 加载配置
读取 `references/category-mapping.md`，获取类目 A/B 的：
- node_id
- 完整路径（如 "Camera & Photo > Lighting & Studio > Lighting > Continuous Output Lighting"）

### 3. 可选 Subagent 工作流（人工增强/并行分析模式）

仅当用户明确要求使用 subagent、并行 agent、或人工增强分析时，在同一个 turn 中同时 spawn 所有 3 个 Subagent。每个 Subagent 自己执行自己目录下的 SQL 查询。该模式不参与默认验收主链路。

**Subagent 1 (Overview):**
```
Read `agents/01-overview/agent.md` and execute the task.

Inputs:
- Templates: `agents/01-overview/templates/00-header.md` and `01-overview.md`
- Category info: [category name, date, category A/B node_ids and full paths]
```

**Subagent 2 (Categories):**
```
Read `agents/02-categories/agent.md` and execute the task.

Inputs:
- Template: `agents/02-categories/templates/02-category.md`
- Category info: [category A/B names and node_ids, date]
```

**Subagent 3 (Ulanzi):**
```
Read `agents/03-ulanzi/agent.md` and execute the task.

Inputs:
- Template: `agents/03-ulanzi/templates/03-ulanzi.md`
- Category info: [category A/B names and node_ids, date]
```

### 4. 验收各 Subagent 输出 ✅

收到所有 Subagent 输出后，必须进行完整性检查：

#### 验收标准：
- **Overview 部分验收**：
  - ✅ 有 header 和 1.1 核心指标对比（含表格）
  - ✅ 有 1.2 核心结论
  - ✅ 没有"数据待补充"等占位文字
  - ✅ **数据量检查**：TOP3产品每个类目每个日期3条

- **Categories 部分验收**：
  - ✅ 有完整的类目 A 分析（第二章，含 5 个子节）
  - ✅ 有完整的类目 B 分析（第三章，含 5 个子节）
  - ✅ 所有表格都有实际数据
  - ✅ 没有"数据待补充"等占位文字
  - ✅ **数据量检查**：TOP10有10条，上升最多10条，下降最多3条，新上榜全量，低分高销最多10条（少于10条时全量呈现），评分分布3条

- **Ulanzi 部分验收**：
  - ✅ 有 4.1 周度产品线明细（含两个类目的表格）
  - ✅ 有 4.2 品牌销售效率全面对比分析（含完整子节）
  - ✅ 没有"数据待补充"等占位文字
  - ✅ **数据量检查**：ULANZI产品全量呈现无遗漏；0 SKU 时明确写出无产品进入 TOP100；品牌效率15条

#### 重试机制：
- 如果某部分不达标，重新调用对应的 Subagent
- 同一 Subagent 最多重试 **3 次**
- 3 次都失败则报告问题给用户

### 5. 生成第 5 章
默认脚本基于查询结果规则化生成第 5 章。Subagent 工作流中，主 Agent 可参考 `references/04-summary.md` 生成总结建议。

### 6. 拼接保存
按顺序拼接所有输出直接保存到 `--out` 指定路径，或 `--out-dir` / `SORFTIME_REPORT_OUTPUT_DIR` 下的 `{YYYYMMDD}{类目}周趋势监测报告.md`。

## 升级后验收命令

### 静态检查

```bash
python3 scripts/generate_weekly_report.py --preflight
python3 scripts/generate_weekly_report.py --check-env
```

### 三类目 dry-run

```bash
python3 scripts/generate_weekly_report.py --category 支架类 --date 2026-04-22 --dry-run
python3 scripts/generate_weekly_report.py --category 脚架类 --date 2026-04-22 --dry-run
python3 scripts/generate_weekly_report.py --category 灯光类 --date 2026-04-22 --dry-run
```

### 真实落盘与校验

```bash
python3 scripts/generate_weekly_report.py --category 灯光类 --date 2026-04-22 --overwrite
python3 scripts/validate_report.py "reports/20260422灯光类周趋势监测报告.md" --category 灯光类 --category-a "Continuous Output Lighting" --category-b "Selfie Lights"
```

### 异常场景

```bash
env -u DORIS_USER -u DORIS_PASSWORD python3 scripts/generate_weekly_report.py --check-env
python3 scripts/generate_weekly_report.py --category 不存在类 --date 2026-04-22 --dry-run
python3 scripts/generate_weekly_report.py --category 灯光类 --date bad-date --dry-run
```

## 注意事项

- 数据表格必须严格按 SQL 结果顺序输出，不重新排序/筛选数据
- 洞察文字必须由 SQL 结果规则化推导（销量变化、均价变化、TOP品牌、最大涨跌、ULANZI状态等），不要自由发挥
- **价格必须除以100并加$前缀**（数据库单位是美分）
- **排名字段用 bsr_rank，不是 rank**
- **必须进行验收，缺失数据要重试**
