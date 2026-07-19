"""Phase 2: 多租户与权限 —— 新增 workspace 体系

1. 新增 4 张表：organizations / workspaces / workspace_members / api_keys
2. 为 17 张核心记忆表新增 workspace_id 列（users 表为 default_workspace_id）
3. Backfill：为每个现有 user 创建 personal workspace，回填 workspace_id

Revision ID: 002_add_workspace
Revises: 001_initial_schema
Create Date: 2026-07-16

Downgrade 提供完整回滚（删除新表 + 删除新列）。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "002_add_workspace"
down_revision: Union[str, Sequence[str], None] = "001_initial_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# 需要添加 workspace_id 列的表清单（users 表单独处理 default_workspace_id）
MEMORY_TABLES_WITH_WORKSPACE_ID = [
    "memory_variables",
    "memory_tables",
    "memory_fragments",
    "memory_lifecycle",
    "memory_delete_log",
    "memory_merge_log",
    "graph_entities",
    "graph_relationships",
    "graph_relationship_history",
    "memory_trace_events",
    "memory_metrics_snapshots",
    "memory_quality_evaluations",
    "memory_extraction_triggers",
    "performance_metrics",
    "extraction_feedback",
    "extraction_prompt_templates",
]


def _workspace_id_column() -> sa.Column:
    """workspace_id 列定义（nullable，兼容旧数据）。"""
    return sa.Column("workspace_id", sa.Integer(), nullable=True)


def upgrade() -> None:
    # --------------------------------------------------------
    # 1. 新建 4 张表
    # --------------------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("plan", sa.Text(), server_default="free"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_table(
        "workspaces",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("org_id", sa.Integer(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False, server_default="personal"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("slug", name="uq_workspaces_slug"),
    )
    op.create_index("idx_workspaces_org", "workspaces", ["org_id"])

    op.create_table(
        "workspace_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.Text(), nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(), server_default=sa.func.now()),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_workspace_members"),
    )
    op.create_index("idx_workspace_members_user", "workspace_members", ["user_id"])

    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer(), sa.ForeignKey("workspaces.id"), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("scopes", sa.Text(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("idx_api_keys_workspace", "api_keys", ["workspace_id"])
    op.create_index(
        "idx_api_keys_key_hash", "api_keys", ["key_hash"], unique=True
    )

    # --------------------------------------------------------
    # 2. 为 users 表添加 default_workspace_id（延迟 FK，因 workspaces 后建）
    # --------------------------------------------------------
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("default_workspace_id", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_users_default_workspace",
            "workspaces",
            ["default_workspace_id"],
            ["id"],
            use_alter=True,
        )

    # --------------------------------------------------------
    # 3. 为 16 张记忆表添加 workspace_id 列 + 索引
    # --------------------------------------------------------
    for table in MEMORY_TABLES_WITH_WORKSPACE_ID:
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(_workspace_id_column())
            batch_op.create_index(
                f"idx_{table}_workspace", ["workspace_id"]
            )

    # --------------------------------------------------------
    # 4. Backfill：为每个现有 user 创建 personal workspace 并回填
    # --------------------------------------------------------
    conn = op.get_bind()

    # 4a. 为每个 user 创建一个 personal org + personal workspace
    users = conn.execute(sa.text("SELECT id, username FROM users")).fetchall()
    for user in users:
        user_id, username = user[0], user[1]
        slug = f"user-{user_id}"

        # 创建 personal org（每个 user 一个）
        result = conn.execute(
            sa.text(
                "INSERT INTO organizations (name, plan) VALUES (:name, 'free')"
            ),
            {"name": f"{username}'s Org"},
        )
        org_id = result.lastrowid

        # 创建 personal workspace
        result = conn.execute(
            sa.text(
                "INSERT INTO workspaces (org_id, name, slug, kind) "
                "VALUES (:org_id, :name, :slug, 'personal')"
            ),
            {"org_id": org_id, "name": f"{username}", "slug": slug},
        )
        workspace_id = result.lastrowid

        # 创建 owner 成员关系
        conn.execute(
            sa.text(
                "INSERT INTO workspace_members (workspace_id, user_id, role) "
                "VALUES (:ws_id, :uid, 'owner')"
            ),
            {"ws_id": workspace_id, "uid": user_id},
        )

        # 更新 users.default_workspace_id
        conn.execute(
            sa.text(
                "UPDATE users SET default_workspace_id = :ws_id WHERE id = :uid"
            ),
            {"ws_id": workspace_id, "uid": user_id},
        )

        # 回填所有记忆表的 workspace_id
        for table in MEMORY_TABLES_WITH_WORKSPACE_ID:
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET workspace_id = :ws_id "
                    f"WHERE user_id = :uid AND workspace_id IS NULL"
                ),
                {"ws_id": workspace_id, "uid": user_id},
            )


def downgrade() -> None:
    # 反向：先删索引/列，再删表
    for table in MEMORY_TABLES_WITH_WORKSPACE_ID:
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_index(f"idx_{table}_workspace")
            batch_op.drop_column("workspace_id")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_constraint("fk_users_default_workspace", type_="foreignkey")
        batch_op.drop_column("default_workspace_id")

    op.drop_table("api_keys")
    op.drop_table("workspace_members")
    op.drop_table("workspaces")
    op.drop_table("organizations")
