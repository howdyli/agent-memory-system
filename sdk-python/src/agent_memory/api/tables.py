"""Tables API submodule."""

from typing import Any, Dict, List, Optional

from agent_memory.transport.base import Transport


class TablesAPI:
    """结构化记忆表操作接口。"""

    def __init__(self, transport: Transport):
        self._t = transport

    def list(self) -> List[Dict[str, Any]]:
        """列出所有记忆表。"""
        result = self._t.request("GET", "/memory/tables/")
        if isinstance(result, dict):
            return result.get("tables", [])
        return result if isinstance(result, list) else []

    def create(
        self,
        table_name: str,
        fields: List[Dict[str, Any]],
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """创建记忆表。"""
        data: Dict[str, Any] = {"table_name": table_name, "fields": fields}
        if description:
            data["description"] = description
        return self._t.request("POST", "/memory/tables/", json=data)

    def info(self, table_name: str) -> Dict[str, Any]:
        """获取表信息。"""
        return self._t.request("GET", f"/memory/tables/{table_name}/info")

    def drop(self, table_name: str) -> Dict[str, Any]:
        """删除表。"""
        return self._t.request("DELETE", f"/memory/tables/{table_name}")

    def add_record(self, table_name: str, record: Dict[str, Any]) -> Dict[str, Any]:
        """添加记录。"""
        return self._t.request(
            "POST", f"/memory/tables/{table_name}/records",
            json={"record": record},
        )

    def query_records(self, table_name: str) -> List[Dict[str, Any]]:
        """查询表中所有记录。"""
        result = self._t.request("GET", f"/memory/tables/{table_name}/records")
        if isinstance(result, dict):
            return result.get("records", [])
        return result if isinstance(result, list) else []

    def update_record(
        self, table_name: str, record_id: int, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        """更新记录。"""
        return self._t.request(
            "PUT", f"/memory/tables/{table_name}/records",
            json={"updates": updates},
            params={"record_id": record_id},
        )

    def delete_record(self, table_name: str, record_id: int) -> Dict[str, Any]:
        """删除记录。"""
        return self._t.request(
            "DELETE", f"/memory/tables/{table_name}/records",
            params={"record_id": record_id},
        )

    def batch_add(self, table_name: str, records: List[Dict[str, Any]]) -> Dict[str, Any]:
        """批量添加记录。"""
        return self._t.request(
            "POST", f"/memory/tables/{table_name}/records/batch",
            json={"records": records},
        )

    def query_with_filters(
        self,
        table_name: str,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> Dict[str, Any]:
        """带过滤条件查询。"""
        data: Dict[str, Any] = {}
        if filters:
            data["filters"] = filters
        if order_by:
            data["order_by"] = order_by
        if limit is not None:
            data["limit"] = limit
        if offset is not None:
            data["offset"] = offset
        return self._t.request("POST", f"/memory/tables/{table_name}/query", json=data)
