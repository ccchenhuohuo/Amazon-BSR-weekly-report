---
name: sorftime-bsr-sync
description: Sorftime API 类目 Top100 BSR 数据同步到 Doris。支持灵活的星期配置（默认周五），支持多类目同步。
version: 1.0.0
author: chenyu
tags: [电商, BSR, Sorftime, Doris, 数据同步]
platforms:
  - Codex
---

# Sorftime BSR 类目数据同步

## 安装依赖

首次使用前请安装 Python 依赖：

```bash
pip install -r .agents/skills/sorftime-bsr-sync/requirements.txt
```

## 配置说明（可选）

Skill 支持通过环境变量或 `.env` 文件自定义配置，硬编码的默认值仍然保留。

### 使用 .env 文件

```bash
cd .agents/skills/sorftime-bsr-sync
cp .env.example .env
# 编辑 .env 修改配置
```

### 支持的环境变量

| 变量名 | 说明 | 默认值 |
|-------|------|-------|
| `SORFTIME_API_KEY` | Sorftime API Key | 无，必填 |
| `DORIS_HOST` | Doris MySQL 查询主机 | 无，必填 |
| `DORIS_MYSQL_PORT` | Doris MySQL 查询端口 | `30930` |
| `DORIS_STREAM_LOAD_HOST` | Doris Stream Load 写入主机 | 默认回退到 `DORIS_HOST` |
| `DORIS_STREAM_LOAD_PORT` | Doris Stream Load HTTP 写入端口（公网） | `33060` |
| `DORIS_STREAM_LOAD_FALLBACK_HOST` | Doris Stream Load 兜底写入主机 | 空 |
| `DORIS_STREAM_LOAD_FALLBACK_PORT` | Doris Stream Load 兜底 HTTP 写入端口 | `0` |
| `DORIS_USER` | Doris 用户名 | 无，必填 |
| `DORIS_PASSWORD` | Doris 密码 | 无，必填 |
| `DORIS_DATABASE` | 数据库名 | 无，必填 |
| `DORIS_TABLE` | 表名 | 无，必填 |
| `MAX_WORKERS` | 并发线程数 | `4` |
| `START_DATE` | 开始查询日期 | `2026-01-01` |
| `TARGET_WEEKDAY` | 目标星期（0-6 或名称） | `friday` |
| `BSR_LOG_DIR` | 日志目录 | `{skill}/logs/` |

## 固定配置

| 配置项 | 值 |
|-------|-----|
| API | `https://standardapi.sorftime.com/api/CategoryRequest` POST |
| domain | `1` (US) |
| 目标表 | `<DORIS_DATABASE>.<DORIS_TABLE>` |
| 模式 | **管道模式**（零临时文件），使用 Doris Stream Load 写入 |
| 新增 | 自动重试、日期验证、并发处理、统一路径管理 |

## 幂等性

⚠️ `DUPLICATE KEY(asin, bsr_date)` 是 append-only，重复写入会翻倍！每次必须先 DELETE 再写入（已内置清理逻辑）。

## 同步所有类目（默认）

```bash
# 自动检测缺失日期 + 写入（默认周五，管道模式零临时文件）
python3 .agents/skills/sorftime-bsr-sync/scripts/sorftime_api/category/CategoryRequest/fill_missing.py

# 指定星期几同步（支持多种格式）
python3 fill_missing.py --weekday wednesday
python3 fill_missing.py --weekday 2
python3 fill_missing.py --weekday 周三

# 指定日期
python3 fill_missing.py --dates 2026-04-10

# 并发模式（推荐）
python3 fill_missing.py --parallel --max-workers 4

# 一键脚本（默认周五）
.agents/skills/sorftime-bsr-sync/scripts/bsr_sync_weekly.sh
.agents/skills/sorftime-bsr-sync/scripts/bsr_sync_weekly.sh 2026-04-10
.agents/skills/sorftime-bsr-sync/scripts/bsr_sync_weekly.sh --parallel

# 一键脚本 + 指定星期
TARGET_WEEKDAY=wednesday .agents/skills/sorftime-bsr-sync/scripts/bsr_sync_weekly.sh
```

## 星期配置说明

### 支持的星期格式

