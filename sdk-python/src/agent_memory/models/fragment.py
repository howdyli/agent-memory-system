"""Memory Fragment model."""

from typing import Optional
from pydantic import BaseModel


class MemoryFragment(BaseModel):
    """语义记忆片段。"""

    id: Optional[int] = None
    content: str
    fragment_type: str = "fact"
    importance_score: float = 0.5
    ttl: Optional[int] = None
    session_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    # 搜索结果附带
    similarity_score: Optional[float] = None
    distance: Optional[float] = None
