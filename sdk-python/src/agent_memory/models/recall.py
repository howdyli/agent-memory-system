"""Recall result model."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class RecallResult(BaseModel):
    """自动召回结果。"""

    success: bool = True
    context: str = ""
    memories: List[Dict[str, Any]] = []
    query: Optional[str] = None
    total_count: int = 0
