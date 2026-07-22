"""
PostgreSQL 数据库客户端模块

实现与 SQLiteClient 相同的公开接口（execute / get_cursor / 若干辅助方法），
使 20+ 依赖 get_db_client() 的服务无需改动即可切换到 PostgreSQL 后端，从而
支持多副本共享存储。

核心是一个"方言翻译层"：现有代码使用 SQLite 方言的裸 SQL（`?` 占位符、
`AUTOINCREMENT`、`TEXT(n)`、`last_insert_rowid()`、`INSERT OR IGNORE` 等），
本模块在执行前将其翻译为 PostgreSQL 等价语法。

已知限制（列入后续项，不阻断本次落地）：
- SQLite FTS5 全文索引（fragments_fts）在 PG 端不创建，hybrid_search 需走
  ILIKE / tsvector 兜底；
- `INSERT OR REPLACE` 仅做尽力翻译。
"""
import logging
import re
import threading
from contextlib import contextmanager
from typing import Any, List, Optional

import psycopg2
import psycopg2.extras

from app.core import schema_ddl

logger = logging.getLogger(__name__)


# ============================================================
# SQL 方言翻译
# ============================================================

def _qmark_to_pct(sql: str) -> str:
    """把处于字符串字面量之外的 `?` 占位符替换为 `%s`（psycopg2 风格）。"""
    out: List[str] = []
    in_str = False
    for ch in sql:
        if ch == "'":
            in_str = not in_str
            out.append(ch)
        elif ch == "?" and not in_str:
            out.append("%s")
        else:
            out.append(ch)
    return "".join(out)


_RE_SERIAL = re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE)
_RE_AUTOINC = re.compile(r"\bAUTOINCREMENT\b", re.IGNORECASE)
_RE_TEXT_LEN = re.compile(r"\bTEXT\s*\(\s*\d+\s*\)", re.IGNORECASE)
_RE_EXPLAIN_QP = re.compile(r"\bEXPLAIN\s+QUERY\s+PLAN\b", re.IGNORECASE)
_RE_DATETIME_NOW = re.compile(r"datetime\(\s*'now'\s*\)", re.IGNORECASE)
_RE_ADD_COLUMN = re.compile(r"\bADD\s+COLUMN\s+(?!IF\s+NOT\s+EXISTS)", re.IGNORECASE)
_RE_BOOL_TRUE = re.compile(r"\bBOOLEAN\s+DEFAULT\s+1\b", re.IGNORECASE)
_RE_BOOL_FALSE = re.compile(r"\bBOOLEAN\s+DEFAULT\s+0\b", re.IGNORECASE)
_RE_INSERT_IGNORE = re.compile(r"^\s*INSERT\s+OR\s+IGNORE\s+INTO", re.IGNORECASE)
_RE_INSERT_REPLACE = re.compile(r"^\s*INSERT\s+OR\s+REPLACE\s+INTO", re.IGNORECASE)


def translate_ddl(sql: str) -> str:
    """把 SQLite 方言的 DDL/关键字翻译为 PostgreSQL 等价语法。"""
    sql = _RE_SERIAL.sub("SERIAL PRIMARY KEY", sql)
    sql = _RE_AUTOINC.sub("", sql)
    sql = _RE_TEXT_LEN.sub("TEXT", sql)
    sql = _RE_EXPLAIN_QP.sub("EXPLAIN", sql)
    sql = _RE_DATETIME_NOW.sub("NOW()", sql)
    sql = _RE_BOOL_TRUE.sub("BOOLEAN DEFAULT TRUE", sql)
    sql = _RE_BOOL_FALSE.sub("BOOLEAN DEFAULT FALSE", sql)
    # ALTER TABLE ... ADD COLUMN -> ADD COLUMN IF NOT EXISTS（PG 幂等补列）
    if re.match(r"^\s*ALTER\s+TABLE", sql, re.IGNORECASE):
        sql = _RE_ADD_COLUMN.sub("ADD COLUMN IF NOT EXISTS ", sql)
    return sql


