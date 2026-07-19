"""ObservabilityManager — Core-layer memory observability.

Pure business logic, no HTTP/auth dependency.
Provides trace events, dashboard stats, quality evaluation, and metrics snapshots.
Migrated from backend/services/memory_observability_service.py.
"""

import json
import logging
import math
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from ..events import EventEmitter, MemoryEvent, MemoryEventType
from ..store.base import RelationalStore, CacheStore

logger = logging.getLogger(__name__)

# ── Quality Evaluation Prompts ──────────────────────────────────

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


class ObservabilityManager:
    """Manage memory observability: traces, dashboard, quality.

    Replaces backend/services/memory_observability_service.py.
    Uses RelationalStore for all data operations and optional
    CacheStore for dashboard hot stats caching.
    """

    def __init__(
        self,
        relational_store: RelationalStore,
        cache_store: Optional[CacheStore] = None,
        event_emitter: Optional[EventEmitter] = None,
        llm_backend: Optional[Any] = None,  # Injected from SDK, not Core
    ):
        self._relational = relational_store
        self._cache = cache_store
        self._events = event_emitter
        self._llm = llm_backend  # LLMBackend ABC instance — optional

    # ── Trace Events ────────────────────────────────────────────

    def record_trace_event(
        self,
        workspace_id: int,
        memory_id: Optional[str] = None,
        memory_type: Optional[str] = None,
        event_type: str = "",
        event_source: Optional[str] = None,
        conversation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        score: Optional[float] = None,
        latency_ms: Optional[float] = None,
        metadata: Optional[Dict] = None,
    ) -> int:
        """Record a trace event. Returns event ID."""
        return self._relational.log_trace_event(
            workspace_id=workspace_id,
            memory_id=memory_id,
            memory_type=memory_type,
            event_type=event_type,
            event_source=event_source,
            conversation_id=conversation_id,
            session_id=session_id,
            score=score,
            latency_ms=latency_ms,
            metadata=metadata,
        )

    def get_memory_trace(
        self,
        memory_id: str,
        memory_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Get lifecycle trace timeline for a specific memory item."""
        events = self._relational.query_trace_events(
            workspace_id=0,  # cross-workspace query by memory_id
            memory_type=memory_type,
        )

        # Filter by memory_id (the store may not support memory_id filter directly)
        filtered = [e for e in events if e.get("memory_id") == memory_id]

        # Parse metadata JSON strings
        for e in filtered:
            if isinstance(e.get("metadata"), str):
                try:
                    e["metadata"] = json.loads(e["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass

        # Build summary
        summary = {
            "total_events": len(filtered),
            "first_seen": filtered[0].get("created_at") if filtered else None,
            "last_seen": filtered[-1].get("created_at") if filtered else None,
            "times_recalled": sum(1 for e in filtered if e.get("event_type") == "recalled"),
            "times_updated": sum(1 for e in filtered if e.get("event_type") == "updated"),
            "current_status": _infer_current_status(filtered),
        }

        return {"events": filtered, "summary": summary}

    def get_trace_events(
        self,
        workspace_id: int,
        event_type: Optional[str] = None,
        event_source: Optional[str] = None,
        days: int = 7,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Get trace events for a workspace with filters."""
        since = (datetime.now() - timedelta(days=days)).isoformat()
        events = self._relational.query_trace_events(
            workspace_id=workspace_id,
            event_type=event_type,
            start_time=since,
            limit=limit,
        )

        for e in events:
            if isinstance(e.get("metadata"), str):
                try:
                    e["metadata"] = json.loads(e["metadata"])
                except (json.JSONDecodeError, TypeError):
                    pass

        # Filter by event_source if needed (store may not support it)
        if event_source:
            events = [e for e in events if e.get("event_source") == event_source]

        return events

    # ── Extraction Triggers ─────────────────────────────────────

    def record_extraction_trigger(
        self,
        workspace_id: int,
        trigger_type: str,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        query_snippet: Optional[str] = None,
        fragments_created: int = 0,
        llm_tokens_used: int = 0,
    ) -> int:
        """Record an extraction trigger event. Returns trigger ID."""
        return self._relational.log_extraction_trigger(
            workspace_id=workspace_id,
            trigger_type=trigger_type,
            session_id=session_id,
            conversation_id=conversation_id,
            query_snippet=query_snippet,
            fragments_created=fragments_created,
            llm_tokens_used=llm_tokens_used,
        )

    # ── Dashboard Stats ─────────────────────────────────────────

    def get_dashboard_stats(self, workspace_id: int) -> Dict[str, Any]:
        """Get aggregated dashboard metrics for a workspace.

        Returns dict with 12+ metrics including total_memories,
        active_memories, recall_hit_rate, latency, token usage, etc.
        """
        # Try cache first
        if self._cache:
            cached = self._cache.get(f"dashboard:{workspace_id}")
            if cached is not None:
                return cached

        now = datetime.now()
        since_24h = (now - timedelta(hours=24)).isoformat()
        since_7d = (now - timedelta(days=7)).isoformat()

        # Memory counts
        fragments = self._relational.list_fragments(workspace_id, limit=10000)
        total_memories = len(fragments)
        active_memories = sum(
            1 for f in fragments
            if f.get("lifecycle_status", "active") in ("active", None)
        )
        cold_memories = sum(1 for f in fragments if f.get("lifecycle_status") == "cold")

        # New rate (24h / 7d)
        daily_new = sum(
            1 for f in fragments
            if f.get("created_at") and f["created_at"] >= since_24h
        )
        weekly_new = sum(
            1 for f in fragments
            if f.get("created_at") and f["created_at"] >= since_7d
        )

        # Recall hit rate from trace events
        trace_events = self._relational.query_trace_events(
            workspace_id=workspace_id,
            event_type="recalled",
            start_time=since_24h,
            limit=10000,
        )
        total_recalls_24h = len(trace_events)
        hit_recalls = sum(1 for e in trace_events if e.get("score") and e["score"] > 0)
        recall_hit_rate = hit_recalls / max(1, total_recalls_24h)

        # Latency P50/P99
        latencies = [
            float(e.get("latency_ms", 0))
            for e in trace_events
            if e.get("latency_ms") is not None
        ]
        p50 = _calc_percentile(latencies, 50)
        p99 = _calc_percentile(latencies, 99)

        # Quality average
        # (quality_evaluations are stored via log_quality_evaluation)
        # For now, use heuristic placeholder; real data comes from evaluate calls

        # Type distribution
        type_distribution = {}
        for f in fragments:
            ft = f.get("fragment_type", "unknown")
            type_distribution[ft] = type_distribution.get(ft, 0) + 1

        # Recent fragments (top 5)
        recent = sorted(
            [f for f in fragments if f.get("created_at") and f["created_at"] >= since_24h],
            key=lambda x: x.get("created_at", ""),
            reverse=True,
        )[:5]

        stats = {
            "total_memories": total_memories,
            "active_memories": active_memories,
            "cold_memories": cold_memories,
            "daily_new_rate": daily_new,
            "weekly_new_rate": weekly_new,
            "avg_new_rate_7d": round(weekly_new / max(1, 7), 2),
            "recall_hit_rate": round(recall_hit_rate, 4),
            "total_recalls_24h": total_recalls_24h,
            "recall_latency_p50_ms": round(p50, 2),
            "recall_latency_p99_ms": round(p99, 2),
            "type_distribution": type_distribution,
            "recent_fragments": recent,
        }

        # Cache for 5 minutes
        if self._cache:
            self._cache.set(f"dashboard:{workspace_id}", stats, ttl=300)

        return stats

    def snapshot_metrics(self, workspace_id: int) -> int:
        """Create a metrics snapshot. Returns snapshot ID."""
        stats = self.get_dashboard_stats(workspace_id)
        return self._relational.create_metrics_snapshot(workspace_id, stats)

    def get_metrics_history(
        self,
        workspace_id: int,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """Get metrics history for a workspace."""
        # The store doesn't have a direct query for this; we use fts_search
        # or a custom approach. For now, return empty — will be implemented
        # when Server layer adds the history query endpoint.
        # In Core, we just return what the store can give us.
        return []

    # ── Quality Evaluation ──────────────────────────────────────

    def evaluate_memory_accuracy(
        self,
        workspace_id: int,
        memory_id: str,
        conversation_text: Optional[str] = None,
        memory_type: str = "fragment",
    ) -> Dict[str, Any]:
        """Evaluate memory accuracy. Uses LLM if available, heuristic fallback."""
        # Get memory content
        if memory_type == "fragment":
            frag = self._relational.get_fragment(workspace_id, int(memory_id))
            memory_content = (frag or {}).get("content", "")
        else:
            memory_content = ""

        if not memory_content:
            return {"memory_id": memory_id, "score": 0.0, "evaluator": "none", "reason": "No content"}

        # If no conversation text, use heuristic
        if not conversation_text:
            heuristic_score = _heuristic_accuracy_score(memory_content)
            self._relational.log_quality_evaluation(
                workspace_id=workspace_id,
                memory_id=memory_id,
                memory_type=memory_type,
                evaluation_type="accuracy",
                score=heuristic_score,
                evaluator="heuristic",
                details={"reason": "基于内容长度的启发式评估"},
            )
            return {
                "memory_id": memory_id,
                "memory_content": memory_content,
                "score": round(heuristic_score, 4),
                "evaluator": "heuristic",
                "reason": "基于内容长度的启发式评估",
            }

        # LLM evaluation if available
        if self._llm:
            prompt = ACCURACY_EVAL_PROMPT.format(
                conversation_text=conversation_text[:2000],
                memory_content=memory_content[:500],
            )
            score, reason = self._call_llm_eval(prompt, workspace_id)
            if score is not None:
                self._relational.log_quality_evaluation(
                    workspace_id=workspace_id,
                    memory_id=memory_id,
                    memory_type=memory_type,
                    evaluation_type="accuracy",
                    score=score,
                    evaluator="llm",
                    details={"reason": reason},
                )
                return {
                    "memory_id": memory_id,
                    "memory_content": memory_content,
                    "score": round(score, 4),
                    "evaluator": "llm",
                    "reason": reason,
                }

        # Fallback to heuristic even with conversation text if LLM unavailable
        heuristic_score = _heuristic_accuracy_score(memory_content)
        return {
            "memory_id": memory_id,
            "memory_content": memory_content,
            "score": round(heuristic_score, 4),
            "evaluator": "heuristic",
            "reason": "LLM unavailable, heuristic fallback",
        }

    def evaluate_recall_relevance(
        self,
        workspace_id: int,
        query: str,
        fragments: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Evaluate recall relevance for a list of fragments against a query."""
        if not fragments:
            return {"evaluations": [], "average_score": 0.0, "count": 0}

        results = []
        for frag in fragments[:10]:
            frag_id = str(frag.get("id", ""))
            content = (frag.get("content", "") or "")[:300]

            if self._llm:
                prompt = RELEVANCE_EVAL_PROMPT.format(query=query, memory_content=content)
                score, reason = self._call_llm_eval(prompt, workspace_id)
            else:
                # Keyword fallback
                score, reason = _keyword_relevance_score(query, content)

            if score is not None:
                self._relational.log_quality_evaluation(
                    workspace_id=workspace_id,
                    memory_id=frag_id,
                    memory_type=frag.get("fragment_type", "fragment"),
                    evaluation_type="relevance",
                    score=score,
                    evaluator="llm" if self._llm else "heuristic",
                    details={"reason": reason, "query": query[:200]},
                )
                results.append({
                    "memory_id": frag_id,
                    "content": content[:100],
                    "score": round(score, 4),
                    "reason": reason,
                })

        avg = sum(r["score"] for r in results) / max(1, len(results))
        return {
            "evaluations": results,
            "average_score": round(avg, 4),
            "count": len(results),
        }

    def batch_evaluate_quality(
        self,
        workspace_id: int,
        limit: int = 10,
    ) -> Dict[str, Any]:
        """Batch evaluate quality of recent memories."""
        fragments = self._relational.list_fragments(workspace_id, limit=limit)
        results = []

        for frag in fragments:
            result = self.evaluate_memory_accuracy(
                workspace_id=workspace_id,
                memory_id=str(frag.get("id", "")),
                conversation_text=None,
                memory_type="fragment",
            )
            if result.get("score"):
                results.append({
                    "memory_id": result["memory_id"],
                    "content": result.get("memory_content", "")[:100],
                    "score": result["score"],
                    "evaluator": result.get("evaluator", "system"),
                })

        scores = [r["score"] for r in results]
        avg = sum(scores) / max(1, len(scores))
        return {
            "evaluations": results,
            "average_score": round(avg, 4),
            "count": len(results),
        }

    def get_quality_report(
        self,
        workspace_id: int,
        days: int = 30,
    ) -> Dict[str, Any]:
        """Get aggregated quality report. Returns by_type breakdown + recent evaluations."""
        # Core doesn't have a direct aggregation query for quality evaluations;
        # this will be properly implemented when Server adds the endpoint.
        # For now, return a basic structure.
        return {
            "by_type": {},
            "total_evaluations": 0,
            "recent_evaluations": [],
        }

    # ── LLM Evaluation Helper ──────────────────────────────────

    def _call_llm_eval(self, prompt: str, workspace_id: int) -> Tuple[Optional[float], Optional[str]]:
        """Call LLM for quality evaluation. Returns (score, reason) or (None, None)."""
        if not self._llm:
            return None, None

        try:
            result = self._llm.chat(
                messages=[
                    {"role": "system", "content": "你是一个评估引擎。请严格按照要求的 JSON 格式返回。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
            )

            if not result or not result.get("content"):
                return None, None

            text = result["content"]

            # Extract JSON
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
            logger.debug(f"LLM eval failed: {e}")
            return None, None

    # ── Real-time Metrics Hooks ────────────────────────────────

    def update_recall_metrics(
        self,
        workspace_id: int,
        query: str,
        fragments: List[Dict[str, Any]],
        latency_ms: float,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> None:
        """Record recall metrics after each recall operation (fire-and-forget hook)."""
        for frag in fragments[:5]:
            memory_id = str(frag.get("id", ""))
            if not memory_id:
                continue
            score = frag.get("_fusion_score") or frag.get("similarity") or 0
            self.record_trace_event(
                workspace_id=workspace_id,
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

    def update_extraction_metrics(
        self,
        workspace_id: int,
        trigger_type: str = "auto_recall",
        conversation_id: Optional[str] = None,
        session_id: Optional[str] = None,
        query_snippet: Optional[str] = None,
        fragments_created: int = 0,
        llm_tokens_used: int = 0,
    ) -> None:
        """Record extraction metrics after each extraction (fire-and-forget hook)."""
        self.record_extraction_trigger(
            workspace_id=workspace_id,
            trigger_type=trigger_type,
            session_id=session_id,
            conversation_id=conversation_id,
            query_snippet=query_snippet,
            fragments_created=fragments_created,
            llm_tokens_used=llm_tokens_used,
        )


# ── Helper Functions ────────────────────────────────────────────

def _infer_current_status(events: List[Dict]) -> str:
    """Infer current memory status from event timeline."""
    status = "active"
    for e in reversed(events):
        et = e.get("event_type", "")
        if et == "deleted":
            return "deleted"
        elif et == "cold_marked":
            return "cold"
        elif et == "restored":
            return "active"
        elif et == "created":
            return "active"
    return status


def _calc_percentile(values: List[float], percentile: float) -> float:
    """Calculate percentile value from a list."""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * percentile / 100)))
    return sorted_vals[idx]


def _heuristic_accuracy_score(content: str) -> float:
    """Heuristic accuracy score based on content length and CJK character ratio."""
    content_len = len(content)
    has_cjk = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
    return min(0.9, 0.5 + content_len * 0.01 + has_cjk * 0.02)


def _keyword_relevance_score(query: str, content: str) -> Tuple[float, str]:
    """Keyword-based relevance score fallback."""
    query_terms = set(query.lower().split())
    content_lower = content.lower()
    matches = sum(1 for t in query_terms if t in content_lower)
    score = min(0.8, matches / max(1, len(query_terms)))
    return score, f"关键词匹配: {matches}/{len(query_terms)}"
