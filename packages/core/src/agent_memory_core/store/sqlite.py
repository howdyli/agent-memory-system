"""
SQLite relational store adapter.

Implements RelationalStore ABC using native sqlite3 (no ORM).
Adapted from backend/app/core/db_client.py — singleton removed, schema in ensure_schema().
Thread-local connections with WAL mode for concurrency.

Usage:
    store = SQLiteStore(db_path="./mem.db")
    store.ensure_schema()
    store.set_variable(workspace_id=1, key="name", value="Alice")
"""

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .base import RelationalStore

logger = logging.getLogger(__name__)


class SQLiteStore(RelationalStore):
    """SQLite implementation of RelationalStore.

    - Thread-local connections (one per thread)
    - WAL mode for read/write concurrency
    - No ORM — native SQL throughout
    - Schema creation in ensure_schema()
    """

    def __init__(self, db_path: str = "agent_memory.db", echo: bool = False):
        self._db_path = db_path
        self._echo = echo
        self._local = threading.local()

    # ── Connection Management ───────────────────────────────────

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local connection with WAL mode."""
        if not hasattr(self._local, "connection") or self._local.connection is None:
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=30,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA cache_size=-20000")
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.connection = conn
            if self._echo:
                logger.info(f"SQLite connection established (WAL, db={self._db_path})")
        return self._local.connection

    @contextmanager
    def _get_cursor(self):
        """Context manager for cursor with auto-commit/rollback."""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()

    def _execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute SQL and return appropriate result."""
        with self._get_cursor() as cursor:
            cursor.execute(sql, params)
            upper = sql.strip().upper()
            if upper.startswith(("SELECT", "PRAGMA", "EXPLAIN")):
                return cursor.fetchall()
            elif upper.startswith("INSERT"):
                return cursor.lastrowid
            else:
                return cursor.rowcount

    def _to_row_dicts(self, rows: list) -> List[Dict]:
        """Convert sqlite3.Row objects to plain dicts."""
        return [dict(r) for r in rows] if rows else []

    # ── Schema Management ───────────────────────────────────────

    def ensure_schema(self) -> None:
        """Create all tables and indexes."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Users (foundation table — workspace isolation will be added in Server layer)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE,
                password_hash TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Backward compat: add password_hash column if missing
        try:
            cursor.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
        except sqlite3.OperationalError:
            pass

        # Memory Variables
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_variables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, key),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mv_user ON memory_variables(user_id)")

        # Memory Tables (dynamic table definitions)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_tables (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                table_name TEXT NOT NULL,
                table_schema TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, table_name),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mt_user ON memory_tables(user_id)")

        # Memory Fragments
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_fragments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                fragment_type TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding_id TEXT,
                ttl INTEGER,
                importance_score REAL DEFAULT 0.5,
                lifecycle_status TEXT DEFAULT 'active',
                last_recalled_at TIMESTAMP,
                cold_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mf_user ON memory_fragments(user_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_mf_expires ON memory_fragments(expires_at)")

        # Memory Lifecycle
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_lifecycle (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_type TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                lifecycle_status TEXT DEFAULT 'active',
                cold_reason TEXT,
                cold_at TIMESTAMP,
                last_recalled_at TIMESTAMP,
                archived_at TIMESTAMP,
                soft_deleted_at TIMESTAMP,
                restore_count INTEGER DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lc_user_status ON memory_lifecycle(user_id, lifecycle_status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_lc_status ON memory_lifecycle(lifecycle_status)")

        # Delete Log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_delete_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_type TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                old_content TEXT,
                operator TEXT DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # Merge Log
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_merge_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_type TEXT NOT NULL,
                source_ids TEXT NOT NULL,
                target_id TEXT,
                merge_type TEXT NOT NULL,
                merge_action TEXT NOT NULL,
                similarity_score REAL,
                old_value TEXT,
                new_value TEXT,
                resolved INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)

        # Graph Entities
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS graph_entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                aliases TEXT,
                metadata TEXT,
                first_seen_at TIMESTAMP,
                last_seen_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                UNIQUE(user_id, name, entity_type)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ge_user ON graph_entities(user_id, entity_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ge_name ON graph_entities(user_id, name)")

        # Graph Relationships
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS graph_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                source_entity_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                relation_subtype TEXT,
                properties TEXT,
                confidence REAL DEFAULT 0.5,
                valid_from TIMESTAMP,
                valid_to TIMESTAMP,
                is_active INTEGER DEFAULT 1,
                extraction_source TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_entity_id) REFERENCES graph_entities(id),
                FOREIGN KEY (target_entity_id) REFERENCES graph_entities(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gr_source ON graph_relationships(source_entity_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gr_target ON graph_relationships(target_entity_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_gr_active ON graph_relationships(user_id, relation_type, is_active)")

        # Graph Relationship History
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS graph_relationship_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                relationship_id INTEGER,
                source_entity_id INTEGER NOT NULL,
                target_entity_id INTEGER NOT NULL,
                relation_type TEXT NOT NULL,
                old_properties TEXT,
                new_properties TEXT,
                valid_from TIMESTAMP,
                valid_to TIMESTAMP,
                change_reason TEXT,
                changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_grh_rel ON graph_relationship_history(relationship_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_grh_entity ON graph_relationship_history(source_entity_id, target_entity_id)")

        # Trace Events (Observability)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_trace_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_id TEXT,
                memory_type TEXT(32),
                event_type TEXT(32) NOT NULL,
                event_source TEXT(32),
                conversation_id TEXT,
                session_id TEXT,
                score REAL,
                latency_ms REAL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_te_user ON memory_trace_events(user_id, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_te_memory ON memory_trace_events(memory_id, event_type)")

        # Metrics Snapshots
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_metrics_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                snapshot_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_memories INTEGER,
                active_memories INTEGER,
                total_storage_bytes INTEGER,
                daily_new_count INTEGER,
                daily_recall_count INTEGER,
                daily_recall_hit_count INTEGER,
                avg_recall_latency_ms REAL,
                p50_recall_latency_ms REAL,
                p99_recall_latency_ms REAL,
                llm_extraction_tokens INTEGER,
                llm_rerank_tokens INTEGER
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ms_user ON memory_metrics_snapshots(user_id, snapshot_time)")

        # Quality Evaluations
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_quality_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_id TEXT NOT NULL,
                memory_type TEXT(32) NOT NULL,
                evaluation_type TEXT(32) NOT NULL,
                score REAL NOT NULL,
                evaluator TEXT(32) NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_qe_memory ON memory_quality_evaluations(user_id, memory_id)")

        # Extraction Triggers
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS memory_extraction_triggers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                session_id TEXT,
                conversation_id TEXT,
                trigger_type TEXT(32) NOT NULL,
                query_snippet TEXT,
                fragments_created INTEGER DEFAULT 0,
                llm_tokens_used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_et_user ON memory_extraction_triggers(user_id, created_at)")

        # Performance Metrics
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS performance_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                metric_type TEXT(32) NOT NULL,
                endpoint TEXT,
                value REAL,
                metadata TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pm_user_type_time ON performance_metrics(user_id, metric_type, created_at)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_pm_user_time ON performance_metrics(user_id, created_at)")

        # Extraction Feedback
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS extraction_feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                extraction_id TEXT NOT NULL,
                rating TEXT NOT NULL,
                correction TEXT,
                source_text TEXT,
                extracted_data TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ef_user ON extraction_feedback(user_id, created_at)")

        # Extraction Prompt Templates
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS extraction_prompt_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                content TEXT NOT NULL,
                is_active INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name),
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ept_user ON extraction_prompt_templates(user_id, is_active)")

        # FTS5 virtual table for full-text search
        try:
            cursor.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS fragments_fts USING fts5(
                    content, fragment_type,
                    tokenize='unicode61'
                )
            """)
            # Triggers to keep FTS index in sync
            for trig_sql in [
                """CREATE TRIGGER IF NOT EXISTS fragments_fts_ai AFTER INSERT ON memory_fragments BEGIN
                    INSERT INTO fragments_fts(rowid, content, fragment_type)
                    VALUES (new.id, new.content, new.fragment_type);
                END;""",
                """CREATE TRIGGER IF NOT EXISTS fragments_fts_ad AFTER DELETE ON memory_fragments BEGIN
                    DELETE FROM fragments_fts WHERE rowid = old.id;
                END;""",
                """CREATE TRIGGER IF NOT EXISTS fragments_fts_au AFTER UPDATE ON memory_fragments BEGIN
                    DELETE FROM fragments_fts WHERE rowid = old.id;
                    INSERT INTO fragments_fts(rowid, content, fragment_type)
                    VALUES (new.id, new.content, new.fragment_type);
                END;""",
            ]:
                try:
                    cursor.execute(trig_sql)
                except sqlite3.OperationalError as e:
                    logger.warning(f"FTS5 trigger creation skipped: {e}")
        except sqlite3.OperationalError as e:
            logger.warning(f"FTS5 virtual table creation skipped: {e}")

        conn.commit()
        logger.info(f"Schema ensured: {self._db_path}")

    # ── Variables ──────────────────────────────────────────────

    def set_variable(self, workspace_id: int, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        value_str = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        self._execute(
            """INSERT INTO memory_variables (user_id, key, value)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
            (workspace_id, key, value_str),
        )
        return True

    def get_variable(self, workspace_id: int, key: str) -> Optional[Any]:
        rows = self._execute(
            "SELECT value FROM memory_variables WHERE user_id = ? AND key = ?",
            (workspace_id, key),
        )
        if not rows:
            return None
        value_str = rows[0]["value"]
        try:
            return json.loads(value_str)
        except (json.JSONDecodeError, TypeError):
            return value_str

    def delete_variable(self, workspace_id: int, key: str) -> bool:
        self._execute(
            "DELETE FROM memory_variables WHERE user_id = ? AND key = ?",
            (workspace_id, key),
        )
        return True

    def list_variables(self, workspace_id: int, prefix: Optional[str] = None) -> List[Dict]:
        if prefix:
            rows = self._execute(
                "SELECT * FROM memory_variables WHERE user_id = ? AND key LIKE ? ORDER BY key",
                (workspace_id, prefix + "%"),
            )
        else:
            rows = self._execute(
                "SELECT * FROM memory_variables WHERE user_id = ? ORDER BY key",
                (workspace_id,),
            )
        return self._to_row_dicts(rows)

    # ── Fragments ──────────────────────────────────────────────

    def create_fragment(
        self, workspace_id: int, fragment_type: str, content: str,
        embedding_id: Optional[str] = None, ttl: Optional[int] = None,
        importance_score: float = 0.5, user_id: Optional[int] = None,
    ) -> int:
        expires_at = None
        if ttl:
            expires_at = (datetime.now() + timedelta(seconds=ttl)).isoformat()
        return self._execute(
            """INSERT INTO memory_fragments (user_id, fragment_type, content, embedding_id, ttl, importance_score, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (workspace_id, fragment_type, content, embedding_id, ttl, importance_score, expires_at),
        )

    def get_fragment(self, workspace_id: int, fragment_id: int) -> Optional[Dict]:
        rows = self._execute(
            "SELECT * FROM memory_fragments WHERE user_id = ? AND id = ?",
            (workspace_id, fragment_id),
        )
        return self._to_row_dicts(rows)[0] if rows else None

    def list_fragments(
        self, workspace_id: int, fragment_type: Optional[str] = None,
        lifecycle_status: Optional[str] = None, limit: int = 100, offset: int = 0,
    ) -> List[Dict]:
        clauses = ["user_id = ?"]
        params: list = [workspace_id]
        if fragment_type:
            clauses.append("fragment_type = ?")
            params.append(fragment_type)
        if lifecycle_status:
            clauses.append("lifecycle_status = ?")
            params.append(lifecycle_status)
        params.extend([limit, offset])
        rows = self._execute(
            f"SELECT * FROM memory_fragments WHERE {' AND '.join(clauses)} ORDER BY importance_score DESC, created_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )
        return self._to_row_dicts(rows)

    def update_fragment(
        self, workspace_id: int, fragment_id: int,
        content: Optional[str] = None, importance_score: Optional[float] = None,
        embedding_id: Optional[str] = None, lifecycle_status: Optional[str] = None,
    ) -> bool:
        updates: list = []
        params: list = []
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if importance_score is not None:
            updates.append("importance_score = ?")
            params.append(importance_score)
        if embedding_id is not None:
            updates.append("embedding_id = ?")
            params.append(embedding_id)
        if lifecycle_status is not None:
            updates.append("lifecycle_status = ?")
            params.append(lifecycle_status)
        if not updates:
            return True
        updates.append("updated_at = CURRENT_TIMESTAMP")
        params.extend([workspace_id, fragment_id])
        self._execute(
            f"UPDATE memory_fragments SET {' , '.join(updates)} WHERE user_id = ? AND id = ?",
            tuple(params),
        )
        return True

    def delete_fragment(self, workspace_id: int, fragment_id: int) -> bool:
        self._execute("DELETE FROM memory_fragments WHERE user_id = ? AND id = ?", (workspace_id, fragment_id))
        return True

    def delete_expired_fragments(self, workspace_id: Optional[int] = None) -> int:
        now = datetime.now().isoformat()
        if workspace_id:
            return self._execute(
                "DELETE FROM memory_fragments WHERE user_id = ? AND expires_at IS NOT NULL AND expires_at < ?",
                (workspace_id, now),
            )
        return self._execute(
            "DELETE FROM memory_fragments WHERE expires_at IS NOT NULL AND expires_at < ?",
            (now,),
        )

    # ── Dynamic Tables ─────────────────────────────────────────

    def create_table(self, workspace_id: int, table_name: str, schema: Dict) -> bool:
        self._execute(
            """INSERT INTO memory_tables (user_id, table_name, table_schema)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, table_name) DO UPDATE SET table_schema = excluded.table_schema, updated_at = CURRENT_TIMESTAMP""",
            (workspace_id, table_name, json.dumps(schema, ensure_ascii=False)),
        )
        return True

    def get_table(self, workspace_id: int, table_name: str) -> Optional[Dict]:
        rows = self._execute(
            "SELECT * FROM memory_tables WHERE user_id = ? AND table_name = ?",
            (workspace_id, table_name),
        )
        return self._to_row_dicts(rows)[0] if rows else None

    def list_tables(self, workspace_id: int) -> List[Dict]:
        rows = self._execute("SELECT * FROM memory_tables WHERE user_id = ?", (workspace_id,))
        return self._to_row_dicts(rows)

    def delete_table(self, workspace_id: int, table_name: str) -> bool:
        self._execute("DELETE FROM memory_tables WHERE user_id = ? AND table_name = ?", (workspace_id, table_name))
        return True

    def add_record(self, workspace_id: int, table_name: str, record: Dict) -> int:
        # Dynamic table data is stored in a separate physical table per definition
        # For now, we use the JSON-based approach from the existing implementation
        physical_table = f"dt_{workspace_id}_{table_name}"
        columns = list(record.keys())
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in record.values()]
        col_str = ", ".join(columns)
        val_str = ", ".join(["?"] * len(values))
        self._execute(f"INSERT INTO {physical_table} ({col_str}) VALUES ({val_str})", tuple(values))
        # Return last rowid
        rows = self._execute(f"SELECT last_insert_rowid()")
        return rows[0][0] if rows else 0

    def query_records(
        self, workspace_id: int, table_name: str,
        filters: Optional[Dict] = None, order_by: Optional[str] = None,
        limit: int = 100, offset: int = 0,
    ) -> List[Dict]:
        physical_table = f"dt_{workspace_id}_{table_name}"
        where_clauses: list = []
        params: list = []
        if filters:
            for k, v in filters.items():
                where_clauses.append(f"{k} = ?")
                params.append(v)
        where_str = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        order_str = f"ORDER BY {order_by}" if order_by else ""
        params.extend([limit, offset])
        rows = self._execute(
            f"SELECT * FROM {physical_table} {where_str} {order_str} LIMIT ? OFFSET ?",
            tuple(params),
        )
        return self._to_row_dicts(rows)

    def update_record(self, workspace_id: int, table_name: str, record_id: int, updates: Dict) -> bool:
        physical_table = f"dt_{workspace_id}_{table_name}"
        set_clauses = [f"{k} = ?" for k in updates.keys()]
        values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in updates.values()]
        self._execute(
            f"UPDATE {physical_table} SET {' , '.join(set_clauses)} WHERE id = ?",
            tuple(values + [record_id]),
        )
        return True

    def delete_record(self, workspace_id: int, table_name: str, record_id: int) -> bool:
        physical_table = f"dt_{workspace_id}_{table_name}"
        self._execute(f"DELETE FROM {physical_table} WHERE id = ?", (record_id,))
        return True

    # ── Graph — Entities ───────────────────────────────────────

    def ensure_entity(
        self, workspace_id: int, name: str, entity_type: str,
        aliases: Optional[List[str]] = None, metadata: Optional[Dict] = None,
        user_id: Optional[int] = None,
    ) -> int:
        aliases_str = json.dumps(aliases, ensure_ascii=False) if aliases else None
        metadata_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
        now = datetime.now().isoformat()
        try:
            return self._execute(
                """INSERT INTO graph_entities (user_id, name, entity_type, aliases, metadata, first_seen_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, name, entity_type) DO UPDATE SET
                       aliases = excluded.aliases, metadata = excluded.metadata,
                       last_seen_at = excluded.last_seen_at, updated_at = CURRENT_TIMESTAMP""",
                (workspace_id, name, entity_type, aliases_str, metadata_str, now, now),
            )
        except sqlite3.IntegrityError:
            rows = self._execute(
                "SELECT id FROM graph_entities WHERE user_id = ? AND name = ? AND entity_type = ?",
                (workspace_id, name, entity_type),
            )
            return rows[0]["id"] if rows else 0

    def get_entity(self, workspace_id: int, entity_id: int) -> Optional[Dict]:
        rows = self._execute(
            "SELECT * FROM graph_entities WHERE user_id = ? AND id = ?",
            (workspace_id, entity_id),
        )
        return self._to_row_dicts(rows)[0] if rows else None

    def get_entity_by_name(self, workspace_id: int, name: str, entity_type: Optional[str] = None) -> Optional[Dict]:
        if entity_type:
            rows = self._execute(
                "SELECT * FROM graph_entities WHERE user_id = ? AND name = ? AND entity_type = ?",
                (workspace_id, name, entity_type),
            )
        else:
            rows = self._execute(
                "SELECT * FROM graph_entities WHERE user_id = ? AND name = ?",
                (workspace_id, name),
            )
        return self._to_row_dicts(rows)[0] if rows else None

    def list_entities(
        self, workspace_id: int, entity_type: Optional[str] = None,
        limit: int = 100, offset: int = 0,
    ) -> List[Dict]:
        if entity_type:
            rows = self._execute(
                "SELECT * FROM graph_entities WHERE user_id = ? AND entity_type = ? LIMIT ? OFFSET ?",
                (workspace_id, entity_type, limit, offset),
            )
        else:
            rows = self._execute(
                "SELECT * FROM graph_entities WHERE user_id = ? LIMIT ? OFFSET ?",
                (workspace_id, limit, offset),
            )
        return self._to_row_dicts(rows)

    def delete_entity(self, workspace_id: int, entity_id: int) -> bool:
        # Also delete all relationships involving this entity
        self._execute("DELETE FROM graph_relationships WHERE source_entity_id = ? OR target_entity_id = ?", (entity_id, entity_id))
        self._execute("DELETE FROM graph_entities WHERE user_id = ? AND id = ?", (workspace_id, entity_id))
        return True

    # ── Graph — Relationships ──────────────────────────────────

    def add_relationship(
        self, workspace_id: int, source_entity_id: int, target_entity_id: int,
        relation_type: str, relation_subtype: Optional[str] = None,
        properties: Optional[Dict] = None, confidence: float = 0.5,
        valid_from: Optional[datetime] = None, valid_to: Optional[datetime] = None,
        extraction_source: Optional[str] = None, user_id: Optional[int] = None,
    ) -> int:
        props_str = json.dumps(properties, ensure_ascii=False) if properties else None
        vf_str = valid_from.isoformat() if valid_from else None
        vt_str = valid_to.isoformat() if valid_to else None
        return self._execute(
            """INSERT INTO graph_relationships
               (user_id, source_entity_id, target_entity_id, relation_type, relation_subtype, properties, confidence, valid_from, valid_to, extraction_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (workspace_id, source_entity_id, target_entity_id, relation_type, relation_subtype, props_str, confidence, vf_str, vt_str, extraction_source),
        )

    def get_relationship(self, workspace_id: int, relationship_id: int) -> Optional[Dict]:
        rows = self._execute(
            "SELECT * FROM graph_relationships WHERE id = ?",
            (relationship_id,),
        )
        return self._to_row_dicts(rows)[0] if rows else None

    def list_relationships(
        self, workspace_id: int, source_entity_id: Optional[int] = None,
        target_entity_id: Optional[int] = None, relation_type: Optional[str] = None,
        is_active: bool = True, limit: int = 100,
    ) -> List[Dict]:
        clauses = ["user_id = ?"]
        params: list = [workspace_id]
        if source_entity_id:
            clauses.append("source_entity_id = ?")
            params.append(source_entity_id)
        if target_entity_id:
            clauses.append("target_entity_id = ?")
            params.append(target_entity_id)
        if relation_type:
            clauses.append("relation_type = ?")
            params.append(relation_type)
        clauses.append("is_active = ?")
        params.append(1 if is_active else 0)
        params.append(limit)
        rows = self._execute(
            f"SELECT * FROM graph_relationships WHERE {' AND '.join(clauses)} LIMIT ?",
            tuple(params),
        )
        return self._to_row_dicts(rows)

    def deactivate_relationship(self, workspace_id: int, relationship_id: int) -> bool:
        self._execute("UPDATE graph_relationships SET is_active = 0 WHERE id = ?", (relationship_id,))
        return True

    # ── Lifecycle ──────────────────────────────────────────────

    def mark_cold(self, workspace_id: int, memory_type: str, memory_id: str, reason: Optional[str] = None, user_id: Optional[int] = None) -> int:
        now = datetime.now().isoformat()
        return self._execute(
            """INSERT INTO memory_lifecycle (user_id, memory_type, memory_id, lifecycle_status, cold_reason, cold_at)
               VALUES (?, ?, ?, 'cold', ?, ?)""",
            (workspace_id, memory_type, memory_id, reason, now),
        )

    def mark_active(self, workspace_id: int, memory_type: str, memory_id: str) -> bool:
        self._execute(
            """UPDATE memory_lifecycle SET lifecycle_status = 'active', restore_count = restore_count + 1
               WHERE user_id = ? AND memory_type = ? AND memory_id = ?""",
            (workspace_id, memory_type, memory_id),
        )
        return True

    def soft_delete(self, workspace_id: int, memory_type: str, memory_id: str, reason: Optional[str] = None, user_id: Optional[int] = None) -> bool:
        now = datetime.now().isoformat()
        self._execute(
            """UPDATE memory_lifecycle SET lifecycle_status = 'deleted', soft_deleted_at = ?
               WHERE user_id = ? AND memory_type = ? AND memory_id = ?""",
            (now, workspace_id, memory_type, memory_id),
        )
        return True

    def get_lifecycle_status(self, workspace_id: int, memory_type: str, memory_id: str) -> Optional[Dict]:
        rows = self._execute(
            "SELECT * FROM memory_lifecycle WHERE user_id = ? AND memory_type = ? AND memory_id = ?",
            (workspace_id, memory_type, memory_id),
        )
        return self._to_row_dicts(rows)[0] if rows else None

    def list_lifecycle_memories(
        self, workspace_id: int, lifecycle_status: Optional[str] = None,
        memory_type: Optional[str] = None, limit: int = 100, offset: int = 0,
    ) -> List[Dict]:
        clauses = ["user_id = ?"]
        params: list = [workspace_id]
        if lifecycle_status:
            clauses.append("lifecycle_status = ?")
            params.append(lifecycle_status)
        if memory_type:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        params.extend([limit, offset])
        rows = self._execute(
            f"SELECT * FROM memory_lifecycle WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ? OFFSET ?",
            tuple(params),
        )
        return self._to_row_dicts(rows)

    # ── Observability ──────────────────────────────────────────

    def log_trace_event(
        self, workspace_id: int, memory_id: Optional[str] = None,
        memory_type: Optional[str] = None, event_type: str = "",
        event_source: Optional[str] = None, conversation_id: Optional[str] = None,
        session_id: Optional[str] = None, score: Optional[float] = None,
        latency_ms: Optional[float] = None, metadata: Optional[Dict] = None,
        user_id: Optional[int] = None,
    ) -> int:
        meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
        return self._execute(
            """INSERT INTO memory_trace_events
               (user_id, memory_id, memory_type, event_type, event_source, conversation_id, session_id, score, latency_ms, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (workspace_id, memory_id, memory_type, event_type, event_source, conversation_id, session_id, score, latency_ms, meta_str),
        )

    def create_metrics_snapshot(self, workspace_id: int, snapshot: Dict, user_id: Optional[int] = None) -> int:
        fields = [
            "user_id", "total_memories", "active_memories", "total_storage_bytes",
            "daily_new_count", "daily_recall_count", "daily_recall_hit_count",
            "avg_recall_latency_ms", "p50_recall_latency_ms", "p99_recall_latency_ms",
            "llm_extraction_tokens", "llm_rerank_tokens",
        ]
        values = [snapshot.get(f.replace("user_id", "workspace_id") if f == "user_id" else f) for f in fields]
        values[0] = workspace_id  # override user_id with workspace_id
        col_str = ", ".join(fields)
        val_str = ", ".join(["?"] * len(fields))
        return self._execute(f"INSERT INTO memory_metrics_snapshots ({col_str}) VALUES ({val_str})", tuple(values))

    def log_quality_evaluation(
        self, workspace_id: int, memory_id: str, memory_type: str,
        evaluation_type: str, score: float, evaluator: str,
        details: Optional[Dict] = None, user_id: Optional[int] = None,
    ) -> int:
        details_str = json.dumps(details, ensure_ascii=False) if details else None
        return self._execute(
            """INSERT INTO memory_quality_evaluations (user_id, memory_id, memory_type, evaluation_type, score, evaluator, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (workspace_id, memory_id, memory_type, evaluation_type, score, evaluator, details_str),
        )

    def log_extraction_trigger(
        self, workspace_id: int, trigger_type: str,
        session_id: Optional[str] = None, conversation_id: Optional[str] = None,
        query_snippet: Optional[str] = None, fragments_created: int = 0,
        llm_tokens_used: int = 0, user_id: Optional[int] = None,
    ) -> int:
        return self._execute(
            """INSERT INTO memory_extraction_triggers (user_id, trigger_type, session_id, conversation_id, query_snippet, fragments_created, llm_tokens_used)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (workspace_id, trigger_type, session_id, conversation_id, query_snippet, fragments_created, llm_tokens_used),
        )

    def query_trace_events(
        self, workspace_id: int, event_type: Optional[str] = None,
        memory_type: Optional[str] = None, start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None, limit: int = 100,
    ) -> List[Dict]:
        clauses = ["user_id = ?"]
        params: list = [workspace_id]
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if memory_type:
            clauses.append("memory_type = ?")
            params.append(memory_type)
        if start_time:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat())
        if end_time:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat())
        params.append(limit)
        rows = self._execute(
            f"SELECT * FROM memory_trace_events WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
            tuple(params),
        )
        return self._to_row_dicts(rows)

    # ── Extraction Feedback ────────────────────────────────────

    def log_extraction_feedback(
        self, workspace_id: int, extraction_id: str, rating: str,
        correction: Optional[str] = None, source_text: Optional[str] = None,
        extracted_data: Optional[str] = None, user_id: Optional[int] = None,
    ) -> int:
        return self._execute(
            """INSERT INTO extraction_feedback (user_id, extraction_id, rating, correction, source_text, extracted_data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (workspace_id, extraction_id, rating, correction, source_text, extracted_data),
        )

    def set_extraction_prompt_template(
        self, workspace_id: int, name: str, content: str,
        is_active: bool = False, user_id: Optional[int] = None,
    ) -> int:
        return self._execute(
            """INSERT INTO extraction_prompt_templates (user_id, name, content, is_active)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, name) DO UPDATE SET content = excluded.content, is_active = excluded.is_active, updated_at = CURRENT_TIMESTAMP""",
            (workspace_id, name, content, 1 if is_active else 0),
        )

    def get_extraction_prompt_template(self, workspace_id: int, name: str) -> Optional[Dict]:
        rows = self._execute(
            "SELECT * FROM extraction_prompt_templates WHERE user_id = ? AND name = ?",
            (workspace_id, name),
        )
        return self._to_row_dicts(rows)[0] if rows else None

    # ── FTS Search ─────────────────────────────────────────────

    def fts_search(self, query_text: str, limit: int = 20) -> List[Dict]:
        rows = self._execute(
            """SELECT fts.rowid, fts.content, fts.fragment_type
               FROM fragments_fts fts
               WHERE fragments_fts MATCH ?
               ORDER BY rank
               LIMIT ?""",
            (query_text, limit),
        )
        return self._to_row_dicts(rows)

    # ── Performance Metrics ────────────────────────────────────

    def log_performance_metric(
        self, workspace_id: int, metric_type: str,
        endpoint: Optional[str] = None, value: Optional[float] = None,
        metadata: Optional[Dict] = None, user_id: Optional[int] = None,
    ) -> int:
        meta_str = json.dumps(metadata, ensure_ascii=False) if metadata else None
        return self._execute(
            """INSERT INTO performance_metrics (user_id, metric_type, endpoint, value, metadata)
               VALUES (?, ?, ?, ?, ?)""",
            (workspace_id, metric_type, endpoint, value, meta_str),
        )

    # ── Merge Log ──────────────────────────────────────────────

    def log_merge(
        self, workspace_id: int, memory_type: str, source_ids: str,
        target_id: Optional[str] = None, merge_type: str = "",
        merge_action: str = "", similarity_score: Optional[float] = None,
        old_value: Optional[str] = None, new_value: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> int:
        return self._execute(
            """INSERT INTO memory_merge_log (user_id, memory_type, source_ids, target_id, merge_type, merge_action, similarity_score, old_value, new_value)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (workspace_id, memory_type, source_ids, target_id, merge_type, merge_action, similarity_score, old_value, new_value),
        )

    # ── Generic SQL ────────────────────────────────────────────

    def execute_sql(self, sql: str, params: tuple = ()) -> Any:
        return self._execute(sql, params)

    # ── Cleanup ────────────────────────────────────────────────

    def close(self) -> None:
        if hasattr(self._local, "connection") and self._local.connection:
            self._local.connection.close()
            self._local.connection = None
            logger.info("SQLite connection closed")
