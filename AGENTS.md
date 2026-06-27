# Amazon Strategic Weekly Report Project Guide

This project owns Amazon/Sorftime weekly reporting workflows.

## Codex Project Layout

- Project-level Codex config: `.codex/config.toml`
- Project-level skills: `.agents/skills`
- Project-scoped skills:
  - `sorftime-bsr-sync`
  - `sorftime-weekly-report`
  - `sorftime-report-base-sync`
- Project workflows:
  - `.agents/workflows/sorftime-weekly-full-workflow.md`
  - `.agents/workflows/run_sorftime_weekly_workflow.py`

Do not move these Sorftime weekly-report skills back to a user-level skills directory unless they become broadly reusable outside this project.

## Working Principles

- Keep Sorftime data synchronization separate from weekly report generation.
- Use `sorftime-bsr-sync` for Sorftime API to Doris sync work.
- Use `sorftime-weekly-report` for weekly trend report generation.
- Use `sorftime-report-base-sync` after report generation when the Markdown/Obsidian report's image-bearing product tables need to be synced into Feishu Base mother tables and split child tables.
- For the recurring Friday 17:00 weekly run, follow `.agents/workflows/sorftime-weekly-full-workflow.md`: sync Wednesday BSR data first, generate all three category reports, sync each report to Feishu Base, verify the Base left-sidebar folder state through CLI output, create the weekly report docx inside each Base sidebar, and send the final Feishu notification.
- Keep generated logs under project or skill-local `logs/` directories; do not place generated outputs directly under `.agents/skills` unless they are skill resources.
