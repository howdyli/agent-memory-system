"""Memory Table models."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class TableField(BaseModel):
    """记忆表字段定义。"""

    name: str
    type: str = "TEXT"
    nullable: bool = True
    default: Optional[Any] = None


class TableRecord(BaseModel):
    """记忆表记录。"""

    id: Optional[int] = None
    data: Dict[str, Any] = {}
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class MemoryTable(BaseModel):
    """动态记忆表。"""

    name: str
    fields: List[TableField] = []
    description: Optional[str] = None
    record_count: Optional[int] = None
    created_at: Optional[str] = None
