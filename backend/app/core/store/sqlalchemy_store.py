"""SQLAlchemy RelationalStore 实现（Phase 1）。

通过 SQLAlchemy 2.0 ORM 实现通用 CRUD，屏蔽底层方言差异，
使 Service 层在 SQLite ↔ PostgreSQL 切换时零代码改动。

约定：
- 以 ORM 模型类为查询单元（如 MemoryFragment / User），不暴露 SQL 字符串。
- 简单过滤使用 `filters` 字典（仅等值匹配）；复杂查询走 session() 逃生舱。
- 所有写操作自动 commit；失败时 rollback 并抛异常给调用方处理。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Dict, Iterator, List, Optional, Type, TypeVar

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.store.base import RelationalStore

logger = logging.getLogger(__name__)

T = TypeVar("T")


@lru_cache(maxsize=1)
def _get_engine() -> Engine:
    """创建全局 Engine（进程内单例，避免连接池重复初始化）。"""
    settings = get_settings()
    url = settings.DATABASE_URL
    kwargs: Dict[str, Any] = {"echo": settings.DB_ECHO, "future": True}

    if url.startswith("sqlite"):
        # SQLite 多线程共享需要 check_same_thread=False；
        # 连接池默认 NullPool 以避免锁竞争（SQLite 单文件写锁）。
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    else:
        # PostgreSQL：pool_size 可控 + pre_ping 防断连（K8s 连接池回收常见）。
        kwargs["pool_size"] = settings.DB_POOL_SIZE
        kwargs["pool_pre_ping"] = True

    engine = create_engine(url, **kwargs)
    logger.info(f"✓ SQLAlchemy Engine 已创建: url={url}")
    return engine


def get_engine() -> Engine:
    """暴露给外部调用（如 Alembic env.py 或测试夹具）。"""
    return _get_engine()


def get_session_factory() -> sessionmaker:
    return sessionmaker(bind=_get_engine(), autoflush=False, expire_on_commit=False)


class SQLAlchemyStore(RelationalStore):
    """基于 SQLAlchemy ORM 的通用关系型存储实现。"""

    def __init__(self, session_factory: Optional[sessionmaker] = None) -> None:
        self._SessionLocal = session_factory or get_session_factory()

    # ---------- 逃生舱：session 上下文 ----------
    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            logger.exception("✗ Store session 事务失败")
            raise
        finally:
            session.close()

    # ---------- CRUD ----------
    def create(self, instance: T) -> T:
        with self.session() as session:
            session.add(instance)
            session.flush()
            session.refresh(instance)
            return instance

    def get(self, model: Type[T], pk: Any) -> Optional[T]:
        with self.session() as session:
            return session.get(model, pk)

    def query(
        self,
        model: Type[T],
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        desc: bool = False,
        limit: Optional[int] = None,
    ) -> List[T]:
        with self.session() as session:
            stmt = select(model)
            if filters:
                for k, v in filters.items():
                    if hasattr(model, k):
                        stmt = stmt.where(getattr(model, k) == v)
                    else:
                        logger.warning(f"query: 模型 {model.__name__} 无字段 {k}，已忽略")
            if order_by and hasattr(model, order_by):
                col = getattr(model, order_by)
                stmt = stmt.order_by(col.desc() if desc else col.asc())
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(session.scalars(stmt).all())

    def update(self, model: Type[T], pk: Any, values: Dict[str, Any]) -> Optional[T]:
        with self.session() as session:
            instance = session.get(model, pk)
            if instance is None:
                return None
            for k, v in values.items():
                if hasattr(instance, k):
                    setattr(instance, k, v)
            session.flush()
            session.refresh(instance)
            return instance

    def delete(self, model: Type[T], pk: Any) -> bool:
        with self.session() as session:
            instance = session.get(model, pk)
            if instance is None:
                return False
            session.delete(instance)
            return True

    def count(self, model: Type[T], filters: Optional[Dict[str, Any]] = None) -> int:
        with self.session() as session:
            stmt = select(func.count()).select_from(model)
            if filters:
                for k, v in filters.items():
                    if hasattr(model, k):
                        stmt = stmt.where(getattr(model, k) == v)
            result = session.scalar(stmt)
            return int(result) if result is not None else 0

    def ping(self) -> bool:
        try:
            with self.session() as session:
                session.execute(text("SELECT 1"))
            return True
        except Exception as e:
            logger.error(f"✗ SQLAlchemyStore.ping 失败: {e}")
            return False


__all__ = ["SQLAlchemyStore", "get_engine", "get_session_factory"]
