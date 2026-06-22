#!/usr/bin/env python3
"""
管道处理模块

提供零临时文件的管道处理逻辑：
fetch_bsr.py -> transform_bsr.py -> 直接调用 stream_load 函数
"""
import json
import socket
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Any
from logging import Logger

SCRIPTS_DIR = Path(__file__).resolve().parents[4]
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

# 统一路径初始化
from utils.path_init import init_path
init_path()

from utils.path_utils import get_stream_load_dir
from utils.constants import LogConfig

# 超时配置
PIPELINE_TIMEOUT_SECONDS = LogConfig.PIPELINE_TIMEOUT_SECONDS
LOG_MAX_LENGTH = LogConfig.LOG_MAX_LENGTH


def _check_stream_load_reachable(host: str, port: int, logger: Logger) -> bool:
    """Fail fast when the Stream Load endpoint is unavailable."""
    try:
        with socket.create_connection((host, int(port)), timeout=5):
            return True
    except OSError as e:
        logger.error(
            "Stream Load 地址不可达: %s:%s。请确认当前网络可访问 Doris Stream Load HTTP 端口。错误: %s",
            host,
            port,
            e,
        )
        return False


def _parse_stream_load_result(stdout: str, returncode: int, logger: Logger) -> bool:
    """
    解析 stream_load 结果

    Args:
        stdout: stream_load.py 的 stdout 输出
        returncode: 进程退出码
        logger: 日志记录器

    Returns:
        True 表示成功
    """
    if returncode != 0:
        return False
    try:
        result = json.loads(stdout)
        if result.get('Status') == 'Success':
            return True
        else:
            logger.error(f"stream_load 返回错误状态: {result.get('Status')}, Message: {result.get('Message')}")
            return False
    except json.JSONDecodeError:
        # JSON 解析失败，回退到检查字符串
        if "Success" in stdout:
            return True
        else:
            logger.error(f"stream_load 输出不是有效 JSON，且未找到 'Success' 标识")
            return False


