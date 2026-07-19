"""SDK configuration."""

from typing import Optional
from pydantic import BaseModel, Field


class SDKConfig(BaseModel):
    """SDK 配置模型。"""

    base_url: Optional[str] = None
    api_key: Optional[str] = None
    token: Optional[str] = None
    workspace_id: Optional[str] = None
    mode: str = Field(default="http", pattern="^(http|embedded)$")
    # 嵌入模式参数
    db_path: str = "agent_memory.db"
    vector_backend: str = "chroma"
    # HTTP 超时
    timeout: float = 30.0
