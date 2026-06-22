---
name: sorftime-report-base-sync
description: 将 Sorftime 周趋势监测报告 Markdown/Obsidian 笔记中的带图商品数据表同步到飞书多维表格 Base。凡是用户提到“Sorftime 周报数据迁移到多维表格”“笔记表格同步到 Base”“母表/子表拆分”“异动数据/低分高销/本品数据同步”“报告数据表替换为多维表格数据源”时都应使用本 skill；即使用户没有明确说 skill，也要优先触发。本 skill 只负责 Base 数据同步与校验，不负责生成 Markdown 报告正文，也不负责通过飞书文档 API 自动插入“关联已有多维表格”块。
version: 1.0.0
author: chenyu
tags: [Sorftime, 飞书多维表格, Base, 周报, 数据迁移, Obsidian]
platforms:
  - Codex
---

# Sorftime 报告数据表同步到飞书 Base

本 skill 把 Sorftime 周趋势监测报告中的**带商品图片的数据表**同步到飞书多维表格。它是 `sorftime-weekly-report` 的后续数据落表流程：先由周报 skill 生成 Markdown 笔记，再由本 skill 解析笔记表格并写入 Base。

涉及飞书 Base 操作时，必须同时使用 `lark-base` skill；涉及飞书文档读取/检查时使用 `lark-doc`，但不要尝试用文档 API 创建“关联已有多维表格”块。

## 私密资源配置

- 模板 Base token 通过 `FEISHU_TEMPLATE_BASE_TOKEN` 或 `--template-base-token <TEMPLATE_BASE_TOKEN>` 提供。
- 目标 Base token 通过 `--base-token <BASE_TOKEN>` 或 runner 的 `--base-token 类目=<BASE_TOKEN>` 提供。
- 战略报告文档 URL 属于团队私密运行配置，不写入仓库。

## 输入与边界

优先从用户输入中确认：

- 本地 Markdown 报告路径，或类目 + 报告日期以推导默认路径。
- 目标 Base URL/token；如果用户只给模板 Base，则先复制模板并把新 Base 作为目标。
- 是否要先清空目标表内旧数据。默认同步同一份报告时允许删除目标表已有数据后重写；不确定时先列出记录数并请用户确认。

默认报告路径由周报生成脚本或 runner 决定。公开仓库默认使用项目 `reports/`，生产环境可通过 `SORFTIME_REPORT_OUTPUT_DIR` 指向 Obsidian 历史周报目录：

```text
${SORFTIME_REPORT_OUTPUT_DIR}/{YYYYMMDD}{类目}周趋势监测报告.md
```

不要迁移不带商品图片的数据表；不要写入 `附件` 字段；不要为 “无 ULANZI 产品进入本周 TOP100” 这类占位行创建记录。

## 可执行接口

优先使用本 skill 自带脚本执行同步，不要手工拼每张表的 payload：

```bash
python3 .agents/skills/sorftime-report-base-sync/scripts/sync_report_to_base.py \
  --report "/path/to/20260513支架类周趋势监测报告.md" \
  --base-token <BASE_TOKEN> \
  --template-base-token <TEMPLATE_BASE_TOKEN> \
  --category 支架类 \
  --date 2026-05-13 \
  --overwrite \
  --rename-folders
```

参数说明：

- `--report`：本地 Markdown/Obsidian 报告路径。
- `--base-token`：目标 Base token。若用户只给模板 Base，应先用 `lark-cli base +base-copy --without-content` 复制空结构，再把新 token 传给脚本。
- `--category`：`灯光类`、`支架类`、`脚架类`；可省略，脚本会从文件名推断。
- `--date`：报告日期，格式 `YYYY-MM-DD`。
- `--previous-date`：上周日期，默认 `--date - 7 days`。
- `--overwrite`：先删除 15 张目标表已有记录，再重写。重跑或修复失败同步时必须加。
- `--prepare-only`：只准备 Base 结构、单选选项和视图字段顺序，不解析报告、不写入记录；适合在报告文件尚未生成时先创建并检查本周目标 Base。
- `--dry-run`：只解析、校验并生成 payload，不写入 Base。
- `--template-base-token`：用于校验并强制同步字段/视图列顺序的模板 Base；也可通过 `FEISHU_TEMPLATE_BASE_TOKEN` 提供。
- `--rename-folders`：用 `lark-cli base +base-block-list/+base-block-rename` 将左侧 `{类目1}`、`{类目2}` 根级 folder 改成当前类目名称。
- `--lark-cli-timeout-seconds`：每个 `lark-cli` 子命令超时时间，默认 240 秒，适合代理/VPS 出口导致飞书响应较慢的环境。

脚本职责：

