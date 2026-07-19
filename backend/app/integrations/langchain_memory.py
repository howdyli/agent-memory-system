"""
LangChain Memory 集成 — 兼容 Re-export

从 SDK 导入并重新导出，保持 backend 内部 import 路径不变。
提供 create_langchain_memory(user_id) 兼容接口。
"""
from typing import Optional

from agent_memory import MemoryClient
from agent_memory.integrations.langchain_memory import (
    AgentMemoryLangChain,
    create_langchain_memory as _create_langchain_memory,
)


def create_langchain_memory(user_id: int, session_id: Optional[str] = None) -> AgentMemoryLangChain:
    """
    兼容旧接口：接受 user_id，内部创建嵌入模式 MemoryClient。
    """
    client = MemoryClient(mode="embedded", user_id=user_id)
    return _create_langchain_memory(client, session_id=session_id)


__all__ = ["AgentMemoryLangChain", "create_langchain_memory"]