| 格式类型 | 示例 | 说明 |
|---------|------|------|
| 数字 | `0`, `1`, `2`, `3`, `4`, `5`, `6` | 0=周一, 1=周二, ..., 6=周日 |
| 中文 | `周一`, `周二`, `周三`, `周四`, `周五`, `周六`, `周日` | 仅支持"周 X"格式 |
| 英文全称 | `monday`, `tuesday`, ..., `sunday` | 不区分大小写 |
| 英文缩写 | `mon`, `tue`, `wed`, `thu`, `fri`, `sat`, `sun` | 不区分大小写 |

### 配置方式

**方式 1: 命令行参数**
```bash
python3 fill_missing.py --weekday wednesday
python3 fill_missing.py -w 2
```

**方式 2: 环境变量**
```bash
export TARGET_WEEKDAY=wednesday
python3 fill_missing.py
```

**方式 3: .env 文件**
```bash
# 在 .env 文件中添加
TARGET_WEEKDAY=wednesday
```

### Shell 脚本使用

```bash
# 使用环境变量指定星期
TARGET_WEEKDAY=monday ./bsr_sync_weekly.sh

# 使用命令行参数
./bsr_sync_weekly.sh --weekday wednesday

# 同时指定日期和星期
./bsr_sync_weekly.sh --weekday friday 2026-04-17
```

## 同步单个类目

```bash
# 同步单个类目
python3 .agents/skills/sorftime-bsr-sync/scripts/sorftime_api/category/CategoryRequest/fill_missing.py --node-id 499310

# 单类目 + 指定日期
python3 .agents/skills/sorftime-bsr-sync/scripts/sorftime_api/category/CategoryRequest/fill_missing.py --node-id 499310 --dates 2026-04-10
```

## 同步产品详情（ProductRequest）

```bash
# 获取产品详情数据
python3 .agents/skills/sorftime-bsr-sync/scripts/sorftime_api/product/ProductRequest/fetch_product.py --asins X000000001 --output products.json

# 处理数据并生成 Stream Load JSON
python3 .agents/skills/sorftime-bsr-sync/scripts/sorftime_api/product/ProductRequest/transform_product.py --date 2026-04-21 --input products.json --output stream_load.json

# 使用 Stream Load（推荐）
python3 /path/to/stream_load.py \
  --database sorftime \
  --table product_request \
  --columns "asin,query_date,update_time,listing_sales_volume_daily_trend,listing_sales_daily_trend,listing_sales_volume_month_trend,listing_sales_month_trend,rank_trend,bsr_rank_trend,price_trend,list_price_trend,asin_sales_count,one_star_ratings,two_star_ratings,three_star_ratings,four_star_ratings,five_star_ratings,product_info,property,attribute,description" \
  --data-file stream_load.json
```

## 补数 & 检测

```bash
# 检测缺失日期（不写入）
python3 .agents/skills/sorftime-bsr-sync/scripts/sorftime_api/category/CategoryRequest/fill_missing.py --check-only

# 强制重新拉取（先删后写）
python3 .agents/skills/sorftime-bsr-sync/scripts/sorftime_api/category/CategoryRequest/fill_missing.py --force --dates 2026-04-10

# 模拟运行
python3 .agents/skills/sorftime-bsr-sync/scripts/sorftime_api/category/CategoryRequest/fill_missing.py --dry-run
```

## 并发模式

```bash
# 使用 4 个线程并发同步（推荐）
python3 fill_missing.py --parallel --max-workers 4

# 使用环境变量配置并发数
export MAX_WORKERS=6
python3 fill_missing.py --parallel

# Shell 脚本使用并发
./bsr_sync_weekly.sh --parallel --max-workers 6
```

**注意**：
- `--max-workers` 建议不超过 8，避免 Sorftime API 限流
- 每个任务独立失败重试，不影响其他任务

## 类目清单

从 `references/bsr-category-list.md` 读取，包含：脚架类、支架类、灯光类共 6 个类目。

## 架构说明

### 核心模块

| 模块 | 用途 |
|-------|------|
| `utils/api_base.py` | **BaseAPIClient 抽象基类**，消除 API 调用重复代码 |
| `utils/common.py` | **通用工具函数**，safe_float/safe_int/read_json/write_json/load_env_safe |
| `utils/constants.py` | **集中常量配置**，APIConfig/BSRConfig/LogConfig |
| `utils/path_init.py` | **统一路径初始化**，解决模块导入问题 |
| `utils/path_utils.py` | **统一路径管理**，get_skill_dir/get_references_dir/get_logs_dir 等 |
| `utils/audit_log.py` | 审计日志，线程安全初始化 + **敏感信息过滤** |
| `utils/log_filter.py` | **日志敏感信息过滤**，自动过滤 API Key、密码等 |
| `utils/base_config.py` | 配置管理基类，支持 TARGET_WEEKDAY 配置 |
| `utils/date_utils.py` | 日期验证、解析、通用星期计算、星期参数解析 |
| `utils/retry.py` | 指数退避重试装饰器 |

