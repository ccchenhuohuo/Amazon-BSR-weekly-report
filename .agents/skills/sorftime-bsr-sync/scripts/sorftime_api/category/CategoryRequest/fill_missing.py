#!/usr/bin/env python3
"""
Sorftime BSR 缺失日期一键补全脚本（仅管道模式）

功能:
    1. 支持单类目或多类目同步
    2. 自动检测 Doris 中缺失的目标星期数据（count != 100 或完全缺失）
    3. 对指定日期执行：fetch -> transform -> backup -> DELETE -> stream_load -> verify
    4. 保证幂等性：完整 Top100 默认跳过；显式 --force 才刷新并启用备份恢复
    5. 支持并发模式
    6. 支持灵活的星期配置

用法:
    # 同步所有类目（默认周三）
    python3 fill_missing.py

    # 指定星期几同步（支持 0-6, 周一/mon, 周五/fri 等）
    python3 fill_missing.py --weekday wednesday
    python3 fill_missing.py --weekday 2
    python3 fill_missing.py --weekday 周三

    # 指定日期 + 所有类目
    python3 fill_missing.py --dates 2026-04-06

    # 只同步单个类目
    python3 fill_missing.py --node-id 499310

    # 单类目 + 指定日期
    python3 fill_missing.py --node-id 499310 --dates 2026-04-06

    # 检测缺失（不写入）
    python3 fill_missing.py --check-only

    # 强制重新拉取
    python3 fill_missing.py --force --dates 2026-04-06

    # 并发模式（推荐）
    python3 fill_missing.py --parallel --max-workers 4
"""
import argparse
import sys
import logging
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[3]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 统一路径初始化
from utils.path_init import init_path
init_path()

# 加载 .env 配置（如果存在）
from utils.common import load_env_safe
load_env_safe()

from utils.audit_log import get_logger
from utils.date_utils import validate_date, is_weekday, parse_weekday_param, WEEKDAY_NAMES
from utils.base_config import DatabaseConfig, BackfillConfig, ConfigError


def main():
    # ============ 解析命令行参数 ============
    parser = argparse.ArgumentParser(
        description="Sorftime BSR 缺失日期补全脚本（仅管道模式，零临时文件）"
    )
    parser.add_argument(
        "--dates", nargs="+",
        help="指定要补全的日期（YYYY-MM-DD），不指定则自动检测"
    )
    parser.add_argument(
        "--node-id", "-n",
        help="指定单个类目 NodeId，不指定则同步所有类目"
    )
    parser.add_argument(
        "--weekday", "-w",
        help=f"指定目标星期（0-6, 周一/mon-周日/sun，默认周三）"
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="只检测缺失日期，不执行写入"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="强制重新拉取（即使检测到数据完整也重新拉取）"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="模拟执行，不实际写入 Doris"
    )
    parser.add_argument(
        "--parallel", action="store_true",
        help="启用并发模式"
    )
    parser.add_argument(
        "--max-workers", type=int, default=None,
        help="最大并发数，默认读取 MAX_WORKERS，未配置时为 4"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别"
    )
    args = parser.parse_args()

    # ============ 加载配置 ============
    try:
        db_config = DatabaseConfig.from_env()
        backfill_config = BackfillConfig.from_env()
    except ConfigError as e:
        print(f"[ERROR] 配置加载失败: {e}", file=sys.stderr)
        sys.exit(1)

    max_workers = args.max_workers if args.max_workers is not None else backfill_config.max_workers

    from backfill import CategoryBSRWorkflow, load_category_list

    # ============ 验证参数 ============
    if max_workers < 1:
        print(f"[ERROR] --max-workers 必须 >= 1，实际: {max_workers}", file=sys.stderr)
        sys.exit(1)
    if max_workers > 8:
        print(f"[WARNING] --max-workers={max_workers} 可能过高，建议不超过 8（避免 API 限流）", file=sys.stderr)

    # 如果指定了 --weekday 参数，覆盖配置中的 target_weekday
    if args.weekday:
        try:
            backfill_config.target_weekday = parse_weekday_param(args.weekday)
        except ValueError as e:
            print(f"[ERROR] --weekday 参数无效: {e}", file=sys.stderr)
            sys.exit(1)

    # ============ 创建工作流实例 ============
    try:
        with CategoryBSRWorkflow(db_config, backfill_config) as workflow:
            # 设置日志级别
            workflow.logger.setLevel(getattr(logging, args.log_level))

            # ============ 设置运行时配置 ============
            # 确定要处理的类目列表
            if args.node_id:
                node_ids = [args.node_id.strip()]
                workflow.set_node_ids(node_ids)

            # 确定要处理的日期列表
            target_dates = None
            if args.dates:
                # 验证所有日期
                target_dates = []
                for d in args.dates:
                    if not validate_date(d):
                        workflow.logger.error(f"日期格式无效: {d}，期望 YYYY-MM-DD")
                        sys.exit(1)
                    target_dates.append(d)

                # 验证日期与目标星期的一致性
                weekday_name = WEEKDAY_NAMES.get(backfill_config.target_weekday, str(backfill_config.target_weekday))
                invalid_dates = [d for d in target_dates if not is_weekday(d, backfill_config.target_weekday)]
                if invalid_dates:
                    workflow.logger.warning(f"以下日期不是 {weekday_name}: {invalid_dates}")

                workflow.set_dates(target_dates)

            # ============ 检查模式 ============
            if args.check_only:
                tasks = workflow.check_missing()
                weekday_name = WEEKDAY_NAMES.get(backfill_config.target_weekday, str(backfill_config.target_weekday))
                workflow.logger.info(f"目标星期: {weekday_name} (数字: {backfill_config.target_weekday})")
                workflow.logger.info(f"待处理: {len(tasks)} 个任务")

                # 详细列出每个类目的情况
                if args.node_id:
                    node_ids_to_check = [args.node_id.strip()]
                else:
                    node_ids_to_check = load_category_list()

                for nid in node_ids_to_check:
                    # 重新检查单个类目的情况（为了详细日志）
                    # 这里调用 workflow 内部的 check 逻辑比较麻烦
                    # 简单起见，直接列出所有任务
                    pass

                if tasks:
                    for date_str, node_id in tasks:
                        workflow.logger.info(f"  需要处理: date={date_str}, node_id={node_id}")
                return

            # ============ 执行同步 ============
            workflow.run(
                force=args.force,
                dry_run=args.dry_run,
                parallel=args.parallel,
                max_workers=max_workers
            )

    except ConfigError as e:
        print(f"[ERROR] 工作流初始化失败: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
