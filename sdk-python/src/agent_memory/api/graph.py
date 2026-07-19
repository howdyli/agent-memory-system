"""Graph API submodule."""

from typing import Any, Dict, List, Optional

from agent_memory.transport.base import Transport


class GraphAPI:
    """知识图谱操作接口。"""

    def __init__(self, transport: Transport):
        self._t = transport

    def search_entities(
        self, query: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """搜索实体。"""
        params = {}
        if query:
            params["query"] = query
        result = self._t.request("GET", "/memory/graph/entities", params=params)
        if isinstance(result, dict):
            return result.get("entities", [])
        return result if isinstance(result, list) else []

    def get_entity(self, entity_id: str) -> Dict[str, Any]:
        """获取实体详情。"""
        return self._t.request("GET", f"/memory/graph/entities/{entity_id}")

    def create_entity(
        self,
        name: str,
        entity_type: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建实体。"""
        data: Dict[str, Any] = {"name": name, "entity_type": entity_type}
        if properties:
            data["properties"] = properties
        return self._t.request("POST", "/memory/graph/entities", json=data)

    def update_entity(
        self,
        entity_id: str,
        name: Optional[str] = None,
        entity_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """更新实体。"""
        data: Dict[str, Any] = {}
        if name is not None:
            data["name"] = name
        if entity_type is not None:
            data["entity_type"] = entity_type
        if metadata is not None:
            data["metadata"] = metadata
        return self._t.request("PUT", f"/memory/graph/entities/{entity_id}", json=data)

    def delete_entity(self, entity_id: str) -> Dict[str, Any]:
        """删除实体。"""
        return self._t.request("DELETE", f"/memory/graph/entities/{entity_id}")

    def merge_entities(self, source_ids: List[str], target_id: str) -> Dict[str, Any]:
        """合并实体。"""
        return self._t.request("POST", "/memory/graph/entities/merge", json={
            "source_entity_ids": source_ids, "target_entity_id": target_id,
        })

    def create_relationship(
        self,
        source_name: str,
        target_name: str,
        relation_type: str,
        source_type: str = "person",
        target_type: str = "organization",
        properties: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """创建关系（基于实体名称）。"""
        data: Dict[str, Any] = {
            "source_name": source_name,
            "target_name": target_name,
            "relation_type": relation_type,
            "source_type": source_type,
            "target_type": target_type,
        }
        if properties:
            data["properties"] = properties
        return self._t.request("POST", "/memory/graph/relationships", json=data)

    def list_relationships(
        self, entity_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """列出关系。"""
        params = {}
        if entity_id:
            params["entity_id"] = entity_id
        result = self._t.request("GET", "/memory/graph/relationships", params=params)
        if isinstance(result, dict):
            return result.get("relationships", [])
        return result if isinstance(result, list) else []

    def deactivate_relationship(self, relationship_id: str) -> Dict[str, Any]:
        """停用关系。"""
        return self._t.request("DELETE", f"/memory/graph/relationships/{relationship_id}")

    def get_neighbors(
        self, entity_id: str, depth: int = 1
    ) -> Dict[str, Any]:
        """查询邻居。"""
        return self._t.request(
            "GET", "/memory/graph/neighbors",
            params={"entity_id": entity_id, "depth": depth},
        )

    def extract_entities(self, text: str) -> Dict[str, Any]:
        """从文本抽取实体。"""
        return self._t.request("POST", "/memory/graph/extract", json={"text": text})

    def query_graph(self, query: str) -> Dict[str, Any]:
        """自然语言图谱查询。"""
        return self._t.request("GET", "/memory/graph/query", params={"q": query})

    def get_statistics(self) -> Dict[str, Any]:
        """获取图谱统计。"""
        return self._t.request("GET", "/memory/graph/statistics")
