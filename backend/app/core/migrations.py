"""
数据库迁移工具

提供版本化的数据库迁移机制，替代 _init_db 中的 ALTER TABLE 兼容性迁移。
采用轻量级 schema_migrations 表记录已应用版本，支持幂等执行。

使用方式：
    from app.core.migrations import run_migrations
    run_migrations(db_client)

迁移规范：
- 每个迁移是 (version, description, sql_list) 三元组
- version 必须单调递增
- 迁移必须幂等（可重复执行不报错）
- 使用 CREATE TABLE IF NOT EXISTS / ALTER TABLE ADD COLUMN（try/except）
"""
import logging
from typing import List, Tuple, Any

logger = logging.getLogger(__name__)


# ============================================================
# 迁移定义（version, description, sql_statements）
# ============================================================

MIGRATIONS: List[Tuple[int, str, List[str]]] = [
    # v1: 初始 schema（由 _init_db 创建，此处仅记录版本）
    (1, "initial schema (users, memory_variables, memory_tables, memory_fragments)", []),

    # v2: 添加 password_hash 列
    (2, "add password_hash to users", [
        "ALTER TABLE users ADD COLUMN password_hash TEXT",
    ]),

    # v3: memory_fragments 生命周期字段
    (3, "add lifecycle fields to memory_fragments", [
        "ALTER TABLE memory_fragments ADD COLUMN lifecycle_status TEXT DEFAULT 'active'",
        "ALTER TABLE memory_fragments ADD COLUMN last_recalled_at TIMESTAMP",
        "ALTER TABLE memory_fragments ADD COLUMN cold_at TIMESTAMP",
    ]),

    # v4: memory_fragments 向量同步标记
    (4, "add vector_synced to memory_fragments", [
        "ALTER TABLE memory_fragments ADD COLUMN vector_synced INTEGER DEFAULT 0",
    ]),

    # v5: 向量写入 Outbox 表
    (5, "create vector_outbox table", [
        """CREATE TABLE IF NOT EXISTS vector_outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fragment_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            workspace_id TEXT,
            fragment_type TEXT NOT NULL,
            content TEXT NOT NULL,
            importance_score REAL DEFAULT 0.5,
            expires_at TEXT,
            retry_count INTEGER DEFAULT 0,
            next_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (fragment_id) REFERENCES memory_fragments(id)
        )""",
        """CREATE INDEX IF NOT EXISTS idx_vector_outbox_pending
           ON vector_outbox(next_retry_at)
           WHERE retry_count < 5""",
    ]),

    # v6: workspace_id 列到核心表（如果不存在）
    (6, "ensure workspace_id on memory_fragments", [
        "ALTER TABLE memory_fragments ADD COLUMN workspace_id INTEGER",
    ]),
]


# ============================================================
# 迁移执行器
# ============================================================

def _ensure_migrations_table(cursor) -> None:
    """确保 schema_migrations 表存在"""
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')


def _get_applied_versions(cursor) -> set:
    """获取已应用的迁移版本集合"""
    cursor.execute("SELECT version FROM schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def _apply_migration(cursor, version: int, description: str, sql_list: List[str]) -> None:
    """应用单个迁移（幂等：ALTER TABLE 失败时忽略）"""
    for sql in sql_list:
        try:
            cursor.execute(sql)
        except Exception as e:
            # ALTER TABLE ADD COLUMN 已存在时抛出 duplicate column，幂等忽略
            err_msg = str(e).lower()
            if "duplicate column" in err_msg or "already exists" in err_msg:
                continue
            else:
                logger.warning(f"迁移 v{version} SQL 执行警告: {e}")
    cursor.execute(
        "INSERT OR REPLACE INTO schema_migrations (version, description) VALUES (?, ?)",
        (version, description),
    )


def run_migrations(db_client: Any) -> dict:
    """
    执行所有待应用的数据库迁移。

    Args:
        db_client: DBClient 实例（需有 execute 方法和获取 cursor 的能力）

    Returns:
        {"applied": [v1, v2], "skipped": [v3], "total": N}
    """
    applied = []
    skipped = []

    def _run_with_cursor(cursor):
        _ensure_migrations_table(cursor)
        applied_versions = _get_applied_versions(cursor)

        for version, description, sql_list in MIGRATIONS:
            if version in applied_versions:
                skipped.append(version)
                continue
            _apply_migration(cursor, version, description, sql_list)
            applied.append(version)
            logger.info(f"✓ 迁移 v{version}: {description}")

    # 使用 db_client 的连接执行
    try:
        # DBClient 风格：通过 _get_connection 获取原生连接
        if hasattr(db_client, '_get_connection'):
            with db_client._get_connection() as conn:
                cursor = conn.cursor()
                _run_with_cursor(cursor)
                conn.commit()
        elif hasattr(db_client, 'execute'):
            # 简化风格：直接 execute
            db_client.execute('''
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    description TEXT,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            rows = db_client.execute("SELECT version FROM schema_migrations") or []
            applied_versions = {r[0] if isinstance(r, (list, tuple)) else r.get("version") for r in rows}

            for version, description, sql_list in MIGRATIONS:
                if version in applied_versions:
                    skipped.append(version)
                    continue
                for sql in sql_list:
                    try:
                        db_client.execute(sql)
                    except Exception as e:
                        err_msg = str(e).lower()
                        if "duplicate column" in err_msg or "already exists" in err_msg:
                            continue
                        logger.warning(f"迁移 v{version} SQL 执行警告: {e}")
                db_client.execute(
                    "INSERT OR REPLACE INTO schema_migrations (version, description) VALUES (?, ?)",
                    (version, description),
                )
                applied.append(version)
                logger.info(f"✓ 迁移 v{version}: {description}")
        else:
            logger.error("不支持的 db_client 类型")
            return {"applied": [], "skipped": [], "total": len(MIGRATIONS), "error": "unsupported db_client"}
    except Exception as e:
        logger.error(f"迁移执行失败: {e}")
        return {"applied": applied, "skipped": skipped, "total": len(MIGRATIONS), "error": str(e)}

    return {"applied": applied, "skipped": skipped, "total": len(MIGRATIONS)}


def get_migration_status(db_client: Any) -> dict:
    """获取迁移状态（已应用/待应用）"""
    try:
        rows = db_client.execute("SELECT version, description, applied_at FROM schema_migrations ORDER BY version") or []
        applied = [dict(r) if hasattr(r, "keys") else {"version": r[0], "description": r[1], "applied_at": r[2]} for r in rows]
        applied_versions = {item["version"] for item in applied}
        pending = [{"version": v, "description": d} for v, d, _ in MIGRATIONS if v not in applied_versions]
        return {
            "applied_count": len(applied),
            "pending_count": len(pending),
            "applied": applied,
            "pending": pending,
            "latest_version": max((v for v, _, _ in MIGRATIONS), default=0),
        }
    except Exception as e:
        return {"error": str(e), "applied_count": 0, "pending_count": len(MIGRATIONS)}
