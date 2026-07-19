"""Graph Entity and Relationship models."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class GraphEntity(BaseModel):
    """知识图谱实体。"""

    id: Optional[str] = None
    name: str
    entity_type: str = "person"
    properties: Dict[str, Any] = {}
    aliases: List[str] = []
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class GraphRelationship(BaseModel):
    """知识图谱关系。"""

    id: Optional[str] = None
    source_entity_id: Optional[str] = None
    target_entity_id: Optional[str] = None
    source_name: Optional[str] = None
    target_name: Optional[str] = None
    relation_type: str = ""
    properties: Dict[str, Any] = {}
    confidence: float = 0.8
    created_at: Optional[str] = None
