"""HTTP Transport implementation using httpx."""

from typing import Any, Dict, Iterator, Optional

import httpx

from agent_memory.exceptions import (
    AuthenticationError,
    HTTPError,
    NotFoundError,
    PermissionDeniedError,
    TransportError,
)
from agent_memory.transport.base import Transport


class HttpTransport(Transport):
    """
    HTTP 传输层实现。

    通过 httpx 客户端访问远程 Agent Memory 服务。
    自动注入认证头（API Key / JWT）和 workspace 上下文。
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        token: Optional[str] = None,
        workspace_id: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.token = token
        self.workspace_id = workspace_id

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        if workspace_id:
            headers["X-Workspace-Id"] = str(workspace_id)

        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
        )

    def request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        try:
            response = self._client.request(
                method=method.upper(),
                url=path,
                json=json,
                params=params,
            )
        except httpx.RequestError as e:
            raise TransportError(f"请求失败: {e}") from e

        return self._handle_response(response)

    def request_stream(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
    ) -> Iterator[str]:
        try:
            with self._client.stream(
                method=method.upper(),
                url=path,
                json=json,
            ) as response:
                if response.status_code >= 400:
                    response.read()
                    self._raise_for_status(response.status_code, response.text)
                for line in response.iter_lines():
                    if line:
                        yield line
        except httpx.RequestError as e:
            raise TransportError(f"流式请求失败: {e}") from e

    def close(self) -> None:
        self._client.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _handle_response(response: httpx.Response) -> Any:
        if response.status_code < 400:
            if response.status_code == 204:
                return None
            try:
                return response.json()
            except Exception:
                return response.text

        HttpTransport._raise_for_status(response.status_code, response.text)

    @staticmethod
    def _raise_for_status(status_code: int, body: str) -> None:
        detail = body[:500] if body else ""
        if status_code == 401:
            raise AuthenticationError(detail)
        elif status_code == 403:
            raise PermissionDeniedError(detail)
        elif status_code == 404:
            raise NotFoundError(detail)
        else:
            raise HTTPError(status_code=status_code, detail=detail)
