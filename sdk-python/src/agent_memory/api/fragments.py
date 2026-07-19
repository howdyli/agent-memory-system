"""Fragments API submodule."""

from typing import Any, Dict, List, Optional

from agent_memory.transport.base import Transport


class FragmentsAPI:
    """语义记忆片段操作接口。"""

    def __init__(self, transport: Transport):
        self._t = transport

    def create(
        self,
        content: str,
        fragment_type: str = "fact",
        importance_score: float = 0.5,
        ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """创建一条语义记忆片段。"""
        return self._t.request("POST", "/memory/fragments/", json={
            "fragment_type": fragment_type,
            "content": content,
            "importance_score": importance_score,
            "ttl": ttl,
        })

    def get(self, fragment_id: int) -> Dict[str, Any]:
        """获取一条记忆片段。"""
        return self._t.request("GET", f"/memory/fragments/{fragment_id}")

    def update(
        self,
        fragment_id: int,
        content: Optional[str] = None,
        importance_score: Optional[float] = None,
        ttl: Optional[int] = None,
    ) -> Dict[str, Any]:
        """更新一条记忆片段。"""
        data: Dict[str, Any] = {}
        if content is not None:
            data["content"] = content
        if importance_score is not None:
            data["importance_score"] = importance_score
        if ttl is not None:
            data["ttl"] = ttl
        return self._t.request("PUT", f"/memory/fragments/{fragment_id}", json=data)

    def delete(self, fragment_id: int) -> bool:
        """删除一条记忆片段。"""
        result = self._t.request("DELETE", f"/memory/fragments/{fragment_id}")
        if isinstance(result, dict):
            return result.get("success", True)
        return bool(result)

    def list(self, fragment_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出记忆片段。"""
        params = {}
        if fragment_type:
            params["type"] = fragment_type
        result = self._t.request("GET", "/memory/fragments/", params=params)
        if isinstance(result, dict):
            return result.get("fragments", result.get("data", []))
        return result if isinstance(result, list) else []

    def semantic_search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.3,
    ) -> List[Dict[str, Any]]:
        """语义搜索记忆片段。"""
        result = self._t.request("POST", "/memory/fragments/search", json={
            "query": query, "top_k": top_k, "threshold": threshold,
        })
        if isinstance(result, dict):
            return result.get("results", result.get("fragments", []))
        return result if isinstance(result, list) else []

    def batch_delete(self, fragment_ids: List[int]) -> Dict[str, Any]:
        """批量删除记忆片段。"""
        return self._t.request("DELETE", "/memory/fragments/batch", json={
            "fragment_ids": fragment_ids,
        })
