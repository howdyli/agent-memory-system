"""Phase 4: 事件与 Webhook —— 新增 webhooks + webhook_deliveries 表

1. webhooks 表：存储 Webhook 订阅配置（URL、事件类型、HMAC 密钥）
2. webhook_deliveries 表：记录每次投递结果（状态码、响应、重试次数）

Revision ID: 003_add_webhooks
Revises: 002_add_workspace
Create Date: 2026-07-20

Downgrade 提供完整回滚。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "003_add_webhooks"
down_revision: Union[str, Sequence[str], None] = "002_add_workspace"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── webhooks 表 ──
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("workspace_id", sa.Integer, sa.ForeignKey("workspaces.id"), nullable=True),
        sa.Column("user_id", sa.Integer, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("secret", sa.Text, nullable=False),
        sa.Column("event_types", sa.Text, nullable=False),       # JSON array
        sa.Column("active", sa.Boolean, default=True, server_default="1"),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_webhooks_user_id", "webhooks", ["user_id"])
    op.create_index("ix_webhooks_workspace_id", "webhooks", ["workspace_id"])
    op.create_index("ix_webhooks_active", "webhooks", ["active"])

    # ── webhook_deliveries 表 ──
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("webhook_id", sa.Integer, sa.ForeignKey("webhooks.id"), nullable=False),
        sa.Column("event_id", sa.Text, nullable=False),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("payload", sa.Text, nullable=False),           # JSON
        sa.Column("status_code", sa.Integer, nullable=True),
        sa.Column("response_body", sa.Text, nullable=True),
        sa.Column("success", sa.Boolean, default=False),
        sa.Column("attempt", sa.Integer, default=1, server_default="1"),
        sa.Column("next_retry_at", sa.DateTime, nullable=True),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index("ix_webhook_deliveries_webhook_id", "webhook_deliveries", ["webhook_id"])
    op.create_index("ix_webhook_deliveries_success", "webhook_deliveries", ["success"])
    op.create_index("ix_webhook_deliveries_next_retry", "webhook_deliveries", ["next_retry_at"])


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
