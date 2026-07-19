"""Variables API submodule."""

from typing import Any, Dict, List, Optional

from agent_memory.transport.base import Transport


class VariablesAPI:
    """记忆变量（KV）操作接口。"""

    def __init__(self, transport: Transport):
        self._t = transport

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """存储一条 KV 记忆变量。"""
        result = self._t.request("POST", "/memory/variables", json={
            "key": key, "value": value, "ttl": ttl,
        })
        if isinstance(result, dict):
            return result.get("success", True)
        return bool(result)

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """获取一条记忆变量。"""
        return self._t.request("GET", f"/memory/variables/{key}")

    def delete(self, key: str) -> bool:
        """删除一条记忆变量。"""
        result = self._t.request("DELETE", f"/memory/variables/{key}")
        if isinstance(result, dict):
            return result.get("success", True)
        return bool(result)

    def list(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """列出所有记忆变量。"""
        params = {}
        if session_id:
            params["session_id"] = session_id
        return self._t.request("GET", "/memory/variables", params=params)

    def update(self, key: str, value: Any) -> bool:
        """更新一条记忆变量的值。"""
        result = self._t.request("PUT", f"/memory/variables/{key}", json={"value": value})
        if isinstance(result, dict):
            return result.get("success", True)
        return bool(result)

    def update_ttl(self, key: str, ttl: Optional[int]) -> bool:
        """更新记忆变量的 TTL。"""
        result = self._t.request("PUT", f"/memory/variables/{key}/ttl", json={"ttl": ttl})
        if isinstance(result, dict):
            return result.get("success", True)
        return bool(result)

    def batch_get(self, keys: List[str]) -> Dict[str, Any]:
        """批量获取记忆变量。"""
        return self._t.request("POST", "/memory/variables/batch-get", json={"keys": keys})

    def batch_set(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """批量设置记忆变量。"""
        return self._t.request("POST", "/memory/variables/batch-set", json={"items": items})
