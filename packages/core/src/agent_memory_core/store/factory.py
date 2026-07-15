"""Store factory — create store instances from CoreConfig."""

from .base import RelationalStore, VectorStore, CacheStore
from .sqlite import SQLiteStore


def create_relational_store(config) -> RelationalStore:
    """Create a RelationalStore based on config.database_url.

    Currently supports: sqlite (native), postgresql (via psycopg2).
    """
    db_url = config.database_url
    if db_url.startswith("sqlite"):
        db_path = db_url.replace("sqlite:///", "").replace("sqlite://", "")
        if not db_path:
            db_path = ":memory:"
        store = SQLiteStore(db_path=db_path, echo=config.database_echo)
        store.ensure_schema()
        return store
    elif db_url.startswith("postgresql") or db_url.startswith("postgres"):
        # PostgreSQL adapter will be implemented in Phase 2 (Server layer)
        raise NotImplementedError("PostgreSQL adapter not yet implemented — use SQLite for now")
    elif db_url.startswith("mysql"):
        raise NotImplementedError("MySQL adapter not yet implemented — use SQLite for now")
    else:
        raise ValueError(f"Unsupported database URL scheme: {db_url}")


def create_vector_store(config) -> VectorStore:
    """Create a VectorStore based on config.vector_backend."""
    backend = config.vector_backend
    if backend == "chroma":
        from .chroma import ChromaStore
        return ChromaStore(persist_directory=config.chroma_path)
    elif backend == "milvus":
        from .milvus import MilvusStore
        return MilvusStore(host=config.milvus_host, port=config.milvus_port)
    elif backend == "none":
        from .null import NullVectorStore
        return NullVectorStore()
    else:
        raise ValueError(f"Unsupported vector backend: {backend}")


def create_cache_store(config) -> CacheStore:
    """Create a CacheStore based on config.cache_backend."""
    backend = config.cache_backend
    if backend == "redis":
        from .redis import RedisCacheStore
        return RedisCacheStore(redis_url=config.redis_url)
    elif backend == "fakeredis":
        from .redis import FakeRedisCacheStore
        return FakeRedisCacheStore()
    elif backend == "none":
        from .null import NullCacheStore
        return NullCacheStore()
    else:
        raise ValueError(f"Unsupported cache backend: {backend}")
