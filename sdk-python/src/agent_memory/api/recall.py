"""Recall API submodule."""

from typing import Any, Dict, List, Optional

from agent_memory.transport.base import Transport


class RecallAPI:
    """自动召回操作接口。"""

    def __init__(self, transport: Transport):
        self._t = transport

    def auto(self, query: str, user_id: Optional[str] = None, top_k: Optional[int] = None) -> Dict[str, Any]:
        """自动召回相关记忆。"""
        data: Dict[str, Any] = {"query": query}
        if user_id:
            data["user_id"] = user_id
        if top_k is not None:
            data["top_k"] = top_k
        return self._t.request("POST", "/memory/recall", json=data)

    def search(self, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """搜索相关记忆。"""
        result = self._t.request("POST", "/memory/recall/search", json={
            "query": query, "top_k": top_k,
        })
        if isinstance(result, dict):
            return result.get("memories", [])
        return result if isinstance(result, list) else []

    def config(self) -> Dict[str, Any]:
        """获取召回配置。"""
        return self._t.request("GET", "/memory/recall/config")

    def update_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """更新召回配置。"""
        return self._t.request("PUT", "/memory/recall/config", json=config)

    def stats(self) -> Dict[str, Any]:
        """获取召回统计。"""
        return self._t.request("GET", "/memory/recall/stats")

    def summary(self, user_id: Optional[str] = None) -> Dict[str, Any]:
        """获取召回摘要。"""
        data: Dict[str, Any] = {}
        if user_id:
            data["user_id"] = user_id
        return self._t.request("POST", "/memory/recall/summary", json=data)

    def inject(self, query: str, memories: List[Any]) -> Dict[str, Any]:
        """注入记忆上下文。"""
        return self._t.request("POST", "/memory/recall/inject", json={
            "query": query, "memories": memories,
        })
