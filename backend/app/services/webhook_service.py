"""
Phase 4: Webhook Service

提供 Webhook CRUD、HMAC 签名投递、指数退避重试、投递记录查询。
"""
import hashlib
import hmac
import json
import logging
import secrets
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core.db_client import get_db_client
from app.core.events import MemoryEvent, EventType
from app.core.tracing import get_tracer

logger = logging.getLogger(__name__)

# 指数退避间隔（秒）：1m, 5m, 30m, 2h, 8h, 24h
RETRY_INTERVALS = [60, 300, 1800, 7200, 28800, 86400]
MAX_RETRY_ATTEMPTS = len(RETRY_INTERVALS)

# 后台 worker 控制
_webhook_worker_task: Optional[asyncio.Task] = None
_webhook_worker_running = False


def _ensure_tables() -> None:
    """确保 webhooks / webhook_deliveries 表存在（SQLite 开发环境）"""
    db = get_db_client()
    db.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER,
            user_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            secret TEXT NOT NULL,
            event_types TEXT NOT NULL,
            active BOOLEAN DEFAULT 1,
            description TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS webhook_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            webhook_id INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            status_code INTEGER,
            response_body TEXT,
            success BOOLEAN DEFAULT 0,
            attempt INTEGER DEFAULT 1,
            next_retry_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (webhook_id) REFERENCES webhooks(id)
        )
    """)


def _generate_secret() -> str:
    """生成 HMAC 签名密钥"""
    return f"whsec_{secrets.token_hex(32)}"


# ============================================================
# Webhook CRUD
# ============================================================

def create_webhook(
    user_id: int,
    url: str,
    event_types: List[str],
    workspace_id: Optional[int] = None,
    description: Optional[str] = None,
) -> Dict[str, Any]:
    """创建 Webhook 订阅"""
    _ensure_tables()
    db = get_db_client()
    secret = _generate_secret()
    event_types_json = json.dumps(event_types)

    row = db.execute(
        """INSERT INTO webhooks (user_id, url, secret, event_types, workspace_id, description)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, url, secret, event_types_json, workspace_id, description),
    )
    webhook_id = row if isinstance(row, int) else db.execute("SELECT last_insert_rowid()")[0][0]

    return {
        "id": webhook_id,
        "user_id": user_id,
        "url": url,
        "secret": secret,
        "event_types": event_types,
        "workspace_id": workspace_id,
        "description": description,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def list_webhooks(
    user_id: int,
    workspace_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """列出用户的 Webhooks"""
    _ensure_tables()
    db = get_db_client()

    if workspace_id:
        rows = db.execute(
            "SELECT * FROM webhooks WHERE user_id = ? AND workspace_id = ? ORDER BY created_at DESC",
            (user_id, workspace_id),
        )
    else:
        rows = db.execute(
            "SELECT * FROM webhooks WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )

    results = []
    if rows:
        for r in rows:
            d = dict(r)
            try:
                d["event_types"] = json.loads(d["event_types"])
            except (json.JSONDecodeError, TypeError):
                d["event_types"] = []
            results.append(d)
    return results


def get_webhook(webhook_id: int) -> Optional[Dict[str, Any]]:
    """获取单个 Webhook"""
    _ensure_tables()
    db = get_db_client()
    rows = db.execute("SELECT * FROM webhooks WHERE id = ?", (webhook_id,))
    if not rows:
        return None
    d = dict(rows[0])
    try:
        d["event_types"] = json.loads(d["event_types"])
    except (json.JSONDecodeError, TypeError):
        d["event_types"] = []
    return d


def update_webhook(
    webhook_id: int,
    url: Optional[str] = None,
    event_types: Optional[List[str]] = None,
    active: Optional[bool] = None,
    description: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """更新 Webhook"""
    _ensure_tables()
    db = get_db_client()

    updates = []
    params = []
    if url is not None:
        updates.append("url = ?")
        params.append(url)
    if event_types is not None:
        updates.append("event_types = ?")
        params.append(json.dumps(event_types))
    if active is not None:
        updates.append("active = ?")
        params.append(1 if active else 0)
    if description is not None:
        updates.append("description = ?")
        params.append(description)

    if not updates:
        return get_webhook(webhook_id)

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(webhook_id)

    db.execute(
        f"UPDATE webhooks SET {', '.join(updates)} WHERE id = ?",
        tuple(params),
    )
    return get_webhook(webhook_id)


def delete_webhook(webhook_id: int) -> bool:
    """删除 Webhook 及其投递记录"""
    _ensure_tables()
    db = get_db_client()
    db.execute("DELETE FROM webhook_deliveries WHERE webhook_id = ?", (webhook_id,))
    db.execute("DELETE FROM webhooks WHERE id = ?", (webhook_id,))
    return True


# ============================================================
# HMAC 签名
# ============================================================

def compute_signature(secret: str, payload: str) -> str:
    """计算 HMAC-SHA256 签名"""
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# ============================================================
# Webhook 投递
# ============================================================

async def deliver_webhook(webhook: Dict[str, Any], event: MemoryEvent) -> Dict[str, Any]:
    """
    投递事件到 Webhook URL。

    POST JSON body + X-Signature-256 头。
    记录投递结果到 webhook_deliveries 表。
    """
    _span = get_tracer().start_span("webhook.deliver")
    _span.set_attribute("webhook.id", webhook.get("id", 0))
    _span.set_attribute("event.type", event.event_type)

    payload = json.dumps({
        "event_id": event.event_id,
        "event_type": event.event_type,
        "timestamp": event.timestamp.isoformat() + "Z" if hasattr(event.timestamp, "isoformat") else str(event.timestamp),
        "data": event.to_dict(),
    }, ensure_ascii=False)

    signature = compute_signature(webhook["secret"], payload)
    url = webhook["url"]

    delivery_record = {
        "webhook_id": webhook["id"],
        "event_id": event.event_id,
        "event_type": event.event_type,
        "payload": payload,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                content=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Signature-256": f"sha256={signature}",
                    "X-Webhook-Id": str(webhook["id"]),
                    "X-Event-Id": event.event_id,
                    "X-Event-Type": event.event_type,
                },
            )
            delivery_record["status_code"] = resp.status_code
            delivery_record["response_body"] = resp.text[:4096]
            delivery_record["success"] = 200 <= resp.status_code < 300
            delivery_record["attempt"] = 1
    except Exception as e:
        delivery_record["status_code"] = None
        delivery_record["response_body"] = str(e)[:4096]
        delivery_record["success"] = False
        delivery_record["attempt"] = 1
        # 设置首次重试时间
        delivery_record["next_retry_at"] = (
            datetime.now(timezone.utc) + timedelta(seconds=RETRY_INTERVALS[0])
        ).isoformat()

    # 写入投递记录
    _save_delivery(delivery_record)

    # Phase 5: metrics
    try:
        import time
        from app.core.metrics import webhook_deliveries_total, webhook_delivery_latency_seconds
        status = "success" if delivery_record.get("success") else "failed"
        webhook_deliveries_total.labels(status=status).inc()
    except Exception:
        pass

    _span.end()

    return delivery_record


def _save_delivery(record: Dict[str, Any]) -> None:
    """保存投递记录"""
    _ensure_tables()
    db = get_db_client()
    db.execute(
        """INSERT INTO webhook_deliveries
           (webhook_id, event_id, event_type, payload, status_code, response_body, success, attempt, next_retry_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            record["webhook_id"],
            record["event_id"],
            record["event_type"],
            record["payload"],
            record.get("status_code"),
            record.get("response_body"),
            1 if record.get("success") else 0,
            record.get("attempt", 1),
            record.get("next_retry_at"),
        ),
    )


# ============================================================
# 重试
# ============================================================

async def retry_failed_deliveries() -> int:
    """
    重试失败的投递。

    查找 next_retry_at <= now 且 success=0 的记录，
    按指数退避重新投递。

    Returns:
        重试数量
    """
    _ensure_tables()
    db = get_db_client()
    now = datetime.now(timezone.utc).isoformat()

    rows = db.execute(
        """SELECT d.*, w.url, w.secret, w.event_types
           FROM webhook_deliveries d
           JOIN webhooks w ON d.webhook_id = w.id
           WHERE d.success = 0
             AND d.attempt < ?
             AND (d.next_retry_at IS NULL OR d.next_retry_at <= ?)
           ORDER BY d.created_at ASC
           LIMIT 50""",
        (MAX_RETRY_ATTEMPTS + 1, now),
    )

    if not rows:
        return 0

    retry_count = 0
    for row in rows:
        record = dict(row)
        webhook = {
            "id": record["webhook_id"],
            "url": record["url"],
            "secret": record["secret"],
        }
        try:
            payload = record["payload"]
            signature = compute_signature(webhook["secret"], payload)

            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    webhook["url"],
                    content=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Signature-256": f"sha256={signature}",
                        "X-Webhook-Id": str(webhook["id"]),
                        "X-Retry-Attempt": str(record["attempt"] + 1),
                    },
                )
                success = 200 <= resp.status_code < 300
                status_code = resp.status_code
                response_body = resp.text[:4096]
        except Exception as e:
            success = False
            status_code = None
            response_body = str(e)[:4096]

        new_attempt = record["attempt"] + 1
        if success:
            next_retry = None
        elif new_attempt <= MAX_RETRY_ATTEMPTS:
            interval = RETRY_INTERVALS[min(new_attempt - 1, len(RETRY_INTERVALS) - 1)]
            next_retry = (datetime.now(timezone.utc) + timedelta(seconds=interval)).isoformat()
        else:
            next_retry = None  # 放弃重试

        db.execute(
            """UPDATE webhook_deliveries
               SET success = ?, status_code = ?, response_body = ?,
                   attempt = ?, next_retry_at = ?
               WHERE id = ?""",
            (1 if success else 0, status_code, response_body, new_attempt, next_retry, record["id"]),
        )
        retry_count += 1

    return retry_count


def get_delivery_logs(
    webhook_id: int,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """查询 Webhook 投递记录"""
    _ensure_tables()
    db = get_db_client()
    rows = db.execute(
        """SELECT * FROM webhook_deliveries
           WHERE webhook_id = ?
           ORDER BY created_at DESC
           LIMIT ?""",
        (webhook_id, limit),
    )
    return [dict(r) for r in rows] if rows else []


# ============================================================
# 事件驱动的 Webhook 投递
# ============================================================

async def dispatch_event_to_webhooks(event: MemoryEvent) -> int:
    """
    将事件分发给所有匹配的活跃 Webhook。

    Returns:
        投递数量
    """
    _ensure_tables()
    db = get_db_client()

    rows = db.execute(
        "SELECT * FROM webhooks WHERE active = 1",
    )
    if not rows:
        return 0

    dispatched = 0
    for row in rows:
        webhook = dict(row)
        try:
            event_types = json.loads(webhook["event_types"])
        except (json.JSONDecodeError, TypeError):
            event_types = []

        # workspace 过滤
        if webhook.get("workspace_id") and event.workspace_id:
            if webhook["workspace_id"] != event.workspace_id:
                continue

        # 事件类型匹配
        if "*" not in event_types and event.event_type not in event_types:
            continue

        await deliver_webhook(webhook, event)
        dispatched += 1

    return dispatched


# ============================================================
# 后台 Worker
# ============================================================

def start_webhook_worker() -> None:
    """启动 Webhook 投递重试 worker"""
    global _webhook_worker_task, _webhook_worker_running

    if _webhook_worker_running:
        return

    _webhook_worker_running = True

    async def _worker():
        while _webhook_worker_running:
            try:
                count = await retry_failed_deliveries()
                if count > 0:
                    logger.debug(f"Webhook retry: {count} deliveries retried")
            except Exception as e:
                logger.error(f"Webhook worker error: {e}")
            await asyncio.sleep(30)  # 每 30 秒检查一次

    _webhook_worker_task = asyncio.create_task(_worker())
    logger.info("Webhook retry worker started")


def stop_webhook_worker() -> None:
    """停止 Webhook 投递重试 worker"""
    global _webhook_worker_task, _webhook_worker_running

    _webhook_worker_running = False
    if _webhook_worker_task:
        _webhook_worker_task.cancel()
        _webhook_worker_task = None
    logger.info("Webhook retry worker stopped")


# ============================================================
# 测试端点
# ============================================================

async def test_webhook(webhook_id: int) -> Dict[str, Any]:
    """发送测试事件到 Webhook"""
    webhook = get_webhook(webhook_id)
    if not webhook:
        return {"success": False, "error": "Webhook not found"}

    test_event = MemoryEvent(
        event_type=EventType.SYSTEM_HEALTH,
        user_id=webhook["user_id"],
        workspace_id=webhook.get("workspace_id"),
        memory_id="test",
        memory_type="fragment",
        data={"test": True, "message": "Webhook test event"},
    )

    result = await deliver_webhook(webhook, test_event)
    return {
        "success": result.get("success", False),
        "status_code": result.get("status_code"),
        "response": result.get("response_body", "")[:500],
    }
