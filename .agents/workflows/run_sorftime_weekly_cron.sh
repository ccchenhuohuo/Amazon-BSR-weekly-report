#!/usr/bin/env bash
set -u

PROJECT_ROOT="${SORFTIME_PROJECT_ROOT:-/opt/ulanzi/report/Amazon-BSR-weekly-report}"
LOCK_FILE="${SORFTIME_WEEKLY_LOCK_FILE:-/tmp/amazon-bsr-weekly-report.lock}"

cd "$PROJECT_ROOT" || exit 1
umask 077
mkdir -p logs/cron
chmod 700 logs logs/cron 2>/dev/null || true
touch logs/cron/cron.log
chmod 600 logs/cron/cron.log 2>/dev/null || true

exec >> logs/cron/cron.log 2>&1

timestamp() {
  date -Is
}

echo "[$(timestamp)] amazon-bsr weekly cron start"
flock -n -E 75 "$LOCK_FILE" .venv/bin/python .agents/workflows/run_sorftime_weekly_workflow.py "$@"
status=$?

if [ "$status" -eq 75 ]; then
  echo "[$(timestamp)] [WARN] amazon-bsr weekly cron lock busy, skipped"
  exit 0
fi

if [ "$status" -ne 0 ]; then
  echo "[$(timestamp)] [ERROR] amazon-bsr weekly cron failed exit_code=$status"
else
  echo "[$(timestamp)] amazon-bsr weekly cron finished"
fi

exit "$status"
