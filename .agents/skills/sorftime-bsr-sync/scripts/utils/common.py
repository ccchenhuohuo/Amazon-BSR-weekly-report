#!/usr/bin/env python3
"""
通用工具模块

提供跨脚本共享的工具函数：
- safe_float/safe_int: 安全类型转换
- read_json_input/write_json_output: JSON 输入输出处理
- load_env_safe: 安全加载 .env 文件
"""

import json
import sys
import os
from pathlib import Path
from typing import Optional, List, Dict, Any


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    安全的 float 类型转换

    Args:
        value: 要转换的值
        default: 转换失败时的默认值

    Returns:
        float: 转换后的值
    """
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    """
    安全的 int 类型转换

    Args:
        value: 要转换的值
        default: 转换失败时的默认值

    Returns:
        int: 转换后的值
    """
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def read_json_input(input_path: Optional[str], script_name: str) -> List[Dict[str, Any]]:
    """
    从文件或 stdin 读取 JSON 输入

    Args:
        input_path: 输入文件路径，None 表示从 stdin 读取
        script_name: 脚本名称，用于日志输出

    Returns:
        List[Dict]: 解析后的 JSON 数据

    Raises:
        SystemExit: 解析失败时退出
    """
    if input_path:
        with open(input_path, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        raw = sys.stdin.read()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # 如果解析失败，可能传入的本身就不是纯 JSON，尝试提取 JSON 部分
            print(f"[{script_name}] stdin 非纯 JSON，尝试解析...", file=sys.stderr)
            # 查找第一个 '[' 或 '{' 开始的位置
            start = max(raw.find('['), raw.find('{'))
            if start >= 0:
                try:
                    return json.loads(raw[start:])
                except json.JSONDecodeError as e:
                    print(f"[{script_name}] 提取部分后仍无法解析: {e}", file=sys.stderr)
                    sys.exit(1)
            else:
                print(f"[{script_name}] 无法解析输入数据", file=sys.stderr)
                sys.exit(1)


def write_json_output(data: Any, output_path: Optional[str], script_name: str) -> None:
    """
    将 JSON 数据写入文件或 stdout

    Args:
        data: 要写入的数据
        output_path: 输出文件路径，None 表示输出到 stdout
        script_name: 脚本名称，用于日志输出
    """
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        print(f"[{script_name}] 输出: {output_path}", file=sys.stderr)
    else:
        print(json.dumps(data, ensure_ascii=False))


def load_env_safe() -> None:
    """
    安全加载 .env 文件

    如果 python-dotenv 不可用或 .env 文件不存在，静默跳过
    """
    try:
        from dotenv import load_dotenv
        skill_root = Path(__file__).resolve().parents[2]
        project_root = Path(__file__).resolve().parents[5]
        for env_path in (project_root / ".env", skill_root / ".env"):
            if env_path.exists():
                load_dotenv(dotenv_path=env_path, override=False)
    except ImportError:
        pass


__all__ = [
    'safe_float',
    'safe_int',
    'read_json_input',
    'write_json_output',
    'load_env_safe'
]
