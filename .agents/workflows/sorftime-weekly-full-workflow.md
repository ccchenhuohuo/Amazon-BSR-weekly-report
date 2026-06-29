# Sorftime 亚马逊战略周报每周自动化工作流

## 调度

- 执行时间：每周五 17:00，按 Asia/Shanghai 时间理解。
- Codex cron RRULE：`FREQ=WEEKLY;BYDAY=FR;BYHOUR=17;BYMINUTE=0;BYSECOND=0`。
- 报告日期：取运行时最近一个已经结束的周三。周五 17:00 运行时，通常使用本周三。
- 执行范围：`灯光类`、`支架类`、`脚架类` 三个报告类目。
- 日志位置：项目 `logs/` 或各 skill-local `logs/`，不要把运行产物写到 `.agents/skills` 根目录。

## 推荐入口

优先使用项目级 runner 串联三个 skill：

```bash
.agents/workflows/run_sorftime_weekly_workflow.py \
  --date <report_date>
```

只做 dry-run 验证时：

```bash
.agents/workflows/run_sorftime_weekly_workflow.py \
  --date <report_date> \
  --dry-run
```

生产前只读门控：

```bash
.agents/workflows/run_sorftime_weekly_workflow.py --preflight
```

runner 只负责编排日期、顺序、Base 复制、命令执行和摘要汇总；BSR 同步、周报生成、Base 同步的业务逻辑仍由各自 skill 维护。生产运行时，runner 会在缺少某类目 Base token 时使用 `FEISHU_TEMPLATE_BASE_TOKEN` 或 `--template-base-token <BASE_TOKEN>` 指向的模板 Base 复制结构，复制后的新 token 只在进程内继续用于该类目的 Base sync。`--base-token 类目=<token>` 只作为重跑或排障时的显式覆盖。Base sync 成功后，runner 会在对应 Base 左侧栏新增一份 docx 文档，并把本类目的周报正文写入该 Base 内文档。

dry-run 不会真实复制 Base，因此只能校验复制请求和命名是否正确；没有真实新 token 时，后续 Base sync 会跳过。若要完整校验 Base sync，可临时传入已有测试 Base token。dry-run 默认不发送飞书完成通知；需要测试通知时显式加 `--notify-dry-run`。

## Skill 串联顺序

### 1. BSR 数据同步

使用 `sorftime-bsr-sync`。

目标：

- 对三类报告涉及的所有 Sorftime 类目同步 report_date 的 Top100 BSR 数据到 Doris。
- 必须先删后写，避免 `DUPLICATE KEY(asin, bsr_date)` append-only 重复写入。
- 默认同步周三数据，可通过 `--weekday wednesday`、`TARGET_WEEKDAY=wednesday` 或显式 `--dates <report_date>` 实现。

建议命令形态：

```bash
TARGET_WEEKDAY=wednesday .agents/skills/sorftime-bsr-sync/scripts/bsr_sync_weekly.sh --parallel --max-workers 4
```

验收：

- report_date 每个目标类目有 Top100 数据。
- 失败类目必须重试或在最终汇报中列出。
- 不允许在未确认清理旧数据的情况下重复追加。

### 2. 三类目周报生成

使用 `sorftime-weekly-report`。

目标：

- 分别生成 `灯光类`、`支架类`、`脚架类` 的 Markdown 周趋势监测报告。
- 默认输出到项目 `reports/` 目录；生产环境可通过 `SORFTIME_REPORT_OUTPUT_DIR` 或 runner 的 `--report-dir` 指向 Obsidian 历史周报目录：

```text
${SORFTIME_REPORT_OUTPUT_DIR}/{YYYYMMDD}{类目}周趋势监测报告.md
```

建议命令形态：

```bash
python3 .agents/skills/sorftime-weekly-report/scripts/generate_weekly_report.py --category 灯光类 --date <report_date> --overwrite
python3 .agents/skills/sorftime-weekly-report/scripts/generate_weekly_report.py --category 支架类 --date <report_date> --overwrite
python3 .agents/skills/sorftime-weekly-report/scripts/generate_weekly_report.py --category 脚架类 --date <report_date> --overwrite
```

验收：

- 运行 `validate_report.py` 校验三份报告。
- 价格必须按美分转美元。
- SQL 排名字段必须使用 `bsr_rank`。
- 不允许保留“数据待补充”等占位内容。
- ULANZI 0 SKU 是合法状态，但报告必须明确写出无产品进入 TOP100。

### 3. 报告数据同步到飞书 Base

使用 `sorftime-report-base-sync`，涉及 Base 操作时同时使用 `lark-base`。

目标：

- 为三份报告复制或准备目标 Base。
- 将报告中的带图商品表同步到三张母表和 12 张子表。
- 校验目标 Base 的 grid 视图集合与字段顺序完全沿用模板；模板没有筛选视图，因此脚本不得创建筛选视图。

建议命令形态：

