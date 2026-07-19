"""
Phase 4: EventBus 抽象层

提供 EventBus 抽象接口 + InMemoryEventBus / RedisEventBus 两种实现。
通过 settings.EVENT_BUS_BACKEND 选择后端（默认 memory）。
"""
import asyncio
import json
import logging
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set

from app.core.events import MemoryEvent

logger = logging.getLogger(__name__)

# 回调签名：async def callback(event: MemoryEvent) -> None
EventCallback = Callable[[MemoryEvent], Coroutine[Any, Any, None]]

# 全局 EventBus 单例
_event_bus: Optional["EventBus"] = None


def get_event_bus() -> "EventBus":
    """获取全局 EventBus 单例（懒初始化）"""
    global _event_bus
    if _event_bus is None:
        from app.core.config import get_settings
        settings = get_settings()
        if settings.EVENT_BUS_BACKEND == "redis":
            _event_bus = RedisEventBus()
        else:
            _event_bus = InMemoryEventBus()
        logger.info(f"EventBus initialized: {settings.EVENT_BUS_BACKEND}")
    return _event_bus


def reset_event_bus():
    """重置全局 EventBus（测试用）"""
    global _event_bus
    _event_bus = None


class EventBus(ABC):
    """事件总线抽象接口"""

    @abstractmethod
    async def publish(self, event: MemoryEvent) -> None:
        """发布事件"""
        ...

    @abstractmethod
    async def subscribe(
        self,
        event_types: List[str],
        callback: EventCallback,
    ) -> str:
        """
        订阅事件。

        Args:
            event_types: 关注的事件类型列表，["*"] 表示全部
            callback: 异步回调函数

        Returns:
            subscription_id
        """
        ...

    @abstractmethod
    async def unsubscribe(self, subscription_id: str) -> None:
        """取消订阅"""
        ...

    @abstractmethod
    async def get_recent_events(
        self,
        event_types: Optional[List[str]] = None,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> List[MemoryEvent]:
        """获取最近事件（历史查询）"""
        ...

    @abstractmethod
    async def start(self) -> None:
        """启动事件总线（后台任务）"""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """停止事件总线"""
        ...


class InMemoryEventBus(EventBus):
    """
    基于内存的事件总线。

    - 使用 asyncio.Queue 分发事件给订阅者
    - 环形缓冲区保存最近事件（默认 10000 条）
    - 适合单进程 / 开发环境
    """

    def __init__(self, buffer_size: int = 10000):
        self._buffer_size = buffer_size
        self._subscribers: Dict[str, Dict[str, Any]] = {}  # sub_id -> {event_types, callback, queue}
        self._event_buffer: List[MemoryEvent] = []
        self._running = False
        self._dispatch_tasks: Dict[str, asyncio.Task] = {}

    async def publish(self, event: MemoryEvent) -> None:
        # Phase 5: metrics
        try:
            from app.core.metrics import event_bus_published_total
            event_bus_published_total.labels(event_type=event.event_type).inc()
        except Exception:
            pass

        # 存入环形缓冲区
        self._event_buffer.append(event)
        if len(self._event_buffer) > self._buffer_size:
            self._event_buffer = self._event_buffer[-self._buffer_size:]

        # 分发给匹配的订阅者
        for sub_id, sub in list(self._subscribers.items()):
            event_types = sub["event_types"]
            queue: asyncio.Queue = sub["queue"]
            if "*" in event_types or event.event_type in event_types:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(f"Subscriber {sub_id} queue full, dropping event")

        logger.debug(f"Event published: {event.event_type} (id={event.event_id[:8]})")

    async def subscribe(
        self,
        event_types: List[str],
        callback: EventCallback,
    ) -> str:
        sub_id = str(uuid.uuid4())
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[sub_id] = {
            "event_types": event_types,
            "callback": callback,
            "queue": queue,
        }

        # 启动分发协程
        async def _dispatch():
            while sub_id in self._subscribers:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    try:
                        await callback(event)
                    except Exception as e:
                        logger.error(f"Event callback error (sub={sub_id[:8]}): {e}")
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

        task = asyncio.create_task(_dispatch())
        self._dispatch_tasks[sub_id] = task
        logger.debug(f"Subscription created: {sub_id[:8]} for {event_types}")
        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        sub = self._subscribers.pop(subscription_id, None)
        task = self._dispatch_tasks.pop(subscription_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if sub:
            logger.debug(f"Subscription removed: {subscription_id[:8]}")

    async def get_recent_events(
        self,
        event_types: Optional[List[str]] = None,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> List[MemoryEvent]:
        events = self._event_buffer
        if event_types and "*" not in event_types:
            events = [e for e in events if e.event_type in event_types]
        if since:
            events = [e for e in events if e.timestamp >= since]
        return events[-limit:]

    async def start(self) -> None:
        self._running = True
        logger.info("InMemoryEventBus started")

    async def stop(self) -> None:
        self._running = False
        for task in self._dispatch_tasks.values():
            task.cancel()
        await asyncio.gather(*self._dispatch_tasks.values(), return_exceptions=True)
        self._dispatch_tasks.clear()
        self._subscribers.clear()
        logger.info("InMemoryEventBus stopped")


class RedisEventBus(EventBus):
    """
    基于 Redis Pub/Sub + Stream 的事件总线。

    - 使用 Redis Pub/Sub 跨进程分发
    - 使用 Redis Stream 持久化最近事件
    - 适合多进程 / 生产环境
    """

    CHANNEL = "agent_memory:events"
    STREAM_KEY = "agent_memory:event_stream"
    STREAM_MAX_LEN = 10000

    def __init__(self):
        self._redis = None
        self._pubsub = None
        self._subscribers: Dict[str, Dict[str, Any]] = {}
        self._listen_task: Optional[asyncio.Task] = None
        self._running = False

    def _get_redis(self):
        if self._redis is None:
            from app.core.redis_client import get_redis_client
            client = get_redis_client()
            self._redis = client._connection
        return self._redis

    async def publish(self, event: MemoryEvent) -> None:
        redis = self._get_redis()
        payload = event.to_json()

        # Pub/Sub 分发
        try:
            await redis.publish(self.CHANNEL, payload)
        except Exception as e:
            logger.error(f"Redis publish failed: {e}")
            return

        # Stream 持久化
        try:
            await redis.xadd(
                self.STREAM_KEY,
                {"event_type": event.event_type, "payload": payload},
                maxlen=self.STREAM_MAX_LEN,
            )
        except Exception as e:
            logger.warning(f"Redis stream write failed: {e}")

        logger.debug(f"Event published to Redis: {event.event_type}")

    async def subscribe(
        self,
        event_types: List[str],
        callback: EventCallback,
    ) -> str:
        sub_id = str(uuid.uuid4())
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[sub_id] = {
            "event_types": event_types,
            "callback": callback,
            "queue": queue,
        }

        # 启动分发协程
        async def _dispatch():
            while sub_id in self._subscribers:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    try:
                        await callback(event)
                    except Exception as e:
                        logger.error(f"Redis event callback error: {e}")
                except asyncio.TimeoutError:
                    continue
                except asyncio.CancelledError:
                    break

        task = asyncio.create_task(_dispatch())
        self._subscribers[sub_id]["task"] = task
        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        sub = self._subscribers.pop(subscription_id, None)
        if sub and "task" in sub:
            sub["task"].cancel()
            try:
                await sub["task"]
            except asyncio.CancelledError:
                pass

    async def get_recent_events(
        self,
        event_types: Optional[List[str]] = None,
        limit: int = 100,
        since: Optional[datetime] = None,
    ) -> List[MemoryEvent]:
        redis = self._get_redis()
        try:
            # 从 Stream 读取最近事件
            raw = await redis.xrevrange(self.STREAM_KEY, count=limit * 2)
            events = []
            for msg_id, fields in raw:
                payload = fields.get(b"payload", fields.get("payload", ""))
                if isinstance(payload, bytes):
                    payload = payload.decode()
                try:
                    event = MemoryEvent.from_dict(json.loads(payload))
                    if event_types and "*" not in event_types:
                        if event.event_type not in event_types:
                            continue
                    if since and event.timestamp < since:
                        continue
                    events.append(event)
                except (json.JSONDecodeError, KeyError):
                    continue
                if len(events) >= limit:
                    break
            return events
        except Exception as e:
            logger.error(f"Redis stream read failed: {e}")
            return []

    async def start(self) -> None:
        self._running = True
        redis = self._get_redis()

        # 启动 Pub/Sub 监听
        self._pubsub = redis.pubsub()
        await self._pubsub.subscribe(self.CHANNEL)

        async def _listen():
            try:
                async for message in self._pubsub.listen():
                    if not self._running:
                        break
                    if message["type"] != "message":
                        continue
                    try:
                        data = message["data"]
                        if isinstance(data, bytes):
                            data = data.decode()
                        event = MemoryEvent.from_dict(json.loads(data))
                        # 分发给本地订阅者
                        for sub_id, sub in list(self._subscribers.items()):
                            if "*" in sub["event_types"] or event.event_type in sub["event_types"]:
                                try:
                                    sub["queue"].put_nowait(event)
                                except asyncio.QueueFull:
                                    pass
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Invalid event from Redis: {e}")
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.error(f"Redis Pub/Sub listener error: {e}")

        self._listen_task = asyncio.create_task(_listen())
        logger.info("RedisEventBus started")

    async def stop(self) -> None:
        self._running = False
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._pubsub:
            try:
                await self._pubsub.unsubscribe(self.CHANNEL)
                await self._pubsub.close()
            except Exception:
                pass
        for sub in self._subscribers.values():
            if "task" in sub:
                sub["task"].cancel()
        self._subscribers.clear()
        logger.info("RedisEventBus stopped")
