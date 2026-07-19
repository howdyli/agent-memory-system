"""
Search — Multi-signal hybrid retrieval module.

Core-layer logic extracted from backend/hybrid_search_service.py.
Uses injected RelationalStore + VectorStore + LLMBackend instead of global singletons.
No Redis-backed config persistence (belongs to Server layer with CacheStore).

Fusion formula:
    final_score = alpha * semantic_score + beta * bm25_score
                + gamma * entity_boost + delta * recency_score

Components:
1. BM25 full-text search (SQLite FTS5 + LIKE fallback for CJK)
2. Entity boost (EntityExtractor from compression module)
3. Time decay (half-life based recency scoring)
4. LLM reranking (optional, via injected LLMBackend)
5. Fusion scoring and result aggregation

Usage:
    from .modules.search import HybridSearchManager
    searcher = HybridSearchManager(
        relational_store=relational,
        vector_store=vector,
        cache_store=cache,
        llm_backend=llm,
    )
    results = searcher.search(workspace_id=1, query="张三 产品经理")
"""

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

# Import EntityExtractor from compression module (shared utility)
from .compression import EntityExtractor, LifecycleHalfLifeCalculator


# ─────────────────────────────────────────────────────────────────
# Default Configuration
# ─────────────────────────────────────────────────────────────────

DEFAULT_SEARCH_CONFIG = {
    # Fusion weights
    "alpha": 0.35,    # Semantic search weight
    "beta": 0.30,     # BM25 full-text search weight
    "gamma": 0.20,    # Entity boost weight
    "delta": 0.15,    # Time decay weight

    # Retrieval parameters
    "top_k_initial": 30,    # Candidate count before fusion
    "top_k_final": 10,      # Final result count
    "bm25_top_k": 30,       # BM25 recall count
    "semantic_top_k": 20,   # Semantic recall count

    # Decay parameters
    "recency_half_life": 90,
    "recency_min_score": 0.30,

    # Entity boost
    "entity_boost_person": 0.15,
    "entity_boost_organization": 0.10,
    "entity_boost_location": 0.05,
    "entity_boost_event": 0.08,

    # Reranking
    "rerank_enabled": True,
    "rerank_top_k": 10,
}


# ─────────────────────────────────────────────────────────────────
# BM25 Full-Text Search
# ─────────────────────────────────────────────────────────────────

def _has_cjk(text: str) -> bool:
    """Check if text contains CJK characters."""
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f':
            return True
    return False


def _tokenize_fts_query(query: str) -> str:
    """Tokenize query for FTS5 search syntax."""
    if not query or not query.strip():
        return ""
    parts = query.strip().split()
    return " AND ".join(parts) if parts else ""


