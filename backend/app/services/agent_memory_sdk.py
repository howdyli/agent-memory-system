"""
Agent Memory SDK

封装所有记忆操作为统一客户端，Agent 可直接 import 使用，
无需关心底层 HTTP 或服务调用细节。
"""
import logging
from typing import Optional, Any, Dict, List

logger = logging.getLogger(__name__)

from app.services.auto_recall_service import (
    auto_recall,
    search_relevant_memories,
    inject_memory_context,
)
from app.services.memory_variable_service import (
    set_memory_variable,
    get_memory_variable,
    delete_memory_variable,
    list_memory_variables,
)
from app.services.memory_extraction_service import (
    get_user_context_for_llm,
    process_user_input,
)
from app.services.memory_table_service import (
    add_record,
    query_records,
    list_tables,
    create_memory_table,
)
from app.services.memory_fragment_service import (
    create_fragment,
    list_fragments,
)


class AgentMemoryClient:
    """
    Agent 记忆客户端

    提供统一的记忆读写接口，供 Agent 循环编排、LangChain 集成、
    MCP Server 等场景直接使用。
    """

    def __init__(self, user_id: int):
        self.user_id = user_id

    # ================================================================
    # 记忆召回
    # ================================================================

    def recall(self, query: str, top_k: int = 5) -> str:
        """
        召回与 query 相关的记忆，返回可直接注入 Prompt 的格式化上下文。

        Args:
            query: 查询文本
            top_k: 返回记忆条数

        Returns:
            格式化的记忆上下文字符串；无相关记忆时返回空字符串
        """
        try:
            result = auto_recall(user_id=self.user_id, query=query)
            if result.get("success") and result.get("context"):
                return result["context"]
            return ""
        except BaseException as e:
            logger.error(f"recall 失败: {e}")
            return ""

    # ================================================================
    # 记忆存储
    # ================================================================

    def remember(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        存储一条 KV 记忆变量。

        Args:
            key: 变量名
            value: 变量值
            ttl: 过期时间（秒），None 表示永久

        Returns:
            是否存储成功
        """
        return set_memory_variable(
            user_id=self.user_id, key=key, value=value, ttl=ttl
        )

    def create_table(
        self, table_name: str, fields: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        创建一个结构化记忆表。

        Args:
            table_name: 表名
            fields: 字段定义 [{"name": "...", "type": "TEXT", ...}]

        Returns:
            创建结果
        """
        return create_memory_table(
            user_id=self.user_id, table_name=table_name, fields=fields
        )

    def remember_structured(
        self, table_name: str, record: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        向动态记忆表添加一条结构化记录。

        Args:
            table_name: 表名
            record: 记录数据

        Returns:
            添加结果
        """
        return add_record(
            user_id=self.user_id, table_name=table_name, record=record
        )

    def remember_fragment(
        self,
        content: str,
        fragment_type: str = "fact",
        importance_score: float = 0.5,
        ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        创建一条语义记忆片段（存入 ChromaDB）。

        Args:
            content: 片段内容
            fragment_type: 类型（info / preference / plan / fact）
            importance_score: 重要性评分 0-1
            ttl: 过期时间（秒）

        Returns:
            创建结果
        """
        return create_fragment(
            user_id=self.user_id,
            fragment_type=fragment_type,
            content=content,
            importance_score=importance_score,
            ttl=ttl,
        )

    # ================================================================
    # 记忆删除
    # ================================================================

    def forget(self, key: str) -> bool:
        """
        删除一条 KV 记忆变量。

        Args:
            key: 变量名

        Returns:
            是否删除成功
        """
        return delete_memory_variable(user_id=self.user_id, key=key)

    # ================================================================
    # 上下文获取
    # ================================================================

    def get_context(self, session_id: Optional[str] = None) -> str:
        """
        获取当前用户完整记忆上下文（格式化字符串，可直接拼入 system prompt）。

        Args:
            session_id: 会话 ID（可选）

        Returns:
            格式化的上下文字符串
        """
        return get_user_context_for_llm(
            user_id=self.user_id, session_id=session_id
        )

    # ================================================================
    # 语义搜索
    # ================================================================

    def search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """
        语义搜索相关记忆片段。

        Args:
            query: 查询文本
            top_k: 返回条数
            threshold: 相似度阈值

        Returns:
            记忆列表
        """
        try:
            result = search_relevant_memories(
                user_id=self.user_id,
                query=query,
                top_k=top_k,
                threshold=threshold,
            )
            if result.get("success"):
                return result.get("memories", [])
            return []
        except BaseException as e:
            logger.error(f"search 失败: {e}")
            return []

    # ================================================================
    # 记忆变量列表
    # ================================================================

    def list_variables(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """列出所有 KV 记忆变量。"""
        return list_memory_variables(user_id=self.user_id, session_id=session_id)

    # ================================================================
    # 记忆表列表
    # ================================================================

    def list_tables(self) -> List[Dict[str, Any]]:
        """列出用户所有动态记忆表。"""
        result = list_tables(user_id=self.user_id)
        if result.get("success"):
            return result.get("tables", [])
        return []

    # ================================================================
    # 带记忆的对话（委托给 agent_loop）
    # ================================================================

    def chat(
        self,
        user_message: str,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        带记忆的完整对话循环。

        内部流程：
        1. 自动召回相关记忆
        2. 注入上下文到 system prompt
        3. 调用 LLM（支持 tool calling）
        4. 从对话中抽取新记忆

        Args:
            user_message: 用户消息
            system_prompt: 自定义 system prompt（可选）
            session_id: 会话 ID（可选）

        Returns:
            对话结果字典
        """
        # 延迟导入避免循环依赖
        from app.services.agent_loop import memory_aware_chat

        return memory_aware_chat(
            user_id=self.user_id,
            user_message=user_message,
            system_prompt=system_prompt,
            session_id=session_id,
        )
