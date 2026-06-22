# Doris SQL 执行约定

Codex 侧默认生产路径是 `scripts/generate_weekly_report.py`，通过 MySQL 协议连接 Doris、查询数据、渲染报告、运行校验并落盘。

## 环境变量

脚本启动时会自动读取 skill 根目录下的 `.env` 文件，并注入尚未在 shell 中设置的变量。shell 中已存在的同名环境变量优先级更高。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DORIS_HOST` | 无 | Doris MySQL FE 地址，必填 |
| `DORIS_MYSQL_PORT` | `30930` | Doris MySQL 端口 |
| `DORIS_USER` | 无 | 必填 |
| `DORIS_PASSWORD` | 无 | 必填 |
| `DORIS_DATABASE` | 无 | 必填，报告查询库名 |
| `DORIS_TABLE` | 无 | 必填，报告查询表名 |

可先检查环境变量：

```bash
python3 scripts/generate_weekly_report.py --check-env
```

示例：先在忽略的本地 `.env` 或进程环境中配置 Doris 连接，再运行：

```bash
python3 scripts/generate_weekly_report.py --category 支架类 --date 2026-04-22 --dry-run
```

真实落盘时显式选择覆盖策略。默认是 `--no-overwrite`，若目标文件已存在，会把报告写入 `_tmp/` 临时文件并在结构化摘要中提示冲突；需要覆盖历史报告时使用：

```bash
python3 scripts/generate_weekly_report.py --category 支架类 --date 2026-04-22 --overwrite
```

## 执行规则

- 所有 SQL 文件继续保存在各 agent 的 `queries/` 目录。
- 脚本只替换 `{table}`、`{node_id}`、`{start_date}`、`{end_date}`、`{date}`、`{category_a_node}`、`{category_b_node}`。
- SQL 查询中价格字段若已 `price / 100`，渲染时只加 `$` 前缀，不再次换算。
- 查询失败、模板缺失、数据量不足时，脚本应直接失败并打印具体原因。
- ULANZI 在某类目 TOP100 中为 0 个 SKU 是合法状态，报告中应明确写出“无 ULANZI 产品进入本周 TOP100”。
- 默认输出文件命名为 `{YYYYMMDD}{类目}周趋势监测报告.md`。旧命名 `{类目}周趋势监测报告-{YYYY-MM-DD}.md` 和 `YYYYMMDD-xxx.md` 不再作为默认产物。
- 商品图片默认输出为 `<img ... width="150" />`。需要临时对比图片尺寸时，可在生成命令中传入 `--image-width`。
- 每次成功执行都会输出 JSON 摘要，包含类目、日期、node_id、输出路径、关键查询行数和校验结果。