### API 客户端

| 客户端 | 用途 |
|-------|------|
| `CategoryAPIClient` | 类目 BSR API 客户端，继承自 BaseAPIClient |
| `ProductAPIClient` | 产品详情 API 客户端，继承自 BaseAPIClient |

### 业务脚本

| 脚本 | 用途 |
|-------|------|
| `category/fetch_bsr.py` | 调用 Sorftime API，支持 `--node-id` 指定类目，自动重试 3 次 |
| `category/transform_bsr.py` | 字段映射 + bsr_rank 过滤（保留 ≤100） |
| `category/fill_missing.py` | 多类目自动检测 + 幂等写入，仅管道模式、日期验证、重试机制、星期参数配置 |
| `product/fetch_product.py` | 调用 ProductRequest API，支持多个 ASINs |
| `product/transform_product.py` | 字段映射 + Stream Load JSON 生成 |
| `backfill/workflow.py` | BSR 数据同步核心工作流 |
| `backfill/pipeline.py` | 管道处理模块，零临时文件，支持 Stream Load 写入函数 |
| `backfill/category_list.py` | 类目列表加载模块 |
| `bsr_sync_weekly.sh` | 每周一键同步，Linux 兼容，支持 --parallel 和 --weekday |

## 业务规则说明

### 数据验证规则

关于"正好 100 条"的说明：
- 每个日期、每个类目必须刚好 100 条记录。
- `transform_bsr.py` 过滤 `bsr_rank > 100` 后若不是 100 条，立即返回错误并终止。
- 写入后再次按 `bsr_date + bsr_category_node` 查库校验，若不是 100 条，任务失败并退出。
- 失败任务不会被视为成功，调用方会收到非零退出码。

### 校验 SQL

```sql
-- 按类目检查记录数
SELECT bsr_date, bsr_category_name, COUNT(*) as cnt
FROM <DORIS_DATABASE>.<DORIS_TABLE>
WHERE bsr_date = '2026-04-10'
GROUP BY bsr_date, bsr_category_name;

-- 检查所有周五的记录数是否为 100
SELECT bsr_date, COUNT(*) as cnt
FROM <DORIS_DATABASE>.<DORIS_TABLE>
WHERE bsr_date >= '2026-01-01' AND DAYOFWEEK(bsr_date) = 6
GROUP BY bsr_date ORDER BY bsr_date;

-- 检查是否有 bsr_rank > 100 的脏数据
SELECT bsr_date, bsr_category_name, COUNT(*) as cnt
FROM <DORIS_DATABASE>.<DORIS_TABLE>
WHERE bsr_rank > 100
GROUP BY bsr_date, bsr_category_name;
```

## 日志路径

- 主日志：`.agents/skills/sorftime-bsr-sync/logs/bsr_sync.log`
- 可通过 `BSR_LOG_DIR` 环境变量覆盖
- **敏感信息自动过滤**：API Key、密码等不会出现在日志中

## Doris 地址与故障排查

真实 Doris 地址由运行环境提供，不写入仓库。排查时使用本地 `.env` 中的主机和端口：

```bash
# 查询端口
nc -G 5 -vz "$DORIS_HOST" "$DORIS_MYSQL_PORT"

# Stream Load 端口
nc -G 5 -vz "$DORIS_STREAM_LOAD_HOST" "$DORIS_STREAM_LOAD_PORT"
curl -sS -m 8 -I "http://${DORIS_STREAM_LOAD_HOST}:${DORIS_STREAM_LOAD_PORT}/api/${DORIS_DATABASE}/${DORIS_TABLE}/_stream_load"
```

如果 Stream Load 地址 TCP 可达且 HTTP 返回 `405 Allow: POST`，说明服务可访问；真实写入仍需使用脚本发起 POST。

## 参考资料

- `references/table-schema.md` — 目标表结构 & API 字段对照
- `references/bsr-category-list.md` — 项目内类目 NodeId 清单
- `references/api-doc.md` — 脱敏 Sorftime API 最小参考
- `.env.example` — 环境变量配置模板
- `requirements.txt` — Python 依赖清单