def translate_sql(sql: str, has_params: bool) -> str:
    """完整翻译一条语句：DDL 关键字 + `INSERT OR IGNORE` + 占位符/百分号。"""
    sql = translate_ddl(sql)

    append_on_conflict = False
    if _RE_INSERT_IGNORE.search(sql):
        sql = _RE_INSERT_IGNORE.sub("INSERT INTO", sql)
        append_on_conflict = True
    elif _RE_INSERT_REPLACE.search(sql):
        # 尽力翻译：退化为普通 INSERT + 冲突忽略
        sql = _RE_INSERT_REPLACE.sub("INSERT INTO", sql)
        append_on_conflict = True

    # 仅当携带参数时才需要转义字面量 `%`（psycopg2 会解析 `%`）
    if has_params:
        sql = sql.replace("%", "%%")
        sql = _qmark_to_pct(sql)

    if append_on_conflict and "ON CONFLICT" not in sql.upper():
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return sql


class _TranslatingCursor:
    """包装 psycopg2 游标：execute 前翻译 SQL，并模拟 SQLite 的 lastrowid。"""

    def __init__(self, raw_cursor):
        self._cur = raw_cursor
        self.lastrowid: Optional[int] = None

    def execute(self, sql: str, params: tuple = ()):
        stripped = sql.strip()
        upper = stripped.upper()
        self.lastrowid = None

        if upper.startswith("PRAGMA"):
            return  # PostgreSQL 无 PRAGMA，忽略

        translated = translate_sql(stripped, has_params=bool(params))
        is_insert = upper.startswith("INSERT")
        # 为 INSERT 追加 RETURNING id 以模拟 lastrowid（本代码库所有表主键均为 id）
        if is_insert and "RETURNING" not in translated.upper():
            translated = translated.rstrip().rstrip(";") + " RETURNING id"

        self._cur.execute(translated, params or None)

        if is_insert:
            try:
                row = self._cur.fetchone()
                self.lastrowid = row[0] if row is not None else None
            except Exception:
                self.lastrowid = None

    def executemany(self, sql: str, seq):
        translated = translate_sql(sql.strip(), has_params=True)
        self._cur.executemany(translated, seq)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size=None):
        return self._cur.fetchmany(size) if size else self._cur.fetchmany()

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()

    def __getattr__(self, name):
        return getattr(self._cur, name)


