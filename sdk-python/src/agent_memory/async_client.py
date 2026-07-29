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
            result = await self._transport.request("POST", "/memory/recall", json={"query": query, "top_k": top_k})
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

    async def get_variable(self, key: str) -> Any:
        """读取单个记忆变量；不存在时返回 None（不抛）。"""
        try:
            result = await self._transport.request("GET", f"/memory/variables/{key}")
        except Exception as e:
            logger.debug(f"get_variable({key}) 失败: {e}")
            return None
        if isinstance(result, dict):
            return result.get("value")
        return None

    async def list_variables(self, detailed: bool = False) -> Any:
        """列出当前租户全部记忆变量。

        detailed=False → {key: value} 字典；
        detailed=True → [{key, value, ttl, expires_at}, ...] 列表。
        """
        params = "?detailed=true" if detailed else ""
        result = await self._transport.request("GET", f"/memory/variables{params}")
        if isinstance(result, dict):
            return result.get("variables", [] if detailed else {})
        return [] if detailed else {}

    async def search(self, query: str, top_k: int = 5, threshold: float = 0.3) -> List[Dict[str, Any]]:
        result = await self._transport.request("POST", "/memory/fragments/search", json={
            "query": query, "top_k": top_k, "threshold": threshold,
        })
        if isinstance(result, dict):
            return result.get("results", result.get("fragments", []))
        return result if isinstance(result, list) else []

    async def remember_fragment(
        self,
        content: str,
        fragment_type: str = "fact",
        importance_score: float = 0.5,
        ttl: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建语义记忆片段（与同步版 MemoryClient.remember_fragment 对齐）。"""
        return await self._transport.request("POST", "/memory/fragments", json={
            "fragment_type": fragment_type,
            "content": content,
            "importance_score": importance_score,
            "ttl": ttl,
            "metadata": metadata,
        })

    async def create_table(self, table_name: str, fields: List[Dict[str, str]]) -> bool:
        """创建记忆表（动态表结构）。表已存在/失败返回 False，不抛。

        fields 形如 [{"name": "title", "type": "TEXT"}, ...]。
        """
        try:
            result = await self._transport.request("POST", "/memory/tables", json={
                "table_name": table_name, "fields": fields,
            })
        except Exception as e:
            logger.debug(f"create_table({table_name}) 失败: {e}")
            return False
        if isinstance(result, dict):
            return result.get("success", True)
        return bool(result)

    async def list_tables(self) -> List[str]:
        """列出当前租户已创建的记忆表名。失败返回 []。"""
        try:
            result = await self._transport.request("GET", "/memory/tables/")
        except Exception as e:
            logger.debug(f"list_tables 失败: {e}")
            return []
        if isinstance(result, dict):
            tables = result.get("tables", [])
            # 兼容 [{"table_name": ...}] 与 ["name"] 两种形态
            return [t.get("table_name", t) if isinstance(t, dict) else t for t in tables]
        return []

    async def add_record(self, table_name: str, record: Dict[str, Any]) -> Optional[int]:
        """向记忆表插入一条记录，返回新记录 ID；失败返回 None。"""
        try:
            result = await self._transport.request(
                "POST", f"/memory/tables/{table_name}/records", json={"record": record},
            )
        except Exception as e:
            logger.debug(f"add_record({table_name}) 失败: {e}")
            return None
        if isinstance(result, dict):
            return result.get("record_id", result.get("id"))
        return None

    async def query_records(
        self,
        table_name: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """查询记忆表记录（等值过滤）。失败返回 []。"""
        try:
            result = await self._transport.request(
                "POST", f"/memory/tables/{table_name}/query",
                json={"filters": filters, "limit": limit},
            )
        except Exception as e:
            logger.debug(f"query_records({table_name}) 失败: {e}")
            return []
        if isinstance(result, dict):
            return result.get("records", [])
        return []

    async def update_record(
        self, table_name: str, record_id: int, updates: Dict[str, Any],
    ) -> bool:
        """按 record_id 更新记忆表记录。失败返回 False，不抛。"""
        try:
            result = await self._transport.request(
                "PUT", f"/memory/tables/{table_name}/records",
                json={"updates": updates}, params={"record_id": record_id},
            )
        except Exception as e:
            logger.debug(f"update_record({table_name}, {record_id}) 失败: {e}")
            return False
        if isinstance(result, dict):
            return result.get("success", True)
        return bool(result)

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    def __repr__(self) -> str:
        return "AsyncMemoryClient()"