class HybridSearchManager:
    """Multi-signal hybrid retrieval manager.

    Core-layer search combining:
    - Semantic vector search (via VectorStore)
    - BM25 full-text search (via RelationalStore fts_search + LIKE fallback)
    - Entity boost (via EntityExtractor)
    - Time decay (via LifecycleHalfLifeCalculator)
    - Optional LLM reranking (via injected LLMBackend)

    All operations scoped by workspace_id.
    """

    def __init__(
        self,
        relational_store: Any,
        vector_store: Any,
        cache_store: Any = None,
        llm_backend: Any = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        self._relational = relational_store
        self._vector = vector_store
        self._cache = cache_store
        self._llm = llm_backend
        self._config = {**DEFAULT_SEARCH_CONFIG, **(config or {})}

    # ── Configuration ────────────────────────────────────────────

    def get_config(self) -> Dict[str, Any]:
        """Get current search configuration."""
        # Check cache_store for overrides
        if self._cache:
            try:
                stored = self._cache.get("hybrid_search_config")
                if stored:
                    stored_config = json.loads(stored) if isinstance(stored, str) else stored
                    return {**DEFAULT_SEARCH_CONFIG, **self._config, **stored_config}
            except Exception:
                pass
        return {**DEFAULT_SEARCH_CONFIG, **self._config}

    def update_config(self, updates: Dict[str, Any]) -> None:
        """Update search configuration. Persist to cache_store if available."""
        self._config.update(updates)
        if self._cache:
            try:
                self._cache.set("hybrid_search_config", json.dumps(self._config, ensure_ascii=False))
            except Exception as e:
                logger.debug(f"Config persistence to cache failed: {e}")
        logger.info(f"Updated HybridSearch config: {updates}")

    def get_weights(self) -> Dict[str, float]:
        """Get current fusion weights."""
        config = self.get_config()
        return {k: config.get(k, DEFAULT_SEARCH_CONFIG[k]) for k in ["alpha", "beta", "gamma", "delta"]}

    def set_weights(
        self,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
        gamma: Optional[float] = None,
        delta: Optional[float] = None,
    ) -> Dict[str, float]:
        """Set fusion weights. Clamped to [0.0, 1.0]."""
        updates = {}
        if alpha is not None:
            updates["alpha"] = max(0.0, min(1.0, alpha))
        if beta is not None:
            updates["beta"] = max(0.0, min(1.0, beta))
        if gamma is not None:
            updates["gamma"] = max(0.0, min(1.0, gamma))
        if delta is not None:
            updates["delta"] = max(0.0, min(1.0, delta))
        self.update_config(updates)
        return self.get_weights()

    # ── BM25 Search ──────────────────────────────────────────────

    def search_bm25(
        self,
        workspace_id: int,
        query: str,
        top_k: int = 20,
    ) -> List[Dict[str, Any]]:
        """BM25 full-text search using FTS5 + LIKE fallback.

        Args:
            workspace_id: Workspace isolation boundary
            query: Search query
            top_k: Maximum results

        Returns:
            List of fragment dicts with bm25_score
        """
        fragments = []

        # Try FTS5 MATCH via RelationalStore.fts_search
        try:
            fts_results = self._relational.fts_search(query, limit=top_k)
            if fts_results:
                for r in fts_results:
                    r = dict(r) if not isinstance(r, dict) else r
                    # FTS5 bm25 score: negative, normalize
                    raw_score = r.pop("bm25_score", 0) if "bm25_score" in r else 0
                    normalized = max(0.0, min(1.0, 1.0 / (1.0 + abs(raw_score))))
                    r["bm25_score"] = round(normalized, 4)
                    r["workspace_id"] = workspace_id
                    fragments.append(r)
        except Exception as e:
            logger.debug(f"FTS5 search failed: {e}")

        # CJK LIKE fallback if FTS5 results are sparse
        if len(fragments) < top_k and _has_cjk(query):
            like_results = self._search_with_like(workspace_id, query, top_k)
            existing_ids = {f.get("id") or f.get("rowid") for f in fragments}
            for lr in like_results:
                lr_id = lr.get("id") or lr.get("rowid")
                if lr_id not in existing_ids:
                    fragments.append(lr)

        logger.info(f"BM25 search: '{query}' → {len(fragments)} results")
        return fragments

    def _search_with_like(
        self,
        workspace_id: int,
        query: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """SQL LIKE search for CJK text (FTS5 fallback).

        Term frequency (TF) as relevance score.
        """
        try:
            # Extract search terms: CJK chars + English words
            terms = []
            current = ""
            for ch in query:
                if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f':
                    if current:
                        terms.append(current.lower())
                        current = ""
                    terms.append(ch)
                elif ch.isalnum() or ch in '-_.':
                    current += ch
                else:
                    if current:
                        terms.append(current.lower())
                        current = ""
            if current:
                terms.append(current.lower())

            terms = list(dict.fromkeys(terms))  # Deduplicate
            if not terms:
                return []

            # Build LIKE conditions
            conditions = []
            params: list = []
            for term in terms:
                if len(term) >= 1:
                    conditions.append("content LIKE ?")
                    params.append(f"%{term}%")

            if not conditions:
                return []

            where_clause = " OR ".join(conditions)
            sql = (
                f"SELECT id, content, fragment_type, importance_score, created_at "
                f"FROM memory_fragments "
                f"WHERE workspace_id = ? AND ({where_clause}) "
                f"ORDER BY importance_score DESC LIMIT ?"
            )
            params_final = [workspace_id] + params + [top_k]

            rows = self._relational.execute_sql(sql, tuple(params_final))

            results = []
            for r in rows:
                r = dict(r) if not isinstance(r, dict) else r
                content = r.get("content", "") or ""
                # Calculate TF score
                match_count = sum(content.lower().count(t.lower()) for t in terms)
                r["bm25_score"] = round(min(1.0, match_count / (match_count + 5)), 4)
                r["workspace_id"] = workspace_id
                results.append(r)

            results.sort(key=lambda x: x.get("bm25_score", 0), reverse=True)
            return results[:top_k]

        except Exception as e:
            logger.debug(f"LIKE search failed: {e}")
            return []

    def rebuild_fts_index(self, workspace_id: Optional[int] = None) -> int:
        """Rebuild FTS5 full-text index from memory_fragments.

        Args:
            workspace_id: Optional workspace scope (None = all)

        Returns:
            Number of indexed entries
        """
        try:
            # Clear and rebuild FTS
            try:
                self._relational.execute_sql("DELETE FROM fragments_fts")
            except Exception:
                pass

            if workspace_id:
                self._relational.execute_sql(
                    "INSERT INTO fragments_fts(rowid, content, fragment_type) "
                    "SELECT id, content, fragment_type FROM memory_fragments "
                    "WHERE workspace_id = ?",
                    (workspace_id,),
                )
            else:
                self._relational.execute_sql(
                    "INSERT INTO fragments_fts(rowid, content, fragment_type) "
                    "SELECT id, content, fragment_type FROM memory_fragments"
                )

            count_rows = self._relational.execute_sql("SELECT COUNT(*) as cnt FROM fragments_fts")
            total = count_rows[0].get("cnt", 0) if count_rows else 0
            logger.info(f"FTS5 index rebuilt: {total} entries")
            return total

        except Exception as e:
            logger.error(f"FTS5 index rebuild failed: {e}")
            return 0

    # ── Entity Boost ─────────────────────────────────────────────

    def compute_entity_boost(self, query: str, fragment: Dict[str, Any]) -> float:
        """Calculate entity boost score for a fragment.

        Extracts entities from query, checks fragment content for matches,
        applies type-specific boost weights.

        Returns:
            Entity boost score (0.0 ~ 0.30)
        """
        try:
            content = (fragment.get("content") or "").lower()
            boost = 0.0

            config = self.get_config()
            type_weights = {
                "person": config.get("entity_boost_person", 0.15),
                "organization": config.get("entity_boost_organization", 0.10),
                "location": config.get("entity_boost_location", 0.05),
                "event": config.get("entity_boost_event", 0.08),
            }

            def _match_entity(name: str, etype: str) -> float:
                name_lower = name.lower().strip()
                if not name_lower or len(name_lower) < 2:
                    return 0.0
                if name_lower in content:
                    count = content.count(name_lower)
                    weight = type_weights.get(etype, 0.05)
                    return weight * min(2.0, 1.0 + count * 0.5)
                return 0.0

            # Source 1: EntityExtractor
            try:
                extracted = EntityExtractor.extract_entities_with_types(query)
                for ent in extracted:
                    boost += _match_entity(ent.get("name", ""), ent.get("type", "person"))
            except Exception:
                pass

            # Source 2: Bigram matching for compact CJK queries
            seen_names: Set[str] = set()
            for i in range(len(query)):
                for j in range(2, min(5, len(query) - i + 1)):
                    sub = query[i:i+j]
                    if all('\u4e00' <= ch <= '\u9fff' for ch in sub):
                        if sub not in seen_names:
                            seen_names.add(sub)
                            if re.search(r'(公司|集团|学院|大学|银行)$', sub):
                                boost += _match_entity(sub, "organization")
                            else:
                                boost += _match_entity(sub, "person")

            # Info-type extra boost
            if fragment.get("fragment_type") == "info" and boost > 0:
                boost += 0.05

            return min(0.30, boost)

        except Exception as e:
            logger.debug(f"Entity boost computation failed: {e}")
            return 0.0

    # ── Time Decay ───────────────────────────────────────────────

    def compute_recency_score(self, fragment: Dict[str, Any]) -> float:
        """Calculate time decay score for a fragment.

        Formula: max(recency_min_score, e^(-days_since / recency_half_life))
        Info type (permanent) = 1.0

        Returns:
            Recency score (recency_min_score ~ 1.0)
        """
        # Info type: permanent, no decay
        if fragment.get("fragment_type") == "info":
            return 1.0

        created_at = fragment.get("created_at")
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
            config = self.get_config()
            half_life = config.get("recency_half_life", 90)
            min_score = config.get("recency_min_score", 0.30)

            score = math.exp(-days_since / half_life)
            return max(min_score, min(1.0, score))

        except Exception:
            return 1.0

    # ── LLM Reranking ────────────────────────────────────────────

    RERANK_SYSTEM_PROMPT = """你是一个记忆检索重排序引擎。
给定用户查询和一组候选记忆片段，请按相关性从高到低重新排序。

仅返回排序后的片段编号列表（使用原始序号），格式为 JSON 数组：
[3, 1, 5, 2, 4]

规则：
1. 与查询直接相关的排在前面
2. 信息更具体的排在前面
3. 保留 top-5 最相关的结果
4. 如果都不相关，返回空数组 []"""

    def rerank_with_llm(
        self,
        query: str,
        fragments: List[Dict[str, Any]],
        workspace_id: int,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """LLM-based reranking of candidate fragments.

        Falls back to fusion score sorting when LLM unavailable.
        """
        if not fragments:
            return []

        config = self.get_config()
        if not config.get("rerank_enabled", True):
            fragments.sort(key=lambda x: x.get("_fusion_score", 0), reverse=True)
            return fragments[:top_k]

        if not self._llm:
            # No LLM: sort by fusion score
            fragments.sort(key=lambda x: x.get("_fusion_score", 0), reverse=True)
            return fragments[:top_k]

        try:
            # Build candidate text
            candidates_text = ""
            for i, frag in enumerate(fragments[:top_k * 2], 1):
                content = (frag.get("content") or "")[:200]
                frag_type = frag.get("fragment_type", "记忆")
                score = frag.get("_fusion_score", 0)
                candidates_text += f"{i}. [{frag_type}] {content} (score: {score:.3f})\n"

            messages = [
                {"role": "system", "content": self.RERANK_SYSTEM_PROMPT},
                {"role": "user", "content": f"用户查询: {query}\n\n候选记忆:\n{candidates_text}\n\n请输出排序后的序号数组:"},
            ]

            result = self._llm.chat(messages=messages, temperature=0.1)

            if result:
                text = result if isinstance(result, str) else result.get("content", "")

                # Extract JSON array
                code_match = re.search(r'```(?:json)?\s*\n?\[.*?\]\n?```', text, re.DOTALL)
                if code_match:
                    text = code_match.group(0).replace('```json', '').replace('```', '').strip()

                array_match = re.search(r'\[[\d,\s]+\]', text)
                if array_match:
                    order = json.loads(array_match.group(0))
                    reranked = []
                    seen = set()
                    for idx in order:
                        idx = int(idx) - 1
                        if 0 <= idx < len(fragments) and idx not in seen:
                            seen.add(idx)
                            reranked.append(fragments[idx])
                    # Add remaining
                    for i in range(len(fragments)):
                        if i not in seen:
                            reranked.append(fragments[i])
                    logger.info(f"LLM reranking: {len(reranked[:top_k])} results")
                    return reranked[:top_k]

        except Exception as e:
            logger.warning(f"LLM reranking failed, fallback to fusion sort: {e}")

        # Fallback: sort by fusion score
        fragments.sort(key=lambda x: x.get("_fusion_score", 0), reverse=True)
        return fragments[:top_k]

    # ── Score Normalization ──────────────────────────────────────

    def _normalize_scores(
        self,
        fragments: List[Dict[str, Any]],
        score_key: str,
    ) -> List[Dict[str, Any]]:
        """Min-max normalize scores to [0, 1]."""
        if not fragments:
            return fragments

        scores = [f.get(score_key, 0) or 0 for f in fragments]
        if not scores:
            return fragments

        min_s = min(scores)
        max_s = max(scores)
        range_s = max_s - min_s if max_s > min_s else 1.0

        for f in fragments:
            raw = f.get(score_key, 0) or 0
            f[f"{score_key}_norm"] = (raw - min_s) / range_s

        return fragments

    # ── Main Search Entry ────────────────────────────────────────

    def search(
        self,
        workspace_id: int,
        query: str,
        alpha: Optional[float] = None,
        beta: Optional[float] = None,
        gamma: Optional[float] = None,
        delta: Optional[float] = None,
        top_k: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Multi-signal hybrid search — main entry point.

        Fusion formula:
            final = α * semantic + β * bm25 + γ * entity_boost + δ * recency

        Flow:
        1. Semantic search (VectorStore) → semantic_score
        2. BM25 search (FTS5 + LIKE) → bm25_score
        3. Merge candidates (union)
        4. Compute entity boost + time decay
        5. Normalize scores
        6. Calculate fusion score
        7. LLM reranking (optional)

        Args:
            workspace_id: Workspace isolation boundary
            query: Search query text
            alpha/beta/gamma/delta: Override fusion weights
            top_k: Override final result count

        Returns:
            {
                "fragments": List[Dict],
                "count": int,
                "query": str,
                "weights_used": Dict,
                "signals": Dict,
            }
        """
        config = self.get_config()
        w_alpha = alpha if alpha is not None else config["alpha"]
        w_beta = beta if beta is not None else config["beta"]
        w_gamma = gamma if gamma is not None else config["gamma"]
        w_delta = delta if delta is not None else config["delta"]
        top_k_final = top_k if top_k is not None else config["top_k_final"]

        logger.info(
            f"Hybrid search: '{query}' "
            f"weights=({w_alpha:.2f}, {w_beta:.2f}, {w_gamma:.2f}, {w_delta:.2f})"
        )

        # ── Step 1: Semantic search ─────────────────────────────
        semantic_map: Dict[Any, Dict] = {}
        try:
            vector_results = self._vector.search(
                "memory_fragments",
                query_text=query,
                n_results=config["semantic_top_k"],
                where={"workspace_id": str(workspace_id)},
            )
            for r in vector_results:
                doc_id = r.get("id", "")
                if doc_id:
                    r["semantic_score"] = r.get("similarity", 0.5) or 0.5
                    semantic_map[doc_id] = r
        except Exception as e:
            logger.debug(f"Semantic search failed: {e}")

        # ── Step 2: BM25 search ──────────────────────────────────
        bm25_map: Dict[Any, Dict] = {}
        try:
            bm25_results = self.search_bm25(workspace_id, query, config["bm25_top_k"])
            for r in bm25_results:
                fid = r.get("id") or r.get("rowid")
                if fid:
                    bm25_map[fid] = r
        except Exception as e:
            logger.debug(f"BM25 search failed: {e}")

        # ── Step 3: Merge candidates (union) ─────────────────────
        all_ids = set(semantic_map.keys()) | set(bm25_map.keys())
        if not all_ids:
            return {
                "fragments": [],
                "count": 0,
                "query": query,
                "weights_used": {"alpha": w_alpha, "beta": w_beta, "gamma": w_gamma, "delta": w_delta},
                "signals": {"semantic": 0, "bm25": 0, "entity": 0, "recency": 0},
            }

        # Get full fragment info from relational store
        fused_fragments: List[Dict[str, Any]] = []

        for fid in all_ids:
            # Try to get from relational store
            try:
                frag_id_int = int(fid) if fid is not None else 0
                frag = self._relational.get_fragment(workspace_id, frag_id_int)
                if not frag:
                    # Fallback: use whatever data we have from search results
                    frag = semantic_map.get(fid) or bm25_map.get(fid) or {}
                    if not frag:
                        continue
                else:
                    frag = dict(frag) if not isinstance(frag, dict) else frag

                    # Merge search scores
                    if fid in semantic_map:
                        frag["semantic_score"] = semantic_map[fid].get("semantic_score", 0.5)
                    else:
                        frag["semantic_score"] = 0.0

                    if fid in bm25_map:
                        frag["bm25_score"] = bm25_map[fid].get("bm25_score", 0.0)
                    else:
                        frag["bm25_score"] = 0.0

                    # Entity boost
                    frag["entity_boost"] = self.compute_entity_boost(query, frag)

                    # Time decay
                    frag["recency_score"] = self.compute_recency_score(frag)

                fused_fragments.append(frag)
            except Exception as e:
                logger.debug(f"Fragment merge for {fid} failed: {e}")

        if not fused_fragments:
            return {
                "fragments": [],
                "count": 0,
                "query": query,
                "weights_used": {"alpha": w_alpha, "beta": w_beta, "gamma": w_gamma, "delta": w_delta},
                "signals": {"semantic": 0, "bm25": 0, "entity": 0, "recency": 0},
            }

        # ── Step 4: Normalize ────────────────────────────────────
        for key in ["semantic_score", "bm25_score"]:
            fused_fragments = self._normalize_scores(fused_fragments, key)
            for f in fused_fragments:
                norm_key = f"{key}_norm"
                f[key] = f.get(norm_key, f.get(key, 0))

        # ── Step 5: Fusion calculation ────────────────────────────
        for frag in fused_fragments:
            s_semantic = frag.get("semantic_score", 0) or 0
            s_bm25 = frag.get("bm25_score", 0) or 0
            s_entity = frag.get("entity_boost", 0) or 0
            s_recency = frag.get("recency_score", 0) or 0

            final_score = (
                w_alpha * s_semantic
                + w_beta * s_bm25
                + w_gamma * s_entity
                + w_delta * s_recency
            )
            frag["_fusion_score"] = round(final_score, 4)
            frag["_signal_breakdown"] = {
                "semantic": round(s_semantic, 4),
                "bm25": round(s_bm25, 4),
                "entity": round(s_entity, 4),
                "recency": round(s_recency, 4),
            }

        # Sort by fusion score descending
        fused_fragments.sort(key=lambda x: x.get("_fusion_score", 0), reverse=True)

        # ── Step 6: LLM reranking ────────────────────────────────
        reranked = self.rerank_with_llm(
            query=query,
            fragments=fused_fragments[:config.get("rerank_top_k", 10)],
            workspace_id=workspace_id,
            top_k=top_k_final,
        )

        if not reranked:
            reranked = fused_fragments[:top_k_final]

        # Signal statistics
        signal_stats = {"semantic": 0, "bm25": 0, "entity": 0, "recency": 0}
        for frag in reranked:
            sd = frag.get("_signal_breakdown", {})
            for k in signal_stats:
                signal_stats[k] += sd.get(k, 0)
        if reranked:
            for k in signal_stats:
                signal_stats[k] = round(signal_stats[k] / len(reranked), 4)

        logger.info(f"Hybrid search complete: {len(reranked)} results")

        return {
            "fragments": reranked,
            "count": len(reranked),
            "query": query,
            "weights_used": {"alpha": w_alpha, "beta": w_beta, "gamma": w_gamma, "delta": w_delta},
            "signals": signal_stats,
        }
