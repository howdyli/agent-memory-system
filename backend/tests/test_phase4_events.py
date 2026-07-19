"""
Phase 4 测试：事件总线 + Webhook CRUD + HMAC 签名 + 重试逻辑
"""
import asyncio
import hashlib
import hmac
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# ============================================================
# 测试 fixtures
# ============================================================

@pytest.fixture
def event():
    """创建测试事件"""
    from app.core.events import MemoryEvent, EventType
    return MemoryEvent(
        event_type=EventType.MEMORY_CREATED,
        user_id=1,
        workspace_id=1,
        memory_id="frag_001",
        memory_type="fragment",
        data={"content": "test memory"},
    )


@pytest.fixture
def event_bus():
    """创建 InMemoryEventBus"""
    from app.core.event_bus import InMemoryEventBus
    return InMemoryEventBus()


# ============================================================
# 1. 事件模型测试
# ============================================================

class TestMemoryEvent:
    """MemoryEvent dataclass 测试"""

    def test_event_creation(self, event):
        assert event.event_type == "memory.created"
        assert event.user_id == 1
        assert event.memory_id == "frag_001"
        assert event.event_id  # UUID 自动生成

    def test_event_to_dict(self, event):
        d = event.to_dict()
        assert d["event_type"] == "memory.created"
        assert d["user_id"] == 1
        assert "timestamp" in d
        assert isinstance(d["timestamp"], str)  # ISO 格式

    def test_event_to_json(self, event):
        j = event.to_json()
        parsed = json.loads(j)
        assert parsed["event_type"] == "memory.created"

    def test_event_from_dict(self, event):
        d = event.to_dict()
        restored = type(event).from_dict(d)
        assert restored.event_id == event.event_id
        assert restored.event_type == event.event_type

    def test_event_from_trace_event(self):
        from app.core.events import MemoryEvent
        e = MemoryEvent.from_trace_event(
            user_id=1,
            memory_id="frag_002",
            memory_type="fragment",
            event_type="created",
            event_source="conversation",
            workspace_id=1,
            score=0.85,
            metadata={"key": "value"},
        )
        assert e.event_type == "memory.created"
        assert e.source == "conversation"
        assert e.data["score"] == 0.85


class TestEventType:
    """EventType 常量测试"""

    def test_all_types_list(self):
        from app.core.events import EventType
        assert len(EventType.ALL) > 20
        assert "memory.created" in EventType.ALL
        assert "webhook.delivery_failed" in EventType.ALL

    def test_trace_event_type_map(self):
        from app.core.events import TRACE_EVENT_TYPE_MAP
        assert TRACE_EVENT_TYPE_MAP["created"] == "memory.created"
        assert TRACE_EVENT_TYPE_MAP["recalled"] == "memory.recalled"


# ============================================================
# 2. EventBus 测试
# ============================================================

class TestInMemoryEventBus:
    """InMemoryEventBus 测试"""

    @pytest.mark.asyncio
    async def test_publish_and_buffer(self, event_bus, event):
        await event_bus.start()
        await event_bus.publish(event)
        events = await event_bus.get_recent_events()
        assert len(events) == 1
        assert events[0].event_id == event.event_id
        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_subscribe_callback(self, event_bus, event):
        await event_bus.start()
        received = []

        async def callback(e):
            received.append(e)

        sub_id = await event_bus.subscribe(["memory.created"], callback)
        await event_bus.publish(event)

        # 等待异步分发
        await asyncio.sleep(0.1)
        assert len(received) == 1
        assert received[0].event_type == "memory.created"

        await event_bus.unsubscribe(sub_id)
        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_subscribe_wildcard(self, event_bus, event):
        await event_bus.start()
        received = []

        async def callback(e):
            received.append(e)

        sub_id = await event_bus.subscribe(["*"], callback)
        await event_bus.publish(event)
        await asyncio.sleep(0.1)
        assert len(received) == 1
        await event_bus.unsubscribe(sub_id)
        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_subscribe_filter_mismatch(self, event_bus, event):
        await event_bus.start()
        received = []

        async def callback(e):
            received.append(e)

        sub_id = await event_bus.subscribe(["table.created"], callback)
        await event_bus.publish(event)  # memory.created 不匹配
        await asyncio.sleep(0.1)
        assert len(received) == 0
        await event_bus.unsubscribe(sub_id)
        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_get_recent_events_with_filter(self, event_bus):
        from app.core.events import MemoryEvent, EventType
        await event_bus.start()

        e1 = MemoryEvent(event_type=EventType.MEMORY_CREATED, user_id=1, memory_id="m1")
        e2 = MemoryEvent(event_type=EventType.MEMORY_DELETED, user_id=1, memory_id="m2")
        await event_bus.publish(e1)
        await event_bus.publish(e2)

        filtered = await event_bus.get_recent_events(event_types=["memory.deleted"])
        assert len(filtered) == 1
        assert filtered[0].event_type == "memory.deleted"
        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_get_recent_events_with_limit(self, event_bus):
        from app.core.events import MemoryEvent, EventType
        await event_bus.start()

        for i in range(10):
            e = MemoryEvent(event_type=EventType.MEMORY_CREATED, user_id=1, memory_id=f"m{i}")
            await event_bus.publish(e)

        events = await event_bus.get_recent_events(limit=5)
        assert len(events) == 5
        await event_bus.stop()

    @pytest.mark.asyncio
    async def test_buffer_size_limit(self):
        from app.core.events import MemoryEvent, EventType
        from app.core.event_bus import InMemoryEventBus
        bus = InMemoryEventBus(buffer_size=5)
        await bus.start()

        for i in range(10):
            e = MemoryEvent(event_type=EventType.MEMORY_CREATED, user_id=1, memory_id=f"m{i}")
            await bus.publish(e)

        events = await bus.get_recent_events()
        assert len(events) == 5  # 只保留最近 5 条
        await bus.stop()


