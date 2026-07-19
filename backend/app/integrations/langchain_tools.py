"""
LangChain Tool 集成 — 兼容 Re-export

从 SDK 导入并重新导出，保持 backend 内部 import 路径不变。
提供 get_memory_tools(user_id) 兼容接口。
"""
from typing import List

from agent_memory import MemoryClient
from agent_memory.integrations.langchain import get_memory_tools as _get_memory_tools


def get_memory_tools(user_id: int) -> List:
    """
    兼容旧接口：接受 user_id，内部创建嵌入模式 MemoryClient。
    """
    client = MemoryClient(mode="embedded", user_id=user_id)
    return _get_memory_tools(client)


__all__ = ["get_memory_tools"]
