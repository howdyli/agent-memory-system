"""
ChromaDB vector store adapter.

Implements VectorStore ABC using ChromaDB PersistentClient.
Adapted from backend/app/core/chromadb_client.py — singleton removed.

Usage:
    store = ChromaStore(persist_directory="./chromadb_data")
    doc_id = store.add("memory_fragments", doc_id="abc", text="hello world")
    results = store.search("memory_fragments", query_text="hello")
"""

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .base import VectorStore

logger = logging.getLogger(__name__)


class ChromaStore(VectorStore):
    """ChromaDB implementation of VectorStore.

    Uses PersistentClient for local storage with cosine similarity.
    Embedding function defaults to ChromaDB's built-in (Sentence Transformers).
    """

    def __init__(self, collection_name: str = "memory_fragments", persist_directory: str = "./chromadb_data"):
        self.collection_name = collection_name
        self.persist_directory = persist_directory
        self.client = None
        self.collection = None
        self._initialize()

    def _initialize(self):
        try:
            import chromadb
            self.client = chromadb.PersistentClient(path=self.persist_directory)
            try:
                self.collection = self.client.get_collection(name=self.collection_name)
                logger.info(f"Connected to existing ChromaDB collection: {self.collection_name}")
            except Exception:
                self.collection = self.client.create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info(f"Created new ChromaDB collection: {self.collection_name}")
        except ImportError:
            logger.error("ChromaDB not installed. Install with: pip install chromadb")
            raise

    def add(
        self, collection: str, doc_id: str, text: str,
        metadata: Optional[Dict] = None, embedding: Optional[List[float]] = None,
    ) -> str:
        if metadata is None:
            metadata = {}
        metadata["text"] = text[:500]
        metadata["created_at"] = datetime.now().isoformat()

        # Use the specified collection (not default)
        col = self._get_or_create_collection(collection)

        if embedding:
            col.add(embeddings=[embedding], documents=[text], metadatas=[metadata], ids=[doc_id])
        else:
            col.add(documents=[text], metadatas=[metadata], ids=[doc_id])
        return doc_id

    def search(
        self, collection: str, query_text: str,
        n_results: int = 5, where: Optional[Dict] = None,
        query_embedding: Optional[List[float]] = None,
    ) -> List[Dict]:
        col = self._get_or_create_collection(collection)
        if query_embedding:
            results = col.query(query_embeddings=[query_embedding], n_results=n_results, where=where or None)
        elif query_text:
            results = col.query(query_texts=[query_text], n_results=n_results, where=where or None)
        else:
            return []

        formatted = []
        if results and results.get("ids"):
            for i in range(len(results["ids"][0])):
                formatted.append({
                    "id": results["ids"][0][i],
                    "document": results["documents"][0][i],
                    "metadata": results["metadatas"][0][i],
                    "distance": results["distances"][0][i] if "distances" in results else None,
                    "similarity": 1 - results["distances"][0][i] if "distances" in results else None,
                })
        return formatted

    def get(self, collection: str, doc_id: str) -> Optional[Dict]:
        col = self._get_or_create_collection(collection)
        result = col.get(ids=[doc_id], include=["documents", "metadatas", "embeddings"])
        if result and result.get("ids") and len(result["ids"]) > 0:
            embeddings = result.get("embeddings")
            return {
                "id": result["ids"][0],
                "document": result["documents"][0] if result.get("documents") else None,
                "metadata": result["metadatas"][0] if result.get("metadatas") else {},
                "embedding": embeddings[0] if embeddings and len(embeddings) > 0 else None,
            }
        return None

    def update(
        self, collection: str, doc_id: str,
        text: Optional[str] = None, metadata: Optional[Dict] = None,
        embedding: Optional[List[float]] = None,
    ) -> bool:
        existing = self.get(collection, doc_id)
        if not existing:
            return False

        updated_meta = existing["metadata"].copy()
        if metadata:
            updated_meta.update(metadata)
        if text:
            updated_meta["text"] = text[:500]

        # ChromaDB update: delete old + add new
        col = self._get_or_create_collection(collection)
        col.delete(ids=[doc_id])
        if embedding:
            col.add(embeddings=[embedding], documents=[text or existing["document"]], metadatas=[updated_meta], ids=[doc_id])
        else:
            col.add(documents=[text or existing["document"]], metadatas=[updated_meta], ids=[doc_id])
        return True

    def delete(self, collection: str, doc_id: str) -> bool:
        col = self._get_or_create_collection(collection)
        col.delete(ids=[doc_id])
        return True

    def count(self, collection: str) -> int:
        col = self._get_or_create_collection(collection)
        return col.count()

    def clear(self, collection: str) -> bool:
        col = self._get_or_create_collection(collection)
        all_docs = col.get(include=[])
        if all_docs and all_docs.get("ids"):
            col.delete(ids=all_docs["ids"])
        return True

    def close(self) -> None:
        logger.info("ChromaStore closed (data auto-persisted)")

    def _get_or_create_collection(self, name: str):
        """Get or create a named collection."""
        if name == self.collection_name:
            return self.collection
        try:
            return self.client.get_collection(name=name)
        except Exception:
            return self.client.create_collection(name=name, metadata={"hnsw:space": "cosine"})
