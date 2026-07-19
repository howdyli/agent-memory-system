"""
Agent Memory SDK — 兼容层

保留原有 AgentMemoryClient 接口，内部委托给新的 agent_memory SDK。
backend 内部代码无需修改 import 路径。
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from agent_memory import MemoryClient as _MemoryClient


class AgentMemoryClient:
    """
    Agent 记忆客户端（兼容层）。

    保持与旧接口完全一致（接受 user_id），
    内部委托给 agent_memory.MemoryClient（嵌入模式）。
    """

    def __init__(self, user_id: int):
        self.user_id = user_id
        self._client = _MemoryClient(mode="embedded", user_id=user_id)

    # ================================================================
    # 记忆召回
    # ================================================================

    def recall(self, query: str, top_k: int = 5) -> str:
        try:
            result = self._client.recall.auto(query=query)
            if isinstance(result, dict) and result.get("context"):
                return result["context"]
            return ""
        except BaseException as e:
            logger.error(f"recall 失败: {e}")
            return ""

    # ================================================================
    # 记忆存储
    # ================================================================

    def remember(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        return self._client.variables.set(key, value, ttl=ttl)

    def create_table(
        self, table_name: str, fields: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        return self._client.tables.create(table_name=table_name, fields=fields)

    def remember_structured(
        self, table_name: str, record: Dict[str, Any]
    ) -> Dict[str, Any]:
        return self._client.tables.add_record(table_name=table_name, record=record)

    def remember_fragment(
        self,
        content: str,
        fragment_type: str = "fact",
        importance_score: float = 0.5,
        ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        return self._client.fragments.create(
            content=content,
            fragment_type=fragment_type,
            importance_score=importance_score,
            ttl=ttl,
        )

    # ================================================================
    # 记忆删除
    # ================================================================

    def forget(self, key: str) -> bool:
        return self._client.variables.delete(key)

    # ================================================================
    # 上下文获取
    # ================================================================

    def get_context(self, session_id: Optional[str] = None) -> str:
        return self._client.get_context(session_id=session_id)

    # ================================================================
    # 语义搜索
    # ================================================================

    def search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.3,
    ) -> List[Dict[str, Any]]:
        return self._client.fragments.semantic_search(
            query, top_k=top_k, threshold=threshold,
        )

    # ================================================================
    # 列表
    # ================================================================

    def list_variables(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        return self._client.variables.list(session_id=session_id)

    def list_tables(self) -> List[Dict[str, Any]]:
        return self._client.tables.list()

    # ================================================================
    # 带记忆的对话（委托给 agent_loop）
    # ================================================================

    def chat(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        from app.services.agent_loop import memory_aware_chat

        return memory_aware_chat(
            user_id=self.user_id,
            user_message=user_message,
            system_prompt=system_prompt,
            session_id=session_id,
        )
