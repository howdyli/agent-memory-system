"""
业务指标端点

提供业务层面的指标聚合端点，补充 Prometheus /metrics 的原始时序数据。
返回当前时刻的业务快照，便于运维和监控仪表盘消费。

端点：
- GET /api/v1/system/business-metrics — 业务指标快照
"""
import logging
from fastapi import APIRouter, Depends
from typing import Dict, Any

from app.core.auth import Principal, get_current_principal
from app.core.db_client import get_db_client

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/system/business-metrics", summary="业务指标快照")
async def get_business_metrics(
    principal: Principal = Depends(get_current_principal),
) -> Dict[str, Any]:
    """
    返回当前时刻的业务指标快照，包含：

    - memory: 记忆总量、活跃数、冷记忆数、按类型分布
    - graph: 实体数、关系数、按类型分布
    - lifecycle: 生命周期状态分布
    - sessions: 会话总数、消息总数
    - vector: 向量同步状态（pending outbox 数）
    - cache: TTL 缓存命中率（如可用）
    """
    db = get_db_client()
    metrics: Dict[str, Any] = {}

    try:
        # ============================================================
        # 记忆指标
        # ============================================================
        frag_rows = db.execute('''
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN lifecycle_status = 'active' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN lifecycle_status = 'cold' THEN 1 ELSE 0 END) as cold,
                SUM(CASE WHEN lifecycle_status = 'archived' THEN 1 ELSE 0 END) as archived,
                SUM(CASE WHEN lifecycle_status = 'deleted' THEN 1 ELSE 0 END) as deleted,
                SUM(CASE WHEN vector_synced = 0 THEN 1 ELSE 0 END) as vector_pending
            FROM memory_fragments
            WHERE user_id = ?
        ''', (principal.user_id,))

        if frag_rows:
            r = frag_rows[0]
            metrics["memory"] = {
                "total": r["total"] if "total" in r.keys() else r[0],
                "active": r["active"] if "active" in r.keys() else r[1],
                "cold": r["cold"] if "cold" in r.keys() else r[2],
                "archived": r["archived"] if "archived" in r.keys() else r[3],
                "deleted": r["deleted"] if "deleted" in r.keys() else r[4],
                "vector_pending_sync": r["vector_pending"] if "vector_pending" in r.keys() else r[5],
            }
        else:
            metrics["memory"] = {"total": 0, "active": 0, "cold": 0, "archived": 0, "deleted": 0, "vector_pending_sync": 0}

        # 按类型分布
        type_rows = db.execute('''
            SELECT fragment_type, COUNT(*) as cnt
            FROM memory_fragments
            WHERE user_id = ?
            GROUP BY fragment_type
        ''', (principal.user_id,)) or []
        metrics["memory"]["by_type"] = {
            r["fragment_type"] if "fragment_type" in r.keys() else r[0]: r["cnt"] if "cnt" in r.keys() else r[1]
            for r in type_rows
        }

        # ============================================================
        # 知识图谱指标
        # ============================================================
        try:
            entity_rows = db.execute('''
                SELECT entity_type, COUNT(*) as cnt
                FROM graph_entities
                WHERE user_id = ?
                GROUP BY entity_type
            ''', (principal.user_id,)) or []
            entity_count = sum(r["cnt"] if "cnt" in r.keys() else r[1] for r in entity_rows)
            metrics["graph"] = {
                "entity_count": entity_count,
                "by_type": {
                    r["entity_type"] if "entity_type" in r.keys() else r[0]: r["cnt"] if "cnt" in r.keys() else r[1]
                    for r in entity_rows
                },
            }

            rel_rows = db.execute('''
                SELECT COUNT(*) as cnt, SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active
                FROM graph_relationships
                WHERE user_id = ?
            ''', (principal.user_id,))
            if rel_rows:
                r = rel_rows[0]
                metrics["graph"]["relationship_count"] = r["cnt"] if "cnt" in r.keys() else r[0]
                metrics["graph"]["active_relationships"] = r["active"] if "active" in r.keys() else r[1]
            else:
                metrics["graph"]["relationship_count"] = 0
                metrics["graph"]["active_relationships"] = 0
        except Exception:
            metrics["graph"] = {"entity_count": 0, "relationship_count": 0}

        # ============================================================
        # 会话指标
        # ============================================================
        try:
            sess_rows = db.execute(
                "SELECT COUNT(*) as cnt FROM chat_sessions WHERE user_id = ?",
                (principal.user_id,),
            )
            metrics["sessions"] = {
                "total": sess_rows[0]["cnt"] if sess_rows and "cnt" in sess_rows[0].keys() else (sess_rows[0][0] if sess_rows else 0),
            }

            msg_rows = db.execute('''
                SELECT COUNT(*) as cnt
                FROM conversation_history h
                JOIN chat_sessions s ON h.session_id = s.session_id
                WHERE s.user_id = ?
            ''', (principal.user_id,))
            metrics["sessions"]["messages"] = msg_rows[0]["cnt"] if msg_rows and "cnt" in msg_rows[0].keys() else (msg_rows[0][0] if msg_rows else 0)
        except Exception:
            metrics["sessions"] = {"total": 0, "messages": 0}

        # ============================================================
        # 向量 Outbox 指标
        # ============================================================
        try:
            outbox_rows = db.execute('''
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN retry_count >= 5 THEN 1 ELSE 0 END) as failed
                FROM vector_outbox
                WHERE user_id = ?
            ''', (principal.user_id,))
            if outbox_rows:
                r = outbox_rows[0]
                metrics["vector_outbox"] = {
                    "pending": r["total"] if "total" in r.keys() else r[0],
                    "failed": r["failed"] if "failed" in r.keys() else r[1],
                }
            else:
                metrics["vector_outbox"] = {"pending": 0, "failed": 0}
        except Exception:
            metrics["vector_outbox"] = {"pending": 0, "failed": 0}

        # ============================================================
        # Prometheus 指标摘要（如可用）
        # ============================================================
        try:
            from app.core.metrics import METRICS_ENABLED
            metrics["prometheus_enabled"] = METRICS_ENABLED
        except Exception:
            metrics["prometheus_enabled"] = False

        return {
            "success": True,
            "metrics": metrics,
            "user_id": principal.user_id,
        }

    except Exception as e:
        logger.error(f"业务指标查询失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "metrics": {},
        }
