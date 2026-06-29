# Amazon Strategic Weekly Report Automation

This repository contains the Codex project-scoped automation for weekly
Amazon/Sorftime strategic reports.

The workflow is intentionally split into three skills plus the project runner:

- `sorftime-bsr-sync`: sync Sorftime category Top 100 BSR data into Doris.
- `sorftime-weekly-report`: generate the weekly Markdown reports.
- `sorftime-report-base-sync`: sync image-bearing report tables into Feishu Base.
- `.agents/workflows/run_sorftime_weekly_workflow.py`: orchestrate the weekly
  run, create the report docx inside each Base sidebar, and send the final
  Feishu completion notification.

Generated logs, reports, Base payloads, credentials, and local runtime state are
not part of the Git repository.

## Setup

Use Python 3.11 or newer.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# or pin to the verified production set
pip install -r requirements.lock
```

Create local runtime configuration:

```bash
cp .env.example .env
```

Fill `.env` with your own Sorftime, Doris, and Feishu values. Real credentials,
Base tokens, internal hosts, and generated logs must never be committed.

## Preflight

```bash
python3 .agents/skills/sorftime-weekly-report/scripts/generate_weekly_report.py --preflight
python3 .agents/skills/sorftime-bsr-sync/scripts/sorftime_api/category/CategoryRequest/fill_missing.py --help
python3 .agents/workflows/run_sorftime_weekly_workflow.py --preflight
python3 .agents/workflows/run_sorftime_weekly_workflow.py --date 2026-06-17 --dry-run --skip-bsr --skip-report --skip-base-sync
pytest
```

`--preflight` verifies project-local templates and mapping files. It does not
connect to Doris. Full report dry-runs require valid Doris credentials because
they execute the report queries.

## Production Run

The project runner is the recommended entry point:

```bash
.agents/workflows/run_sorftime_weekly_workflow.py --date <YYYY-MM-DD>
```

By default, generated Markdown reports are written to `reports/`. To write to an
Obsidian vault or another production location, set:

```bash
export SORFTIME_REPORT_OUTPUT_DIR="/path/to/history-reports"
```

Feishu template Base access must be provided at runtime through the ignored
local `.env` file or the process environment. Set `FEISHU_TEMPLATE_BASE_TOKEN`
locally; do not commit the value.

The runner creates a report docx block inside each generated Base sidebar and
writes the Markdown report into that in-Base document. Remote product image tags
are replaced with `图片见 Base 数据表` in the docx content; the image-bearing
tables remain in Base.

Publication is idempotent by default. The runner records per-date/per-category
Base and docx tokens in the ignored local registry `state/publications.json`
with file mode `600`. On rerun it reuses the registered Base/docx and updates
the existing in-Base report document after confirming the same-named docx still
exists in the Base sidebar. Use `--force-new-publication` only when a fresh set
of Feishu resources is intentionally required.

The scheduled BSR sync does not force-refresh complete Doris data. Existing
date/category rows with the expected Top 100 count are skipped unless the BSR
tool is run explicitly with `--force`. Forced refreshes back up old rows before
DELETE and attempt restoration if load or verification fails.

Markdown report files are written atomically: render to a same-directory
temporary file, validate that file, then replace the final path. A validation
failure leaves the previous report intact.

Base overwrite sync takes a local snapshot before clearing records. If a write
or readback verification fails, the sync attempts to restore the snapshot and
exits non-zero with `overwrite_recovery` details in the JSON summary.

To send the final Feishu notification, set one recipient locally:

```bash
FEISHU_NOTIFY_USER_ID=ou_xxx
# or
FEISHU_NOTIFY_CHAT_ID=oc_xxx
FEISHU_NOTIFY_AS=bot
FEISHU_REQUIRE_NOTIFY=1
```

Legacy `LARK_REPORT_USER_ID` and `LARK_REPORT_CHAT_ID` are still accepted as
fallbacks, but the `FEISHU_NOTIFY_*` names take precedence.

For cron, use an absolute `LARK_CLI_BIN` path such as
`/usr/local/bin/lark-cli` and keep `LARKSUITE_CLI_DATA_DIR` pointed at the data
root that contains the authorized user token.

If `lark-cli` credentials are stored in a non-default data root, also configure
`LARK_CLI_BIN` and `LARKSUITE_CLI_DATA_DIR` locally.

The runner redacts token-like values from its summary and run report. Runtime
logs still belong under ignored `logs/` directories and should be treated as
local operational artifacts.

## Weekly Schedule

The recurring Codex automation should run every Friday at 17:00 Asia/Shanghai:

```text
FREQ=WEEKLY;BYDAY=FR;BYHOUR=17;BYMINUTE=0;BYSECOND=0
```

The automation prompt should reference
`.agents/workflows/sorftime-weekly-full-workflow.md` and run the skills in this
order:

1. Sync Wednesday BSR data.
2. Generate the three category Markdown reports.
3. Reuse registered Feishu Bases or copy from the template when missing.
4. Sync each report to Feishu Base with overwrite snapshots and verification.
5. Reuse or create the weekly report docx inside each Base sidebar, then update it.
6. Send the final Feishu notification with Base and docx links.

The local cron command should call the wrapper, which sets a restrictive umask,
keeps `logs/cron` at `700`, keeps `cron.log` at `600`, and writes an explicit
skip line when the lock is already held:

```text
0 17 * * 5 /opt/ulanzi/report/Amazon-BSR-weekly-report/.agents/workflows/run_sorftime_weekly_cron.sh
```

## Safety Boundary

Before publishing or pushing:

```bash
find . -path './.venv' -prune -o -path './logs' -prune -o -path './reports' -prune -o -type l -print
find . -type d -name .git
rg -n --hidden -g '!README.md' -g '!tests/**' -g '!**/tests/**' \
  -g '!logs/**' -g '!reports/**' -g '!output/**' -g '!dist/**' \
  '(SORFTIME_API_KEY\s*=.+|DORIS_PASSWORD\s*=.+|Authorization: BasicAuth|base/[A-Za-z0-9]{12,}|docx/[A-Za-z0-9]{12,}|docs/[A-Za-z0-9]{12,}|drive/[A-Za-z0-9]{12,}|folder/[A-Za-z0-9]{12,}|wiki/[A-Za-z0-9]{12,}|ou_[A-Za-z0-9]{20,}|oc_[A-Za-z0-9]{20,}|om_[A-Za-z0-9]{20,}|"(app_token|base_token|docx_token|document_id|chat_id|message_id)"\s*:\s*"[A-Za-z0-9_-]{12,}")'
rg -n --hidden -g '!logs/**' -g '!reports/**' -g '!output/**' -g '!dist/**' "$HOME"
```

Expected results:

- No symlinks in runtime resources.
- Only the root `.git` directory after repository initialization.
- No real secrets, Base links, generated reports, or personal absolute paths in
  tracked files.
