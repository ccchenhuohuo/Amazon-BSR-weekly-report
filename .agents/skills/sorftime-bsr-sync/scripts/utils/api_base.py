#!/usr/bin/env python3
"""
API 客户端基类模块

提供统一的 API 调用基类，消除重复代码，解决全局状态污染问题。
"""

import json
import sys
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple, List
from pathlib import Path

# 添加 utils 到路径
# 注意：这个模块被其他模块导入，所以需要先初始化路径
script_dir = Path(__file__).parent
project_root = script_dir.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import requests
from utils.retry import retry, REQUESTS_EXCEPTIONS
from utils.base_config import APIConfig, ConfigError


class APIError(Exception):
    """API 返回非零 code 时的异常"""
    pass


# 统一重试异常列表
FETCH_EXCEPTIONS = REQUESTS_EXCEPTIONS + (APIError,)


class BaseAPIClient(ABC):
    """API 客户端基类 - 无全局状态，线程安全"""

    def __init__(self, api_url_path: str, config: Optional[APIConfig] = None):
        """
        初始化 API 客户端

        Args:
            api_url_path: API 路径，如 "CategoryRequest" 或 "ProductRequest"
            config: API 配置，如果为 None 则从环境变量加载
        """
        self._config: Optional[APIConfig] = None
        self._api_url_path = api_url_path

        if config is not None:
            self._config = config
            # 确保 base_url 是正确的
            if self._config.base_url.endswith(api_url_path):
                pass  # 已经正确
            else:
                # 重新构建 base_url
                self._config = APIConfig(
                    api_key=self._config.api_key,
                    base_url=f"https://standardapi.sorftime.com/api/{api_url_path}",
                    domain=self._config.domain,
                    timeout=self._config.timeout
                )

    def get_config(self) -> APIConfig:
        """
        获取 API 配置（懒加载）

        Returns:
            APIConfig 实例
        """
        if self._config is None:
            base_config = APIConfig.from_env()
            self._config = APIConfig(
                api_key=base_config.api_key,
                base_url=f"https://standardapi.sorftime.com/api/{self._api_url_path}",
                domain=base_config.domain,
                timeout=base_config.timeout
            )
        return self._config

    def _build_headers(self) -> Dict[str, str]:
        """
        构建统一的请求头

        Returns:
            请求头字典
        """
        config = self.get_config()
        return {
            "Authorization": f"BasicAuth {config.api_key}",
            "Content-Type": "application/json;charset=UTF-8"
        }

    def _build_url(self) -> str:
        """
        构建完整的请求 URL

        Returns:
            完整 URL
        """
        config = self.get_config()
        return f"{config.base_url}?domain={config.domain}"

    @staticmethod
    def _parse_response_code(data: Dict[str, Any]) -> Tuple[int, str]:
        """
        解析响应中的 code 和 message（处理大小写）

        Args:
            data: API 响应字典

        Returns:
            (code, message) 元组
        """
        code = data.get("code") or data.get("Code")
        message = data.get("message") or data.get("Message", "")
        return code, message

    @abstractmethod
    def _parse_data(self, data: Dict[str, Any], **kwargs) -> Any:
        """
        解析响应数据（子类实现）

        Args:
            data: API 响应数据
            **kwargs: 其他参数

        Returns:
            解析后的数据
        """
        pass

    @retry(
        exceptions=FETCH_EXCEPTIONS,
        max_retries=3,
        base_delay=2.0,
        max_delay=15.0
    )
    def _post(self, payload: Dict[str, Any], timeout: Optional[int] = None, **kwargs) -> Any:
        """
        发送 POST 请求并解析响应

        Args:
            payload: 请求数据
            timeout: 超时时间（秒），None 则使用配置中的值
            **kwargs: 传递给 _parse_data 的参数

        Returns:
            解析后的响应数据

        Raises:
            APIError: API 返回错误码
            requests.RequestException: 请求失败
            json.JSONDecodeError: JSON 解析失败
        """
        config = self.get_config()
        actual_timeout = timeout if timeout is not None else config.timeout

        response = requests.post(
            self._build_url(),
            json=payload,
            headers=self._build_headers(),
            timeout=actual_timeout,
            verify=True
        )
        response.raise_for_status()

        try:
            data = response.json()
        except json.JSONDecodeError as e:
            print(f"JSON parse error: {e}\nRaw output: {response.text[:500]}", file=sys.stderr)
            raise

        # 检查响应码
        code, message = self._parse_response_code(data)
        if code != 0 and code is not None:
            print(f"API error: code={code}, message={message}", file=sys.stderr)
            raise APIError(f"API error: code={code}, message={message}")

        return self._parse_data(data, **kwargs)


__all__ = [
    "APIError",
    "BaseAPIClient",
    "FETCH_EXCEPTIONS"
]