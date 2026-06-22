
#!/bin/bash
#
# Sorftime BSR 每周一键同步脚本
#
# 用法:
#   bsr_sync_weekly.sh              # 同步上周五数据（默认）
#   bsr_sync_weekly.sh 2026-04-10  # 同步指定日期
#   bsr_sync_weekly.sh --parallel   # 使用并发模式
#
# 环境变量:
#   TARGET_WEEKDAY: 目标星期（0-6, 周一/mon-周日/sun，默认周五）
#   BSR_SYNC_DATE: 指定同步日期（YYYY-MM-DD）
#   MAX_WORKERS: 并发数（默认 4）
#
# 说明:
#   - 自动检测目标日期是否已有 100 条记录，有则跳过
#   - 使用 --force 可强制重新拉取（先删后写）
#   - 日志路径: {skill_dir}/logs/bsr_sync.log
#   - 100% Linux 兼容，不使用 date -v/-d
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="${SKILL_DIR}/logs"
LOG_FILE="${LOG_DIR}/bsr_sync.log"

# 创建日志目录
mkdir -p "$LOG_DIR"

# 日期计算函数（使用 Python，100% 跨平台）
get_current_datetime() {
    python3 -c "from datetime import datetime; print(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))"
}

# 获取上个指定星期的日期
get_last_weekday() {
    local weekday="$1"
    python3 -c "
from datetime import date, timedelta
import sys
sys.path.insert(0, '$SKILL_DIR/scripts')
from utils.date_utils import get_last_weekday
print(get_last_weekday(int(sys.argv[1])))
" "$weekday" 2>/dev/null || python3 -c "
from datetime import date, timedelta
import sys
weekday = int(sys.argv[1])
today = date.today()
days_since_target = (today.weekday() - weekday) % 7
if days_since_target == 0:
    days_since_target = 7
last_target = today - timedelta(days=days_since_target)
print(last_target.isoformat())
" "$weekday"
}

# 解析星期参数（返回 Python weekday 数字：0=周一, 6=周日）
parse_weekday() {
    local weekday_str="$1"
    python3 -c "
import sys
sys.path.insert(0, '$SKILL_DIR/scripts')
from utils.date_utils import parse_weekday_param
try:
    print(parse_weekday_param(sys.argv[1]))
except Exception as e:
    print(f'ERROR: {e}', file=sys.stderr)
    sys.exit(1)
" "$weekday_str"
}

# 确定星期：环境变量 > 默认周五
TARGET_WEEKDAY_NUM=4  # 默认周五 (0=周一, 4=周五, 6=周日)
if [[ -n "$TARGET_WEEKDAY" ]]; then
    TARGET_WEEKDAY_NUM=$(parse_weekday "$TARGET_WEEKDAY")
    if [[ $? -ne 0 ]]; then
        echo "[$(get_current_datetime)] [ERROR] [bsr_sync_weekly] TARGET_WEEKDAY 无效: $TARGET_WEEKDAY" >&2
        exit 1
    fi
fi

# 确定日期：参数 > 环境变量 > 默认上个目标星期
DATE=""
EXTRA_ARGS=()

# 解析参数
while [[ $# -gt 0 ]]; do
    case "$1" in
        --weekday|-w)
            TARGET_WEEKDAY_NUM=$(parse_weekday "$2")
            if [[ $? -ne 0 ]]; then
                echo "[$(get_current_datetime)] [ERROR] [bsr_sync_weekly] --weekday 参数无效: $2" >&2
                exit 1
            fi
            EXTRA_ARGS+=("--weekday" "$2")
            shift 2
            ;;
        --parallel)
            EXTRA_ARGS+=("--parallel")
            shift
            ;;
        --max-workers)
            EXTRA_ARGS+=("--max-workers" "$2")
            shift 2
            ;;
        --force)
            EXTRA_ARGS+=("--force")
            shift
            ;;
        --*)
            EXTRA_ARGS+=("$1")
            shift
            ;;
        *)
            if [[ -z "$DATE" ]]; then
                DATE="$1"
            else
                EXTRA_ARGS+=("$1")
            fi
            shift
            ;;
    esac
done

# 如果没有指定日期，使用默认值
if [[ -z "$DATE" ]]; then
    DATE="${BSR_SYNC_DATE:-$(get_last_weekday "$TARGET_WEEKDAY_NUM")}"
fi

if [[ -z "$DATE" ]]; then
    echo "[$(get_current_datetime)] [ERROR] [bsr_sync_weekly] 无法确定日期，请手动指定或检查 Python 可用性" >&2
    exit 1
fi

# 获取星期名称用于显示
WEEKDAY_NAME=$(python3 -c "
import sys
sys.path.insert(0, '$SKILL_DIR/scripts')
from utils.date_utils import WEEKDAY_NAMES
print(WEEKDAY_NAMES.get($TARGET_WEEKDAY_NUM, '星期$TARGET_WEEKDAY_NUM'))
" 2>/dev/null || echo "星期$TARGET_WEEKDAY_NUM")

echo "[$(get_current_datetime)] [INFO] [bsr_sync_weekly] 开始同步 date=$DATE, target_weekday=$WEEKDAY_NAME"

# 调用 fill_missing.py 执行同步
python3 "${SKILL_DIR}/scripts/sorftime_api/category/CategoryRequest/fill_missing.py" \
    --dates "$DATE" \
    --log-level INFO \
    "${EXTRA_ARGS[@]}"

EXIT_CODE=$?

if [[ $EXIT_CODE -eq 0 ]]; then
    echo "[$(get_current_datetime)] [INFO] [bsr_sync_weekly] 同步完成 date=$DATE, target_weekday=$WEEKDAY_NAME"
else
    echo "[$(get_current_datetime)] [ERROR] [bsr_sync_weekly] 同步失败 date=$DATE, target_weekday=$WEEKDAY_NAME exit=$EXIT_CODE" >&2
fi

exit $EXIT_CODE

