"""Alembic 迁移环境（Phase 1）。

- 从 app.core.config 读取 DATABASE_URL（环境变量 > .env > 默认）。
- target_metadata 指向 app.models.orm.Base.metadata，支持 autogenerate。
- SQLite 下启用 batch 模式，保证 ALTER TABLE 兼容。
"""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# 确保能 import app.*（backend 目录加入 sys.path）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings  # noqa: E402
from app.models.orm import Base  # noqa: E402

config = context.config

# 用集中配置覆盖 sqlalchemy.url（避免把连接串写死在 ini 里）
_settings = get_settings()
config.set_main_option("sqlalchemy.url", _settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _is_sqlite() -> bool:
    return _settings.DATABASE_URL.startswith("sqlite")


def run_migrations_offline() -> None:
    """离线（--sql）模式：仅生成 SQL，不连接数据库。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(),
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite(),
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
