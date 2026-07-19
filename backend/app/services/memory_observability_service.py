"""
Memory Observability - 记忆观测性模块

提供三大能力:
1. 仪表盘指标 - 记忆总量/新增速率/召回命中率/存储占用/检索延迟/Token消耗
2. 记忆追踪 - 记忆从创建到收回的完整链路/对话触发抽取记录
3. 质量评估 - LLM自动评估准确率/召回相关性/用户满意度
"""
import asyncio
import logging
import json
import math
import time
import re
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.redis_client import get_redis_client


# ============================================================
# 数据记录
# ============================================================

def record_trace_event(
    user_id: int,
    memory_id: str,
    memory_type: str = "fragment",
    event_type: str = "created",
    event_source: str = "system",
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    score: Optional[float] = None,
    latency_ms: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    记录记忆生命周期追踪事件。

    event_type: created|recalled|updated|deleted|decayed|merged|cold_marked|restored
    event_source: conversation|extraction|recall|lifecycle|manual|system

    Args:
        user_id: 用户 ID
        memory_id: 记忆 ID（fragment_id / variable_id）
        memory_type: 记忆类型 (fragment|variable|table)
        event_type: 事件类型
        event_source: 事件来源
        conversation_id: 关联对话 ID
        session_id: 关联会话 ID
        score: 关联分数（召回分数等）
        latency_ms: 延迟（毫秒）
        metadata: 附加元数据

    Returns:
        记录结果
    """
    try:
        db = get_db_client()
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None
        db.execute(
            '''INSERT INTO memory_trace_events
               (user_id, memory_id, memory_type, event_type, event_source,
                conversation_id, session_id, score, latency_ms, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, memory_id, memory_type, event_type, event_source,
             conversation_id, session_id, score, latency_ms, metadata_json)
        )
        result = {"success": True}
    except Exception as e:
        logger.debug(f"记录追踪事件失败: {e}")
        return {"success": False, "error": str(e)}

    # Phase 4: 异步发布事件到 EventBus（fire-and-forget）
    try:
        from app.core.events import MemoryEvent
        from app.core.event_bus import get_event_bus

        event = MemoryEvent.from_trace_event(
            user_id=user_id,
            memory_id=memory_id,
            memory_type=memory_type,
            event_type=event_type,
            event_source=event_source,
            workspace_id=workspace_id,
            score=score,
            metadata=metadata,
        )
        event_bus = get_event_bus()
        loop = asyncio.get_event_loop()
        loop.create_task(event_bus.publish(event))
    except Exception as e:
        logger.debug(f"EventBus publish failed (non-critical): {e}")

    return result


def get_memory_trace(
    memory_id: str,
    memory_type: Optional[str] = None,
) -> Dict[str, Any]:
    """
    获取某条记忆的完整生命周期记录。

    Args:
        memory_id: 记忆 ID
        memory_type: 记忆类型过滤

    Returns:
        生命周期时间线
    """
    try:
        db = get_db_client()
        if memory_type:
            rows = db.execute(
                '''SELECT * FROM memory_trace_events
                   WHERE memory_id = ? AND memory_type = ?
                   ORDER BY created_at ASC''',
                (memory_id, memory_type)
            )
        else:
            rows = db.execute(
                '''SELECT * FROM memory_trace_events
                   WHERE memory_id = ?
                   ORDER BY created_at ASC''',
                (memory_id,)
            )

        events = []
        if rows:
            for r in rows:
                d = dict(r)
                if d.get("metadata"):
                    try:
                        d["metadata"] = json.loads(d["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                events.append(d)

        # 生成摘要
        summary = {
            "total_events": len(events),
            "first_seen": events[0]["created_at"] if events else None,
            "last_seen": events[-1]["created_at"] if events else None,
            "times_recalled": sum(1 for e in events if e["event_type"] == "recalled"),
            "times_updated": sum(1 for e in events if e["event_type"] == "updated"),
            "current_status": _get_current_status(events),
        }

        return {
            "success": True,
            "events": events,
            "summary": summary,
        }

    except Exception as e:
        logger.error(f"获取记忆追踪失败: {e}")
        return {"success": False, "error": str(e)}


def _get_current_status(events: List[Dict]) -> str:
    """从事件列表推断当前状态"""
    status = "active"
    for e in reversed(events):
        et = e["event_type"]
        if et == "deleted":
            status = "deleted"
            break
        elif et == "cold_marked":
            status = "cold"
        elif et == "restored":
            status = "active"
            break
        elif et == "created":
            status = "active"
            break
    return status


def get_trace_events(
    user_id: int,
    event_type: Optional[str] = None,
    event_source: Optional[str] = None,
    days: int = 7,
    limit: int = 100,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    获取追踪事件列表。

    Args:
        user_id: 用户 ID
        event_type: 事件类型过滤
        event_source: 事件来源过滤
        days: 查询最近 N 天
        limit: 最大条数

    Returns:
        事件列表
    """
    try:
        db = get_db_client()
        since = (datetime.now() - timedelta(days=days)).isoformat()

        conditions = ["user_id = ?", "created_at >= ?"]
        params = [user_id, since]

        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if event_source:
            conditions.append("event_source = ?")
            params.append(event_source)

        where = " AND ".join(conditions)
        rows = db.execute(
            f'''SELECT * FROM memory_trace_events
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ?''',
            tuple(params) + (limit,)
        )

        events = []
        if rows:
            for r in rows:
                d = dict(r)
                if d.get("metadata"):
                    try:
                        d["metadata"] = json.loads(d["metadata"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                events.append(d)

        return {"success": True, "events": events, "count": len(events)}

    except Exception as e:
        logger.error(f"获取追踪事件失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 抽取触发记录
# ============================================================

def record_extraction_trigger(
    user_id: int,
    trigger_type: str,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    query_snippet: Optional[str] = None,
    fragments_created: int = 0,
    workspace_id: Optional[int] = None,
    llm_tokens_used: int = 0,
) -> Dict[str, Any]:
    """
    记录一次记忆抽取触发事件。

    Args:
        user_id: 用户 ID
        trigger_type: 触发类型 (auto_recall|conversation_end|periodic|manual)
        conversation_id: 关联对话 ID
        session_id: 关联会话 ID
        query_snippet: 触发查询摘要
        fragments_created: 创建的片段数
        llm_tokens_used: 消耗的 LLM Token 数

    Returns:
        记录结果
    """
    try:
        db = get_db_client()
        db.execute(
            '''INSERT INTO memory_extraction_triggers
               (user_id, session_id, conversation_id, trigger_type,
                query_snippet, fragments_created, llm_tokens_used)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (user_id, session_id, conversation_id, trigger_type,
             query_snippet, fragments_created, llm_tokens_used)
        )
        return {"success": True}
    except Exception as e:
        logger.debug(f"记录抽取触发失败: {e}")
        return {"success": False, "error": str(e)}


def get_extraction_triggers(
    user_id: int,
    limit: int = 50,
    days: int = 30,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    获取抽取触发记录。

    Args:
        user_id: 用户 ID
        limit: 最大条数
        days: 查询最近 N 天

    Returns:
        触发记录列表
    """
    try:
        db = get_db_client()
        since = (datetime.now() - timedelta(days=days)).isoformat()
        rows = db.execute(
            '''SELECT * FROM memory_extraction_triggers
               WHERE user_id = ? AND created_at >= ?
               ORDER BY created_at DESC
               LIMIT ?''',
            (user_id, since, limit)
        )
        triggers = [dict(r) for r in rows] if rows else []
        return {"success": True, "triggers": triggers, "count": len(triggers)}
    except Exception as e:
        logger.error(f"获取抽取触发记录失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 仪表盘指标
# ============================================================

def _estimate_storage_bytes() -> int:
    """估算 SQLite 数据库文件大小"""
    try:
        db = get_db_client()
        rows = db.execute("PRAGMA page_count")
        page_count = rows[0][0] if rows else 0
        rows = db.execute("PRAGMA page_size")
        page_size = rows[0][0] if rows else 4096
        return page_count * page_size
    except Exception:
        return 0


def _calc_percentile(values: List[float], percentile: float) -> float:
    """计算百分位值"""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * percentile / 100)))
    return sorted_vals[idx]


def get_dashboard_stats(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    获取观测仪表盘聚合指标。

    Returns:
        {
            "total_memories": int,          # 记忆总量
            "active_memories": int,         # 活跃记忆数
            "cold_memories": int,           # 冷记忆数
            "daily_new_rate": float,        # 日新增速率(24h)
            "weekly_new_rate": float,       # 周新增速率(7d)
            "recall_hit_rate": float,       # 召回命中率
            "storage_bytes": int,           # 存储占用
            "recall_latency_p50": float,    # 检索延迟 P50
            "recall_latency_p99": float,    # 检索延迟 P99
            "llm_tokens_24h": int,          # 24h LLM Token
            "llm_tokens_7d": int,           # 7d LLM Token
            "quality_avg": float,           # 平均质量分
        }
    """
    try:
        db = get_db_client()
        now = datetime.now()
        since_24h = (now - timedelta(hours=24)).isoformat()
        since_7d = (now - timedelta(days=7)).isoformat()

        # 1. 记忆总量和活跃量
        total_rows = db.execute(
            'SELECT COUNT(*) as cnt FROM memory_fragments WHERE user_id = ?',
            (user_id,)
        )
        total_memories = total_rows[0]["cnt"] if total_rows else 0

        active_rows = db.execute(
            "SELECT COUNT(*) as cnt FROM memory_fragments WHERE user_id = ? AND (lifecycle_status IS NULL OR lifecycle_status = 'active')",
            (user_id,)
        )
        active_memories = active_rows[0]["cnt"] if active_rows else 0

        cold_rows = db.execute(
            "SELECT COUNT(*) as cnt FROM memory_fragments WHERE user_id = ? AND lifecycle_status = 'cold'",
            (user_id,)
        )
        cold_memories = cold_rows[0]["cnt"] if cold_rows else 0

        # 2. 新增速率
        daily_new = db.execute(
            'SELECT COUNT(*) as cnt FROM memory_fragments WHERE user_id = ? AND created_at >= ?',
            (user_id, since_24h)
        )
        daily_new_rate = daily_new[0]["cnt"] if daily_new else 0

        weekly_new = db.execute(
            'SELECT COUNT(*) as cnt FROM memory_fragments WHERE user_id = ? AND created_at >= ?',
            (user_id, since_7d)
        )
        weekly_new_rate = weekly_new[0]["cnt"] if weekly_new else 0

        # 3. 召回命中率
        total_recalls = db.execute(
            '''SELECT COUNT(*) as cnt FROM memory_trace_events
               WHERE user_id = ? AND event_type = 'recalled' AND created_at >= ?''',
            (user_id, since_24h)
        )
        total_recall_count = total_recalls[0]["cnt"] if total_recalls else 0

        hit_recalls = db.execute(
            '''SELECT COUNT(*) as cnt FROM memory_trace_events
               WHERE user_id = ? AND event_type = 'recalled'
               AND score IS NOT NULL AND score > 0
               AND created_at >= ?''',
            (user_id, since_24h)
        )
        hit_recall_count = hit_recalls[0]["cnt"] if hit_recalls else 0
        recall_hit_rate = hit_recall_count / max(1, total_recall_count)

        # 4. 存储占用
        storage_bytes = _estimate_storage_bytes()

        # 5. 检索延迟 P50/P99
        latency_rows = db.execute(
            '''SELECT latency_ms FROM memory_trace_events
               WHERE user_id = ? AND event_type = 'recalled'
               AND latency_ms IS NOT NULL
               AND created_at >= ?
               ORDER BY created_at DESC LIMIT 1000''',
            (user_id, since_7d)
        )
        latencies = [r["latency_ms"] for r in latency_rows] if latency_rows else []
        p50 = _calc_percentile(latencies, 50)
        p99 = _calc_percentile(latencies, 99)

        # 6. LLM Token 消耗
        extraction_tokens_24h = db.execute(
            '''SELECT COALESCE(SUM(llm_tokens_used), 0) as total
               FROM memory_extraction_triggers
               WHERE user_id = ? AND created_at >= ?''',
            (user_id, since_24h)
        )
        tokens_24h = extraction_tokens_24h[0]["total"] if extraction_tokens_24h else 0

        extraction_tokens_7d = db.execute(
            '''SELECT COALESCE(SUM(llm_tokens_used), 0) as total
               FROM memory_extraction_triggers
               WHERE user_id = ? AND created_at >= ?''',
            (user_id, since_7d)
        )
        tokens_7d = extraction_tokens_7d[0]["total"] if extraction_tokens_7d else 0

        # 7. 质量评分
        quality_rows = db.execute(
            '''SELECT AVG(score) as avg_score FROM memory_quality_evaluations
               WHERE user_id = ?''',
            (user_id,)
        )
        quality_avg = round(quality_rows[0]["avg_score"], 4) if quality_rows and quality_rows[0]["avg_score"] else 0.0
        if quality_avg is None:
            quality_avg = 0.0

        # 8. 记忆类型分布
        type_dist = db.execute(
            '''SELECT fragment_type, COUNT(*) as cnt
               FROM memory_fragments
               WHERE user_id = ?
               GROUP BY fragment_type
               ORDER BY cnt DESC''',
            (user_id,)
        )
        type_distribution = {}
        if type_dist:
            for r in type_dist:
                type_distribution[r["fragment_type"]] = r["cnt"]

        # 9. 今日新增片段 (top-5)
        today_new_fragments = db.execute(
            '''SELECT id, content, fragment_type, created_at
               FROM memory_fragments
               WHERE user_id = ? AND created_at >= ?
               ORDER BY created_at DESC
               LIMIT 5''',
            (user_id, since_24h)
        )
        recent_fragments = [dict(r) for r in today_new_fragments] if today_new_fragments else []

        return {
            "success": True,
            "total_memories": total_memories,
            "active_memories": active_memories,
            "cold_memories": cold_memories,
            "daily_new_rate": daily_new_rate,
            "weekly_new_rate": weekly_new_rate,
            "avg_new_rate_7d": round(weekly_new_rate / max(1, 7), 2),
            "recall_hit_rate": round(recall_hit_rate, 4),
            "total_recalls_24h": total_recall_count,
            "storage_bytes": storage_bytes,
            "storage_mb": round(storage_bytes / (1024 * 1024), 2),
            "recall_latency_p50_ms": round(p50, 2),
            "recall_latency_p99_ms": round(p99, 2),
            "llm_tokens_24h": tokens_24h,
            "llm_tokens_7d": tokens_7d,
            "quality_avg_score": quality_avg,
            "type_distribution": type_distribution,
            "recent_fragments": recent_fragments,
        }

    except Exception as e:
        logger.error(f"获取仪表盘指标失败: {e}")
        return {"success": False, "error": str(e)}


def snapshot_metrics(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    对当前指标执行快照，写入 memory_metrics_snapshots 表。

    Args:
        user_id: 用户 ID

    Returns:
        快照结果
    """
    try:
        stats = get_dashboard_stats(user_id)
        if not stats.get("success"):
            return stats

        db = get_db_client()
        db.execute(
            '''INSERT INTO memory_metrics_snapshots
               (user_id, total_memories, active_memories, total_storage_bytes,
                daily_new_count, daily_recall_count, daily_recall_hit_count,
                avg_recall_latency_ms, p50_recall_latency_ms, p99_recall_latency_ms,
                llm_extraction_tokens, llm_rerank_tokens)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id,
             stats["total_memories"], stats["active_memories"], stats["storage_bytes"],
             stats["daily_new_rate"], stats["total_recalls_24h"],
             int(stats["recall_hit_rate"] * stats["total_recalls_24h"]),
             (stats["recall_latency_p50_ms"] + stats["recall_latency_p99_ms"]) / 2,
             stats["recall_latency_p50_ms"], stats["recall_latency_p99_ms"],
             stats["llm_tokens_24h"], 0)
        )

        logger.info(f"✓ 指标快照完成: user={user_id}")
        return {"success": True, "snapshot_time": datetime.now().isoformat()}

    except Exception as e:
        logger.error(f"指标快照失败: {e}")
        return {"success": False, "error": str(e)}


def get_metrics_history(
    user_id: int,
    days: int = 30,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    获取指标历史时间序列。

    Args:
        user_id: 用户 ID
        days: 查询最近 N 天

    Returns:
        历史指标列表
    """
    try:
        db = get_db_client()
        since = (datetime.now() - timedelta(days=days)).isoformat()
        rows = db.execute(
            '''SELECT * FROM memory_metrics_snapshots
               WHERE user_id = ? AND snapshot_time >= ?
               ORDER BY snapshot_time ASC''',
            (user_id, since)
        )
        snapshots = [dict(r) for r in rows] if rows else []
        return {"success": True, "snapshots": snapshots, "count": len(snapshots)}
    except Exception as e:
        logger.error(f"获取指标历史失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 质量评估
# ============================================================

ACCURACY_EVAL_PROMPT = """你是一个记忆质量评估专家。

分析原文对话和抽取的记忆片段，判断记忆片段的**准确性**。

对话原文: {conversation_text}

记忆片段: {memory_content}

请从以下维度打分（0~1）：
1. 事实准确性：记忆是否准确反映了原文信息（权重 0.6）
2. 完整性：记忆是否包含了足够的关键信息（权重 0.2）
3. 无幻觉：记忆是否添加了原文没有的信息（权重 0.2）

只需返回一个 JSON 对象：
{{"score": 0.85, "reason": "记忆准确反映了用户信息，但缺少部分细节"}}"""


RELEVANCE_EVAL_PROMPT = """你是一个召回相关性评估专家。

用户查询: {query}

记忆片段: {memory_content}

请判断该记忆与用户查询的相关性（0~1）：
- 1.0 = 直接命中查询核心
- 0.7 = 高度相关
- 0.4 = 部分相关
- 0.1 = 边缘相关
- 0.0 = 完全无关

只需返回一个 JSON 对象：
{{"score": 0.8, "reason": "记忆提到了用户工作的公司信息"}}"""


def _call_llm_eval(prompt: str, user_id: int) -> Tuple[Optional[float], Optional[str]]:
    """调用 LLM 进行评估"""
    try:
        from app.services.llm_backend_service import llm_chat

        result = llm_chat(
            user_id=user_id,
            messages=[
                {"role": "system", "content": "你是一个评估引擎。请严格按照要求的 JSON 格式返回。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            enqueue_on_failure=True,
        )
        if not result.get("success") or not result.get("content"):
            return None, None

        text = result["content"]

        # 提取 JSON
        code_match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\n?```', text, re.DOTALL)
        if code_match:
            text = code_match.group(1)
        brace_match = re.search(r'\{[^{}]*"score"[^{}]*\}', text)
        if brace_match:
            text = brace_match.group(0)

        data = json.loads(text)
        score = float(data.get("score", 0))
        reason = data.get("reason", "")
        return max(0.0, min(1.0, score)), reason

    except Exception as e:
        logger.debug(f"LLM 评估调用失败: {e}")
        return None, None


def evaluate_memory_accuracy(
    user_id: int,
    memory_id: str,
    conversation_text: Optional[str] = None,
    memory_type: str = "fragment",
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    LLM 自动评估记忆片段准确率。

    需要提供原文对话和记忆片段内容。
    如果 conversation_text 未提供，则尝试从 DB 获取。

    Args:
        user_id: 用户 ID
        memory_id: 记忆 ID
        conversation_text: 原文对话（可选）
        memory_type: 记忆类型

    Returns:
        评估结果
    """
    try:
        db = get_db_client()

        # 获取记忆内容
        if memory_type == "fragment":
            rows = db.execute(
                'SELECT content, fragment_type FROM memory_fragments WHERE id = ?',
                (int(memory_id),)
            )
        else:
            rows = db.execute(
                'SELECT content FROM memory_variables WHERE id = ?',
                (int(memory_id),)
            )

        if not rows:
            return {"success": False, "error": "记忆不存在"}

        memory_row = dict(rows[0])
        memory_content = memory_row.get("content", "")

        # 如果未提供原文，使用通用评估
        if not conversation_text:
            # 简单评估：基于内容的启发式评分
            content_len = len(memory_content)
            has_cjk = sum(1 for c in memory_content if '\u4e00' <= c <= '\u9fff')
            heuristic_score = min(0.9, 0.5 + content_len * 0.01 + has_cjk * 0.02)
            return {
                "success": True,
                "memory_id": memory_id,
                "memory_content": memory_content,
                "score": round(heuristic_score, 4),
                "evaluator": "heuristic",
                "reason": "基于内容长度的启发式评估",
            }

        # LLM 评估
        prompt = ACCURACY_EVAL_PROMPT.format(
            conversation_text=conversation_text[:2000],
            memory_content=memory_content[:500],
        )

        score, reason = _call_llm_eval(prompt, user_id)
        if score is None:
            return {"success": False, "error": "LLM 评估不可用"}

        # 记录评估结果
        db.execute(
            '''INSERT INTO memory_quality_evaluations
               (user_id, memory_id, memory_type, evaluation_type, score, evaluator, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (user_id, memory_id, memory_type, "accuracy", score, "llm",
             json.dumps({"reason": reason, "conversation_length": len(conversation_text)}, ensure_ascii=False))
        )

        return {
            "success": True,
            "memory_id": memory_id,
            "memory_content": memory_content,
            "score": round(score, 4),
            "evaluator": "llm",
            "reason": reason,
        }

    except Exception as e:
        logger.error(f"评估记忆准确率失败: {e}")
        return {"success": False, "error": str(e)}


def evaluate_recall_relevance(
    user_id: int,
    query: str,
    fragments: List[Dict[str, Any]],
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    评估召回片段与查询的相关性。

    Args:
        user_id: 用户 ID
        query: 查询文本
        fragments: 召回的记忆片段列表

    Returns:
        每个片段的相关性评分
    """
    try:
        if not fragments:
            return {"success": True, "evaluations": [], "count": 0}

        results = []
        for frag in fragments[:10]:  # 最多评估 10 条
            frag_id = str(frag.get("id", ""))
            content = (frag.get("content", "") or "")[:300]
            memory_type = frag.get("fragment_type", "fragment")

            # LLM 评估
            prompt = RELEVANCE_EVAL_PROMPT.format(query=query, memory_content=content)
            score, reason = _call_llm_eval(prompt, user_id)

            if score is None:
                # 回退：使用关键词匹配评估
                query_terms = set(query.lower().split())
                content_lower = content.lower()
                matches = sum(1 for t in query_terms if t in content_lower)
                score = min(0.8, matches / max(1, len(query_terms)))
                reason = f"关键词匹配: {matches}/{len(query_terms)}"

            # 记录评估结果
            try:
                db = get_db_client()
                db.execute(
                    '''INSERT INTO memory_quality_evaluations
                       (user_id, memory_id, memory_type, evaluation_type, score, evaluator, details)
                       VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (user_id, frag_id, memory_type, "relevance", score,
                     "llm" if reason and not reason.startswith("关键词") else "system",
                     json.dumps({"reason": reason, "query": query[:200]}, ensure_ascii=False))
                )
            except Exception:
                pass

            results.append({
                "memory_id": frag_id,
                "content": content[:100],
                "score": round(score, 4),
                "reason": reason,
            })

        avg_score = sum(r["score"] for r in results) / max(1, len(results))
        return {
            "success": True,
            "evaluations": results,
            "average_score": round(avg_score, 4),
            "count": len(results),
        }

    except Exception as e:
        logger.error(f"评估召回相关性失败: {e}")
        return {"success": False, "error": str(e)}


def batch_evaluate_quality(
    user_id: int,
    limit: int = 10,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    批量评估最近创建的记忆片段质量。

    Args:
        user_id: 用户 ID
        limit: 评估条数

    Returns:
        批量评估结果
    """
    try:
        db = get_db_client()
        rows = db.execute(
            '''SELECT id, content, fragment_type, created_at
               FROM memory_fragments
               WHERE user_id = ?
               ORDER BY created_at DESC
               LIMIT ?''',
            (user_id, limit)
        )
        fragments = [dict(r) for r in rows] if rows else []

        if not fragments:
            return {"success": True, "evaluations": [], "count": 0}

        accuracy_results = []
        for frag in fragments:
            result = evaluate_memory_accuracy(
                user_id=user_id,
                memory_id=str(frag["id"]),
                conversation_text=None,
                memory_type="fragment",
            )
            if result.get("success"):
                accuracy_results.append({
                    "memory_id": str(frag["id"]),
                    "content": (frag.get("content", "") or "")[:100],
                    "score": result.get("score", 0),
                    "evaluator": result.get("evaluator", "system"),
                })

        scores = [r["score"] for r in accuracy_results]
        return {
            "success": True,
            "evaluations": accuracy_results,
            "average_score": round(sum(scores) / max(1, len(scores)), 4),
            "count": len(accuracy_results),
        }

    except Exception as e:
        logger.error(f"批量评估质量失败: {e}")
        return {"success": False, "error": str(e)}


def get_quality_report(
    user_id: int,
    days: int = 30,
) -> Dict[str, Any]:
    """
    获取质量评估聚合报告。

    Args:
        user_id: 用户 ID
        days: 查询最近 N 天

    Returns:
        质量报告
    """
    try:
        db = get_db_client()
        since = (datetime.now() - timedelta(days=days)).isoformat()

        # 按评估类型聚合
        eval_rows = db.execute(
            '''SELECT evaluation_type, AVG(score) as avg_score,
                      COUNT(*) as total, MIN(score) as min_score, MAX(score) as max_score
               FROM memory_quality_evaluations
               WHERE user_id = ? AND created_at >= ?
               GROUP BY evaluation_type''',
            (user_id, since)
        )

        by_type = {}
        if eval_rows:
            for r in eval_rows:
                by_type[r["evaluation_type"]] = {
                    "average": round(r["avg_score"], 4) if r["avg_score"] else 0,
                    "min": round(r["min_score"], 4) if r["min_score"] else 0,
                    "max": round(r["max_score"], 4) if r["max_score"] else 0,
                    "count": r["total"],
                }

        # 最近评估记录
        recent = db.execute(
            '''SELECT * FROM memory_quality_evaluations
               WHERE user_id = ? AND created_at >= ?
               ORDER BY created_at DESC
               LIMIT 20''',
            (user_id, since)
        )
        recent_evaluations = [dict(r) for r in recent] if recent else []

        return {
            "success": True,
            "by_type": by_type,
            "total_evaluations": sum(v["count"] for v in by_type.values()),
            "recent_evaluations": recent_evaluations,
        }

    except Exception as e:
        logger.error(f"获取质量报告失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 实时指标更新（供埋点使用）
# ============================================================

def update_recall_metrics(
    user_id: int,
    query: str,
    fragments: List[Dict[str, Any]],
    latency_ms: float,
    session_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> None:
    """
    每次召回后更新指标（埋点，无返回）。

    记录每次召回事件到 trace_events。

    Args:
        user_id: 用户 ID
        query: 查询
        fragments: 召回结果
        latency_ms: 延迟（毫秒）
        session_id: 会话 ID
        conversation_id: 对话 ID
    """
    try:
        for frag in fragments[:5]:  # 最多记录 5 条
            memory_id = str(frag.get("id", ""))
            if not memory_id:
                continue
            score = frag.get("_fusion_score") or frag.get("similarity") or 0
            record_trace_event(
                user_id=user_id,
                memory_id=memory_id,
                memory_type="fragment",
                event_type="recalled",
                event_source="recall",
                conversation_id=conversation_id,
                session_id=session_id,
                score=float(score) if score else None,
                latency_ms=latency_ms,
                metadata={"query": query[:200]},
            )
    except Exception as e:
        logger.debug(f"更新召回指标失败: {e}")


def update_extraction_metrics(
    user_id: int,
    trigger_type: str = "auto_recall",
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    query_snippet: Optional[str] = None,
    fragments_created: int = 0,
    llm_tokens_used: int = 0,
) -> None:
    """
    每次记忆抽取后更新指标（埋点，无返回）。

    Args:
        user_id: 用户 ID
        trigger_type: 触发类型
        conversation_id: 对话 ID
        session_id: 会话 ID
        query_snippet: 触发查询
        fragments_created: 创建的片段数
        llm_tokens_used: 消耗的 Token 数
    """
    try:
        record_extraction_trigger(
            user_id=user_id,
            trigger_type=trigger_type,
            conversation_id=conversation_id,
            session_id=session_id,
            query_snippet=query_snippet,
            fragments_created=fragments_created,
            llm_tokens_used=llm_tokens_used,
        )
    except Exception as e:
        logger.debug(f"更新抽取指标失败: {e}")


# ============================================================
# 测试
# ============================================================

def test_observability():
    """测试观测性模块"""
    print("\n" + "=" * 60)
    print("测试 Memory Observability 模块")
    print("=" * 60 + "\n")

    test_user_id = 996
    db = get_db_client()

    # 清理
    for tbl in ["memory_trace_events", "memory_quality_evaluations",
                 "memory_extraction_triggers", "memory_metrics_snapshots"]:
        db.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (test_user_id,))
    db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (test_user_id,))
    print("  清理完成\n")

    # 创建测试用记忆片段
    from app.services.memory_fragment_service import create_fragment

    frag_ids = []
    test_contents = [
        ("info", "用户叫张三，是一名产品经理"),
        ("info", "张三在北京的腾讯公司工作"),
        ("plan", "张三计划明天完成架构设计文档"),
        ("preference", "张三喜欢极简设计风格"),
        ("plan", "王五是张三的领导，负责项目评审"),
    ]
    for ftype, content in test_contents:
        result = create_fragment(test_user_id, ftype, content)
        fid = result.get("fragment_id")
        if fid:
            frag_ids.append(fid)
    print(f"  创建 {len(frag_ids)} 条测试记忆\n")

    # ================================================================
    # 1. 追踪事件记录
    # ================================================================
    print("--- 1. 追踪事件记录 ---\n")

    print("1.1 记录生命周期事件...")
    # create_fragment 已自动记录 created 事件，此处只手动记录 recalled 事件
    r = record_trace_event(test_user_id, str(frag_ids[0]), "fragment",
                           "recalled", "recall", "conv_002", "sess_001",
                           score=0.85, latency_ms=45.2)
    assert r["success"]
    r = record_trace_event(test_user_id, str(frag_ids[0]), "fragment",
                           "recalled", "recall", "conv_003", "sess_001",
                           score=0.92, latency_ms=32.1)
    assert r["success"]
    print("  ✓ 2 条召回事件记录成功\n")

    print("1.2 记录抽取触发...")
    r = record_extraction_trigger(test_user_id, "auto_recall", "conv_001",
                                  "sess_001", "用户说我叫张三", 2, 150)
    assert r["success"]
    r = record_extraction_trigger(test_user_id, "conversation_end", "conv_002",
                                  "sess_001", "对话结束抽取", 3, 220)
    assert r["success"]
    print("  ✓ 2 条抽取触发记录成功\n")

    # ================================================================
    # 2. 记忆追踪查询
    # ================================================================
    print("--- 2. 记忆追踪查询 ---\n")

    print("2.1 获取记忆追踪...")
    r = get_memory_trace(str(frag_ids[0]))
    assert r["success"]
    assert r["summary"]["total_events"] == 3  # 1 created(create_fragment自动) + 2 recalled
    assert r["summary"]["times_recalled"] == 2
    print(f"  事件数: {r['summary']['total_events']}, 召回次数: {r['summary']['times_recalled']}")
    print("  ✓ 记忆追踪查询成功\n")

    print("2.2 获取事件列表...")
    r = get_trace_events(test_user_id, event_type="recalled")
    assert r["success"]
    print(f"  召回事件: {r['count']} 条")
    assert r["count"] >= 2
    print("  ✓ 事件列表查询成功\n")

    # ================================================================
    # 3. 仪表盘指标
    # ================================================================
    print("--- 3. 仪表盘指标 ---\n")

    print("3.1 获取仪表盘统计...")
    r = get_dashboard_stats(test_user_id)
    assert r["success"]
    print(f"  记忆总量: {r['total_memories']}")
    print(f"  活跃记忆: {r['active_memories']}")
    print(f"  日新增: {r['daily_new_rate']}")
    print(f"  存储占用: {r['storage_mb']} MB")
    print(f"  召回延迟 P50: {r['recall_latency_p50_ms']}ms")
    print(f"  召回延迟 P99: {r['recall_latency_p99_ms']}ms")
    print(f"  LLM Token(24h): {r['llm_tokens_24h']}")
    print(f"  类型分布: {r['type_distribution']}")
    assert r["total_memories"] >= 5
    print("  ✓ 仪表盘统计完成\n")

    print("3.2 指标快照...")
    r = snapshot_metrics(test_user_id)
    assert r["success"]
    print("  ✓ 指标快照成功\n")

    print("3.3 指标历史...")
    r = get_metrics_history(test_user_id)
    assert r["success"]
    print(f"  快照数: {r['count']}")
    assert r["count"] >= 1
    print("  ✓ 指标历史查询成功\n")

    # ================================================================
    # 4. 质量评估
    # ================================================================
    print("--- 4. 质量评估 ---\n")

    print("4.1 评估记忆准确率（启发式）...")
    r = evaluate_memory_accuracy(test_user_id, str(frag_ids[0]))
    assert r["success"]
    print(f"  记忆片段: {r['memory_content']}")
    print(f"  准确率得分: {r['score']} (evaluator={r['evaluator']})")
    assert r["score"] > 0
    print("  ✓ 准确率评估完成\n")

    print("4.2 评估召回相关性...")
    fragments = [
        {"id": frag_ids[0], "content": "用户叫张三，是一名产品经理"},
        {"id": frag_ids[1], "content": "张三在北京的腾讯公司工作"},
        {"id": frag_ids[2], "content": "张三计划明天完成架构设计文档"},
    ]
    r = evaluate_recall_relevance(test_user_id, "张三 产品经理", fragments)
    assert r["success"]
    print(f"  平均相关性得分: {r['average_score']}")
    print(f"  评估数: {r['count']}")
    assert r["count"] >= 1
    print("  ✓ 召回相关性评估完成\n")

    print("4.3 批量质量评估...")
    r = batch_evaluate_quality(test_user_id, limit=5)
    assert r["success"]
    print(f"  批量评估: {r['count']} 条, 平均分: {r.get('average_score', 0)}")
    assert r["count"] >= 1
    print("  ✓ 批量质量评估完成\n")

    print("4.4 质量报告...")
    r = get_quality_report(test_user_id)
    assert r["success"]
    print(f"  评估类型: {list(r['by_type'].keys())}")
    print(f"  总评估数: {r['total_evaluations']}")
    assert r["total_evaluations"] >= 1
    print("  ✓ 质量报告完成\n")

    # ================================================================
    # 5. 实时指标埋点
    # ================================================================
    print("--- 5. 实时指标埋点 ---\n")

    print("5.1 更新召回指标...")
    test_frags = [{"id": frag_ids[0], "_fusion_score": 0.85}]
    update_recall_metrics(test_user_id, "张三", test_frags, 35.5)
    r = get_trace_events(test_user_id, event_type="recalled")
    print(f"  召回事件数: {r['count']}")
    assert r["count"] >= 3  # 之前有 2 条 + 新增
    print("  ✓ 召回埋点成功\n")

    print("5.2 更新抽取指标...")
    update_extraction_metrics(test_user_id, "periodic", "conv_003",
                              "sess_001", "定时抽取", 2, 300)
    r = get_extraction_triggers(test_user_id)
    print(f"  抽取触发记录: {r['count']} 条")
    assert r["count"] >= 3
    print("  ✓ 抽取埋点成功\n")

    # 清理
    print("--- 清理测试数据 ---")
    for tbl in ["memory_trace_events", "memory_quality_evaluations",
                 "memory_extraction_triggers", "memory_metrics_snapshots"]:
        db.execute(f"DELETE FROM {tbl} WHERE user_id = ?", (test_user_id,))
    db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (test_user_id,))
    print("  清理完成")

    print("\n" + "=" * 60)
    print("✅ Memory Observability 模块测试完成！")
    print("=" * 60 + "\n")

    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    test_observability()