# ============================================================
# 3. Webhook Service 测试
# ============================================================

class TestWebhookService:
    """Webhook Service CRUD 测试"""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        """使用临时 SQLite 数据库"""
        db_path = tmp_path / "test.db"
        self.db_path = str(db_path)

    def _mock_db_client(self):
        """Mock get_db_client 使用临时 SQLite"""
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        mock = MagicMock()
        mock.execute = lambda sql, params=(): self._execute(conn, sql, params)
        return mock

    def _execute(self, conn, sql, params):
        cursor = conn.cursor()
        if params:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        if "INSERT" in sql.upper():
            conn.commit()
            return cursor.lastrowid
        elif "SELECT" in sql.upper():
            return cursor.fetchall()
        else:
            conn.commit()
            return cursor.rowcount

    @patch("app.services.webhook_service.get_db_client")
    def test_create_webhook(self, mock_get_db):
        from app.services.webhook_service import create_webhook
        mock_get_db.return_value = self._mock_db_client()

        result = create_webhook(
            user_id=1,
            url="https://example.com/webhook",
            event_types=["memory.created", "memory.deleted"],
            workspace_id=1,
        )
        assert result["url"] == "https://example.com/webhook"
        assert result["secret"].startswith("whsec_")
        assert "memory.created" in result["event_types"]

    @patch("app.services.webhook_service.get_db_client")
    def test_list_webhooks(self, mock_get_db):
        from app.services.webhook_service import create_webhook, list_webhooks
        mock_get_db.return_value = self._mock_db_client()

        create_webhook(user_id=1, url="https://example.com/hook1", event_types=["memory.created"])
        create_webhook(user_id=1, url="https://example.com/hook2", event_types=["memory.deleted"])

        hooks = list_webhooks(user_id=1)
        assert len(hooks) == 2

    @patch("app.services.webhook_service.get_db_client")
    def test_update_webhook(self, mock_get_db):
        from app.services.webhook_service import create_webhook, update_webhook, get_webhook
        mock_get_db.return_value = self._mock_db_client()

        created = create_webhook(user_id=1, url="https://old.com", event_types=["memory.created"])
        webhook_id = created["id"]

        update_webhook(webhook_id, url="https://new.com", active=False)
        updated = get_webhook(webhook_id)
        assert updated["url"] == "https://new.com"
        assert updated["active"] == 0  # SQLite returns int for bool

    @patch("app.services.webhook_service.get_db_client")
    def test_delete_webhook(self, mock_get_db):
        from app.services.webhook_service import create_webhook, delete_webhook, list_webhooks
        mock_get_db.return_value = self._mock_db_client()

        create_webhook(user_id=1, url="https://example.com", event_types=["memory.created"])
        assert len(list_webhooks(user_id=1)) == 1

        delete_webhook(1)
        assert len(list_webhooks(user_id=1)) == 0


# ============================================================
# 4. HMAC 签名测试
# ============================================================