- 解析 12 个目标带图表格，跳过无真实 ASIN/图片的占位行。
- 同步三张母表和 12 张子表。
- 自动把本品数据排名字段改为 `{上周日期}排名` 与 `{报告日期}排名`。
- 自动补齐 `类目`、`数据类型`、`是否新品` 单选字段选项；飞书 API 实测不会为单选字段稳定自动新增选项。
- 从模板 Base 读取每张表 grid 视图集合与 `visible_fields` 顺序，要求目标 Base 的视图集合和字段顺序与模板完全一致；模板没有的筛选视图不得由脚本创建。
- 读取 Base block 目录，校验根级 folder 和 15 张表的 `parent_id` 层级。
- 在传入 `--rename-folders` 时用 CLI 重命名 `{类目1}` / `{类目2}`，并回读确认。
- 写入后回读目标 Base，校验实际记录数、重复键、商品图片 URL 和 ASIN 链接。
- 对 `lark-cli` 偶发网络 timeout 做有限重试。
- 在 `logs/sorftime-report-base-sync/` 下保存本次解析摘要和批量写入 payload。

## 类目映射

| 报告类目 | 章节二类目 | 章节三类目 |
| --- | --- | --- |
| 灯光类 | Continuous Output Lighting | Selfie Lights |
| 支架类 | Cradles | Grips |
| 脚架类 | Complete Tripods | Tripods |

## 表分组/文件夹名称

模板 Base 左侧表列表里有两个表分组/文件夹占位名：`{类目1}`、`{类目2}`。这些名称不是数据表名，`lark-cli base +table-list` 不返回它们；新版 `lark-cli base +base-block-list` 会以 `type=folder` 返回这些资源，并可用 `+base-block-rename` 重命名。

复制模板并完成脚本同步后，生产自动化应优先用 CLI 将它们改成类目映射中的章节二/章节三类目：

| 报告类目 | `{类目1}` 应改为 | `{类目2}` 应改为 |
| --- | --- | --- |
| 灯光类 | Continuous Output Lighting | Selfie Lights |
| 支架类 | Cradles | Grips |
| 脚架类 | Complete Tripods | Tripods |

操作建议：

- 默认传入 `--rename-folders`，脚本自动执行 `+base-block-list`、`+base-block-rename` 和回读校验。
- 需要 user 授权 scope：`base:block:update`。缺 scope 时脚本输出 `folder_rename.status=blocked_missing_scope`，并提示 `lark-cli auth login --scope "base:block:update"`。
- Chrome/Computer Use 只作为 CLI 不可用或人工排障 fallback。不要删除/重建文件夹，也不要移动表，除非用户明确要求。

## 目标 Base 表结构

模板与目标 Base 应包含 15 张表：

- 母表：
  - `异动数据`
  - `低分高销数据`
  - `本品数据`
- 章节子表：
  - `2.1.1`, `2.2`, `2.3`, `2.4`, `2.5.2`
  - `3.1.1`, `3.2`, `3.3`, `3.4`, `3.5.2`
  - `4.1.1`, `4.1.2`

写入前必须执行：

```bash
lark-cli base +table-list --base-token <BASE_TOKEN> --as user
lark-cli base +field-list --base-token <BASE_TOKEN> --table-id <TABLE_ID> --as user
```

如果字段缺失或字段名不一致，先停止并报告差异；不要猜字段。

## 需要解析的报告表格

### 异动数据

| 章节 | 子表 | 数据类型 | 类目 |
| --- | --- | --- | --- |
| `2.1.1` | `2.1.1` | TOP10产品 | 章节二类目 |
| `2.2` | `2.2` | 强势上升产品 | 章节二类目 |
| `2.3` | `2.3` | 强势下降产品 | 章节二类目 |
| `2.4` | `2.4` | 新上榜产品 | 章节二类目 |
| `3.1.1` | `3.1.1` | TOP10产品 | 章节三类目 |
| `3.2` | `3.2` | 强势上升产品 | 章节三类目 |
| `3.3` | `3.3` | 强势下降产品 | 章节三类目 |
| `3.4` | `3.4` | 新上榜产品 | 章节三类目 |

这些记录同时写入 `异动数据` 母表和对应章节子表。

### 低分高销数据

| 章节 | 子表 | 母表 | 类目 |
| --- | --- | --- | --- |
| `2.5.2` | `2.5.2` | 低分高销数据 | 章节二类目 |
| `3.5.2` | `3.5.2` | 低分高销数据 | 章节三类目 |

### 本品数据

| 章节 | 子表 | 母表 | 类目 |
| --- | --- | --- | --- |
| `4.1.1` | `4.1.1` | 本品数据 | 章节二类目 |
| `4.1.2` | `4.1.2` | 本品数据 | 章节三类目 |

ULANZI 本品章节若只有空状态说明、没有真实商品 ASIN，则写入 0 条。

## 字段映射与清洗规则

### 通用字段

