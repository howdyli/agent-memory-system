"""
Null store adapters — no-op fallbacks for when a store type is not needed.

Used when config.vector_backend="none" or config.cache_backend="none".
"""


from .base import VectorStore, CacheStore
from typing import Any, Dict, List, Optional


class NullVectorStore(VectorStore):
    """No-op VectorStore — all operations return empty/results."""

    def add(self, collection, doc_id, text, metadata=None, embedding=None) -> str:
        return doc_id

    def search(self, collection, query_text, n_results=5, where=None, query_embedding=None) -> List[Dict]:
        return []

    def get(self, collection, doc_id) -> Optional[Dict]:
        return None

    def update(self, collection, doc_id, text=None, metadata=None, embedding=None) -> bool:
        return True

    def delete(self, collection, doc_id) -> bool:
        return True

    def count(self, collection) -> int:
        return 0

    def clear(self, collection) -> bool:
        return True

    def close(self) -> None:
        pass


class NullCacheStore(CacheStore):
    """No-op CacheStore — all operations return None/True."""

    def set(self, key, value, ttl=None) -> bool:
        return True

    def get(self, key) -> Optional[Any]:
        return None

    def delete(self, key) -> bool:
        return True

    def exists(self, key) -> bool:
        return False

    def expire(self, key, ttl) -> bool:
        return True

    def set_hash(self, name, mapping) -> bool:
        return True

    def get_hash(self, name) -> Optional[Dict]:
        return None

    def close(self) -> None:
        pass
