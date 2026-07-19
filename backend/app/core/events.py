"""
Phase 4: 事件模型定义

定义事件类型常量和 MemoryEvent dataclass，作为 EventBus 的统一数据载体。
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid
import json


class EventType:
    """事件类型常量（点分隔命名空间）"""

    # 记忆片段
    MEMORY_CREATED = "memory.created"
    MEMORY_UPDATED = "memory.updated"
    MEMORY_DELETED = "memory.deleted"
    MEMORY_RECALLED = "memory.recalled"
    MEMORY_DECAYED = "memory.decayed"
    MEMORY_MERGED = "memory.merged"
    MEMORY_COLD_MARKED = "memory.cold_marked"
    MEMORY_RESTORED = "memory.restored"

    # 记忆表
    TABLE_CREATED = "table.created"
    TABLE_DROPPED = "table.dropped"
    RECORD_ADDED = "table.record_added"
    RECORD_UPDATED = "table.record_updated"
    RECORD_DELETED = "table.record_deleted"

    # 知识图谱
    GRAPH_ENTITY_CREATED = "graph.entity_created"
    GRAPH_ENTITY_UPDATED = "graph.entity_updated"
    GRAPH_RELATIONSHIP_CREATED = "graph.relationship_created"
    GRAPH_RELATIONSHIP_DELETED = "graph.relationship_deleted"

    # Webhook
    WEBHOOK_CREATED = "webhook.created"
    WEBHOOK_UPDATED = "webhook.updated"
    WEBHOOK_DELETED = "webhook.deleted"
    WEBHOOK_DELIVERY_SUCCESS = "webhook.delivery_success"
    WEBHOOK_DELIVERY_FAILED = "webhook.delivery_failed"

    # 系统
    SYSTEM_HEALTH = "system.health"
    SYSTEM_STARTUP = "system.startup"
    SYSTEM_SHUTDOWN = "system.shutdown"

    ALL = [
        MEMORY_CREATED, MEMORY_UPDATED, MEMORY_DELETED, MEMORY_RECALLED,
        MEMORY_DECAYED, MEMORY_MERGED, MEMORY_COLD_MARKED, MEMORY_RESTORED,
        TABLE_CREATED, TABLE_DROPPED, RECORD_ADDED, RECORD_UPDATED, RECORD_DELETED,
        GRAPH_ENTITY_CREATED, GRAPH_ENTITY_UPDATED,
        GRAPH_RELATIONSHIP_CREATED, GRAPH_RELATIONSHIP_DELETED,
        WEBHOOK_CREATED, WEBHOOK_UPDATED, WEBHOOK_DELETED,
        WEBHOOK_DELIVERY_SUCCESS, WEBHOOK_DELIVERY_FAILED,
        SYSTEM_HEALTH, SYSTEM_STARTUP, SYSTEM_SHUTDOWN,
    ]


# observability trace event_type → EventType 映射
TRACE_EVENT_TYPE_MAP = {
    "created": EventType.MEMORY_CREATED,
    "updated": EventType.MEMORY_UPDATED,
    "deleted": EventType.MEMORY_DELETED,
    "recalled": EventType.MEMORY_RECALLED,
    "decayed": EventType.MEMORY_DECAYED,
    "merged": EventType.MEMORY_MERGED,
    "cold_marked": EventType.MEMORY_COLD_MARKED,
    "restored": EventType.MEMORY_RESTORED,
}


@dataclass
class MemoryEvent:
    """
    统一事件数据对象。

    Attributes:
        event_id: 唯一事件 ID（UUID4）
        event_type: 事件类型（参见 EventType）
        user_id: 触发用户
        workspace_id: 所属 workspace（可选）
        memory_id: 关联记忆 ID
        memory_type: 记忆类型 (fragment|variable|table|entity|relationship)
        timestamp: 事件时间戳（UTC）
        data: 附加数据负载
        source: 事件来源 (system|conversation|extraction|recall|lifecycle|webhook)
    """

    event_type: str
    user_id: int
    memory_id: str = ""
    memory_type: str = "fragment"
    workspace_id: Optional[int] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    data: Dict[str, Any] = field(default_factory=dict)
    source: str = "system"
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（datetime 转 ISO 格式字符串）"""
        d = asdict(self)
        if isinstance(d.get("timestamp"), datetime):
            d["timestamp"] = d["timestamp"].isoformat() + "Z"
        return d

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "MemoryEvent":
        """从字典反序列化"""
        ts = d.get("timestamp")
        if isinstance(ts, str):
            try:
                d = {**d, "timestamp": datetime.fromisoformat(ts.rstrip("Z"))}
            except ValueError:
                pass
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_trace_event(
        cls,
        user_id: int,
        memory_id: str,
        memory_type: str,
        event_type: str,
        event_source: str,
        workspace_id: Optional[int] = None,
        score: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "MemoryEvent":
        """从 observability trace 事件构造 MemoryEvent"""
        mapped_type = TRACE_EVENT_TYPE_MAP.get(event_type, f"memory.{event_type}")
        return cls(
            event_type=mapped_type,
            user_id=user_id,
            memory_id=memory_id,
            memory_type=memory_type,
            workspace_id=workspace_id,
            source=event_source,
            data={
                "score": score,
                "metadata": metadata,
                "original_event_type": event_type,
            },
        )
