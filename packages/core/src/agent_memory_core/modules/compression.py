"""
Compression — Context compression and layered memory injection.

Core-layer logic extracted from backend/context_compressor.py.
Uses injected RelationalStore + VectorStore + CacheStore + LLMBackend instead of global singletons.
No DB-backed conversation_history/chat_sessions persistence (belongs to Server layer).

Usage:
    from .modules.compression import ContextCompressor
    compressor = ContextCompressor(
        relational_store=relational,
        vector_store=vector,
        cache_store=cache,
        variable_manager=var_mgr,
        fragment_manager=frag_mgr,
        recall_manager=recall_mgr,
        llm_backend=llm,
    )
    context = compressor.build_context(
        workspace_id=1, session_id="sess-1", user_query="hello"
    )
"""

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# Default Configuration
# ─────────────────────────────────────────────────────────────────

DEFAULT_COMPRESSION_CONFIG = {
    # Sliding window
    "recent_rounds": 5,          # Keep last 5 full rounds
    "max_context_tokens": 4000,  # Total memory injection token budget
    "reserve_tokens": 200,       # Buffer tokens

    # Layered budget
    "level1_tokens": 600,        # Level 1 Profile budget
    "level2_tokens": 2000,       # Level 2 semantic memory budget
    "level3_tokens": 1200,       # Level 3 entity expansion budget

    # Retrieval parameters
    "semantic_top_k": 10,
    "entity_top_k": 5,
    "entity_relevance_threshold": 0.3,
    "priority_decay_factor": 0.85,

    # Window compression
    "compression_min_rounds": 3,
    "compression_interval": 10,

    # Lifecycle
    "lifecycle_cold_threshold": 0.3,
    "lifecycle_recall_update": True,
}


