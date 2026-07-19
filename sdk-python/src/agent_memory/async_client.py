"""AsyncMemoryClient — async/await unified entry point."""

import logging
from typing import Any, Dict, List, Optional

from agent_memory.transport.base import Transport
from agent_memory.transport.http import HttpTransport

logger = logging.getLogger(__name__)


class _AsyncHttpTransport:
    """
    异步 HTTP 传输包装。

    内部使用 httpx.AsyncClient，提供 async request 接口。
    注意：这不是 Transport 的子类（因为 Transport 是同步的），
    而是独立的异步实现。
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        token: Optional[str] = None,
        workspace_id: Optional[str] = None,
        timeout: float = 30.0,
    ):
        import httpx

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        if workspace_id:
            headers["X-Workspace-Id"] = str(workspace_id)

        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers=headers,
            timeout=timeout,
        )

    async def request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        response = await self._client.request(
            method=method.upper(), url=path, json=json, params=params,
        )
        if response.status_code < 400:
            if response.status_code == 204:
                return None
            try:
                return response.json()
            except Exception:
                return response.text
        from agent_memory.exceptions import HTTPError
        raise HTTPError(status_code=response.status_code, detail=response.text[:500])

    async def close(self) -> None:
        await self._client.aclose()


class AsyncMemoryClient:
    """
    Agent Memory 异步客户端。

    用法::

        async with AsyncMemoryClient(base_url="https://mem.example.com", api_key="amk_xxx") as client:
            await client.remember("user_name", "鑫海")
            ctx = await client.recall_context("鑫海的项目")
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        workspace_id: Optional[str] = None,
        token: Optional[str] = None,
        timeout: float = 30.0,
    ):
        self._transport = _AsyncHttpTransport(
            base_url=base_url,
            api_key=api_key,
            token=token,
            workspace_id=workspace_id,
            timeout=timeout,
        )

    async def remember(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        result = await self._transport.request("POST", "/memory/variables", json={
            "key": key, "value": value, "ttl": ttl,
        })
        if isinstance(result, dict):
            return result.get("success", True)
        return bool(result)

    async def recall_context(self, query: str, top_k: int = 5) -> str:
        try:
            result = await self._transport.request("POST", "/memory/recall/", json={"query": query})
            if isinstance(result, dict) and result.get("context"):
                return result["context"]
            return ""
        except Exception as e:
            logger.error(f"recall_context 失败: {e}")
            return ""

    async def forget(self, key: str) -> bool:
        result = await self._transport.request("DELETE", f"/memory/variables/{key}")
        if isinstance(result, dict):
            return result.get("success", True)
        return bool(result)

    async def search(self, query: str, top_k: int = 5, threshold: float = 0.3) -> List[Dict[str, Any]]:
        result = await self._transport.request("POST", "/memory/fragments/search", json={
            "query": query, "top_k": top_k, "threshold": threshold,
        })
        if isinstance(result, dict):
            return result.get("results", result.get("fragments", []))
        return result if isinstance(result, list) else []

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    def __repr__(self) -> str:
        return "AsyncMemoryClient()"
