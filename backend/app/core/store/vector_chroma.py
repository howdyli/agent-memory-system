"""ChromaDB VectorStore 实现（Phase 1）。

薄封装现有 app.core.chromadb_client，使其符合 VectorStore 抽象接口，
后续切换到 Milvus / Qdrant 时只需新增实现，不动 Service 层。

容错策略：
- 构造时若 ChromaDB 不可用（数据损坏/未安装），ping() 恒返回 False。
- 所有写/查/删操作在内部 try/except 捕获异常并返回空结果，
  不抛出到 Service 层，保持与现有 chromadb_client 的容错语义一致。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core.chromadb_client import ChromaDBClient
from app.core.store.base import VectorStore

logger = logging.getLogger(__name__)


class ChromaVectorStore(VectorStore):
    """基于 ChromaDB 的向量存储实现。"""

    def __init__(
        self,
        collection_name: str = "memory_fragments",
        persist_directory: str = "./chromadb_data",
    ) -> None:
        self._client: Optional[ChromaDBClient] = None
        try:
            self._client = ChromaDBClient(
                collection_name=collection_name,
                persist_directory=persist_directory,
            )
        except BaseException as e:  # ChromaDB 初始化失败可能抛 BaseException
            logger.error(f"✗ ChromaVectorStore 初始化失败: {e}")

    # ---------- VectorStore 接口实现 ----------
    def add(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> Optional[str]:
        if not self._client:
            return None
        try:
            return self._client.add_embedding(text=text, metadata=metadata, embedding=embedding)
        except Exception as e:
            logger.error(f"✗ ChromaVectorStore.add 失败: {e}")
            return None

    def search(
        self,
        query_text: str,
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        if not self._client:
            return []
        return self._client.search_embeddings(
            query_text=query_text, n_results=n_results, where=where
        )

    def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        if not self._client:
            return None
        return self._client.get_by_id(doc_id)

    def update(
        self,
        doc_id: str,
        text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> bool:
        if not self._client:
            return False
        return self._client.update_by_id(
            doc_id=doc_id, text=text, metadata=metadata, embedding=embedding
        )

    def delete(self, doc_id: str) -> bool:
        if not self._client:
            return False
        return self._client.delete_by_id(doc_id)

    def count(self) -> int:
        if not self._client:
            return 0
        return self._client.count()

    def ping(self) -> bool:
        return self._client is not None


__all__ = ["ChromaVectorStore"]
