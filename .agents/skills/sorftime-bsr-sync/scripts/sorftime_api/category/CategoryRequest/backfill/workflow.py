#!/usr/bin/env python3
"""
BSR 数据同步核心工作流

仅保留管道模式（零临时文件）
"""
import os
import re
import sys
import threading
import logging
from typing import Optional, List, Tuple, Any, Dict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[4]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 统一路径初始化
from utils.path_init import init_path
init_path()

from utils.audit_log import get_logger, log_sync_event
from utils.retry import retry, DATABASE_EXCEPTIONS
from utils.path_utils import get_skill_dir, get_stream_load_script
from utils.date_utils import validate_date, is_weekday, get_all_weekdays_since, MYSQL_WEEKDAY_MAP, WEEKDAY_NAMES
from utils.base_config import DatabaseConfig, BackfillConfig
from utils.constants import BSRConfig

from .category_list import load_category_list
from .pipeline import _call_stream_load_direct, _fetch_and_transform


class CategoryBSRWorkflow:
    """
    BSR 数据同步工作流（仅管道模式）
    """

    def __init__(self, db_config: DatabaseConfig, backfill_config: BackfillConfig):
        """
        初始化工作流

        Args:
            db_config: 数据库配置（必须传入）
            backfill_config: 回填配置（必须传入）
        """
        self._db_config = db_config
        self._backfill_config = backfill_config

        # 使用 path_utils 计算路径
        skill_dir = get_skill_dir()
        self._fetch_script = skill_dir / "scripts" / "sorftime_api" / "category" / "CategoryRequest" / "fetch_bsr.py"
        self._transform_script = skill_dir / "scripts" / "sorftime_api" / "category" / "CategoryRequest" / "transform_bsr.py"
        self._stream_load_script = get_stream_load_script()

        self._columns = (
            "asin,bsr_date,title,photo,ebc_photo,store_name,"
            "listing_sales_volume_of_daily,listing_sales_of_daily,"
            "listing_sales_volume_of_month,listing_sales_of_month,"
            "parent_asin,price,list_price,product_type,sales_price,"
            "brand,profit,profit_rate,online_date,online_days,"
            "ratings_count,category,bsr_category_name,bsr_category_node,"
            "bsr_rank,rank,ratings,size,insert_time"
        )

        # 运行时配置
        self._target_node_ids: Optional[List[str]] = None
        self._target_dates: Optional[List[str]] = None

        # 日志
        self.logger = get_logger("fill_missing")

        # 数据库连接池（懒加载，线程安全）
        self._db_pool: Optional[Any] = None
        self._db_pool_lock = threading.Lock()

    def set_node_ids(self, node_ids: List[str]):
        """
        设置要同步的类目列表

        Args:
            node_ids: node_id 列表
        """
        self._target_node_ids = node_ids

    def set_dates(self, dates: List[str]):
        """
        设置要同步的日期列表

        Args:
            dates: 日期字符串列表 (YYYY-MM-DD)
        """
        self._target_dates = dates

    def _validate_db_name(self, name: str) -> str:
        """
        验证数据库/表名称，防止 SQL 注入

        Args:
            name: 数据库名或表名

        Returns:
            验证通过的名称

        Raises:
            ValueError: 名称包含非法字符
        """
        if not re.match(r'^[a-zA-Z0-9_]+$', name):
            raise ValueError(f"Invalid database or table name: {name}")
        return name

    def _build_table_ref(self) -> str:
        """
        安全构建 database.table 引用

        Returns:
            格式为 "database.table" 的安全字符串
        """
        db = self._validate_db_name(self._db_config.database)
        tbl = self._validate_db_name(self._db_config.table)
        return f"{db}.{tbl}"

    def _get_db_pool(self) -> Any:
        """
        获取或创建数据库连接池（线程安全的懒加载）

        Returns:
            数据库连接池实例
        """
        if self._db_pool is None:
            with self._db_pool_lock:
                # 双重检查锁定
                if self._db_pool is None:
                    try:
                        from dbutils.pooled_db import PooledDB
                        import pymysql
                        # 连接池最大连接数设置为 max_workers + 2，避免并发时等待
                        max_connections = max(self._backfill_config.max_workers + 2, 4)
                        self._db_pool = PooledDB(
                            creator=pymysql,
                            maxconnections=max_connections,
                            mincached=2,
                            maxcached=min(max_connections, 8),
                            blocking=True,
                            host=self._db_config.host,
                            port=self._db_config.mysql_port,
                            user=self._db_config.user,
                            password=self._db_config.password,
                            database=self._db_config.database,
                            charset="utf8mb4",
                            cursorclass=pymysql.cursors.DictCursor,
                            connect_timeout=30,
                            autocommit=True
                        )
                    except ImportError:
                        # dbutils 不可用，不使用连接池
                        self._db_pool = None
        return self._db_pool

    def close(self):
        """
        关闭数据库连接池，释放资源
        """
        if self._db_pool is not None:
            try:
                self._db_pool.close()
                self._db_pool = None
            except Exception:
                # 关闭时忽略异常，避免影响主流程
                pass

    def __enter__(self) -> 'CategoryBSRWorkflow':
        """支持 with 语句上下文管理器"""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any):
        """退出 with 语句时自动关闭连接池"""
        self.close()

    @retry(
        exceptions=DATABASE_EXCEPTIONS,
        max_retries=3,
        base_delay=1.0,
        max_delay=10.0
    )
    def _exec_query(self, sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
        """
        通过 Doris MySQL 协议接口执行查询，参数化防注入

        如果可用，使用连接池；否则每次创建新连接。
        """
        import pymysql
        pool = self._get_db_pool()

        conn = None
        try:
            if pool:
                conn = pool.connection()
            else:
                conn = pymysql.connect(
                    host=self._db_config.host,
                    port=self._db_config.mysql_port,
                    user=self._db_config.user,
                    password=self._db_config.password,
                    database=self._db_config.database,
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=30,
                    autocommit=True
                )

            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                result = cursor.fetchall()
                return list(result) if result else []
        finally:
            if conn:
                conn.close()

    def _count_records(self, date_str: str, node_id: str) -> int:
        """
        统计指定日期和类目的记录数

        Args:
            date_str: 日期字符串
            node_id: 类目 node_id

        Returns:
            记录数，失败返回 0
        """
        import pymysql
        table_ref = self._build_table_ref()
        try:
            result = self._exec_query(
                f"SELECT COUNT(*) as cnt FROM {table_ref} WHERE bsr_date = %s AND bsr_category_node = %s",
                (date_str, node_id)
            )
            return result[0]['cnt'] if result else 0
        except (pymysql.Error, OSError) as e:
            self.logger.error(f"查询 Doris 失败: {e}")
            return 0

    @retry(
        exceptions=DATABASE_EXCEPTIONS,
        max_retries=2,
        base_delay=2.0,
        max_delay=8.0
    )
    def _batch_insert_rows(self, json_rows: List[Dict[str, Any]]) -> bool:
        """
        Stream Load 不可用时，回退到 Doris MySQL 协议执行批量 INSERT。
        """
        import pymysql

        if not json_rows:
            self.logger.error("MySQL 批量 INSERT 跳过：没有可写入的数据")
            return False

        columns = [column.strip() for column in self._columns.split(",")]
        placeholders = ",".join(["%s"] * len(columns))
        column_sql = ",".join(columns)
        table_ref = self._build_table_ref()
        sql = f"INSERT INTO {table_ref} ({column_sql}) VALUES ({placeholders})"
        rows = [tuple(row.get(column) for column in columns) for row in json_rows]

        pool = self._get_db_pool()
        conn = None
        try:
            if pool:
                conn = pool.connection()
            else:
                conn = pymysql.connect(
                    host=self._db_config.host,
                    port=self._db_config.mysql_port,
                    user=self._db_config.user,
                    password=self._db_config.password,
                    database=self._db_config.database,
                    charset="utf8mb4",
                    cursorclass=pymysql.cursors.DictCursor,
                    connect_timeout=30,
                    autocommit=True
                )

            with conn.cursor() as cursor:
                cursor.executemany(sql, rows)
            self.logger.info(f"MySQL 批量 INSERT 成功: rows={len(rows)}")
            return True
        except (pymysql.Error, OSError) as e:
            self.logger.error(f"MySQL 批量 INSERT 失败: {e}")
            return False
        finally:
            if conn:
                conn.close()

    @retry(
        exceptions=DATABASE_EXCEPTIONS,
        max_retries=2,
        base_delay=2.0,
        max_delay=8.0
    )
    def _delete_old_data(self, date_str: str, node_id: str) -> bool:
        """
        删除旧数据

        Args:
            date_str: 日期字符串
            node_id: 类目 node_id

        Returns:
            True 表示删除成功
        """
        import pymysql
        table_ref = self._build_table_ref()
        try:
            self._exec_query(
                f"DELETE FROM {table_ref} WHERE bsr_date = %s AND bsr_category_node = %s",
                (date_str, node_id)
            )
            self.logger.info(f"已清理 Doris 数据: date={date_str}, node_id={node_id}")
            return True
        except (pymysql.Error, OSError) as e:
            self.logger.error(f"DELETE 失败: {e}")
            return False

    def _check_missing_for_node(self, node_id: str) -> Tuple[List[str], List[str]]:
        """
        检查 Doris 中指定类目的缺失/异常日期

        Returns:
            (missing_dates, bad_dates)
        """
        target_weekday = self._backfill_config.target_weekday
        if target_weekday not in MYSQL_WEEKDAY_MAP:
            raise ValueError(f'Invalid weekday: {target_weekday}, must be 0-6')
        mysql_weekday = MYSQL_WEEKDAY_MAP[target_weekday]

        table_ref = self._build_table_ref()
        sql = f"""
        SELECT bsr_date, COUNT(*) as cnt
        FROM {table_ref}
        WHERE bsr_date >= %s AND DAYOFWEEK(bsr_date) = %s
          AND bsr_category_node = %s
        GROUP BY bsr_date
        ORDER BY bsr_date
        """
        result = self._exec_query(
            sql,
            (self._backfill_config.start_date, mysql_weekday, node_id)
        )
        existing = {row['bsr_date']: row['cnt'] for row in result}

        all_dates = get_all_weekdays_since(target_weekday, self._backfill_config.start_date)
        missing = [d for d in all_dates if d not in existing]
        bad = [d for d, cnt in existing.items() if cnt != 100]
        return missing, bad

    def _check_missing_batch(self, dates: List[str], node_ids: List[str]) -> List[Tuple[str, str]]:
        """
        批量检查缺失数据（性能优化）

        Args:
            dates: 日期列表
            node_ids: 类目 ID 列表

        Returns:
            需要回填的任务列表 [(date, node_id), ...]
        """
        if not dates or not node_ids:
            return []

        table_ref = self._build_table_ref()
        # 构建 IN 子句的占位符
        date_placeholders = ','.join(['%s'] * len(dates))
        node_placeholders = ','.join(['%s'] * len(node_ids))

        sql = f"""
        SELECT bsr_date, bsr_category_node, COUNT(*) as cnt
        FROM {table_ref}
        WHERE bsr_date IN ({date_placeholders})
          AND bsr_category_node IN ({node_placeholders})
        GROUP BY bsr_date, bsr_category_node
        """

        params = dates + node_ids
        results = self._exec_query(sql, tuple(params))

        # 构建已存在的记录映射
        existing = {(row['bsr_date'], row['bsr_category_node']): row['cnt']
                    for row in results}

        # 检查每个组合
        tasks = []
        for d in dates:
            for nid in node_ids:
                cnt = existing.get((d, nid), 0)
                if cnt != 100:
                    tasks.append((d, nid))

        return tasks

    def _needs_backfill(self, date_str: str, node_id: str) -> bool:
        """
        检查指定任务是否需要回填

        Args:
            date_str: 日期字符串
            node_id: 类目 node_id

        Returns:
            True 表示需要回填
        """
        existing_cnt = self._count_records(date_str, node_id)

        if existing_cnt == 0:
            self.logger.debug(f"task={date_str}+{node_id}: 数据缺失，需要回填")
            return True
        elif existing_cnt != 100:
            self.logger.debug(f"task={date_str}+{node_id}: 数据异常 (cnt={existing_cnt})，需要回填")
            return True
        else:
            self.logger.debug(f"task={date_str}+{node_id}: 数据完整 (cnt=100)，无需回填")
            return False

    def _verify_data(self, date_str: str, node_id: str, expected_count: Optional[int] = None) -> bool:
        """
        验证数据完整性

        Args:
            date_str: 日期字符串
            node_id: 类目 node_id

        Returns:
            True 表示验证通过。生产要求每个日期、每个类目必须刚好 100 条。
        """
        cnt = self._count_records(date_str, node_id)
        expected = BSRConfig.EXPECTED_RECORDS_PER_CATEGORY
        if cnt != expected:
            self.logger.error(
                f"校验不通过: date={date_str}, node_id={node_id}, expected={expected}, actual={cnt}"
            )
            return False
        if expected_count is not None and expected_count != expected:
            self.logger.error(
                f"转换行数不符合 Top100 要求: date={date_str}, node_id={node_id}, "
                f"expected={expected}, transformed={expected_count}"
            )
            return False
        return True

    def check_missing(self) -> List[Tuple[str, str]]:
        """
        检查缺失/异常的任务

        Returns:
            任务列表 [(date, node_id), ...]
        """
        # 确定要处理的类目列表
        if self._target_node_ids:
            node_ids = self._target_node_ids
        else:
            node_ids = load_category_list()

        # 确定要处理的日期列表
        if self._target_dates:
            # 指定了日期：使用批量查询
            return self._check_missing_batch(self._target_dates, node_ids)
        else:
            # 自动检测模式：逐个 (date, node_id) 检测
            tasks = []
            for nid in node_ids:
                missing, bad = self._check_missing_for_node(nid)
                for d in missing + bad:
                    tasks.append((d, nid))
            # 按日期排序，相同日期的任务放在一起
            tasks.sort()
            return tasks

    # 重试配置
    MAX_RETRY = 2

    def _process_single_task(self, task: Tuple[str, str], force: bool = False) -> bool:
        """
        处理单个任务（带重试机制）

        Args:
            task: (date_str, node_id)
            force: 是否强制重新拉取

        Returns:
            True 表示成功
        """
        date_str, node_id = task
        task_str = f"{date_str}+{node_id}"
        log_sync_event("fill_missing", "START", task_str, "")

        for attempt in range(self.MAX_RETRY + 1):
            # 检查是否需要处理（只在第一次尝试时检查）
            if attempt == 0:
                try:
                    needs_backfill = self._needs_backfill(date_str, node_id)
                except Exception as e:
                    self.logger.error(f"检查任务状态失败: task={task_str}, error={e}")
                    log_sync_event("fill_missing", "FAIL", task_str, "检查失败")
                    return False

                if not needs_backfill and not force:
                    self.logger.info(f"任务无需处理，跳过: task={task_str}")
                    log_sync_event("fill_missing", "SKIP", task_str, "无需回填")
                    return True

            # Step 1-2: 先拉取并转换。避免 API 失败时先删掉已有数据。
            json_rows = _fetch_and_transform(
                date_str,
                node_id,
                self._fetch_script,
                self._transform_script,
                self.logger
            )
            if json_rows is None or len(json_rows) == 0:
                log_sync_event("fill_missing", "FAIL", task_str, "管道处理失败")
                if attempt < self.MAX_RETRY:
                    self.logger.info(f"准备重试: task={task_str}, next_attempt={attempt + 2}")
                    continue
                break
            if len(json_rows) != BSRConfig.EXPECTED_RECORDS_PER_CATEGORY:
                self.logger.error(
                    f"转换行数不符合 Top100 要求: task={task_str}, "
                    f"expected={BSRConfig.EXPECTED_RECORDS_PER_CATEGORY}, actual={len(json_rows)}"
                )
                log_sync_event("fill_missing", "FAIL", task_str, "转换行数不是100")
                if attempt < self.MAX_RETRY:
                    self.logger.info(f"准备重试: task={task_str}, next_attempt={attempt + 2}")
                    continue
                break

            # Step 3: 删除旧数据（幂等性保证）
            self.logger.info(f"清理旧数据（幂等性保证）: task={task_str}, attempt={attempt + 1}")
            if not self._delete_old_data(date_str, node_id):
                log_sync_event("fill_missing", "FAIL", task_str, "删除旧数据失败")
                if attempt < self.MAX_RETRY:
                    self.logger.info(f"准备重试: task={task_str}, next_attempt={attempt + 2}")
                    continue
                break

            # Step 4: Stream Load 写入
            from datetime import datetime
            import uuid
            label = (
                f"bsr_sync_{node_id}_{date_str.replace('-', '')}_"
                f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
            )
            if not _call_stream_load_direct(
                host=self._db_config.stream_load_host,
                port=self._db_config.stream_load_port,
                fallback_host=self._db_config.stream_load_fallback_host,
                fallback_port=self._db_config.stream_load_fallback_port,
                user=self._db_config.user,
                password=self._db_config.password,
                database=self._db_config.database,
                table=self._db_config.table,
                columns=self._columns,
                json_rows=json_rows,
                label=label,
                logger=self.logger
            ):
                self.logger.warning(f"Stream Load 失败，降级使用 MySQL 批量 INSERT: task={task_str}")
                if not self._batch_insert_rows(json_rows):
                    log_sync_event("fill_missing", "FAIL", task_str, "Stream Load/MySQL INSERT 均失败")
                    if attempt < self.MAX_RETRY:
                        self.logger.info(f"准备重试: task={task_str}, next_attempt={attempt + 2}")
                        continue
                    break

            # Step 5: 验证数据
            if self._verify_data(date_str, node_id, expected_count=len(json_rows)):
                log_sync_event("fill_missing", "SUCCESS", task_str, "")
                return True

            # 验证失败，回滚
            self.logger.warning(f"验证失败，回滚: task={task_str}, attempt={attempt + 1}")
            self._delete_old_data(date_str, node_id)

            # 如果还有重试次数，再试一次
            if attempt < self.MAX_RETRY:
                self.logger.info(f"准备重试: task={task_str}, next_attempt={attempt + 2}")
            else:
                self.logger.error(f"重试{self.MAX_RETRY}次后仍失败: task={task_str}")

        log_sync_event("fill_missing", "FAIL", task_str, f"重试{self.MAX_RETRY}次后失败")
        return False

    def _process_batch(self, tasks: List[Tuple[str, str]], max_workers: int, force: bool = False) -> Tuple[int, List[str]]:
        """
        并发处理一批任务

        Args:
            tasks: 任务列表
            max_workers: 最大并发数
            force: 是否强制重新拉取

        Returns:
            (success_count, failed_list)
        """
        success = 0
        failed = []

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._process_single_task, task, force): task
                for task in tasks
            }

            for future in as_completed(futures):
                task = futures[future]
                task_str = f"{task[0]}+{task[1]}"
                try:
                    if future.result():
                        success += 1
                    else:
                        failed.append(task_str)
                except Exception as e:
                    self.logger.error(f"处理异常: task={task_str}, error={e}")
                    failed.append(task_str)

        return success, failed

    def run(self, force: bool = False, dry_run: bool = False, parallel: bool = False, max_workers: Optional[int] = None):
        """
        执行同步

        Args:
            force: 是否强制重新拉取（即使数据完整也重拉）
            dry_run: 模拟执行（不实际写入）
            parallel: 是否启用并发模式
            max_workers: 最大并发数（默认使用配置中的值）
        """
        # 确定并发数
        if max_workers is None:
            max_workers = self._backfill_config.max_workers

        # 获取任务列表。force + 指定日期时必须绕过完整性检查，否则已有 100 条
        # 数据会让任务列表为空，导致强制重拉失效。
        if force and self._target_dates:
            node_ids = self._target_node_ids if self._target_node_ids else load_category_list()
            tasks = [(date_str, node_id) for date_str in self._target_dates for node_id in node_ids]
        else:
            tasks = self.check_missing()

        if not tasks:
            self.logger.info("没有需要处理的任务")
            return

        weekday_name = WEEKDAY_NAMES.get(self._backfill_config.target_weekday, str(self._backfill_config.target_weekday))
        self.logger.info(f"目标星期: {weekday_name} (数字: {self._backfill_config.target_weekday})")
        self.logger.info(f"待处理: {len(tasks)} 个任务")

        # 确定要处理的类目（用于日志）
        if self._target_node_ids:
            self.logger.info(f"单类目模式: {self._target_node_ids}")
        else:
            node_ids = load_category_list()
            self.logger.info(f"全类目模式: {len(node_ids)} 个类目")

        if dry_run:
            self.logger.info(f"[dry-run] 模拟执行，不实际写入")
            for task in tasks:
                task_str = f"{task[0]}+{task[1]}"
                self.logger.info(f"[dry-run] {task_str}: DELETE -> fetch -> transform -> load -> verify")
            return

        # 执行同步
        success = 0
        failed = []

        if parallel and len(tasks) > 1:
            self.logger.info(f"并发模式: max-workers={max_workers}")
            success, failed = self._process_batch(tasks, max_workers, force=force)
        else:
            for task in tasks:
                if self._process_single_task(task, force=force):
                    success += 1
                else:
                    failed.append(f"{task[0]}+{task[1]}")

        self.logger.info(f"处理完成: 成功={success}, 失败={len(failed)}, 总任务={len(tasks)}")
        if failed:
            self.logger.error(f"失败的任务: {failed}")
            sys.exit(1)


__all__ = ['CategoryBSRWorkflow']
