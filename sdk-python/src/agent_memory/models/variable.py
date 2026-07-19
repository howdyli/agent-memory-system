"""Memory Variable model."""

from typing import Any, Optional
from pydantic import BaseModel


class MemoryVariable(BaseModel):
    """KV 记忆变量。"""

    key: str
    value: Any
    ttl: Optional[int] = None
    session_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    expires_at: Optional[str] = None