class PostgresClient:
    """PostgreSQL 数据库客户端（线程局部连接 + SQLite 方言兼容层）。"""

    dialect = "postgresql"
    _instance = None
    _local = threading.local()

    def __new__(cls, dsn: Optional[str] = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, dsn: str):
        # 单例：避免重复初始化
        if getattr(self, "_initialized", False):
            return
        self._dsn = self._normalize_dsn(dsn)
        self._ensure_schema()
        self._initialized = True

    @staticmethod
    def _normalize_dsn(dsn: str) -> str:
        """把 SQLAlchemy 风格 URL 规整为 libpq 可识别形式。"""
        return dsn.replace("postgresql+psycopg2://", "postgresql://").replace(
            "postgres+psycopg2://", "postgresql://"
        )

    def _get_connection(self):
        conn = getattr(self._local, "connection", None)
        if conn is None or conn.closed:
            conn = psycopg2.connect(
                self._dsn,
                cursor_factory=psycopg2.extras.DictCursor,
                connect_timeout=10,
            )
            self._local.connection = conn
            logger.info("✓ PostgreSQL 连接已建立")
        return conn

    @contextmanager
    def get_cursor(self):
        """获取翻译游标（上下文管理器），成功提交 / 失败回滚。"""
        conn = self._get_connection()
        raw = conn.cursor()
        cursor = _TranslatingCursor(raw)
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"数据库操作失败: {e}")
            raise
        finally:
            raw.close()

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """执行 SQL，返回值语义与 SQLiteClient.execute 对齐。"""
        stripped = sql.strip()
        upper = stripped.upper()

        if upper.startswith("PRAGMA"):
            return []
        if "LAST_INSERT_ROWID()" in upper:
            return [(getattr(self._local, "last_id", None),)]

        try:
            with self.get_cursor() as cursor:
                cursor.execute(stripped, params)
                if (
                    upper.startswith("SELECT")
                    or upper.startswith("WITH")
                    or upper.startswith("EXPLAIN")
                ):
                    return cursor.fetchall()
                elif upper.startswith("INSERT"):
                    self._local.last_id = cursor.lastrowid
                    return cursor.lastrowid
                else:
                    return cursor.rowcount
        except Exception as e:
            logger.error(f"✗ SQL 执行失败: {stripped[:100]}. {e}")
            raise

    def _ensure_schema(self):
        """创建核心表结构（复用 schema_ddl 单一数据源，跳过 SQLite 专属 FTS）。"""
        try:
            for stmt in schema_ddl.CORE_DDL:
                self.execute(stmt)
            for stmt in schema_ddl.COMPAT_ALTERS:
                try:
                    self.execute(stmt)
                except Exception:
                    pass  # 补列失败（列已存在等）忽略
            logger.info("✓ PostgreSQL 核心表初始化完成")
        except Exception as e:
            logger.error(f"✗ PostgreSQL 初始化失败: {e}")
            raise

    # ------------------------------------------------------------
    # 与 SQLiteClient 对齐的辅助方法
    # ------------------------------------------------------------

    def create_user(self, username: str, email: Optional[str] = None) -> Optional[int]:
        try:
            with self.get_cursor() as cursor:
                cursor.execute(
                    "INSERT INTO users (username, email) VALUES (?, ?)",
                    (username, email),
                )
                return cursor.lastrowid
        except psycopg2.IntegrityError:
            result = self.execute("SELECT id FROM users WHERE username = ?", (username,))
            return result[0]["id"] if result else None

    def create_memory_variable(self, user_id: int, key: str, value: Any) -> bool:
        try:
            import json

            value_str = (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
            )
            self.execute(
                """
                INSERT INTO memory_variables (user_id, key, value)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, key)
                DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, key, value_str),
            )
            return True
        except Exception as e:
            logger.error(f"✗ 创建记忆变量失败: {e}")
            return False

    def get_memory_variable(self, user_id: int, key: str) -> Optional[Any]:
        try:
            import json

            result = self.execute(
                "SELECT value FROM memory_variables WHERE user_id = ? AND key = ?",
                (user_id, key),
            )
            if not result:
                return None
            value_str = result[0]["value"]
            try:
                return json.loads(value_str)
            except (json.JSONDecodeError, TypeError):
                return value_str
        except Exception as e:
            logger.error(f"✗ 获取记忆变量失败: {e}")
            return None

    def create_memory_table(self, user_id: int, table_name: str, schema: dict) -> bool:
        try:
            import json

            schema_str = json.dumps(schema, ensure_ascii=False)
            self.execute(
                """
                INSERT INTO memory_tables (user_id, table_name, table_schema)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, table_name)
                DO UPDATE SET table_schema = excluded.table_schema, updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, table_name, schema_str),
            )
            return True
        except Exception as e:
            logger.error(f"✗ 创建记忆表失败: {e}")
            return False

    def create_memory_fragment(
        self,
        user_id: int,
        fragment_type: str,
        content: str,
        ttl: Optional[int] = None,
        importance_score: float = 0.5,
    ) -> Optional[int]:
        try:
            from datetime import datetime, timedelta

            expires_at = None
            if ttl:
                expires_at = datetime.now() + timedelta(seconds=ttl)
            return self.execute(
                """
                INSERT INTO memory_fragments (user_id, fragment_type, content, ttl, importance_score, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, fragment_type, content, ttl, importance_score, expires_at),
            )
        except Exception as e:
            logger.error(f"✗ 创建记忆片段失败: {e}")
            return None

    def get_memory_fragments(
        self, user_id: int, fragment_type: Optional[str] = None, limit: int = 100
    ) -> List[dict]:
        try:
            from datetime import datetime

            self.execute(
                "DELETE FROM memory_fragments WHERE expires_at IS NOT NULL AND expires_at < ?",
                (datetime.now(),),
            )
            if fragment_type:
                result = self.execute(
                    """
                    SELECT * FROM memory_fragments
                    WHERE user_id = ? AND fragment_type = ?
                    ORDER BY importance_score DESC, created_at DESC
                    LIMIT ?
                    """,
                    (user_id, fragment_type, limit),
                )
            else:
                result = self.execute(
                    """
                    SELECT * FROM memory_fragments
                    WHERE user_id = ?
                    ORDER BY importance_score DESC, created_at DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                )
            return [dict(row) for row in result] if result else []
        except Exception as e:
            logger.error(f"✗ 获取记忆片段失败: {e}")
            return []

    def close(self):
        conn = getattr(self._local, "connection", None)
        if conn is not None:
            conn.close()
            self._local.connection = None
            logger.info("✓ PostgreSQL 连接已关闭")
