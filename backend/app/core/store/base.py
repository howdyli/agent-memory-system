"""Store 抽象接口（Phase 1：存储可插拔）。

定义关系型存储与向量存储的统一抽象，使 Service 层不再直接耦合
具体的 SQLite/ChromaDB 实现，为后续切换 PostgreSQL / Milvus / Qdrant 铺路。

设计要点：
- RelationalStore 以 ORM 模型类为操作单元，提供通用 CRUD + session 逃生舱。
- VectorStore 对齐现有 chromadb_client 的能力，屏蔽后端差异。
- 两者均提供 ping()，供 readiness 健康检查复用。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Iterator, List, Optional, Type, TypeVar

T = TypeVar("T")


class RelationalStore(ABC):
    """关系型存储抽象接口（基于 SQLAlchemy ORM 模型）。"""

    @abstractmethod
    def session(self) -> Iterator[Any]:
        """返回一个 SQLAlchemy Session 上下文管理器（自动 commit / rollback）。

        实现方应用 @contextmanager 修饰；用于抽象接口无法覆盖的
        复杂查询（join、聚合、原生 SQL 等）。
        """
        raise NotImplementedError

    @abstractmethod
    def create(self, instance: T) -> T:
        """插入一条 ORM 实例并返回（已刷新主键）。"""
        raise NotImplementedError

    @abstractmethod
    def get(self, model: Type[T], pk: Any) -> Optional[T]:
        """按主键获取实例，不存在返回 None。"""
        raise NotImplementedError

    @abstractmethod
    def query(
        self,
        model: Type[T],
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        desc: bool = False,
        limit: Optional[int] = None,
    ) -> List[T]:
        """按等值过滤条件查询实例列表。"""
        raise NotImplementedError

    @abstractmethod
    def update(self, model: Type[T], pk: Any, values: Dict[str, Any]) -> Optional[T]:
        """按主键更新字段，返回更新后的实例（不存在返回 None）。"""
        raise NotImplementedError

    @abstractmethod
    def delete(self, model: Type[T], pk: Any) -> bool:
        """按主键删除，返回是否删除成功。"""
        raise NotImplementedError

    @abstractmethod
    def count(self, model: Type[T], filters: Optional[Dict[str, Any]] = None) -> int:
        """按等值过滤条件统计行数。"""
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> bool:
        """健康检查：连接可用返回 True。"""
        raise NotImplementedError


class VectorStore(ABC):
    """向量存储抽象接口（对齐 chromadb_client 能力）。"""

    @abstractmethod
    def add(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> Optional[str]:
        """写入文本 + 元数据（可选预计算向量），返回文档 ID。"""
        raise NotImplementedError

    @abstractmethod
    def search(
        self,
        query_text: str,
        n_results: int = 5,
        where: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """相似度检索，返回 [{id, document, metadata, distance, similarity}]。"""
        raise NotImplementedError

    @abstractmethod
    def get(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """按 ID 获取文档。"""
        raise NotImplementedError

    @abstractmethod
    def update(
        self,
        doc_id: str,
        text: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        embedding: Optional[List[float]] = None,
    ) -> bool:
        """按 ID 更新文档。"""
        raise NotImplementedError

    @abstractmethod
    def delete(self, doc_id: str) -> bool:
        """按 ID 删除文档。"""
        raise NotImplementedError

    @abstractmethod
    def count(self) -> int:
        """返回文档数量。"""
        raise NotImplementedError

    @abstractmethod
    def ping(self) -> bool:
        """健康检查：后端可用返回 True。"""
        raise NotImplementedError


__all__ = ["RelationalStore", "VectorStore"]
