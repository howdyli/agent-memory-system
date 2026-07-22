"""Events API 路由（Phase 4）。

提供事件历史查询 + SSE 实时事件流。
挂载在 /api/v1/events 下。

Stability: BETA — 接口可能在小版本间调整，生产使用前请关注 CHANGELOG。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth import Principal, get_current_principal
from app.core.event_bus import get_event_bus
from app.core.events import EventType, MemoryEvent

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================
# 响应模型
# ============================================================
class EventOut(BaseModel):
    event_id: str
    event_type: str
    user_id: int
    workspace_id: Optional[int] = None
    memory_id: str
    memory_type: str
    timestamp: str
    data: dict
    source: str


# ============================================================
# 事件历史查询
# ============================================================

@router.get("", response_model=List[EventOut])
async def list_events(
    event_type: Optional[str] = Query(None, description="过滤事件类型"),
    days: int = Query(7, ge=1, le=90, description="查询最近 N 天"),
    limit: int = Query(100, ge=1, le=500),
    principal: Principal = Depends(get_current_principal),
):
    """查询事件历史"""
    event_bus = get_event_bus()

    event_types = [event_type] if event_type else None
    since = datetime.now(timezone.utc) - timedelta(days=days)

    events = await event_bus.get_recent_events(
        event_types=event_types,
        limit=limit,
        since=since,
    )

    return [
        EventOut(
            event_id=e.event_id,
            event_type=e.event_type,
            user_id=e.user_id,
            workspace_id=e.workspace_id,
            memory_id=e.memory_id,
            memory_type=e.memory_type,
            timestamp=e.timestamp.isoformat() + "Z" if hasattr(e.timestamp, "isoformat") else str(e.timestamp),
            data=e.data,
            source=e.source,
        )
        for e in events
    ]


# ============================================================
# SSE 实时事件流
# ============================================================

@router.get("/stream")
async def event_stream(
    request: Request,
    event_types: Optional[str] = Query(None, description="逗号分隔的事件类型，* 表示全部"),
    principal: Principal = Depends(get_current_principal),
):
    """
    SSE 实时事件流。

    客户端通过 EventSource 连接，实时接收记忆变更事件。
    每 30 秒发送心跳注释保持连接。
    """
    event_bus = get_event_bus()

    # 解析事件类型过滤
    if event_types and event_types != "*":
        filter_types = [t.strip() for t in event_types.split(",") if t.strip()]
    else:
        filter_types = ["*"]

    async def generator():
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)

        async def on_event(event: MemoryEvent):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

        sub_id = await event_bus.subscribe(filter_types, on_event)
        try:
            # 发送初始连接成功事件
            yield f"data: {json.dumps({'type': 'connected', 'subscription_id': sub_id})}\n\n"

            while True:
                # 检查客户端是否断开
                if await request.is_disconnected():
                    break

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    payload = json.dumps(event.to_dict(), ensure_ascii=False)
                    yield f"event: {event.event_type}\ndata: {payload}\n\n"
                except asyncio.TimeoutError:
                    # 心跳保活
                    yield ": heartbeat\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            await event_bus.unsubscribe(sub_id)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
        },
    )


# ============================================================
# 事件类型列表（辅助端点）
# ============================================================

@router.get("/types")
async def list_event_types():
    """列出所有支持的事件类型"""
    return {"event_types": EventType.ALL}
