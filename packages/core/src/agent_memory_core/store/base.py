"""
Store abstraction layer — ABC interfaces for all data operations.

Three store types:
- RelationalStore: SQLite / PostgreSQL / MySQL — structured data
- VectorStore: ChromaDB / Milvus / Qdrant — similarity search
- CacheStore: Redis / FakeRedis — hot data caching

All methods use workspace_id (not user_id) as the isolation boundary.
user_id is preserved as an audit field where needed.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime


# ─────────────────────────────────────────────────────────────────
# RelationalStore ABC
# ─────────────────────────────────────────────────────────────────

class RelationalStore(ABC):
    """Abstract interface for relational database operations.

    Implementations: SQLiteStore, PostgreSQLStore, MySQLStore.
    All methods are workspace-scoped (workspace_id parameter).
    """

    # ── Schema Management ──────────────────────────────────────
    @abstractmethod
    def ensure_schema(self) -> None:
        """Create/migrate all required tables and indexes."""
        ...

    # ── Variables ──────────────────────────────────────────────
    @abstractmethod
    def set_variable(self, workspace_id: int, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Create or update a memory variable (upsert)."""
        ...

    @abstractmethod
    def get_variable(self, workspace_id: int, key: str) -> Optional[Any]:
        """Get a variable value by key."""
        ...

    @abstractmethod
    def delete_variable(self, workspace_id: int, key: str) -> bool:
        """Delete a variable by key."""
        ...

    @abstractmethod
    def list_variables(self, workspace_id: int, prefix: Optional[str] = None) -> List[Dict]:
        """List all variables, optionally filtered by key prefix."""
        ...

    # ── Fragments ──────────────────────────────────────────────
    @abstractmethod
    def create_fragment(
        self,
        workspace_id: int,
        fragment_type: str,
        content: str,
        embedding_id: Optional[str] = None,
        ttl: Optional[int] = None,
        importance_score: float = 0.5,
        user_id: Optional[int] = None,
    ) -> int:
        """Create a memory fragment. Returns fragment ID."""
        ...

    @abstractmethod
    def get_fragment(self, workspace_id: int, fragment_id: int) -> Optional[Dict]:
        """Get a single fragment by ID."""
        ...

    @abstractmethod
    def list_fragments(
        self,
        workspace_id: int,
        fragment_type: Optional[str] = None,
        lifecycle_status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """List fragments with optional type/status filters."""
        ...

    @abstractmethod
    def update_fragment(
        self,
        workspace_id: int,
        fragment_id: int,
        content: Optional[str] = None,
        importance_score: Optional[float] = None,
        embedding_id: Optional[str] = None,
        lifecycle_status: Optional[str] = None,
    ) -> bool:
        """Update fragment fields."""
        ...

    @abstractmethod
    def delete_fragment(self, workspace_id: int, fragment_id: int) -> bool:
        """Hard-delete a fragment."""
        ...

    @abstractmethod
    def delete_expired_fragments(self, workspace_id: Optional[int] = None) -> int:
        """Remove fragments past their expires_at. Returns count deleted."""
        ...

    # ── Dynamic Tables ─────────────────────────────────────────
    @abstractmethod
    def create_table(self, workspace_id: int, table_name: str, schema: Dict) -> bool:
        """Register a dynamic table definition (upsert)."""
        ...

    @abstractmethod
    def get_table(self, workspace_id: int, table_name: str) -> Optional[Dict]:
        """Get table schema definition."""
        ...

    @abstractmethod
    def list_tables(self, workspace_id: int) -> List[Dict]:
        """List all dynamic table definitions for a workspace."""
        ...

    @abstractmethod
    def delete_table(self, workspace_id: int, table_name: str) -> bool:
        """Delete a dynamic table definition and its data."""
        ...

    @abstractmethod
    def add_record(self, workspace_id: int, table_name: str, record: Dict) -> int:
        """Insert a record into a dynamic table. Returns record ID."""
        ...

    @abstractmethod
    def query_records(
        self,
        workspace_id: int,
        table_name: str,
        filters: Optional[Dict] = None,
        order_by: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """Query records from a dynamic table with filters."""
        ...

    @abstractmethod
    def update_record(self, workspace_id: int, table_name: str, record_id: int, updates: Dict) -> bool:
        """Update a record in a dynamic table."""
        ...

    @abstractmethod
    def delete_record(self, workspace_id: int, table_name: str, record_id: int) -> bool:
        """Delete a record from a dynamic table."""
        ...

    # ── Graph — Entities ───────────────────────────────────────
    @abstractmethod
    def ensure_entity(
        self,
        workspace_id: int,
        name: str,
        entity_type: str,
        aliases: Optional[List[str]] = None,
        metadata: Optional[Dict] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Create or update an entity. Returns entity ID."""
        ...

    @abstractmethod
    def get_entity(self, workspace_id: int, entity_id: int) -> Optional[Dict]:
        """Get entity by ID."""
        ...

    @abstractmethod
    def get_entity_by_name(self, workspace_id: int, name: str, entity_type: Optional[str] = None) -> Optional[Dict]:
        """Find entity by name (and optional type)."""
        ...

    @abstractmethod
    def list_entities(
        self,
        workspace_id: int,
        entity_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """List entities with optional type filter."""
        ...

    @abstractmethod
    def delete_entity(self, workspace_id: int, entity_id: int) -> bool:
        """Delete an entity and its relationships."""
        ...

    # ── Graph — Relationships ──────────────────────────────────
    @abstractmethod
    def add_relationship(
        self,
        workspace_id: int,
        source_entity_id: int,
        target_entity_id: int,
        relation_type: str,
        relation_subtype: Optional[str] = None,
        properties: Optional[Dict] = None,
        confidence: float = 0.5,
        valid_from: Optional[datetime] = None,
        valid_to: Optional[datetime] = None,
        extraction_source: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Create a relationship between two entities. Returns relationship ID."""
        ...

    @abstractmethod
    def get_relationship(self, workspace_id: int, relationship_id: int) -> Optional[Dict]:
        """Get relationship by ID."""
        ...

    @abstractmethod
    def list_relationships(
        self,
        workspace_id: int,
        source_entity_id: Optional[int] = None,
        target_entity_id: Optional[int] = None,
        relation_type: Optional[str] = None,
        is_active: bool = True,
        limit: int = 100,
    ) -> List[Dict]:
        """List relationships with optional filters."""
        ...

    @abstractmethod
    def deactivate_relationship(self, workspace_id: int, relationship_id: int) -> bool:
        """Mark relationship as inactive (soft end)."""
        ...

    # ── Lifecycle ──────────────────────────────────────────────
    @abstractmethod
    def mark_cold(
        self,
        workspace_id: int,
        memory_type: str,
        memory_id: str,
        reason: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Transition memory to cold status. Returns lifecycle record ID."""
        ...

    @abstractmethod
    def mark_active(self, workspace_id: int, memory_type: str, memory_id: str) -> bool:
        """Restore memory from cold/archived back to active."""
        ...

    @abstractmethod
    def soft_delete(
        self,
        workspace_id: int,
        memory_type: str,
        memory_id: str,
        reason: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> bool:
        """Soft-delete memory (mark as deleted, preserve for audit)."""
        ...

    @abstractmethod
    def get_lifecycle_status(self, workspace_id: int, memory_type: str, memory_id: str) -> Optional[Dict]:
        """Get current lifecycle status for a memory item."""
        ...

    @abstractmethod
    def list_lifecycle_memories(
        self,
        workspace_id: int,
        lifecycle_status: Optional[str] = None,
        memory_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict]:
        """List memories by lifecycle status."""
        ...

    # ── Observability ──────────────────────────────────────────
    @abstractmethod
    def log_trace_event(
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
        user_id: Optional[int] = None,
    ) -> int:
        """Insert a trace event record."""
        ...

    @abstractmethod
    def create_metrics_snapshot(self, workspace_id: int, snapshot: Dict, user_id: Optional[int] = None) -> int:
        """Create a metrics snapshot."""
        ...

    @abstractmethod
    def log_quality_evaluation(
        self,
        workspace_id: int,
        memory_id: str,
        memory_type: str,
        evaluation_type: str,
        score: float,
        evaluator: str,
        details: Optional[Dict] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Log a quality evaluation."""
        ...

    @abstractmethod
    def log_extraction_trigger(
        self,
        workspace_id: int,
        trigger_type: str,
        session_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
        query_snippet: Optional[str] = None,
        fragments_created: int = 0,
        llm_tokens_used: int = 0,
        user_id: Optional[int] = None,
    ) -> int:
        """Log an extraction trigger event."""
        ...

    @abstractmethod
    def query_trace_events(
        self,
        workspace_id: int,
        event_type: Optional[str] = None,
        memory_type: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
    ) -> List[Dict]:
        """Query trace events with filters."""
        ...

    # ── Extraction Feedback ────────────────────────────────────
    @abstractmethod
    def log_extraction_feedback(
        self,
        workspace_id: int,
        extraction_id: str,
        rating: str,
        correction: Optional[str] = None,
        source_text: Optional[str] = None,
        extracted_data: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Log extraction feedback from user."""
        ...

    @abstractmethod
    def set_extraction_prompt_template(
        self,
        workspace_id: int,
        name: str,
        content: str,
        is_active: bool = False,
        user_id: Optional[int] = None,
    ) -> int:
        """Create or update an extraction prompt template."""
        ...

    @abstractmethod
    def get_extraction_prompt_template(self, workspace_id: int, name: str) -> Optional[Dict]:
        """Get an extraction prompt template by name."""
        ...

    # ── FTS (Full-Text Search) ─────────────────────────────────
    @abstractmethod
    def fts_search(self, query_text: str, limit: int = 20) -> List[Dict]:
        """Full-text search across fragments content."""
        ...

    # ── Performance Metrics ────────────────────────────────────
    @abstractmethod
    def log_performance_metric(
        self,
        workspace_id: int,
        metric_type: str,
        endpoint: Optional[str] = None,
        value: Optional[float] = None,
        metadata: Optional[Dict] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Log a performance metric."""
        ...

    # ── Merge Log ──────────────────────────────────────────────
    @abstractmethod
    def log_merge(
        self,
        workspace_id: int,
        memory_type: str,
        source_ids: str,
        target_id: Optional[str] = None,
        merge_type: str = "",
        merge_action: str = "",
        similarity_score: Optional[float] = None,
        old_value: Optional[str] = None,
        new_value: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Log a merge operation."""
        ...

    # ── Generic SQL (for dynamic tables) ───────────────────────
    @abstractmethod
    def execute_sql(self, sql: str, params: tuple = ()) -> Any:
        """Execute raw SQL — used ONLY by TableManager for dynamic table operations.
        Must go through security_service validation first."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close all connections."""
        ...


# ─────────────────────────────────────────────────────────────────
# VectorStore ABC
# ─────────────────────────────────────────────────────────────────

class VectorStore(ABC):
    """Abstract interface for vector similarity search.

    Implementations: ChromaStore, MilvusStore.
    """

    @abstractmethod
    def add(
        self,
        collection: str,
        doc_id: str,
        text: str,
        metadata: Optional[Dict] = None,
        embedding: Optional[List[float]] = None,
    ) -> str:
        """Add a document with optional precomputed embedding.
        Returns doc_id."""
        ...

    @abstractmethod
    def search(
        self,
        collection: str,
        query_text: str,
        n_results: int = 5,
        where: Optional[Dict] = None,
        query_embedding: Optional[List[float]] = None,
    ) -> List[Dict]:
        """Search similar documents by text or embedding.
        Each result: {id, document, metadata, distance, similarity}."""
        ...

    @abstractmethod
    def get(self, collection: str, doc_id: str) -> Optional[Dict]:
        """Get a document by ID. Returns {id, document, metadata, embedding}."""
        ...

    @abstractmethod
    def update(
        self,
        collection: str,
        doc_id: str,
        text: Optional[str] = None,
        metadata: Optional[Dict] = None,
        embedding: Optional[List[float]] = None,
    ) -> bool:
        """Update a document's text, metadata, or embedding."""
        ...

    @abstractmethod
    def delete(self, collection: str, doc_id: str) -> bool:
        """Delete a document by ID."""
        ...

    @abstractmethod
    def count(self, collection: str) -> int:
        """Count documents in a collection."""
        ...

    @abstractmethod
    def clear(self, collection: str) -> bool:
        """Clear all documents from a collection."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close the vector store connection."""
        ...


# ─────────────────────────────────────────────────────────────────
# CacheStore ABC
# ─────────────────────────────────────────────────────────────────

class CacheStore(ABC):
    """Abstract interface for hot data caching.

    Implementations: RedisCacheStore, FakeRedisCacheStore, DictCacheStore.
    """

    @abstractmethod
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """Set a cache value with optional TTL in seconds."""
        ...

    @abstractmethod
    def get(self, key: str) -> Optional[Any]:
        """Get a cache value by key."""
        ...

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Delete a cache key."""
        ...

    @abstractmethod
    def exists(self, key: str) -> bool:
        """Check if a key exists."""
        ...

    @abstractmethod
    def expire(self, key: str, ttl: int) -> bool:
        """Set TTL on an existing key."""
        ...

    @abstractmethod
    def set_hash(self, name: str, mapping: Dict) -> bool:
        """Set a hash map."""
        ...

    @abstractmethod
    def get_hash(self, name: str) -> Optional[Dict]:
        """Get all fields of a hash."""
        ...

    @abstractmethod
    def close(self) -> None:
        """Close cache connection."""
        ...