class TestHMACSignature:
    """HMAC 签名计算测试"""

    def test_compute_signature(self):
        from app.services.webhook_service import compute_signature
        secret = "whsec_test_secret"
        payload = '{"event": "test"}'

        sig = compute_signature(secret, payload)
        expected = hmac.new(
            secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        assert sig == expected

    def test_signature_consistency(self):
        from app.services.webhook_service import compute_signature
        secret = "whsec_abc123"
        payload = '{"data": "hello"}'

        sig1 = compute_signature(secret, payload)
        sig2 = compute_signature(secret, payload)
        assert sig1 == sig2  # 相同输入产生相同签名

    def test_signature_different_secrets(self):
        from app.services.webhook_service import compute_signature
        payload = '{"data": "hello"}'
        sig1 = compute_signature("secret1", payload)
        sig2 = compute_signature("secret2", payload)
        assert sig1 != sig2


# ============================================================
# 5. 指数退避重试测试
# ============================================================

class TestRetryIntervals:
    """指数退避间隔测试"""

    def test_retry_intervals_defined(self):
        from app.services.webhook_service import RETRY_INTERVALS
        assert len(RETRY_INTERVALS) == 6
        assert RETRY_INTERVALS[0] == 60       # 1 分钟
        assert RETRY_INTERVALS[-1] == 86400   # 24 小时

    def test_max_retry_attempts(self):
        from app.services.webhook_service import MAX_RETRY_ATTEMPTS
        assert MAX_RETRY_ATTEMPTS == 6


# ============================================================
# 6. Webhook 投递测试（mock httpx）
# ============================================================

class TestWebhookDelivery:
    """Webhook 投递测试"""

    @pytest.mark.asyncio
    @patch("app.services.webhook_service._save_delivery")
    @patch("httpx.AsyncClient")
    async def test_deliver_webhook_success(self, mock_client_cls, mock_save):
        from app.services.webhook_service import deliver_webhook
        from app.core.events import MemoryEvent, EventType

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        webhook = {
            "id": 1,
            "url": "https://example.com/webhook",
            "secret": "whsec_test",
        }
        event = MemoryEvent(
            event_type=EventType.MEMORY_CREATED,
            user_id=1,
            memory_id="frag_001",
        )

        result = await deliver_webhook(webhook, event)
        assert result["success"] is True
        assert result["status_code"] == 200
        mock_save.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.services.webhook_service._save_delivery")
    @patch("httpx.AsyncClient")
    async def test_deliver_webhook_failure(self, mock_client_cls, mock_save):
        from app.services.webhook_service import deliver_webhook
        from app.core.events import MemoryEvent, EventType

        # Mock httpx failure
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        webhook = {
            "id": 1,
            "url": "https://example.com/webhook",
            "secret": "whsec_test",
        }
        event = MemoryEvent(
            event_type=EventType.MEMORY_CREATED,
            user_id=1,
            memory_id="frag_001",
        )

        result = await deliver_webhook(webhook, event)
        assert result["success"] is False
        assert "next_retry_at" in result
        mock_save.assert_called_once()


# ============================================================
# 7. EventBus 工厂测试
# ============================================================

class TestEventBusFactory:
    """EventBus 工厂函数测试"""

    def test_default_inmemory(self):
        from app.core.event_bus import get_event_bus, reset_event_bus, InMemoryEventBus
        reset_event_bus()
        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.EVENT_BUS_BACKEND = "memory"
            bus = get_event_bus()
            assert isinstance(bus, InMemoryEventBus)
        reset_event_bus()

    def test_redis_backend(self):
        from app.core.event_bus import get_event_bus, reset_event_bus, RedisEventBus
        reset_event_bus()
        with patch("app.core.config.get_settings") as mock_settings:
            mock_settings.return_value.EVENT_BUS_BACKEND = "redis"
            bus = get_event_bus()
            assert isinstance(bus, RedisEventBus)
        reset_event_bus()


# ============================================================
# 8. observability 集成测试
# ============================================================

class TestObservabilityIntegration:
    """record_trace_event 集成 EventBus 测试"""

    @patch("app.services.memory_observability_service.get_db_client")
    def test_record_trace_event_publishes(self, mock_get_db):
        """record_trace_event 应该发布事件到 EventBus"""
        from app.services.memory_observability_service import record_trace_event

        # Mock DB
        mock_db = MagicMock()
        mock_db.execute = MagicMock()
        mock_get_db.return_value = mock_db

        # Mock EventBus
        mock_bus = AsyncMock()
        mock_get_bus = MagicMock(return_value=mock_bus)

        # Mock event loop
        mock_loop = MagicMock()
        mock_loop.create_task = MagicMock()

        with patch("app.core.event_bus.get_event_bus", mock_get_bus), \
             patch("asyncio.get_event_loop", return_value=mock_loop):
            result = record_trace_event(
                user_id=1,
                memory_id="frag_001",
                memory_type="fragment",
                event_type="created",
                event_source="test",
            )

        assert result["success"] is True
        # 验证 create_task 被调用（异步发布）
        mock_loop.create_task.assert_called_once()
