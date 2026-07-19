"""
MCP Server 集成 — 兼容 Re-export

从 SDK 导入并重新导出，保持 backend 内部 import 路径不变。
提供 create_mcp_server(user_id) 兼容接口。
"""
from typing import Optional

from agent_memory.integrations.mcp import create_mcp_server as _create_mcp_server


def create_mcp_server(user_id: Optional[int] = None):
    """
    兼容旧接口：接受 user_id，内部委托给 SDK。
    """
    return _create_mcp_server(user_id=user_id)


__all__ = ["create_mcp_server"]