```bash
python3 .agents/skills/sorftime-report-base-sync/scripts/sync_report_to_base.py \
  --report "/path/to/{YYYYMMDD}灯光类周趋势监测报告.md" \
  --base-token <BASE_TOKEN> \
  --template-base-token <TEMPLATE_BASE_TOKEN> \
  --category 灯光类 \
  --date <report_date> \
  --overwrite \
  --rename-folders
```

验收：

- 三张母表记录数正确：`异动数据`、`低分高销数据`、`本品数据`。
- 12 张子表记录数与母表过滤结果一致。
- 按业务键检查无重复。
- `商品图片` 为纯 `https://...` URL，不包含 `<img`。
- ASIN 是 Markdown Amazon 链接。
- 新上榜记录的 `上周排名` 和 `排名变化` 为空，非新上榜记录的 `是否新品` 为空。
- `field_order.changed_views` 已汇总本次修正过的视图；抽查目标视图的 `view-get-visible-fields`，应与模板同名视图顺序一致；目标 Base 不应出现模板没有的额外筛选视图。
- `folder_rename` 与 `block_layout` 已输出，左侧 `{类目1}` / `{类目2}` 分组通过 CLI 改名并回读校验；若缺 `base:block:update` scope，必须标为阻塞。
- `server_verification` 已回读目标 Base，记录数、重复检查、图片 URL 和 ASIN 链接均通过。

### 4. Base 左侧分组名称 CLI 处理

新版 `lark-cli base +base-block-list/+base-block-rename` 已支持读取和重命名飞书 Base 左侧表分组/文件夹。生产自动化默认由 Base sync 脚本用 CLI 完成，不再依赖 Chrome/Computer Use 页面操作。

必须把根级 folder 分组改为：

| 报告类目 | `{类目1}` 应改为 | `{类目2}` 应改为 |
| --- | --- | --- |
| 灯光类 | Continuous Output Lighting | Selfie Lights |
| 支架类 | Cradles | Grips |
| 脚架类 | Complete Tripods | Tripods |

验收：

- `lark-cli base +base-block-list --type folder` 回读不再出现 `{类目1}`、`{类目2}`。
- `block_layout.table_parent_checks` 确认 12 张章节子表仍在正确 folder 下，三张母表仍在根级。
- 若 `+base-block-rename --dry-run` 返回缺少 `base:block:update`，最终汇报必须明确标为阻塞，并提示授权命令。
- 不要删除或重建文件夹，不要移动表，除非用户明确要求。Chrome/Computer Use 只作为 CLI 不可用或人工排障时的 fallback。

### 5. Base 左侧栏内周报文档

runner 会在每个目标 Base 左侧栏创建 docx 类型块：

```bash
lark-cli base +base-block-create --base-token <BASE_TOKEN> --type docx --name <YYYYMMDD类目周趋势监测报告> --as user
```

生产 runner 默认先读取本地 `state/publications.json`。若对应
`report_date + 类目` 已有 Base/docx token，则先通过
`base +base-block-list --type docx` 校验同名文档仍在 Base 左侧栏中，确认后
复用并更新；若登记的 docx 已 stale，按同名查找结果复用或重新创建。只有显式
`--force-new-publication` 才跳过注册表并创建新的 Base/docx。

随后通过 `lark-cli docs +update --api-version v2 --command overwrite --doc-format markdown` 写入周报正文。写入前必须处理两类内容：

- 去掉 Markdown YAML front matter，避免飞书文档正文开头出现元数据。
- 把远程 `<img ...>` 标签替换为 `图片见 Base 数据表`，避免文档导入图片超时或部分写入；商品图片以 Base 表格为准。

验收：

- Base 左侧栏能看到对应类目的周报文档。
- 云文档正文从标题和第一节开始，不得从第三节或中间章节开始。
- 文档链接和 Base 链接都应出现在最终通知中。

### 6. 飞书完成通知

runner 结束后会发送一条飞书 markdown 消息。收件人通过本地 `.env` 或环境变量配置：

```bash
FEISHU_NOTIFY_USER_ID=ou_xxx
# 或
FEISHU_NOTIFY_CHAT_ID=oc_xxx
FEISHU_NOTIFY_AS=bot
FEISHU_REQUIRE_NOTIFY=1
```

`FEISHU_NOTIFY_CHAT_ID` 和 `FEISHU_NOTIFY_USER_ID` 同时存在时优先发到群聊。生产环境应设置 `FEISHU_REQUIRE_NOTIFY=1`，这样未配置收件人或通知未确认投递都会让 `notify:feishu` 标记为 failed，并使 workflow 非零退出。dry-run 默认不发送通知；显式加 `--notify-dry-run` 时才会测试通知链路。
旧变量 `LARK_REPORT_USER_ID` / `LARK_REPORT_CHAT_ID` 仍作为 fallback 兼容，但不覆盖新的 `FEISHU_NOTIFY_*` 配置。

成功模板固定为：

```text
【Amazon BSR 战略周报】已完成

报告日期：{report_date}
运行结果：{success_count}/3 类目成功
完成时间：{finished_at}

链接：
灯光类：[多维表格]({lighting_base_url}) ｜ [周报文档]({lighting_doc_url})
支架类：[多维表格]({mount_base_url}) ｜ [周报文档]({mount_doc_url})
脚架类：[多维表格]({tripod_base_url}) ｜ [周报文档]({tripod_doc_url})

说明：周报文档已新增到对应多维表格左侧栏；商品图片见 Base 数据表。
```

