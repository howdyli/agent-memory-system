"""MemoryClient — synchronous unified entry point."""

import logging
from typing import Any, Dict, List, Optional

from agent_memory.api.fragments import FragmentsAPI
from agent_memory.api.graph import GraphAPI
from agent_memory.api.recall import RecallAPI
from agent_memory.api.tables import TablesAPI
from agent_memory.api.variables import VariablesAPI
from agent_memory.api.events import EventsAPI, WebhooksAPI
from agent_memory.transport import HttpTransport, EmbeddedTransport
from agent_memory.transport.base import Transport

logger = logging.getLogger(__name__)


class MemoryClient:
    """
    Agent Memory 统一客户端。

    支持两种模式：

    HTTP 模式（连接远程服务）::

        client = MemoryClient(base_url="https://mem.example.com", api_key="amk_xxx")
        client.remember("user_name", "鑫海")
        ctx = client.recall("鑫海的项目")

    嵌入模式（直连本地 Store）::

        client = MemoryClient(mode="embedded", db_path="./mem.db")
        client.remember_fragment("喜欢极简设计", importance_score=0.9)
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        workspace_id: Optional[str] = None,
        mode: str = "http",
        # 嵌入模式参数
        db_path: str = "agent_memory.db",
        vector_backend: str = "chroma",
        token: Optional[str] = None,
        # 嵌入模式 user_id
        user_id: int = 1,
        timeout: float = 30.0,
    ):
        if mode == "http":
            if not base_url:
                raise ValueError("HTTP 模式需要 base_url 参数")
            self._transport: Transport = HttpTransport(
                base_url=base_url,
                api_key=api_key,
                token=token,
                workspace_id=workspace_id,
                timeout=timeout,
            )
        elif mode == "embedded":
            self._transport = EmbeddedTransport(
                db_path=db_path,
                vector_backend=vector_backend,
                user_id=user_id,
                workspace_id=int(workspace_id) if workspace_id else None,
            )
        else:
            raise ValueError(f"未知 mode: {mode}，支持 'http' 或 'embedded'")

        # 初始化各 API 子模块
        self.variables = VariablesAPI(self._transport)
        self.fragments = FragmentsAPI(self._transport)
        self.tables = TablesAPI(self._transport)
        self.graph = GraphAPI(self._transport)
        self.recall = RecallAPI(self._transport)
        self.events = EventsAPI(self._transport)
        self.webhooks = WebhooksAPI(self._transport)

    # ================================================================
    # 高层便捷方法（与现有 AgentMemoryClient 兼容）
    # ================================================================

    def remember(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """存储一条 KV 记忆变量。"""
        return self.variables.set(key, value, ttl=ttl)

    def recall_context(self, query: str, top_k: int = 5) -> str:
        """
        召回与 query 相关的记忆，返回可直接注入 Prompt 的格式化上下文。

        注意：方法名 recall_context 避免与子模块 self.recall 冲突。
        """
        try:
            result = self.recall.auto(query=query)
            if isinstance(result, dict) and result.get("context"):
                return result["context"]
            return ""
        except Exception as e:
            logger.error(f"recall_context 失败: {e}")
            return ""

    def forget(self, key: str) -> bool:
        """删除一条 KV 记忆变量。"""
        return self.variables.delete(key)

    def search(self, query: str, top_k: int = 5, threshold: float = 0.3) -> List[Dict[str, Any]]:
        """语义搜索相关记忆片段。"""
        return self.fragments.semantic_search(query, top_k=top_k, threshold=threshold)

    def get_context(self, session_id: Optional[str] = None) -> str:
        """获取当前用户完整记忆上下文。"""
        result = self._transport.request(
            "GET", "/memory/extraction/context",
            params={"session_id": session_id} if session_id else None,
        )
        if isinstance(result, dict):
            return result.get("context", "")
        return str(result) if result else ""

    def create_table(
        self, table_name: str, fields: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """创建结构化记忆表。"""
        return self.tables.create(table_name=table_name, fields=fields)

    def remember_structured(
        self, table_name: str, record: Dict[str, Any]
    ) -> Dict[str, Any]:
        """向记忆表添加结构化记录。"""
        return self.tables.add_record(table_name=table_name, record=record)

    def remember_fragment(
        self,
        content: str,
        fragment_type: str = "fact",
        importance_score: float = 0.5,
        ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """创建语义记忆片段。"""
        return self.fragments.create(
            content=content,
            fragment_type=fragment_type,
            importance_score=importance_score,
            ttl=ttl,
        )

    def list_variables(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """列出所有 KV 记忆变量。"""
        return self.variables.list(session_id=session_id)

    def list_tables(self) -> List[Dict[str, Any]]:
        """列出所有动态记忆表。"""
        return self.tables.list()

    # ================================================================
    # 生命周期
    # ================================================================

    def close(self) -> None:
        """关闭客户端，释放资源。"""
        self._transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __repr__(self) -> str:
        mode = "embedded" if isinstance(self._transport, EmbeddedTransport) else "http"
        return f"MemoryClient(mode={mode!r})"
