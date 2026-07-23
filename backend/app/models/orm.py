"""
SQLAlchemy ORM 模型（Phase 1：存储可插拔）

将 db_client.py 中的核心静态表转为 SQLAlchemy 2.0 声明式模型，
作为 Alembic 迁移与 Store 抽象层的 schema 事实来源（source of truth）。

设计原则：
- 使用跨方言通用类型（Integer/Text/Float/DateTime），兼容 SQLite 与 PostgreSQL。
- 时间戳默认值用 server_default=func.now()，两种方言均可映射。
- 不包含运行时动态表（memory_{user_id}_{name}）与 FTS5 虚拟表——它们由服务层按需创建。
- 与 db_client.py 并存，Service 层双写过渡，不破坏现有逻辑。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


# ============================================================
# 基础表
# ============================================================
class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    email: Mapped[Optional[str]] = mapped_column(Text, unique=True)
    password_hash: Mapped[Optional[str]] = mapped_column(Text)
    # Phase 2: 多租户 —— 当前激活的 workspace（兼容层：旧调用路径仍按 user_id 隔离）
    default_workspace_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("workspaces.id", use_alter=True)
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class MemoryVariable(Base):
    __tablename__ = "memory_variables"
    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_memory_variables_user_key"),
        Index("idx_memory_variables_user", "user_id"),
        Index("idx_memory_variables_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class MemoryTable(Base):
    __tablename__ = "memory_tables"
    __table_args__ = (
        UniqueConstraint("user_id", "table_name", name="uq_memory_tables_user_name"),
        Index("idx_memory_tables_user", "user_id"),
        Index("idx_memory_tables_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    table_name: Mapped[str] = mapped_column(Text, nullable=False)
    table_schema: Mapped[str] = mapped_column(Text, nullable=False)  # JSON 字符串
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class MemoryFragment(Base):
    __tablename__ = "memory_fragments"
    __table_args__ = (
        Index("idx_memory_fragments_user", "user_id"),
        Index("idx_memory_fragments_expires", "expires_at"),
        Index("idx_memory_fragments_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    fragment_type: Mapped[str] = mapped_column(Text, nullable=False)  # info, preference, plan
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_id: Mapped[Optional[str]] = mapped_column(Text)
    ttl: Mapped[Optional[int]] = mapped_column(Integer)
    importance_score: Mapped[Optional[float]] = mapped_column(Float, default=0.5)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())
    expires_at: Mapped[Optional[datetime]] = mapped_column()
    # 生命周期兼容字段（db_client 通过 ALTER TABLE 追加）
    lifecycle_status: Mapped[Optional[str]] = mapped_column(Text, default="active")
    last_recalled_at: Mapped[Optional[datetime]] = mapped_column()
    cold_at: Mapped[Optional[datetime]] = mapped_column()


# ============================================================
# Memory Lifecycle 生命周期管理
# ============================================================
class MemoryLifecycle(Base):
    __tablename__ = "memory_lifecycle"
    __table_args__ = (
        Index("idx_lifecycle_user_status", "user_id", "lifecycle_status"),
        Index("idx_lifecycle_status", "lifecycle_status"),
        Index("idx_lifecycle_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    memory_type: Mapped[str] = mapped_column(Text, nullable=False)
    memory_id: Mapped[str] = mapped_column(Text, nullable=False)
    lifecycle_status: Mapped[Optional[str]] = mapped_column(Text, default="active")
    cold_reason: Mapped[Optional[str]] = mapped_column(Text)
    cold_at: Mapped[Optional[datetime]] = mapped_column()
    last_recalled_at: Mapped[Optional[datetime]] = mapped_column()
    archived_at: Mapped[Optional[datetime]] = mapped_column()
    soft_deleted_at: Mapped[Optional[datetime]] = mapped_column()
    restore_count: Mapped[Optional[int]] = mapped_column(Integer, default=0)


class MemoryDeleteLog(Base):
    __tablename__ = "memory_delete_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    memory_type: Mapped[str] = mapped_column(Text, nullable=False)
    memory_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    old_content: Mapped[Optional[str]] = mapped_column(Text)
    operator: Mapped[Optional[str]] = mapped_column(Text, default="user")
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class MemoryMergeLog(Base):
    __tablename__ = "memory_merge_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    memory_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_ids: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[Optional[str]] = mapped_column(Text)
    merge_type: Mapped[str] = mapped_column(Text, nullable=False)
    merge_action: Mapped[str] = mapped_column(Text, nullable=False)
    similarity_score: Mapped[Optional[float]] = mapped_column(Float)
    old_value: Mapped[Optional[str]] = mapped_column(Text)
    new_value: Mapped[Optional[str]] = mapped_column(Text)
    resolved: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())
    resolved_at: Mapped[Optional[datetime]] = mapped_column()


# ============================================================
# Graph Memory 知识图谱
# ============================================================
class GraphEntity(Base):
    __tablename__ = "graph_entities"
    __table_args__ = (
        UniqueConstraint("user_id", "name", "entity_type", name="uq_graph_entities"),
        Index("idx_graph_entities_user", "user_id", "entity_type"),
        Index("idx_graph_entities_name", "user_id", "name"),
        Index("idx_graph_entities_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[Optional[str]] = mapped_column(Text)
    entity_metadata: Mapped[Optional[str]] = mapped_column("metadata", Text)
    first_seen_at: Mapped[Optional[datetime]] = mapped_column()
    last_seen_at: Mapped[Optional[datetime]] = mapped_column()
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class GraphRelationship(Base):
    __tablename__ = "graph_relationships"
    __table_args__ = (
        Index("idx_graph_rels_source", "source_entity_id"),
        Index("idx_graph_rels_target", "target_entity_id"),
        Index("idx_graph_rels_active", "user_id", "relation_type", "is_active"),
        Index("idx_graph_rels_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    source_entity_id: Mapped[int] = mapped_column(
        ForeignKey("graph_entities.id"), nullable=False
    )
    target_entity_id: Mapped[int] = mapped_column(
        ForeignKey("graph_entities.id"), nullable=False
    )
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    relation_subtype: Mapped[Optional[str]] = mapped_column(Text)
    properties: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[float]] = mapped_column(Float, default=0.5)
    valid_from: Mapped[Optional[datetime]] = mapped_column()
    valid_to: Mapped[Optional[datetime]] = mapped_column()
    observed_at: Mapped[Optional[datetime]] = mapped_column()
    expired_at: Mapped[Optional[datetime]] = mapped_column()
    is_active: Mapped[Optional[int]] = mapped_column(Integer, default=1)
    extraction_source: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class GraphRelationshipHistory(Base):
    __tablename__ = "graph_relationship_history"
    __table_args__ = (
        Index("idx_graph_history_rel", "relationship_id"),
        Index("idx_graph_history_entity", "source_entity_id", "target_entity_id"),
        Index("idx_graph_history_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    relationship_id: Mapped[Optional[int]] = mapped_column(Integer)
    source_entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    target_entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    old_properties: Mapped[Optional[str]] = mapped_column(Text)
    new_properties: Mapped[Optional[str]] = mapped_column(Text)
    valid_from: Mapped[Optional[datetime]] = mapped_column()
    valid_to: Mapped[Optional[datetime]] = mapped_column()
    observed_at: Mapped[Optional[datetime]] = mapped_column()
    expired_at: Mapped[Optional[datetime]] = mapped_column()
    change_reason: Mapped[Optional[str]] = mapped_column(Text)
    changed_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


# ============================================================
# Memory Observability 观测性
# ============================================================
class MemoryTraceEvent(Base):
    __tablename__ = "memory_trace_events"
    __table_args__ = (
        Index("idx_trace_user", "user_id", "created_at"),
        Index("idx_trace_memory", "memory_id", "event_type"),
        Index("idx_trace_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    memory_id: Mapped[Optional[str]] = mapped_column(Text)
    memory_type: Mapped[Optional[str]] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    event_source: Mapped[Optional[str]] = mapped_column(Text)
    conversation_id: Mapped[Optional[str]] = mapped_column(Text)
    session_id: Mapped[Optional[str]] = mapped_column(Text)
    score: Mapped[Optional[float]] = mapped_column(Float)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float)
    event_metadata: Mapped[Optional[str]] = mapped_column("metadata", Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class MemoryMetricsSnapshot(Base):
    __tablename__ = "memory_metrics_snapshots"
    __table_args__ = (
        Index("idx_snapshot_user", "user_id", "snapshot_time"),
        Index("idx_snapshot_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    snapshot_time: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())
    total_memories: Mapped[Optional[int]] = mapped_column(Integer)
    active_memories: Mapped[Optional[int]] = mapped_column(Integer)
    total_storage_bytes: Mapped[Optional[int]] = mapped_column(Integer)
    daily_new_count: Mapped[Optional[int]] = mapped_column(Integer)
    daily_recall_count: Mapped[Optional[int]] = mapped_column(Integer)
    daily_recall_hit_count: Mapped[Optional[int]] = mapped_column(Integer)
    avg_recall_latency_ms: Mapped[Optional[float]] = mapped_column(Float)
    p50_recall_latency_ms: Mapped[Optional[float]] = mapped_column(Float)
    p99_recall_latency_ms: Mapped[Optional[float]] = mapped_column(Float)
    llm_extraction_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    llm_rerank_tokens: Mapped[Optional[int]] = mapped_column(Integer)


class MemoryQualityEvaluation(Base):
    __tablename__ = "memory_quality_evaluations"
    __table_args__ = (
        Index("idx_quality_memory", "user_id", "memory_id"),
        Index("idx_quality_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    memory_id: Mapped[str] = mapped_column(Text, nullable=False)
    memory_type: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_type: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    evaluator: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class MemoryExtractionTrigger(Base):
    __tablename__ = "memory_extraction_triggers"
    __table_args__ = (
        Index("idx_extraction_user", "user_id", "created_at"),
        Index("idx_extraction_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    session_id: Mapped[Optional[str]] = mapped_column(Text)
    conversation_id: Mapped[Optional[str]] = mapped_column(Text)
    trigger_type: Mapped[str] = mapped_column(Text, nullable=False)
    query_snippet: Mapped[Optional[str]] = mapped_column(Text)
    fragments_created: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    llm_tokens_used: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


# ============================================================
# Performance Metrics 性能指标
# ============================================================
class PerformanceMetric(Base):
    __tablename__ = "performance_metrics"
    __table_args__ = (
        Index(
            "idx_perf_metrics_user_type_time",
            "user_id",
            "metric_type",
            "created_at",
        ),
        Index("idx_perf_metrics_user_time", "user_id", "created_at"),
        Index("idx_perf_metrics_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    metric_type: Mapped[str] = mapped_column(Text, nullable=False)
    endpoint: Mapped[Optional[str]] = mapped_column(Text)
    value: Mapped[Optional[float]] = mapped_column(Float)
    metric_metadata: Mapped[Optional[str]] = mapped_column("metadata", Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


# ============================================================
# Extraction Feedback & Prompt Templates
# ============================================================
class ExtractionFeedback(Base):
    __tablename__ = "extraction_feedback"
    __table_args__ = (
        Index("idx_extraction_feedback_user", "user_id", "created_at"),
        Index("idx_extraction_feedback_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    extraction_id: Mapped[str] = mapped_column(Text, nullable=False)
    rating: Mapped[str] = mapped_column(Text, nullable=False)
    correction: Mapped[Optional[str]] = mapped_column(Text)
    source_text: Mapped[Optional[str]] = mapped_column(Text)
    extracted_data: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class ExtractionPromptTemplate(Base):
    __tablename__ = "extraction_prompt_templates"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_extraction_templates_user_name"),
        Index("idx_extraction_templates_user", "user_id", "is_active"),
        Index("idx_extraction_templates_workspace", "workspace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[Optional[int]] = mapped_column(ForeignKey("workspaces.id"))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[Optional[int]] = mapped_column(Integer, default=0)
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())
    updated_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


# ============================================================
# Phase 2: 多租户 —— Organization / Workspace / WorkspaceMember / ApiKey
# ============================================================

class Organization(Base):
    """组织（顶层租户容器）。个人用户默认属于一个 personal org，团队场景可扩展为多 org。"""
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    plan: Mapped[Optional[str]] = mapped_column(Text, default="free")  # free / pro / enterprise
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class Workspace(Base):
    """工作空间：记忆数据的隔离单元。kind=personal 为个人默认空间，team 为协作空间。"""
    __tablename__ = "workspaces"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_workspaces_slug"),
        Index("idx_workspaces_org", "org_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="personal")  # personal / team
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class WorkspaceMember(Base):
    """工作空间成员关系。role: owner / admin / member / viewer。"""
    __tablename__ = "workspace_members"
    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members"),
        Index("idx_workspace_members_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="member")
    joined_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


class ApiKey(Base):
    """API Key：用于机器对机器认证（SDK / 第三方集成）。明文仅生成一次，存储 SHA-256 哈希。"""
    __tablename__ = "api_keys"
    __table_args__ = (
        Index("idx_api_keys_workspace", "workspace_id"),
        Index("idx_api_keys_key_hash", "key_hash", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), nullable=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    key_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scopes: Mapped[Optional[str]] = mapped_column(Text)  # JSON 数组，如 ["memory:read","memory:write"]
    last_used_at: Mapped[Optional[datetime]] = mapped_column()
    expires_at: Mapped[Optional[datetime]] = mapped_column()
    revoked_at: Mapped[Optional[datetime]] = mapped_column()
    created_at: Mapped[Optional[datetime]] = mapped_column(server_default=func.now())


# 供 Alembic env.py 与 Store 层引用的元数据对象
metadata = Base.metadata

__all__ = [
    "Base",
    "metadata",
    "User",
    "MemoryVariable",
    "MemoryTable",
    "MemoryFragment",
    "MemoryLifecycle",
    "MemoryDeleteLog",
    "MemoryMergeLog",
    "GraphEntity",
    "GraphRelationship",
    "GraphRelationshipHistory",
    "MemoryTraceEvent",
    "MemoryMetricsSnapshot",
    "MemoryQualityEvaluation",
    "MemoryExtractionTrigger",
    "PerformanceMetric",
    "ExtractionFeedback",
    "ExtractionPromptTemplate",
    # Phase 2 新增
    "Organization",
    "Workspace",
    "WorkspaceMember",
    "ApiKey",
]
