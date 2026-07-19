"""Transport abstract base class."""

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterator, Optional


class Transport(ABC):
    """
    传输层抽象接口。

    HTTP 模式和嵌入模式都实现此接口，
    使上层 API 子模块无需关心底层通信方式。
    """

    @abstractmethod
    def request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        """
        发送同步请求并返回响应数据。

        Args:
            method: HTTP 方法（GET/POST/PUT/DELETE）
            path: API 路径（如 /memory/variables）
            json: 请求体 JSON
            params: URL 查询参数

        Returns:
            解析后的响应数据（dict / list / str）
        """
        ...

    @abstractmethod
    def request_stream(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
    ) -> Iterator[str]:
        """
        发送流式请求并逐行返回 SSE 数据。

        Args:
            method: HTTP 方法
            path: API 路径
            json: 请求体 JSON

        Yields:
            SSE 数据行字符串
        """
        ...

    @abstractmethod
    def close(self) -> None:
        """关闭传输连接，释放资源。"""
        ...