- `报告日期`：固定写为报告日期 `YYYY-MM-DD 00:00:00`。
- `类目`：章节二/三对应类目名。
- `ASIN`：写成 Markdown 链接：`[X000000001](https://www.amazon.com/dp/X000000001)`。
- `商品图片`：从 `<img src="...">` 提取纯 `https://...` 图片 URL，不写 HTML。
- `价格`：去掉 `$` 后写数字。
- `月销`：去掉千分位逗号后写数字。
- `评分`、`上架天数`、`上周排名`、`排名变化`：写数字；不要保留 `+`、逗号或单位。
- `品牌`、`产品名称`：按笔记表格原文写入。

### 异动数据字段

- TOP10：
  - `排名 <- 排名`
  - `上周排名 <- 上周排名`
  - `排名变化 <- 排名变化`
  - `是否新品 <- null`
- 强势上升/强势下降：
  - `排名 <- 本周排名`
  - `上周排名 <- 上周排名`
  - `排名变化 <- 排名变化`
  - `是否新品 <- null`
- 新上榜：
  - `排名 <- 本周排名`
  - `上周排名 <- null`
  - `排名变化 <- null`
  - `是否新品 <- 是/否`

### 低分高销数据

按目标表字段名与笔记表头同名映射，至少保留：报告日期、类目、排名、品牌、产品名称、ASIN、价格、评分、月销、上架天数、商品图片。若目标表还有额外同名字段，按同名写入。

### 本品数据

按目标表字段名与笔记表头同名映射。常见字段包括：

- `报告日期`
- `类目`
- `2026-04-29排名` / `上周排名`
- `2026-05-06排名` / `本周排名`
- `排名变化`
- `品牌`
- `产品名称`
- `ASIN`
- `价格`
- `评分`
- `月销`
- `上架天数`
- `商品图片`

如果模板中本品排名字段仍是占位名称，先把字段名改成对应报告周；字段顺序校验会把模板视图中的占位字段适配为对应日期字段：

- `{上周日期}排名`
- `{报告日期}排名`

避免对字段做 no-op 更新；飞书可能返回 `800070003 no operation produced`。

## 字段顺序强约束

字段顺序以模板 Base 各 grid 视图的 `visible_fields` 为准，不以 `field-list` 返回顺序为准。模板没有筛选视图，所以同步脚本不得创建筛选视图；如果目标 Base 已经多出模板不存在的视图，应停止并报告差异，使用重新复制模板或人工清理后的 Base 重跑。

执行同步时脚本必须：

1. 读取模板 Base 15 张表的 grid 视图集合和可见字段列表。
2. 对目标 Base 做字段结构严格比对；除本品数据日期排名字段可由模板占位名适配为本周/上周日期字段外，不允许多字段、少字段或字段名不一致。
3. 对目标 Base 做视图集合严格比对；不允许存在模板没有的额外 grid 视图。
4. 将模板顺序写回目标 Base 中与模板同名的 grid 视图。
5. 在最终 JSON 的 `field_order.changed_views` 中记录本次被修正的视图；为空表示已与模板一致。

排查字段顺序问题时优先执行：

```bash
lark-cli base +view-list --base-token <BASE_TOKEN> --table-id <TABLE_ID> --as user
lark-cli base +view-get-visible-fields --base-token <BASE_TOKEN> --table-id <TABLE_ID> --view-id <VIEW_ID> --as user
```

不要只看 `field-list`。`field-list` 返回的是字段对象列表，不能代表某个视图在 UI 中的列顺序。

## 推荐执行流程

1. 解析输入：报告路径、报告日期、报告类目、目标 Base token。
2. 读取目标 Base 表清单和字段结构，确认 15 张表存在。
3. 解析 Markdown 中目标 12 个带图表格，生成三类记录集合：
   - `movement_records`
   - `low_rating_high_sales_records`
   - `ulanzi_records`
4. 同步母表：
   - 删除目标表已有本报告记录或清空目标表。
   - 批量写入 `异动数据`、`低分高销数据`、`本品数据`。
5. 同步 12 张子表：
   - 子表内容必须来自对应母表的过滤结果，不要重新二次解析造成差异。
   - 每张子表先删除旧记录，再批量写入。
6. 校验并同步 Base 视图结构：
   - 目标 Base 的 grid 视图名称集合必须与模板一致。
   - 不创建模板中不存在的筛选视图。
   - 每个同名 grid 视图的可见字段顺序必须与模板一致。
7. 用 CLI 重命名并校验左侧表分组：
   - `{类目1}` -> 章节二类目。
   - `{类目2}` -> 章节三类目。
8. 回读目标 Base，向用户汇报记录数、重复检查、图片/ASIN 检查、block layout 和表分组重命名状态。