异常模板固定为：

```text
【Amazon BSR 战略周报】运行异常

报告日期：{report_date}
运行结果：{success_count}/3 类目成功
失败环节：{failed_steps}
完成时间：{finished_at}

已生成链接：
{generated_links}

排查日志：{run_report_path}
```

## 最终汇报

每次自动化结束后，最终回复至少包含：

- report_date。
- 三份 Markdown 报告路径。
- 三份 Base 准备状态；本地日志只记录 token 是否存在，不记录真实 token。
- 三张母表和 12 张子表记录数。
- 重复检查、图片检查、ASIN 检查结果。
- Base 左侧分组 CLI 重命名状态、block layout 校验状态和缺 scope 阻塞信息。
- Base 左侧栏内周报文档创建/写入状态。
- 飞书完成通知发送状态；若未配置收件人，明确标记为 skipped。

## 当前限制与迭代项

- Codex 定时任务必须通过 `automation_update` 注册。不要手写 `~/.codex/automations` 配置作为替代；如果工具返回 `No handler registered for tool: automation_update`，说明当前 Codex App 会话没有挂载自动化 handler，需要在 App/工具层恢复后再注册。
- Base 复制/目标 token 准备仍是链路中最需要显式记录的步骤。自动化执行时必须在最终汇报中列出每个类目对应的 Base 准备状态；真实 token 不写入仓库、summary 或 run-report。
- `state/publications.json` 是本机敏感运行状态，必须保持 gitignored 和 `600` 权限。summary、run-report、cron log 只能记录 token/link 是否存在或脱敏后的值。
- cron 应调用 `.agents/workflows/run_sorftime_weekly_cron.sh`，不要直接在 crontab 写裸 `flock -n`。wrapper 会在锁占用时写入 `lock busy, skipped`，并收紧 `logs/cron` 与 `cron.log` 权限。
- 周五生产 runner 不默认传 `--force` 给 BSR 同步。已有完整 100 条的 Doris date/category 会跳过；显式 force 刷新时必须保留旧数据备份和失败恢复。
- 周报 Markdown 必须先写同目录临时文件并 validate，通过后再原子替换正式路径。
- Base sync 的 `--overwrite` 会在清表前写 `snapshots/`，写入或回读失败时必须尝试恢复，并在 JSON 里输出 `overwrite_recovery`。
- Base 左侧分组名已改为优先 CLI 处理；需要 `lark-cli >= 1.0.56` 和 `base:block:update` 授权。cron 环境应在 `.env` 中使用绝对 `LARK_CLI_BIN=/usr/local/bin/lark-cli`，并设置可访问授权缓存的 `LARKSUITE_CLI_DATA_DIR`。
- 新增 Base 内文档和通知链路依赖：Base/Doc 操作用 user 身份，至少需要 `base:block:create`、`base:block:read`、`base:block:update`、`docx:document:write_only`；通知默认用 bot 身份，需要 `im:message`，若改用 user 身份则需要对应 user 发送 scope。授权失效时先用 `lark-cli auth status --json` 和 `lark-cli auth login --scope <scope> --no-wait --json` 处理。
- 已新增 project-level runner：`.agents/workflows/run_sorftime_weekly_workflow.py`。runner 在生产运行时会优先复用 `state/publications.json` 里的 Base/docx token；缺失时才从模板 Base 复制新 Base，并会生成 `logs/sorftime-weekly-workflow/{run_id}/summary.json` 与 `run-report.md`。
- `.agents/workflows/command_runner.py` 使用独立 process group 执行子命令；超时时会杀掉整个进程组，避免 lark-cli 等孙进程继续写入。
- runner 会解析 Base sync 末尾 JSON，结构化汇总三张母表、十二张子表记录数、重复检查结果和 Base sync 日志目录。
- runner 会在 Base 左侧栏新增周报 docx 文档，并在流程结束后通过飞书机器人发送完成/异常通知。
- ProductRequest 旁路流程已修复两个审查问题：`transform_product.py` 不再输出 Stream Load columns 之外的 `photo` 字段；`DatabaseConfig` 直接构造时 `stream_load_host` 会回退到 `host`。注意：最终周报和 Base 的 `商品图片` URL 来自 CategoryRequest 同步后的配置表 `photo` 字段，不是 ProductRequest 旁路表。
- Chrome/Computer Use 左侧分组重命名保留为人工 fallback，不作为默认自动化步骤。

## Codex 自动化提示词

创建 Codex 定时自动化时，提示词应引用本 workflow，并要求按顺序使用 `sorftime-bsr-sync`、`sorftime-weekly-report`、`sorftime-report-base-sync`。自动化任务必须用 `folder_rename` / `block_layout` 汇报左侧分组状态；若缺少 `base:block:update` 授权，必须把该项标为阻塞并说明授权恢复命令。
