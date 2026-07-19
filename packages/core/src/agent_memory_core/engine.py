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
from .modules import (
    VariableManager,
    FragmentManager,
    TableManager,
    ObservabilityManager,
    GraphManager,
    LifecycleManager,
    RecallManager,
    LLMBackend,
    ContextCompressor,
    HybridSearchManager,
    SecurityManager,
    ExtractionManager,
    create_backend,
)


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
        llm_backend: Optional[LLMBackend] = None,
        config: Optional[CoreConfig] = None,
    ):
        self._relational = relational_store
        self._vector = vector_store
        self._cache = cache_store
        self._events = event_emitter or EventEmitter()
        self._config = config or CoreConfig()
        self._llm = llm_backend

        # Ensure schema exists
        self._relational.ensure_schema()

        # Initialize all sub-managers
        self._init_managers()

    def _init_managers(self) -> None:
        """Assemble all sub-managers with proper dependency injection."""
        # ── Core managers (no cross-manager dependencies) ─────────

        self._variable_mgr = VariableManager(
            relational_store=self._relational,
            cache_store=self._cache,
            event_emitter=self._events,
        )

        self._fragment_mgr = FragmentManager(
            relational_store=self._relational,
            vector_store=self._vector,
            event_emitter=self._events,
        )

        self._table_mgr = TableManager(
            relational_store=self._relational,
            event_emitter=self._events,
        )

        self._security_mgr = SecurityManager()

        # ── Managers with optional LLM ────────────────────────────

        self._observability_mgr = ObservabilityManager(
            relational_store=self._relational,
            cache_store=self._cache,
            event_emitter=self._events,
            llm_backend=self._llm,
        )

        self._graph_mgr = GraphManager(
            relational_store=self._relational,
            event_emitter=self._events,
            llm_backend=self._llm,
        )

        self._lifecycle_mgr = LifecycleManager(
            relational_store=self._relational,
            event_emitter=self._events,
        )

        # ── Managers with cross-manager dependencies ──────────────

        self._recall_mgr = RecallManager(
            relational_store=self._relational,
            vector_store=self._vector,
            event_emitter=self._events,
            lifecycle_manager=self._lifecycle_mgr,
        )

        self._extraction_mgr = ExtractionManager(
            llm_backend=self._llm,
            variable_manager=self._variable_mgr,
            fragment_manager=self._fragment_mgr,
            event_emitter=self._events,
        )

        self._search_mgr = HybridSearchManager(
            relational_store=self._relational,
            vector_store=self._vector,
            cache_store=self._cache,
            llm_backend=self._llm,
        )

        self._compressor = ContextCompressor(
            relational_store=self._relational,
            vector_store=self._vector,
            variable_manager=self._variable_mgr,
            fragment_manager=self._fragment_mgr,
            recall_manager=self._recall_mgr,
            cache_store=self._cache,
            llm_backend=self._llm,
        )

        self._managers_initialized = True

    @classmethod
    def from_config(cls, config: Optional[CoreConfig] = None) -> "MemoryEngine":
        """Create engine from CoreConfig — auto-creates all store instances."""
        if config is None:
            config = CoreConfig()

        relational = create_relational_store(config)
        vector = create_vector_store(config)
        cache = create_cache_store(config)

        # Create LLM backend from config if available
        llm = None
        if config.llm_provider and config.llm_api_key:
            llm = create_backend({
                "provider": config.llm_provider,
                "api_key": config.llm_api_key,
                "model": config.llm_model,
                "base_url": config.llm_base_url,
            })

        return cls(
            relational_store=relational,
            vector_store=vector,
            cache_store=cache,
            event_emitter=EventEmitter(),
            llm_backend=llm,
            config=config,
        )

    # ── Sub-manager Access ───────────────────────────────────────

    @property
    def variables(self) -> VariableManager:
        """Access VariableManager directly."""
        return self._variable_mgr

    @property
    def fragments(self) -> FragmentManager:
        """Access FragmentManager directly."""
        return self._fragment_mgr

    @property
    def tables(self) -> TableManager:
        """Access TableManager directly."""
        return self._table_mgr

    @property
    def observability(self) -> ObservabilityManager:
        """Access ObservabilityManager directly."""
        return self._observability_mgr

    @property
    def graph(self) -> GraphManager:
        """Access GraphManager directly."""
        return self._graph_mgr

    @property
    def lifecycle(self) -> LifecycleManager:
        """Access LifecycleManager directly."""
        return self._lifecycle_mgr

    @property
    def recall_manager(self) -> RecallManager:
        """Access RecallManager directly."""
        return self._recall_mgr

    @property
    def extraction(self) -> ExtractionManager:
        """Access ExtractionManager directly."""
        return self._extraction_mgr

    @property
    def search_manager(self) -> HybridSearchManager:
        """Access HybridSearchManager directly."""
        return self._search_mgr

    @property
    def compressor(self) -> ContextCompressor:
        """Access ContextCompressor directly."""
        return self._compressor

    @property
    def security(self) -> SecurityManager:
        """Access SecurityManager directly."""
        return self._security_mgr

    # ── High-level Convenience Methods ──────────────────────────
    # Compatible with existing AgentMemoryClient API for smooth migration

    def remember(self, workspace_id: int, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set a memory variable. Delegates to VariableManager."""
        return self._variable_mgr.set(workspace_id, key, value, ttl=ttl)

    def recall(self, workspace_id: int, query: str, top_k: int = 5, budget_tokens: Optional[int] = None) -> Dict:
        """Recall relevant memories — delegates to RecallManager."""
        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.RECALL_TRIGGERED,
            workspace_id=workspace_id,
            data={"query": query, "top_k": top_k},
        ))

        result = self._recall_mgr.recall(
            workspace_id=workspace_id,
            query=query,
            top_k=top_k,
            budget_tokens=budget_tokens,
        )

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.RECALL_COMPLETED,
            workspace_id=workspace_id,
            data={"query": query, "results_count": len(result.memories)},
        ))

        return {
            "memories": result.memories,
            "context_text": result.context_text,
            "total_candidates": result.total_candidates,
            "token_used": result.token_used,
        }

    def forget(self, workspace_id: int, key: str) -> bool:
        """Delete a memory variable. Delegates to VariableManager."""
        return self._variable_mgr.delete(workspace_id, key)

    def search(self, workspace_id: int, query: str, top_k: int = 5, threshold: float = 0.3) -> Dict:
        """Hybrid search across memory fragments. Delegates to HybridSearchManager."""
        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.SEARCH_TRIGGERED,
            workspace_id=workspace_id,
            data={"query": query, "top_k": top_k, "threshold": threshold},
        ))

        result = self._search_mgr.search(
            workspace_id=workspace_id,
            query=query,
            top_k=top_k,
        )

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.SEARCH_COMPLETED,
            workspace_id=workspace_id,
            data={"results_count": result.get("count", 0)},
        ))

        return result

    def get_context(self, workspace_id: int, session_id: Optional[str] = None) -> Dict:
        """Get assembled context for a workspace — all active memories."""
        variables = self._variable_mgr.list(workspace_id)
        fragments = self._fragment_mgr.list(workspace_id, lifecycle_status="active")
        tables = self._table_mgr.list_tables(workspace_id)
        entities = self._graph_mgr.search_entities(workspace_id, query="", limit=100)

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
        """Create a memory fragment with automatic embedding. Delegates to FragmentManager."""
        return self._fragment_mgr.create(
            workspace_id=workspace_id,
            fragment_type=fragment_type,
            content=content,
            ttl=ttl,
            importance_score=importance_score,
        )

    # ── Build Context ────────────────────────────────────────────

    def build_context(
        self,
        workspace_id: int,
        session_id: str,
        user_query: str,
    ) -> str:
        """Build complete injection context. Delegates to ContextCompressor."""
        return self._compressor.build_context(
            workspace_id=workspace_id,
            session_id=session_id,
            user_query=user_query,
        )

    # ── Event Hooks ─────────────────────────────────────────────

    def on(self, event_type: str, handler: EventHandler) -> None:
        """Register an event handler."""
        self._events.on(event_type, handler)

    def off(self, event_type: str, handler: EventHandler) -> None:
        """Remove an event handler."""
        self._events.off(event_type, handler)

    # ── Direct Store Access (for advanced use) ──────────────────

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
