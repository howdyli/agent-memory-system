"""
任务 3.3：事件总线 publish/subscribe 完整性测试

覆盖：MemoryEvent 序列化往返、InMemoryEventBus publish→callback 分发、
事件类型过滤、通配订阅、取消订阅、历史查询(get_recent_events)、
回调异常隔离、EventBus 工厂/重置。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.core.events import MemoryEvent, EventType
from app.core.event_bus import (
    InMemoryEventBus,
    get_event_bus,
    reset_event_bus,
)


@pytest.fixture
def bus():
    return InMemoryEventBus(buffer_size=100)


def _make_event(event_type=EventType.MEMORY_CREATED, user_id=1, **kw):
    return MemoryEvent(event_type=event_type, user_id=user_id, **kw)


# ============================================================
# 1. 事件序列化完整性
# ============================================================

class TestEventSerialization:
    def test_to_dict_from_dict_roundtrip(self):
        ev = _make_event(memory_id="m1", data={"k": "v"}, workspace_id=9)
        restored = MemoryEvent.from_dict(ev.to_dict())
        assert restored.event_type == ev.event_type
        assert restored.user_id == ev.user_id
        assert restored.memory_id == ev.memory_id
        assert restored.data == ev.data
        assert restored.workspace_id == ev.workspace_id

    def test_to_json_roundtrip(self):
        import json
        ev = _make_event(memory_id="m2")
        parsed = json.loads(ev.to_json())
        assert parsed["event_type"] == EventType.MEMORY_CREATED
        assert parsed["memory_id"] == "m2"

    def test_from_dict_ignores_unknown_fields(self):
        ev = MemoryEvent.from_dict({
            "event_type": "memory.updated",
            "user_id": 3,
            "bogus_field": "ignored",
        })
        assert ev.event_type == "memory.updated"
        assert ev.user_id == 3

    def test_from_trace_event_maps_type(self):
        ev = MemoryEvent.from_trace_event(
            user_id=5, memory_id="mm", memory_type="fragment",
            event_type="created", event_source="extraction",
        )
        assert ev.event_type == EventType.MEMORY_CREATED
        assert ev.data["original_event_type"] == "created"

    def test_from_trace_event_unknown_type_prefixed(self):
        ev = MemoryEvent.from_trace_event(
            user_id=5, memory_id="mm", memory_type="fragment",
            event_type="weird", event_source="system",
        )
        assert ev.event_type == "memory.weird"


# ============================================================
# 2. publish → subscribe 分发
# ============================================================

class TestPublishSubscribe:
    @pytest.mark.asyncio
    async def test_subscriber_receives_matching_event(self, bus):
        received = []

        async def cb(ev):
            received.append(ev)

        await bus.subscribe([EventType.MEMORY_CREATED], cb)
        await bus.publish(_make_event(memory_id="x"))
        await asyncio.sleep(0.05)  # 等待分发协程处理
        assert len(received) == 1
        assert received[0].memory_id == "x"
        await bus.stop()

    @pytest.mark.asyncio
    async def test_wildcard_subscription(self, bus):
        received = []

        async def cb(ev):
            received.append(ev)

        await bus.subscribe(["*"], cb)
        await bus.publish(_make_event(event_type=EventType.MEMORY_CREATED))
        await bus.publish(_make_event(event_type=EventType.TABLE_CREATED))
        await asyncio.sleep(0.05)
        assert len(received) == 2
        await bus.stop()

    @pytest.mark.asyncio
    async def test_non_matching_event_not_delivered(self, bus):
        received = []

        async def cb(ev):
            received.append(ev)

        await bus.subscribe([EventType.TABLE_CREATED], cb)
        await bus.publish(_make_event(event_type=EventType.MEMORY_DELETED))
        await asyncio.sleep(0.05)
        assert received == []
        await bus.stop()

    @pytest.mark.asyncio
    async def test_multiple_subscribers_each_receive(self, bus):
        a, b = [], []

        async def cb_a(ev):
            a.append(ev)

        async def cb_b(ev):
            b.append(ev)

        await bus.subscribe(["*"], cb_a)
        await bus.subscribe(["*"], cb_b)
        await bus.publish(_make_event())
        await asyncio.sleep(0.05)
        assert len(a) == 1 and len(b) == 1
        await bus.stop()

    @pytest.mark.asyncio
    async def test_callback_exception_isolated(self, bus):
        """一个回调抛异常不应影响其它订阅者"""
        good = []

        async def bad_cb(ev):
            raise ValueError("callback boom")

        async def good_cb(ev):
            good.append(ev)

        await bus.subscribe(["*"], bad_cb)
        await bus.subscribe(["*"], good_cb)
        await bus.publish(_make_event())
        await asyncio.sleep(0.05)
        assert len(good) == 1  # 好回调仍被调用
        await bus.stop()


# ============================================================
# 3. 取消订阅
# ============================================================

class TestUnsubscribe:
    @pytest.mark.asyncio
    async def test_unsubscribe_stops_delivery(self, bus):
        received = []

        async def cb(ev):
            received.append(ev)

        sub_id = await bus.subscribe(["*"], cb)
        await bus.unsubscribe(sub_id)
        await bus.publish(_make_event())
        await asyncio.sleep(0.05)
        assert received == []
        await bus.stop()


# ============================================================
# 4. 历史查询
# ============================================================

class TestRecentEvents:
    @pytest.mark.asyncio
    async def test_buffer_retains_events(self, bus):
        await bus.publish(_make_event(memory_id="a"))
        await bus.publish(_make_event(memory_id="b"))
        recent = await bus.get_recent_events()
        assert len(recent) == 2

    @pytest.mark.asyncio
    async def test_recent_filter_by_type(self, bus):
        await bus.publish(_make_event(event_type=EventType.MEMORY_CREATED))
        await bus.publish(_make_event(event_type=EventType.TABLE_CREATED))
        recent = await bus.get_recent_events(event_types=[EventType.TABLE_CREATED])
        assert len(recent) == 1
        assert recent[0].event_type == EventType.TABLE_CREATED

    @pytest.mark.asyncio
    async def test_recent_limit(self, bus):
        for i in range(5):
            await bus.publish(_make_event(memory_id=str(i)))
        recent = await bus.get_recent_events(limit=2)
        assert len(recent) == 2

    @pytest.mark.asyncio
    async def test_recent_since_filter(self, bus):
        old = _make_event(memory_id="old")
        old.timestamp = datetime.now(timezone.utc) - timedelta(hours=1)
        await bus.publish(old)
        await bus.publish(_make_event(memory_id="new"))
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=1)
        recent = await bus.get_recent_events(since=cutoff)
        assert all(e.memory_id != "old" for e in recent)

    @pytest.mark.asyncio
    async def test_buffer_ring_eviction(self):
        small = InMemoryEventBus(buffer_size=3)
        for i in range(5):
            await small.publish(_make_event(memory_id=str(i)))
        recent = await small.get_recent_events(limit=100)
        assert len(recent) == 3  # 仅保留最近 3 条
        assert recent[-1].memory_id == "4"


# ============================================================
# 5. 生命周期 + 工厂
# ============================================================

class TestBusLifecycleAndFactory:
    @pytest.mark.asyncio
    async def test_start_stop(self, bus):
        await bus.start()
        await bus.stop()

    def test_factory_default_inmemory(self):
        reset_event_bus()
        b = get_event_bus()
        assert isinstance(b, InMemoryEventBus)
        reset_event_bus()

    def test_factory_returns_singleton(self):
        reset_event_bus()
        assert get_event_bus() is get_event_bus()
        reset_event_bus()
