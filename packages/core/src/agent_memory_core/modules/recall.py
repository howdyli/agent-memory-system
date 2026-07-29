"""RecallManager — Core-layer unified recall engine.

Pure business logic, no HTTP/auth dependency.
Provides semantic search, lifecycle filtering, budget-controlled selection,
and entity graph expansion recall.
Migrated from backend/services/recall_engine.py.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..events import EventEmitter, MemoryEvent, MemoryEventType
from ..store.base import RelationalStore, VectorStore

logger = logging.getLogger(__name__)

# ── Default Config ──────────────────────────────────────────────

DEFAULT_ENGINE_CONFIG = {
    "semantic_top_k": 10,
    "entity_top_k": 5,
    "entity_relevance_threshold": 0.3,
    "use_hybrid_search": True,
    "lifecycle_recall_update": True,
    "lifecycle_cold_threshold": 0.3,
}

# ── Profile Keys ────────────────────────────────────────────────

PROFILE_KEYS = [
    "user_name", "name", "username",
    "user_role", "role",
    "organization", "company", "org",
    "email", "phone", "location",
    "department", "title", "position",
]

PROFILE_LABELS = {
    "user_name": "姓名", "name": "姓名", "username": "用户名",
    "user_role": "角色", "role": "角色",
    "organization": "组织", "company": "公司", "org": "组织",
    "department": "部门", "title": "头衔", "position": "职位",
    "email": "邮箱", "phone": "电话", "location": "位置",
}

TYPE_LABELS = {
    "info": "信息",
    "preference": "偏好",
    "plan": "计划",
    "fact": "事实",
}


# ── Data Classes ────────────────────────────────────────────────

@dataclass
class RecallResult:
    """Recall result container."""
    memories: List[Dict[str, Any]] = field(default_factory=list)
    context_text: str = ""
    total_candidates: int = 0
    token_used: int = 0
    source: str = "recall_engine"


class RecallManager:
    """Unified recall engine — semantic search, lifecycle filter, budget control.

    Replaces backend/services/recall_engine.py.
    Uses RelationalStore + VectorStore for search, EventEmitter for hooks.
    Depends on LifecycleManager (injected) for decay scoring and cold marking.
    """

    def __init__(
        self,
        relational_store: RelationalStore,
        vector_store: VectorStore,
        event_emitter: Optional[EventEmitter] = None,
        lifecycle_manager: Optional[Any] = None,  # LifecycleManager instance
        config: Optional[Dict[str, Any]] = None,
    ):
        self._relational = relational_store
        self._vector = vector_store
        self._events = event_emitter
        self._lifecycle = lifecycle_manager
        self.config = {**DEFAULT_ENGINE_CONFIG, **(config or {})}

    # ── Core Recall ─────────────────────────────────────────────

    def recall(
        self,
        workspace_id: int,
        query: str,
        budget_tokens: int = 2000,
        top_k: Optional[int] = None,
        update_lifecycle: Optional[bool] = None,
    ) -> RecallResult:
        """Unified semantic recall with lifecycle filtering and budget control.

        Flow:
        1. Semantic search (vector + FTS)
        2. Lifecycle status filter (exclude soft_deleted/archived)
        3. Budget-controlled selection (MemoryValueScorer)
        4. Lifecycle update (optional)
        5. Format context text
        """
        try:
            _top_k = top_k or self.config["semantic_top_k"]
            _budget = budget_tokens if budget_tokens is not None else 2000
            _update_lc = update_lifecycle if update_lifecycle is not None else self.config.get("lifecycle_recall_update", True)

            if self._events:
                self._events.emit(MemoryEvent(
                    event_type=MemoryEventType.RECALL_TRIGGERED,
                    workspace_id=workspace_id,
                    data={"query": query, "top_k": _top_k},
                ))

            # 1. Semantic search
            vector_results = self._vector.search(
                "memory_fragments",
                query_text=query,
                n_results=_top_k,
                where={"user_id": str(workspace_id)},
            )

            # Also FTS
            fts_results = self._relational.fts_search(query, limit=_top_k)

            # Combine results
            all_memories = []
            seen_ids = set()

            for r in vector_results:
                frag_id = r.get("id", "")
                if frag_id not in seen_ids:
                    seen_ids.add(frag_id)
                    # Get full fragment from relational
                    try:
                        frag = self._relational.get_fragment(workspace_id, int(frag_id))
                        if frag:
                            frag["similarity"] = r.get("similarity", 0)
                            all_memories.append(frag)
                    except (ValueError, TypeError):
                        all_memories.append({
                            "content": r.get("document", ""),
                            "fragment_type": r.get("metadata", {}).get("fragment_type", ""),
                            "similarity": r.get("similarity", 0),
                            "lifecycle_status": "active",
                        })

            for r in fts_results:
                frag_id = str(r.get("rowid", ""))
                if frag_id not in seen_ids:
                    seen_ids.add(frag_id)
                    try:
                        frag = self._relational.get_fragment(workspace_id, int(frag_id))
                        if frag:
                            all_memories.append(frag)
                    except (ValueError, TypeError):
                        all_memories.append({
                            "content": r.get("content", ""),
                            "fragment_type": r.get("fragment_type", ""),
                            "lifecycle_status": "active",
                        })

            total_candidates = len(all_memories)

            # 2. Lifecycle filter
            active_memories = [
                mem for mem in all_memories
                if mem.get("lifecycle_status", "active") not in ("soft_deleted", "archived")
            ]

            # 3. Budget-controlled selection (simple token estimation)
            selected = self._select_by_budget(active_memories, query, _budget)

            if not selected:
                return RecallResult(total_candidates=total_candidates)

            # 4. Lifecycle update
            if _update_lc and self._lifecycle:
                self._update_lifecycle(workspace_id, selected)

            # 5. Format context
            context_text = self._format_context(selected)
            token_used = _estimate_tokens(context_text)

            if self._events:
                self._events.emit(MemoryEvent(
                    event_type=MemoryEventType.RECALL_COMPLETED,
                    workspace_id=workspace_id,
                    data={"query": query, "results_count": len(selected), "token_used": token_used},
                ))

            return RecallResult(
                memories=selected,
                context_text=context_text,
                total_candidates=total_candidates,
                token_used=token_used,
            )

        except Exception as e:
            logger.warning(f"RecallManager.recall failed: {e}")
            return RecallResult()

    # ── Profile Recall ──────────────────────────────────────────

    def recall_profile(self, workspace_id: int, budget: int = 600) -> str:
        """Recall user profile from KV variables."""
        variables = self._relational.list_variables(workspace_id)

        # Convert list to dict
        var_dict = {}
        for v in variables:
            key = v.get("key", "")
            value = v.get("value")
            if key and value is not None:
                var_dict[key] = value

        profile_lines = []
        used_tokens = 30

        for key in PROFILE_KEYS:
            value = var_dict.get(key)
            if value is not None and used_tokens < budget:
                value_str = str(value)
                token_cost = _estimate_tokens(f"{key}: {value_str}")
                if used_tokens + token_cost <= budget:
                    label = PROFILE_LABELS.get(key, key)
                    profile_lines.append(f"- {label}: {value_str}")
                    used_tokens += token_cost

        if profile_lines:
            return "[用户基本信息]\n" + "\n".join(profile_lines)
        return ""

    # ── Entity-expanded Recall ──────────────────────────────────

    def recall_with_entities(
        self,
        workspace_id: int,
        query: str,
        budget_tokens: int = 1200,
        top_k: Optional[int] = None,
    ) -> RecallResult:
        """Entity graph expanded recall — search via entity associations.

        Requires GraphManager to be injected from engine.
        """
        _top_k = top_k or self.config["entity_top_k"]

        # This method requires graph_manager — which will be injected
        # from engine after all managers are assembled.
        # For now, fall back to standard recall.
        return self.recall(workspace_id, query, budget_tokens, _top_k)

    # ── Memory Details Extraction ───────────────────────────────

    def extract_memory_details(self, selected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract details from selected memories for context building."""
        details = []
        for mem in selected:
            content = (mem.get("content") or "").strip()[:200]
            mem_type = mem.get("fragment_type", "memory")
            similarity = mem.get("similarity", 0)

            try:
                similarity = float(similarity)
            except (ValueError, TypeError):
                similarity = 0.0

            details.append({
                "content": content,
                "type": mem_type,
                "score": round(similarity, 4),
            })
        return details

    # ── Internal Helpers ────────────────────────────────────────

    def _select_by_budget(
        self,
        memories: List[Dict[str, Any]],
        query: str,
        budget_tokens: int,
    ) -> List[Dict[str, Any]]:
        """Select top memories within token budget.

        Simple greedy selection: sort by relevance, add until budget exhausted.
        """
        # Sort by similarity/importance (higher first)
        scored = []
        for mem in memories:
            sim = float(mem.get("similarity", 0) or 0)
            importance = float(mem.get("importance_score", 0.5) or 0.5)
            # Composite score: similarity * 0.7 + importance * 0.3
            composite = sim * 0.7 + importance * 0.3
            scored.append((composite, mem))

        scored.sort(key=lambda x: x[0], reverse=True)

        selected = []
        used = 0
        for composite, mem in scored:
            content = (mem.get("content") or "").strip()
            est_tokens = _estimate_tokens(content)
            if used + est_tokens <= budget_tokens:
                selected.append(mem)
                used += est_tokens

        return selected

    def _update_lifecycle(self, workspace_id: int, selected: List[Dict[str, Any]]):
        """Update lifecycle for recalled memories (last_recalled_at + cold check)."""
        if not self._lifecycle:
            return

        now = datetime.now().isoformat()
        cold_threshold = self.config.get("lifecycle_cold_threshold", 0.3)

        for mem in selected:
            memory_id = mem.get("id")
            if not memory_id:
                continue

            # Check decay score and auto-cold-mark if below threshold
            fragment_type = mem.get("fragment_type", "")
            half_life_days = self._lifecycle.get_half_life(fragment_type)
            decay = self._lifecycle.calculate_decay_score(mem.get("created_at"), half_life_days)

            if decay < cold_threshold:
                try:
                    self._lifecycle.mark_cold(
                        workspace_id=workspace_id,
                        memory_type="fragment",
                        memory_id=str(memory_id),
                        reason="decay_below_threshold",
                    )
                except Exception:
                    pass

    def _format_context(self, selected: List[Dict[str, Any]]) -> str:
        """Format recall results as context text."""
        lines = ["[相关记忆]"]
        for i, mem in enumerate(selected, 1):
            content = (mem.get("content") or "").strip()
            mem_type = mem.get("fragment_type", "memory")
            importance = float(mem.get("importance_score", 0.5) or 0.5)
            similarity = float(mem.get("similarity", 0) or 0)

            label = TYPE_LABELS.get(mem_type, mem_type)
            lines.append(
                f"  {i}. [{label}] {content} "
                f"(相关度: {similarity:.2f}, 重要: {importance:.2f})"
            )

        return "\n".join(lines)


# ── Helper Functions ────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Rough token estimation: ~4 chars per token for English, ~2 chars for CJK."""
    if not text:
        return 0
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    non_cjk = len(text) - cjk_count
    return int(cjk_count / 2 + non_cjk / 4) + 1
