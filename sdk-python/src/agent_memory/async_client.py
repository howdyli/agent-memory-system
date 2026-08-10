"""AsyncMemoryClient — async/await unified entry point."""

import logging
from typing import Any, Dict, List, Optional

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

        self.base_url = base_url.rstrip("/")
        # 自动补齐 /api/v1 前缀，避免用户忘记配置导致 404
        if "/api/" not in self.base_url:
            self.base_url = self.base_url + "/api/v1"

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        elif token:
            headers["Authorization"] = f"Bearer {token}"
        if workspace_id:
            headers["X-Workspace-Id"] = str(workspace_id)

        # 不使用 httpx 的 base_url（绝对路径会丢失前缀），改为手动拼接
        self._client = httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,  # 防御尾斜杠 307/308（httpx 会保留方法与 body 重放）
        )

    async def request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        # 防御：base_url 手动拼接后，绝对 URL 会拼出畸形地址静默打错服务
        if path.startswith(("http://", "https://")):
            raise ValueError("request() 仅接受相对路径（如 /memory/...），请勿传入绝对 URL")
        response = await self._client.request(
            method=method.upper(), url=self.base_url + path, json=json, params=params,
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

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        """通用请求透传（稳定公开接口）。

        供需要原始响应体、降级信号或自定义端点的消费方使用：
        不吞异常、不改写响应体，<400 返回已解析 JSON（非 JSON 返回文本），
        异常（HTTPError/TransportError 等）原样抛出。

        Args:
            method: HTTP 方法（GET/POST/PUT/DELETE）
            path: API 路径，必须为以 / 开头的相对路径（相对 /api/v1，
                如 /memory/hybrid-search）；传入绝对 URL 抛 ValueError
            json: 请求体 JSON
            params: URL 查询参数
        """
        return await self._transport.request(method, path, json=json, params=params)

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

    # ================================================================
    # Graph API（知识图谱操作）
    # ================================================================

    async def graph_query(self, entity: str, *, depth: int = 2) -> List[Dict[str, Any]]:
        """查询实体关联图谱。

        调用 GET /memory/graph/query?q={entity}
        返回邻居节点列表，失败返回空列表。
        """
        try:
            result = await self._transport.request(
                "GET", "/memory/graph/query", params={"q": entity.strip()},
            )
        except Exception as e:
            logger.warning("graph_query 失败: %s", e)
            return []
        if isinstance(result, dict):
            return result.get("neighbors", result.get("results", []))
        return result if isinstance(result, list) else []

    async def graph_ingest(self, text: str, *, session_id: Optional[str] = None) -> Dict[str, Any]:
        """从文本中抽取实体和关系，写入图谱。

        调用 POST /memory/graph/extract
        返回 {"entities_extracted": N, "relations_created": N}。
        """
        try:
            result = await self._transport.request(
                "POST", "/memory/graph/extract", json={"text": text},
            )
        except Exception as e:
            logger.warning("graph_ingest 失败: %s", e)
            return {"entities_extracted": 0, "relations_created": 0}
        return result if isinstance(result, dict) else {"entities_extracted": 0, "relations_created": 0}

    # ================================================================
    # Lifecycle API（记忆生命周期管理）
    # ================================================================

    async def run_lifecycle_maintenance(self) -> Dict[str, Any]:
        """调用生命周期维护 API，执行记忆清理/衰减/归档。

        调用 POST /memory/lifecycle/run-cleanup
        返回含 {marked_cold, soft_deleted, decayed} 的统计字典。
        """
        try:
            result = await self._transport.request(
                "POST", "/memory/lifecycle/run-cleanup",
            )
        except Exception as e:
            logger.warning("run_lifecycle_maintenance 失败: %s", e)
            return {"marked_cold": 0, "soft_deleted": 0, "decayed": 0}
        return result if isinstance(result, dict) else {}

    async def detect_memory_conflicts(
        self,
        content: str = "*",
        threshold: float = 0.85,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """调用冲突检测 API，查找重复/矛盾记忆。

        调用 POST /memory/lifecycle/duplicates/find
        返回重复记忆对列表。
        """
        try:
            result = await self._transport.request(
                "POST", "/memory/lifecycle/duplicates/find",
                json={"content": content, "threshold": threshold, "limit": limit},
            )
        except Exception as e:
            logger.warning("detect_memory_conflicts 失败: %s", e)
            return []
        if isinstance(result, dict):
            return result.get("duplicates", result.get("conflicts", []))
        return result if isinstance(result, list) else []

    # ================================================================
    # Extraction API（LLM 驱动的结构化记忆抽取）
    # ================================================================

    async def extract_and_save(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """从对话中抽取结构化记忆并保存。

        调用 POST /memory/extraction/batch-extract
        返回 {"variables": [...], "facts": [...], "preferences": [...], "plans": [...]}。
        """
        try:
            result = await self._transport.request(
                "POST", "/memory/extraction/batch-extract",
                json={"session_id": session_id, "conversation_history": messages},
            )
        except Exception as e:
            logger.warning("extract_and_save 失败: %s", e)
            return {"variables": [], "facts": [], "preferences": [], "plans": []}
        return result if isinstance(result, dict) else {}

    # 别名，与 extract_and_save 等价
    batch_extract = extract_and_save

    # ================================================================
    # 生命周期
    # ================================================================

    async def close(self) -> None:
        await self._transport.close()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    def __repr__(self) -> str:
        return "AsyncMemoryClient()"
