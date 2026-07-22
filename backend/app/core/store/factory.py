"""Store 工厂 + 包入口（Phase 1）。

按配置 / 参数创建存储后端，Service 层只需：
    from app.core.store import get_relational_store, get_vector_store

后续扩展 PostgreSQL / Milvus 等后端只需在 _RELATIONAL_MAP 和 _VECTOR_MAP 中
注册新实现，不改工厂逻辑。
"""
from __future__ import annotations

import logging
from typing import Callable, Dict, Optional

from app.core.config import get_settings
from app.core.store.base import RelationalStore, VectorStore
from app.core.store.sqlalchemy_store import SQLAlchemyStore
from app.core.store.vector_chroma import ChromaVectorStore

logger = logging.getLogger(__name__)


# 关系型存储后端映射（key 对应 DATABASE_URL 方言或显式配置）
_RELATIONAL_MAP: Dict[str, Callable[[], RelationalStore]] = {
    "sqlalchemy": SQLAlchemyStore,
    "sqlite": SQLAlchemyStore,
    "postgresql": SQLAlchemyStore,
    "postgres": SQLAlchemyStore,
}

# 向量存储后端映射（key 对应 VECTOR_BACKEND 配置）
_VECTOR_MAP: Dict[str, Callable[[], VectorStore]] = {
    "chroma": ChromaVectorStore,
}


def get_relational_store(name: Optional[str] = None) -> RelationalStore:
    """返回关系型存储实例。

    Args:
        name: 后端名（默认按 DATABASE_URL 自动推断）。
    """
    if name is None:
        url = get_settings().DATABASE_URL
        if url.startswith("sqlite"):
            name = "sqlite"
        elif url.startswith(("postgres", "psycopg")):
            name = "postgresql"
        else:
            name = "sqlalchemy"

    factory = _RELATIONAL_MAP.get(name.lower())
    if factory is None:
        raise ValueError(f"未知的关系型存储后端: {name}，可选: {list(_RELATIONAL_MAP)}")
    logger.info(f"✓ 实例化 RelationalStore 后端: {name}")
    return factory()


def get_vector_store(name: Optional[str] = None) -> VectorStore:
    """返回向量存储实例。

    Args:
        name: 后端名（默认按 VECTOR_BACKEND 配置）。
    """
    settings = get_settings()
    if name is None:
        name = settings.VECTOR_BACKEND

    if name.lower() == "chroma":
        logger.info(f"✓ 实例化 VectorStore 后端: chroma (dir={settings.CHROMA_PERSIST_DIR})")
        return ChromaVectorStore(persist_directory=settings.CHROMA_PERSIST_DIR)

    factory = _VECTOR_MAP.get(name.lower())
    if factory is None:
        raise ValueError(f"未知的向量存储后端: {name}，可选: {list(_VECTOR_MAP)}")
    logger.info(f"✓ 实例化 VectorStore 后端: {name}")
    return factory()


__all__ = [
    "get_relational_store",
    "get_vector_store",
]