def _fetch_and_transform(
    date_str: str,
    node_id: str,
    fetch_script: Path,
    transform_script: Path,
    logger: Logger
) -> Optional[list]:
    """
    执行 fetch 和 transform，返回转换后的 JSON 数据

    Args:
        date_str: 日期字符串
        node_id: 类目 node_id
        fetch_script: fetch_bsr.py 路径
        transform_script: transform_bsr.py 路径
        logger: 日志记录器

    Returns:
        转换后的 JSON 数据列表，失败返回 None
    """
    # Step 1: fetch_bsr.py -> stdout
    p1 = subprocess.Popen(
        ["python3", str(fetch_script), "--date", date_str, "--node-id", node_id],
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True
    )

    # Step 2: transform_bsr.py <- stdin, -> stdout
    p2 = subprocess.Popen(
        ["python3", str(transform_script), "--date", date_str],
        stdin=p1.stdout,
        stdout=subprocess.PIPE,
        stderr=sys.stderr,
        text=True
    )
    p1.stdout.close()

    # 等待完成
    try:
        stdout2, stderr2 = p2.communicate(timeout=PIPELINE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        logger.error(f"fetch/transform 超时: date={date_str}, node_id={node_id}")
        # 先尝试优雅终止下游进程
        for p in [p2, p1]:  # 注意顺序：先终止下游，再终止上游
            if p.poll() is None:
                p.terminate()
                try:
                    p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait(timeout=2)
        # 丢弃剩余输出，避免僵尸进程
        try:
            p2.communicate()
        except Exception:
            pass
        return None

    # 确保 p1 也结束
    p1.wait()

    if p1.returncode != 0:
        logger.error(f"fetch_bsr 失败，退出码: {p1.returncode}")
        return None

    if p2.returncode != 0:
        logger.error(f"transform_bsr 失败，退出码: {p2.returncode}")
        return None

    # 解析 JSON
    try:
        json_rows = json.loads(stdout2)
        if not isinstance(json_rows, list):
            logger.error(f"transform_bsr 输出不是数组格式")
            return None
        return json_rows
    except json.JSONDecodeError as e:
        logger.error(f"transform_bsr 输出不是有效 JSON: {e}")
        return None


def _call_stream_load_direct(
    host: str,
    port: int,
    fallback_host: str,
    fallback_port: int,
    user: str,
    password: str,
    database: str,
    table: str,
    columns: str,
    json_rows: list,
    label: str,
    logger: Logger
) -> bool:
    """
    直接调用 stream_load 函数，不通过 subprocess

    Args:
        host: Doris FE 地址
        port: Stream Load 端口
        user: 用户名
        password: 密码
        database: 数据库名
        table: 表名
        columns: 列名，逗号分隔
        json_rows: JSON 数据列表
        label: 自定义 label
        logger: 日志记录器

    Returns:
        True 表示成功
    """
    try:
        endpoints = [("primary", host, int(port))]
        if fallback_host and (fallback_host != host or int(fallback_port) != int(port)):
            endpoints.append(("fallback", fallback_host, int(fallback_port)))

        import requests
        payload = json.dumps(json_rows, ensure_ascii=False)
        expected_count = len(json_rows)

        for role, endpoint_host, endpoint_port in endpoints:
            if not _check_stream_load_reachable(endpoint_host, endpoint_port, logger):
                continue

            logger.info(f"直接调用 Stream Load ({role}): {endpoint_host}:{endpoint_port}, label={label}")
            method = "POST" if endpoint_port == 33060 else "PUT"
            content_type = "application/json" if method == "POST" else "text/plain; charset=utf-8"
            url = f"http://{endpoint_host}:{endpoint_port}/api/{database}/{table}/_stream_load"
            response = requests.request(
                method,
                url,
                auth=(user, password),
                headers={
                    "Expect": "100-continue",
                    "Content-Type": content_type,
                    "label": label,
                    "format": "json",
                    "strip_outer_array": "true",
                    "ignore_json_size": "true",
                    "disable_stream_load_sql_check": "true",
                    "columns": columns,
                    "timezone": "+08:00",
                },
                data=payload.encode("utf-8"),
                timeout=120,
            )

            try:
                result = response.json()
            except ValueError:
                logger.error(
                    "Stream Load 返回非 JSON: endpoint=%s, http_status=%s, body=%s",
                    role,
                    response.status_code,
                    response.text[:LOG_MAX_LENGTH],
                )
                continue

            status = result.get("Status") or result.get("status")
            message = result.get("Message") or result.get("msg") or "未知错误"
            if response.ok and status == "Success":
                loaded_rows = result.get("NumberLoadedRows")
                try:
                    loaded_count = int(loaded_rows)
                except (TypeError, ValueError):
                    loaded_count = None
                if loaded_count is not None and loaded_count != expected_count:
                    logger.error(
                        f"Stream Load 行数不一致: endpoint={role}, label={label}, "
                        f"expected={expected_count}, loaded={loaded_count}"
                    )
                    continue
                logger.info(f"Stream Load 成功: endpoint={role}, label={label}")
                return True

            logger.error(
                f"Stream Load 失败: endpoint={role}, http_status={response.status_code}, "
                f"status={status}, message={message}"
            )

        return False
    except Exception as e:
        logger.error(f"调用 Stream Load 异常: {e}")
        return False


def process_single_task_pipeline(
    date_str: str,
    node_id: str,
    fetch_script: Path,
    transform_script: Path,
    stream_load_script: Path,
    db_config: Any,
    columns: str,
    logger: Logger
) -> bool:
    """
    管道模式处理单个任务（零临时文件）

    执行流程:
        fetch_bsr.py -> transform_bsr.py -> 直接调用 stream_load 函数

    Args:
        date_str: 日期字符串 (YYYY-MM-DD)
        node_id: 类目 node_id
        fetch_script: fetch_bsr.py 路径
        transform_script: transform_bsr.py 路径
        stream_load_script: stream_load.py 路径 (未使用，保留用于兼容性)
        db_config: 数据库配置对象
        columns: 列名逗号分隔字符串
        logger: 日志记录器

    Returns:
        True 表示成功
    """
    # Step 1-2: fetch and transform
    json_rows = _fetch_and_transform(date_str, node_id, fetch_script, transform_script, logger)
    if json_rows is None:
        return False

    # 检查数据
    if len(json_rows) == 0:
        logger.warning(f"没有有效数据: date={date_str}, node_id={node_id}")
        return False

    # Step 3: 直接调用 stream_load 函数
    unique_suffix = uuid.uuid4().hex[:8]
    label = f"bsr_sync_{node_id}_{date_str.replace('-', '')}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{unique_suffix}"

    return _call_stream_load_direct(
        host=getattr(db_config, "stream_load_host", db_config.host),
        port=getattr(db_config, "stream_load_port", db_config.port),
        fallback_host=getattr(db_config, "stream_load_fallback_host", ""),
        fallback_port=getattr(db_config, "stream_load_fallback_port", 0),
        user=db_config.user,
        password=db_config.password,
        database=db_config.database,
        table=db_config.table,
        columns=columns,
        json_rows=json_rows,
        label=label,
        logger=logger
    )


__all__ = [
    "process_single_task_pipeline",
    "_fetch_and_transform",
    "_call_stream_load_direct",
]