# ─────────────────────────────────────────────────────────────────
# Token Estimation
# ─────────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """Estimate token count for text.

    Mixed content strategy:
    - CJK characters: ~2 tokens each
    - Latin/numbers: ~1 token per 4 chars
    - Whitespace: ignored
    """
    if not text:
        return 0

    chinese_chars = 0
    other_chars = 0

    for c in text:
        if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef':
            chinese_chars += 1
        elif c in ' \t\n\r':
            continue
        else:
            other_chars += 1

    return chinese_chars * 2 + math.ceil(other_chars / 4)


# ─────────────────────────────────────────────────────────────────
# Memory Value Scorer
# ─────────────────────────────────────────────────────────────────

class MemoryValueScorer:
    """Score memory value/cost ratio for budget-controlled selection.

    Value factors: importance, relevance, recency, half-life decay, completeness.
    """

    @staticmethod
    def score_memory_value(memory: Dict[str, Any], query: str) -> float:
        """Calculate composite value score (0-1) for a memory fragment.

        Factors:
        - Base importance (importance_score)
        - Semantic relevance (relevance/similarity)
        - Half-life decay (fragment_type-specific)
        - Recency (time decay)
        - Completeness (content length heuristic)
        """
        # Base importance
        importance = memory.get("importance_score", 0.5)
        try:
            importance = float(importance)
        except (ValueError, TypeError):
            importance = 0.5

        # Relevance
        relevance = memory.get("relevance", memory.get("similarity", 0.5)) or 0.5
        try:
            relevance = float(relevance)
        except (ValueError, TypeError):
            relevance = 0.5

        # Half-life decay
        fragment_type = memory.get("fragment_type", "")
        decay_factor = LifecycleHalfLifeCalculator.calculate_decay(
            created_at=memory.get("created_at"),
            fragment_type=fragment_type,
        )

        # Recency
        recency = 1.0
        created_at = memory.get("created_at")
        if created_at:
            try:
                if isinstance(created_at, str):
                    created_time = datetime.fromisoformat(
                        created_at.replace("Z", "+00:00").split(".")[0]
                    )
                else:
                    created_time = created_at
                days_since = max(0, (datetime.now() - created_time).days)
                recency = max(0.3, 1.0 - days_since / 180)
            except Exception:
                pass

        # Completeness
        content = memory.get("content", "")
        completeness = min(1.0, len(content) / 200) if content else 0.3

        score = (
            0.30 * importance
            + 0.30 * relevance
            + 0.15 * recency
            + 0.15 * decay_factor
            + 0.10 * completeness
        )

        return min(1.0, max(0.0, score))

    @staticmethod
    def value_per_token(memory: Dict[str, Any], query: str) -> float:
        """Calculate value density (value/cost ratio) for budget selection."""
        value = MemoryValueScorer.score_memory_value(memory, query)
        content = memory.get("content", "")
        cost = estimate_tokens(content) + 10  # +10 format overhead
        if cost <= 0:
            return 0
        return value / cost

    @staticmethod
    def select_top_by_budget(
        memories: List[Dict[str, Any]],
        query: str,
        budget_tokens: int,
    ) -> List[Dict[str, Any]]:
        """Greedy selection of optimal memory combination within token budget.

        Sort by value density descending, pick until budget exhausted.
        """
        if not memories or budget_tokens <= 0:
            return []

        scored = []
        for m in memories:
            density = MemoryValueScorer.value_per_token(m, query)
            cost = estimate_tokens(m.get("content", "")) + 10
            scored.append((density, cost, m))

        scored.sort(key=lambda x: x[0], reverse=True)

        selected = []
        used_tokens = 0

        for density, cost, memory in scored:
            if used_tokens + cost <= budget_tokens:
                selected.append(memory)
                used_tokens += cost

        logger.debug(
            f"Budget selection: {len(memories)} candidates → "
            f"{len(selected)} selected, used {used_tokens}/{budget_tokens} tokens"
        )

        return selected


# ─────────────────────────────────────────────────────────────────
# Half-Life Calculator (standalone, no lifecycle manager dependency)
# ─────────────────────────────────────────────────────────────────

class LifecycleHalfLifeCalculator:
    """Standalone half-life calculation for use in compression/search.

    Does NOT require LifecycleManager — pure math based on fragment_type.
    """

    # Half-life configs (days)
    HALF_LIFE_CONFIG = {
        "info": None,        # Permanent — no decay
        "plan": 90,
        "preference": 1,     # Very short-lived
        "event": 30,
        "procedure": 180,
        "relationship": None, # Permanent
    }

    @staticmethod
    def get_half_life_days(fragment_type: str) -> Optional[float]:
        """Get half-life in days for a fragment type. None = permanent."""
        cfg = LifecycleHalfLifeCalculator.HALF_LIFE_CONFIG
        return cfg.get(fragment_type, 90)  # Default 90 days

    @staticmethod
    def calculate_decay(created_at: Any, fragment_type: str) -> float:
        """Calculate decay score based on half-life.

        Formula: 2^(-days_since / half_life_days)
        Permanent types return 1.0.
        """
        half_life = LifecycleHalfLifeCalculator.get_half_life_days(fragment_type)
        if half_life is None:
            return 1.0  # Permanent

        if not created_at:
            return 1.0

        try:
            if isinstance(created_at, str):
                created_time = datetime.fromisoformat(
                    created_at.replace("Z", "+00:00").split(".")[0]
                )
            else:
                created_time = created_at
            days_since = max(0, (datetime.now() - created_time).days)
            score = 2 ** (-days_since / half_life)
            return max(0.0, min(1.0, score))
        except Exception:
            return 1.0


# ─────────────────────────────────────────────────────────────────
# Entity Extraction (for Level 3 expansion)
# ─────────────────────────────────────────────────────────────────

class EntityExtractor:
    """Extract key entities from text for graph traversal / entity boost.

    Supports CJK and Latin entity patterns:
    - Quoted content (中文引号)
    - "X是X" subject extraction
    - "关于X" / "X相关" patterns
    - Capitalized English entities
    - Named entities with suffixes (项目/系统/平台 etc.)
    - Person names (XX说/问/告诉)
    - Organization names (XX公司/集团 etc.)
    """

    @staticmethod
    def extract_entities(text: str) -> List[str]:
        """Extract entity names from text."""
        entities = set()

        # 1. Quoted content
        quoted = re.findall(r'[""](.+?)[""]', text)
        for q in quoted:
            q = q.strip()
            if 2 <= len(q) <= 20:
                entities.add(q)

        # 2. "X是X" subject extraction
        for s in re.findall(r'([\u4e00-\u9fff]{2,6})(?:是|叫|指|代表)', text):
            entities.add(s)

        # 3. "关于X" / "X相关"
        for a in re.findall(r'关于([\u4e00-\u9fff]{2,10})', text):
            entities.add(a)

        # 4. Capitalized English entities
        for e in re.findall(r'\b([A-Z][a-zA-Z]{2,20})\b', text):
            entities.add(e)

        # 5. Named entities with suffixes
        for s in re.findall(
            r'([\u4e00-\u9fff]{2,10})(?:项目|系统|平台|工具|方案|技术|产品|功能)', text
        ):
            entities.add(s)

        # 6. Person names (XX说/问/告诉)
        for n in re.findall(r'([\u4e00-\u9fff]{2,4})(?:说|问|告诉|通知|联系|找|叫)', text):
            entities.add(n)

        # 7. Organization names
        for o in re.findall(
            r'([\u4e00-\u9fff]{2,10})(?:公司|集团|学院|医院|大学|银行)', text
        ):
            entities.add(o)

        return list(entities)

    @staticmethod
    def extract_entities_with_types(text: str) -> List[Dict[str, str]]:
        """Extract entities with type classification."""
        entities = EntityExtractor.extract_entities(text)
        typed = []
        for name in entities:
            etype = EntityExtractor._guess_type(name, text)
            typed.append({"name": name, "type": etype})
        return typed

    @staticmethod
    def _guess_type(name: str, context: str = "") -> str:
        """Guess entity type from name patterns."""
        if re.match(r'^[\u4e00-\u9fff]{2,4}$', name):
            return "person"
        if re.search(r'(公司|集团|学院|医院|大学|银行|机构|团队)$', name):
            return "organization"
        if re.search(r'(市|区|省|路|街|大厦)$', name):
            return "location"
        if re.search(r'(会|节|赛|战|活动|峰会|大会)', name):
            return "event"
        return "organization"  # Default


# ─────────────────────────────────────────────────────────────────
# Context Compressor (Main Class)
# ─────────────────────────────────────────────────────────────────

class ContextCompressor:
    """Context compression and layered memory injection.

    Core flow:
    1. Build dialog context (sliding window + summary compression)
    2. Layered memory injection (Profile → semantic → entity expansion)
    3. Token budget control (greedy selection of optimal memory combination)
    4. Output formatted context string for system prompt injection

    Conversation persistence (conversation_history, chat_sessions) belongs to
    Server layer. This Core module handles ONLY the compression/injection logic.
    """

    def __init__(
        self,
        relational_store: Any,
        vector_store: Any,
        variable_manager: Any,
        fragment_manager: Any,
        recall_manager: Any = None,
        cache_store: Any = None,
        llm_backend: Any = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self._relational = relational_store
        self._vector = vector_store
        self._variable_mgr = variable_manager
        self._fragment_mgr = fragment_manager
        self._recall_mgr = recall_manager
        self._cache = cache_store
        self._llm = llm_backend
        self.config = {**DEFAULT_COMPRESSION_CONFIG, **(config or {})}

        self.value_scorer = MemoryValueScorer()
        self.entity_extractor = EntityExtractor()

        # Track memories used in last build (for streaming output)
        self._last_memories_used: List[Dict[str, Any]] = []

    # ── Main Entry ──────────────────────────────────────────────

    def build_context(
        self,
        workspace_id: int,
        session_id: str,
        user_query: str,
    ) -> str:
        """Build complete injection context.

        Flow:
        1. Build dialog context (if conversation data available)
        2. Layered memory injection (budget-controlled)
        3. Format output

        Args:
            workspace_id: Workspace isolation boundary
            session_id: Session ID for conversation history
            user_query: Current user query

        Returns:
            Formatted context string
        """
        # Reset tracking
        self._last_memories_used = []

        context_parts = []

        # Step 1: Dialog context (from Server-provided conversation)
        dialog_context = self._build_dialog_context(workspace_id, session_id)
        if dialog_context:
            context_parts.append(dialog_context)

        # Step 2: Layered memory injection
        remaining_budget = self.config["max_context_tokens"] - self.config["reserve_tokens"]

        if dialog_context:
            used = estimate_tokens(dialog_context)
            remaining_budget = max(100, remaining_budget - used)

        memory_context = self._build_memory_context(
            workspace_id=workspace_id,
            user_query=user_query,
            budget=remaining_budget,
        )
        if memory_context:
            context_parts.append(memory_context)

        return "\n\n".join(context_parts)

    def build_context_with_details(
        self,
        workspace_id: int,
        session_id: str,
        user_query: str,
    ) -> Dict[str, Any]:
        """Build context and return memory details for streaming output.

        Returns:
            {"context_text": str, "memories_used": [...]}
        """
        context_text = self.build_context(
            workspace_id=workspace_id,
            session_id=session_id,
            user_query=user_query,
        )
        return {
            "context_text": context_text,
            "memories_used": self._last_memories_used,
        }

    # ── Step 1: Dialog Context ──────────────────────────────────

    def _build_dialog_context(self, workspace_id: int, session_id: str) -> str:
        """Build dialog context from conversation history.

        NOTE: In Core layer, conversation data is provided externally
        (from Server layer). This method attempts to query conversation
        tables if they exist, otherwise returns empty.

        Args:
            workspace_id: Workspace ID
            session_id: Session ID

        Returns:
            Dialog context string
        """
        try:
            # Try to query conversation_history from relational store
            # The Server layer should have created these tables
            rows = self._relational.execute_sql(
                "SELECT role, content, compressed_flag, round_number "
                "FROM conversation_history "
                f"WHERE session_id = ? AND workspace_id = ? "
                "ORDER BY round_number ASC, id ASC",
                (session_id, workspace_id),
            )

            if not rows:
                return ""

            # Separate compressed summary and recent messages
            summary_rows = self._relational.execute_sql(
                "SELECT summary FROM conversation_summaries "
                f"WHERE session_id = ? AND workspace_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (session_id, workspace_id),
            )

            parts = []

            # Add summary if available
            if summary_rows:
                summary_text = summary_rows[0].get("summary", "")
                if summary_text:
                    parts.append(f"[对话摘要]\n{summary_text}")

            # Get recent uncompressed messages
            recent_n = self.config["recent_rounds"]
            max_round_row = self._relational.execute_sql(
                "SELECT COALESCE(MAX(round_number), 0) as max_round "
                "FROM conversation_history "
                f"WHERE session_id = ? AND workspace_id = ?",
                (session_id, workspace_id),
            )
            max_round = max_round_row[0].get("max_round", 0) if max_round_row else 0

            start_round = max(1, max_round - recent_n + 1)

            recent_rows = self._relational.execute_sql(
                "SELECT role, content "
                "FROM conversation_history "
                f"WHERE session_id = ? AND workspace_id = ? "
                "AND round_number >= ? AND compressed_flag = 0 "
                "ORDER BY round_number ASC, id ASC",
                (session_id, workspace_id, start_round),
            )

            if recent_rows:
                recent_parts = ["[最近对话]"]
                for msg in recent_rows:
                    role_label = "用户" if msg.get("role") == "user" else "助手"
                    content = msg.get("content", "")
                    if content:
                        recent_parts.append(f"{role_label}: {content}")
                parts.append("\n".join(recent_parts))

            return "\n\n".join(parts) if parts else ""

        except Exception as e:
            logger.debug(f"Dialog context build failed (conversation tables may not exist): {e}")
            return ""

    # ── Step 2: Layered Memory Injection ────────────────────────

    def _build_memory_context(
        self,
        workspace_id: int,
        user_query: str,
        budget: int,
    ) -> str:
        """Build memory injection context (3-layer structure, budget-controlled).

        Level 1: User Profile (always inject)
        Level 2: High-relevance semantic memories
        Level 3: Entity expansion memories
        """
        memory_parts = []

        l1_budget = min(self.config["level1_tokens"], budget)
        l2_budget = min(self.config["level2_tokens"], max(0, budget - l1_budget))
        l3_budget = min(self.config["level3_tokens"], max(0, budget - l1_budget - l2_budget))

        # Level 1: Profile
        l1_result = self._inject_level1(workspace_id, l1_budget)
        if l1_result:
            memory_parts.append(l1_result)

        # Level 2: Semantic memories
        l2_actual_budget = max(0, budget - estimate_tokens(l1_result or ""))
        l2_result = ""
        if l2_actual_budget > 100:
            l2_result = self._inject_level2(
                workspace_id=workspace_id,
                query=user_query,
                budget=l2_actual_budget,
            )
            if l2_result:
                memory_parts.append(l2_result)

        # Level 3: Entity expansion
        l3_actual_budget = max(0, budget - estimate_tokens(
            (l1_result or "") + (l2_result or "")
        ))
        l3_result = ""
        if l3_actual_budget > 100:
            l3_result = self._inject_level3(
                workspace_id=workspace_id,
                query=user_query,
                budget=l3_actual_budget,
            )
            if l3_result:
                memory_parts.append(l3_result)

        return "\n\n".join(memory_parts)

    # ── Level 1: Profile ────────────────────────────────────────

    def _inject_level1(self, workspace_id: int, budget: int) -> str:
        """Inject user Profile — KV variables + high-importance fragments."""
        parts = []

        kv_context = self._get_kv_profile(workspace_id, budget)
        if kv_context:
            parts.append(kv_context)

        return "\n".join(parts)

    def _get_kv_profile(self, workspace_id: int, budget: int) -> str:
        """Get KV Profile from VariableManager."""
        try:
            variables = self._variable_mgr.list(workspace_id)
            if not variables:
                return ""

            profile_keys = [
                "user_name", "name", "username",
                "user_role", "role",
                "organization", "company", "org",
                "email", "phone", "location",
                "department", "title", "position",
            ]

            display_map = {
                "user_name": "姓名", "name": "姓名", "username": "用户名",
                "user_role": "角色", "role": "角色",
                "organization": "组织", "company": "公司", "org": "组织",
                "department": "部门", "title": "头衔", "position": "职位",
                "email": "邮箱", "phone": "电话", "location": "位置",
            }

            profile_lines = []
            used_tokens = 30  # Format overhead

            for key in profile_keys:
                value = variables.get(key)
                if value is not None and used_tokens < budget:
                    value_str = str(value)
                    token_cost = estimate_tokens(f"{key}: {value_str}")
                    if used_tokens + token_cost <= budget:
                        display_key = display_map.get(key, key)
                        profile_lines.append(f"- {display_key}: {value_str}")
                        used_tokens += token_cost

            # Supplement with high-importance fragments if KV is sparse
            if len(profile_lines) < 3:
                extra = self._get_high_importance_fragments(workspace_id, budget - used_tokens)
                profile_lines.extend(extra)

            if profile_lines:
                return "[用户基本信息]\n" + "\n".join(profile_lines)

        except Exception as e:
            logger.debug(f"KV Profile retrieval failed: {e}")

        return ""

    def _get_high_importance_fragments(self, workspace_id: int, budget: int) -> List[str]:
        """Get high-importance fragments as Profile supplement."""
        lines = []
        used = 0
        try:
            fragments = self._fragment_mgr.list(
                workspace_id,
                lifecycle_status="active",
            )
            # Sort by importance
            fragments.sort(key=lambda x: x.get("importance_score", 0.5), reverse=True)

            for frag in fragments[:5]:
                content = frag.get("content", "").strip()
                if not content:
                    continue
                token_cost = estimate_tokens(content)
                if used + token_cost <= budget:
                    lines.append(f"- {content}")
                    used += token_cost
        except Exception as e:
            logger.debug(f"High importance fragment retrieval failed: {e}")

        return lines

    # ── Level 2: Semantic Memories ──────────────────────────────

    def _inject_level2(self, workspace_id: int, query: str, budget: int) -> str:
        """Inject high-relevance semantic memories via RecallManager or direct search."""
        try:
            if self._recall_mgr:
                result = self._recall_mgr.recall(
                    workspace_id=workspace_id,
                    query=query,
                    budget_tokens=budget,
                )
                if result.memories:
                    # Track for build_context_with_details
                    for m in result.memories:
                        self._last_memories_used.append({
                            "content": m.get("content", ""),
                            "type": m.get("fragment_type", "memory"),
                            "score": m.get("_fusion_score", m.get("similarity", 0)),
                        })
                    return result.context_text
                return ""

            # Fallback: direct vector search
            vector_results = self._vector.search(
                "memory_fragments",
                query_text=query,
                n_results=self.config["semantic_top_k"],
                where={"workspace_id": str(workspace_id)},
            )

            if not vector_results:
                return ""

            # Filter active lifecycle
            active = []
            for r in vector_results:
                content = r.get("document", "")
                similarity = r.get("similarity", 0.5) or 0.5
                metadata = r.get("metadata", {})
                if metadata.get("lifecycle_status", "active") in ("soft_deleted", "archived"):
                    continue
                active.append({
                    "content": content,
                    "similarity": similarity,
                    "fragment_type": metadata.get("fragment_type", "info"),
                })

            selected = MemoryValueScorer.select_top_by_budget(
                active, query, budget
            )

            if selected:
                lines = ["[相关记忆]"]
                for m in selected:
                    lines.append(f"- {m['content']}")
                    self._last_memories_used.append({
                        "content": m["content"],
                        "type": m.get("fragment_type", "memory"),
                        "score": m.get("similarity", 0),
                    })
                return "\n".join(lines)

        except Exception as e:
            logger.warning(f"Level 2 semantic injection failed: {e}")

        return ""

    # ── Level 3: Entity Expansion ───────────────────────────────

    def _inject_level3(self, workspace_id: int, query: str, budget: int) -> str:
        """Inject entity expansion memories via RecallManager or graph search."""
        try:
            if self._recall_mgr:
                result = self._recall_mgr.recall_with_entities(
                    workspace_id=workspace_id,
                    query=query,
                    budget_tokens=budget,
                )
                if result.memories:
                    for m in result.memories:
                        self._last_memories_used.append({
                            "content": m.get("content", ""),
                            "type": "entity_expansion",
                            "score": m.get("_fusion_score", 0),
                        })
                    return result.context_text
                return ""

            # Fallback: entity extraction + vector search per entity
            entities = EntityExtractor.extract_entities(query)
            if not entities:
                return ""

            related_memories = []
            seen_ids = set()

            for entity in entities:
                try:
                    results = self._vector.search(
                        "memory_fragments",
                        query_text=entity,
                        n_results=self.config["entity_top_k"],
                        where={"workspace_id": str(workspace_id)},
                    )
                    for r in results:
                        doc_id = r.get("id", "")
                        if doc_id not in seen_ids:
                            seen_ids.add(doc_id)
                            related_memories.append({
                                "content": r.get("document", ""),
                                "similarity": r.get("similarity", 0.5) or 0.5,
                                "source_entity": entity,
                                "fragment_type": r.get("metadata", {}).get("fragment_type", ""),
                            })
                except Exception as e:
                    logger.debug(f"Entity '{entity}' search failed: {e}")

            # Filter by threshold
            filtered = [
                m for m in related_memories
                if m["similarity"] >= self.config["entity_relevance_threshold"]
            ]

            selected = MemoryValueScorer.select_top_by_budget(filtered, query, budget)

            if selected:
                lines = ["[关联记忆]"]
                for m in selected:
                    lines.append(f"- {m['content']} (via {m.get('source_entity', '')})")
                    self._last_memories_used.append({
                        "content": m["content"],
                        "type": "entity_expansion",
                        "score": m["similarity"],
                    })
                return "\n".join(lines)

        except Exception as e:
            logger.warning(f"Level 3 entity expansion failed: {e}")

        return ""

    # ── LLM Compression Summary ─────────────────────────────────

    def generate_compression_summary(self, conversation_text: str) -> str:
        """Generate a compression summary for old conversation rounds.

        Uses injected LLMBackend if available, falls back to heuristic.
        """
        if not conversation_text:
            return "(对话历史)"

        # Try LLM
        if self._llm:
            try:
                summary_prompt = (
                    "请将以下对话历史压缩为一段简洁的中文摘要（不超过 200 字）。\n"
                    "保留以下关键信息：\n"
                    "1. 用户的基本信息（姓名、角色、组织等）\n"
                    "2. 用户的偏好和习惯\n"
                    "3. 用户的计划和待办事项\n"
                    "4. 已完成的对话主题\n"
                    "5. 重要的约定和决定\n\n"
                    "对话历史：\n"
                    f"{conversation_text}\n\n"
                    "摘要："
                )

                result = self._llm.chat(
                    messages=[{"role": "user", "content": summary_prompt}],
                    temperature=0.3,
                    max_tokens=500,
                )

                if result:
                    content = result if isinstance(result, str) else result.get("content", "")
                    if content:
                        summary = content.strip()
                        if len(summary) > 500:
                            summary = summary[:497] + "..."
                        return summary

            except Exception as e:
                logger.warning(f"LLM compression summary failed, falling back: {e}")

        # Heuristic fallback
        return self._heuristic_summary(conversation_text)

    def _heuristic_summary(self, conversation_text: str) -> str:
        """Rule-based summary when LLM is unavailable."""
        lines = conversation_text.strip().split("\n")
        user_messages = []
        total_turns = 0

        for line in lines:
            if line.startswith("用户:") or line.startswith("user:"):
                content = line.split(":", 1)[1].strip() if ":" in line else ""
                if content:
                    user_messages.append(content)
                total_turns += 1

        if user_messages:
            recent = "；".join(user_messages[-3:])
            return f"对话共 {total_turns} 轮，最近话题：{recent}"

        return "(对话历史)"

    # ── Configuration ────────────────────────────────────────────

    def update_config(self, updates: Dict[str, Any]) -> None:
        """Update compression configuration."""
        self.config.update(updates)
        logger.info(f"Updated ContextCompressor config: {updates}")

    def get_config(self) -> Dict[str, Any]:
        """Get current configuration."""
        return self.config.copy()
