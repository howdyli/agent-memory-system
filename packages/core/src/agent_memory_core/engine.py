"""
MemoryEngine — Core layer unified entry point.

Pure business logic, no HTTP/auth dependencies.
Composes all sub-managers and provides high-level convenience methods.

Usage (embedded mode):
    engine = MemoryEngine.from_config(CoreConfig(database_url="sqlite:///./mem.db"))
    engine.remember(workspace_id=1, key="name", value="Alice")
    results = engine.recall(workspace_id=1, query="Alice's preferences")

Usage (explicit):
    engine = MemoryEngine(
        relational_store=SQLiteStore("./mem.db"),
        vector_store=ChromaStore("./chromadb_data"),
    )
    engine.relational_store.ensure_schema()
"""

from typing import Any, Callable, Dict, List, Optional

from .config import CoreConfig
from .events import EventEmitter, MemoryEvent, MemoryEventType, EventHandler
from .store.base import RelationalStore, VectorStore, CacheStore
from .store.factory import create_relational_store, create_vector_store, create_cache_store


class MemoryEngine:
    """Pure logic memory engine — no HTTP/auth dependency.

    Embeddable directly or used via Server thin wrapper.
    Composes sub-managers for each memory domain.
    """

    def __init__(
        self,
        relational_store: RelationalStore,
        vector_store: VectorStore,
        cache_store: Optional[CacheStore] = None,
        event_emitter: Optional[EventEmitter] = None,
        config: Optional[CoreConfig] = None,
    ):
        self._relational = relational_store
        self._vector = vector_store
        self._cache = cache_store
        self._events = event_emitter or EventEmitter()
        self._config = config or CoreConfig()

        # Ensure schema exists
        self._relational.ensure_schema()

        # Sub-managers will be initialized in Phase 1 module migration
        # For now, the engine delegates directly to stores
        self._managers_initialized = False

    @classmethod
    def from_config(cls, config: Optional[CoreConfig] = None) -> "MemoryEngine":
        """Create engine from CoreConfig — auto-creates all store instances."""
        if config is None:
            config = CoreConfig()

        relational = create_relational_store(config)
        vector = create_vector_store(config)
        cache = create_cache_store(config)

        return cls(
            relational_store=relational,
            vector_store=vector,
            cache_store=cache,
            event_emitter=EventEmitter(),
            config=config,
        )

    # ── High-level Convenience Methods ──────────────────────────
    # Compatible with existing AgentMemoryClient API for smooth migration

    def remember(self, workspace_id: int, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set a memory variable. Compatible with existing SDK API."""
        success = self._relational.set_variable(workspace_id, key, value, ttl=ttl)
        if success:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.VARIABLE_SET,
                workspace_id=workspace_id,
                memory_type="variable",
                memory_id=key,
                data={"key": key, "value": str(value)[:100]},
            ))
        return success

    def recall(self, workspace_id: int, query: str, top_k: int = 5) -> List[Dict]:
        """Recall relevant memories — hybrid search (vector + FTS + lifecycle)."""
        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.RECALL_TRIGGERED,
            workspace_id=workspace_id,
            data={"query": query, "top_k": top_k},
        ))

        # Vector search
        vector_results = self._vector.search(
            "memory_fragments",
            query_text=query,
            n_results=top_k,
            where={"user_id": str(workspace_id)} if self._vector else None,
        )

        # FTS keyword search
        fts_results = self._relational.fts_search(query, limit=top_k)

        # Combine: vector results first (higher quality), then FTS
        results = []
        seen_ids = set()
        for r in vector_results:
            frag_id = r.get("id", "")
            if frag_id not in seen_ids:
                seen_ids.add(frag_id)
                results.append({
                    "type": "vector",
                    "content": r.get("document", ""),
                    "metadata": r.get("metadata", {}),
                    "similarity": r.get("similarity"),
                    "distance": r.get("distance"),
                })

        for r in fts_results:
            frag_id = str(r.get("rowid", ""))
            if frag_id not in seen_ids:
                seen_ids.add(frag_id)
                results.append({
                    "type": "fts",
                    "content": r.get("content", ""),
                    "fragment_type": r.get("fragment_type", ""),
                })

        # Also check variables for exact key match
        var_value = self._relational.get_variable(workspace_id, query)
        if var_value is not None:
            results.insert(0, {
                "type": "variable",
                "key": query,
                "value": var_value,
            })

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.RECALL_COMPLETED,
            workspace_id=workspace_id,
            data={"query": query, "results_count": len(results)},
        ))

        return results[:top_k]

    def forget(self, workspace_id: int, key: str) -> bool:
        """Delete a memory variable."""
        success = self._relational.delete_variable(workspace_id, key)
        if success:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.VARIABLE_DELETED,
                workspace_id=workspace_id,
                memory_type="variable",
                memory_id=key,
            ))
        return success

    def search(self, workspace_id: int, query: str, top_k: int = 5, threshold: float = 0.3) -> List[Dict]:
        """Semantic search across memory fragments."""
        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.SEARCH_TRIGGERED,
            workspace_id=workspace_id,
            data={"query": query, "top_k": top_k, "threshold": threshold},
        ))

        results = self._vector.search(
            "memory_fragments",
            query_text=query,
            n_results=top_k,
            where={"user_id": str(workspace_id)},
        )

        # Filter by threshold
        filtered = [r for r in results if (r.get("similarity") or 0) >= threshold]

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.SEARCH_COMPLETED,
            workspace_id=workspace_id,
            data={"results_count": len(filtered)},
        ))
        return filtered

    def get_context(self, workspace_id: int, session_id: Optional[str] = None) -> Dict:
        """Get assembled context for a workspace — all active memories."""
        variables = self._relational.list_variables(workspace_id)
        fragments = self._relational.list_fragments(workspace_id, lifecycle_status="active")
        tables = self._relational.list_tables(workspace_id)
        entities = self._relational.list_entities(workspace_id)

        return {
            "workspace_id": workspace_id,
            "variables": variables,
            "fragments": fragments,
            "tables": tables,
            "entities": entities,
            "session_id": session_id,
        }

    def remember_fragment(
        self, workspace_id: int, content: str,
        fragment_type: str = "info", ttl: Optional[int] = None,
        importance_score: float = 0.5,
    ) -> int:
        """Create a memory fragment with automatic embedding."""
        fragment_id = self._relational.create_fragment(
            workspace_id=workspace_id,
            fragment_type=fragment_type,
            content=content,
            ttl=ttl,
            importance_score=importance_score,
        )

        # Add to vector store for semantic search
        embedding_id = self._vector.add(
            "memory_fragments",
            doc_id=str(fragment_id),
            text=content,
            metadata={"user_id": str(workspace_id), "fragment_type": fragment_type},
        )

        # Link embedding back to fragment
        self._relational.update_fragment(workspace_id, fragment_id, embedding_id=embedding_id)

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.FRAGMENT_CREATED,
            workspace_id=workspace_id,
            memory_type="fragment",
            memory_id=str(fragment_id),
            data={"fragment_type": fragment_type, "content_preview": content[:100]},
        ))

        return fragment_id

    # ── Event Hooks ─────────────────────────────────────────────

    def on(self, event_type: str, handler: EventHandler) -> None:
        """Register an event handler."""
        self._events.on(event_type, handler)

    def off(self, event_type: str, handler: EventHandler) -> None:
        """Remove an event handler."""
        self._events.off(event_type, handler)

    # ── Direct Store Access (for sub-managers) ──────────────────

    @property
    def relational_store(self) -> RelationalStore:
        return self._relational

    @property
    def vector_store(self) -> VectorStore:
        return self._vector

    @property
    def cache_store(self) -> Optional[CacheStore]:
        return self._cache

    @property
    def event_emitter(self) -> EventEmitter:
        return self._events

    @property
    def config(self) -> CoreConfig:
        return self._config

    # ── Cleanup ─────────────────────────────────────────────────

    def close(self) -> None:
        """Close all store connections."""
        self._relational.close()
        self._vector.close()
        if self._cache:
            self._cache.close()