`record-batch-create` 单次不要超过 200 条。CLI 的 `@file` payload 建议放在当前工作目录或项目 `logs/` 下；避免使用绝对 `/tmp/...` 路径，历史上容易被 CLI 拒绝。

覆盖写入注意事项：

- `record-list --format json` 的行数据在 `data.data`，记录 ID 在 `data.record_id_list`；不要按 `records/items` 解析，否则会误判为空表，导致 `--overwrite` 清表失败。
- 如果某次同步在中途失败，重新执行时必须带 `--overwrite`，并读回母表/子表记录数确认没有残留重复。
- `--without-content` 复制模板后仍需补齐单选字段选项；模板选项可能不完整或为空。

## 校验标准

每次同步后必须输出：

- 母表记录数：
  - `异动数据`
  - `低分高销数据`
  - `本品数据`
- 子表记录数：
  - 12 张子表逐一列出。
- 一致性：
  - 子表记录数 = 母表按 `类目/数据类型` 或 `类目` 过滤后的数量。
  - 子表 ASIN/产品集合 = 母表对应过滤集合。
- 重复检查：
  - `异动数据`：按 `ASIN + 类目 + 数据类型` 检查重复。
  - `低分高销数据`：按 `ASIN + 类目` 检查重复。
  - `本品数据`：按 `ASIN + 类目` 检查重复。
- 图片检查：
  - `商品图片` 全部为 `https://...`，不包含 `<img`。
- ASIN 检查：
  - ASIN 必须是 Markdown Amazon 链接。
- 字段顺序检查：
  - `field_order.changed_views` 已输出；必要时抽查 `view-get-visible-fields`，确认目标视图顺序与模板默认 `表格` 视图完全一致。
- 表分组/文件夹名称检查：
  - 左侧表列表不再出现 `{类目1}`、`{类目2}`。
  - 两个分组名必须分别等于当前报告类目的章节二类目、章节三类目。
  - 该项优先通过 `folder_rename` 和 `block_layout` 的 CLI 回读结果验收。
  - 若缺少 `base:block:update`，明确标为阻塞；Chrome/Computer Use 只作为人工 fallback。
- 空值检查：
  - 新上榜记录的 `上周排名` 和 `排名变化` 为空。
  - 非新上榜记录的 `是否新品` 为空。

如果发现重复，先列出重复 record_id 和业务键，让用户确认后再删除；不要静默删母表。

## 战略报告文档处理限制

会议用战略报告文档的文字部分应与笔记一致，但表格可以由用户手工替换为“关联已有多维表格”。

已验证限制：

- `docs +update` 写入 `<base_refer>` 可能返回 success，但实际不会创建关联多维表格块。
- 原生 Docx `bitable` block API 只能创建空 Bitable，不能稳定绑定已有 Base 子表。
- 自动 UI 操作容易误触 ASIN/Amazon 链接，不应作为默认自动化方案。

因此本 skill 只负责把 Base 数据同步完整，并输出人工插入清单：

- 每个战略报告文档要插入的 12 张子表。
- 每张子表对应的 Base token、table_id、view_id。
- 提醒用户手工在飞书文档中插入“关联已有多维表格”。

不要把无法自动完成的文档插入伪装成已完成。

## 常用命令片段

```bash
# 表清单
lark-cli base +table-list --base-token <BASE_TOKEN> --as user

# 字段结构
lark-cli base +field-list --base-token <BASE_TOKEN> --table-id <TABLE_ID> --as user

# 记录读取
lark-cli base +record-list --base-token <BASE_TOKEN> --table-id <TABLE_ID> --limit 200 --format json --as user

# 批量创建
lark-cli base +record-batch-create --base-token <BASE_TOKEN> --table-id <TABLE_ID> --json @payload.json --as user

# Base 左侧资源目录
lark-cli base +base-block-list --base-token <BASE_TOKEN> --type folder --as user
lark-cli base +base-block-rename --base-token <BASE_TOKEN> --block-id <FOLDER_BLOCK_ID> --name "New Folder Name" --as user

# 删除记录，高风险写操作，只有用户已明确允许清空/重写时才加 --yes
lark-cli base +record-delete --base-token <BASE_TOKEN> --table-id <TABLE_ID> --record-id <REC_ID> --yes --as user
```

## 成功输出格式

最终回复应简洁列出：

- 同步的报告、日期、类目、Base。
- 三张母表写入记录数。
- 12 张子表写入记录数。
- 重复检查结果。
- 字段顺序检查结果，尤其是 `field_order.changed_views` 是否为空或列出已修正视图。
- 左侧表分组 `{类目1}`/`{类目2}` 的 CLI 重命名状态，以及 `block_layout` 校验状态。
- 需要用户手工插入战略报告文档的 Base 子表清单是否已准备好。

如果没有完成写入或没有完成文档替换，要明确说明原因和当前恢复状态。
