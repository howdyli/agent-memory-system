"""
SQLite 数据库客户端模块

提供 SQLite 数据库连接和基础CRUD操作接口
用于开发和测试（生产环境建议使用 PostgreSQL）
"""
import logging
import sqlite3
import threading
from typing import Optional, Any, List, Dict
from contextlib import contextmanager

logger = logging.getLogger(__name__)

class SQLiteClient:
    """SQLite 数据库客户端（使用内存连接池）"""

    dialect = "sqlite"
    _instance = None
    _local = threading.local()
    _db_path = "agent_memory.db"
    
    def __new__(cls, db_path: Optional[str] = None):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            if db_path:
                cls._db_path = db_path
        return cls._instance
    
    def __init__(self, db_path: Optional[str] = None):
        """初始化数据库路径"""
        if db_path:
            self._db_path = db_path
        self._ensure_database_exists()
    
    def _get_connection(self):
        """获取线程局部的数据库连接（启用 WAL 模式提升并发性能）"""
        if not hasattr(self._local, 'connection'):
            conn = sqlite3.connect(
                self._db_path,
                check_same_thread=False,
                timeout=30,  # 锁等待超时 30s
            )
            conn.row_factory = sqlite3.Row
            # 启用 WAL 模式：读写并发不互斥，性能提升 10x+
            conn.execute("PRAGMA journal_mode=WAL")
            # NORMAL：每次 commit 不强制 fsync，WAL 模式下数据安全仍有保障
            conn.execute("PRAGMA synchronous=NORMAL")
            # WAL 自动检查点阈值（默认 1000 页，适当增大减少 checkpoint 频率）
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            # 临时表和索引存内存，减少磁盘 IO
            conn.execute("PRAGMA temp_store=MEMORY")
            # 缓存大小 20MB（默认 2MB），减少磁盘读取
            conn.execute("PRAGMA cache_size=-20000")
            # 外键约束开启
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.connection = conn
            logger.info(f"✓ SQLite 连接已建立 (WAL 模式, cache=20MB, db={self._db_path})")
        return self._local.connection
    
    def _ensure_database_exists(self):
        """确保数据库文件存在，并创建基础表"""
        try:
            conn = self._get_connection()
            cursor = conn.cursor()
            
            # 创建用户表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    email TEXT UNIQUE,
                    password_hash TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # 确保 password_hash 列存在（向后兼容旧表）
            try:
                cursor.execute("ALTER TABLE users ADD COLUMN password_hash TEXT")
            except:
                pass
            
            # 创建记忆变量表
            cursor.execute('''
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
            ''')
            
            # 创建记忆表（动态表结构存储在 metadata 中）
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS memory_tables (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    table_name TEXT NOT NULL,
                    table_schema TEXT NOT NULL,  -- JSON格式存储表结构
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, table_name),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')
            
            # 创建记忆片段表
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS memory_fragments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    fragment_type TEXT NOT NULL,  -- info, preference, plan
                    content TEXT NOT NULL,
                    embedding_id TEXT,  -- 关联向量数据库ID
                    ttl INTEGER,  -- 过期时间（秒）
                    importance_score REAL DEFAULT 0.5,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP,  -- 计算后的过期时间
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            ''')
            
            # 创建索引
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_memory_variables_user ON memory_variables(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_memory_tables_user ON memory_tables(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_memory_fragments_user ON memory_fragments(user_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_memory_fragments_expires ON memory_fragments(expires_at)')

            # ============================================================
            # Memory Lifecycle 生命周期管理表
            # ============================================================

            # memory_lifecycle: 记忆生命周期状态跟踪
            cursor.execute('''
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
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_lifecycle_user_status
                ON memory_lifecycle(user_id, lifecycle_status)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_lifecycle_status
                ON memory_lifecycle(lifecycle_status)
            ''')

            # memory_delete_log: 删除操作审计日志
            cursor.execute('''
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
            ''')

            # memory_merge_log: 合并与冲突操作审计日志
            cursor.execute('''
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
            ''')

            # ============================================================
            # 兼容性迁移：为 memory_fragments 增加生命周期字段
            # ============================================================
            for col, col_type in [
                ("lifecycle_status", "TEXT DEFAULT 'active'"),
                ("last_recalled_at", "TIMESTAMP"),
                ("cold_at", "TIMESTAMP"),
            ]:
                try:
                    cursor.execute(f"ALTER TABLE memory_fragments ADD COLUMN {col} {col_type}")
                except Exception:
                    pass

            # ============================================================
            # Graph Memory 知识图谱表
            # ============================================================

            # graph_entities: 实体节点
            cursor.execute('''
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
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_graph_entities_user ON graph_entities(user_id, entity_type)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_graph_entities_name ON graph_entities(user_id, name)')

            # graph_relationships: 关系边表（邻接表 + 时序）
            cursor.execute('''
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
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_graph_rels_source ON graph_relationships(source_entity_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_graph_rels_target ON graph_relationships(target_entity_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_graph_rels_active ON graph_relationships(user_id, relation_type, is_active)')

            # graph_relationship_history: 关系变更历史（时序追踪）
            cursor.execute('''
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
            ''')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_graph_history_rel ON graph_relationship_history(relationship_id)')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_graph_history_entity
                ON graph_relationship_history(source_entity_id, target_entity_id)
            ''')

            # ============================================================
            # Memory Observability（观测性）
            # ============================================================

            cursor.execute('''
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
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_trace_user
                ON memory_trace_events(user_id, created_at)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_trace_memory
                ON memory_trace_events(memory_id, event_type)
            ''')

            cursor.execute('''
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
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_snapshot_user
                ON memory_metrics_snapshots(user_id, snapshot_time)
            ''')

            cursor.execute('''
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
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_quality_memory
                ON memory_quality_evaluations(user_id, memory_id)
            ''')

            cursor.execute('''
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
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_extraction_user
                ON memory_extraction_triggers(user_id, created_at)
            ''')

            # ============================================================
            # Performance Metrics（性能指标）
            # ============================================================

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    metric_type TEXT(32) NOT NULL,
                    endpoint TEXT,
                    value REAL,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_perf_metrics_user_type_time
                ON performance_metrics(user_id, metric_type, created_at)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_perf_metrics_user_time
                ON performance_metrics(user_id, created_at)
            ''')

            # ============================================================
            # Memory Extraction Feedback & Prompt Templates
            # ============================================================

            # extraction_feedback: 抽取结果用户反馈记录
            cursor.execute('''
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
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_extraction_feedback_user
                ON extraction_feedback(user_id, created_at)
            ''')

            # extraction_prompt_templates: 自定义抽取 Prompt 模板
            cursor.execute('''
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
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_extraction_templates_user
                ON extraction_prompt_templates(user_id, is_active)
            ''')

            # ============================================================
            # FTS5 全文搜索索引（关键词匹配）
            # ============================================================
            try:
                cursor.execute('''
                    CREATE VIRTUAL TABLE IF NOT EXISTS fragments_fts USING fts5(
                        content, fragment_type,
                        tokenize='unicode61'
                    )
                ''')
                cursor.execute('''
                    INSERT OR IGNORE INTO fragments_fts(rowid, content, fragment_type)
                    SELECT id, content, fragment_type FROM memory_fragments
                ''')
            except Exception as e:
                logger.warning(f"FTS5 虚拟表创建失败: {e}")
            for trig_sql in [
                '''CREATE TRIGGER IF NOT EXISTS fragments_fts_ai AFTER INSERT ON memory_fragments BEGIN
                    INSERT INTO fragments_fts(rowid, content, fragment_type)
                    VALUES (new.id, new.content, new.fragment_type);
                END;''',
                '''CREATE TRIGGER IF NOT EXISTS fragments_fts_ad AFTER DELETE ON memory_fragments BEGIN
                    DELETE FROM fragments_fts WHERE rowid = old.id;
                END;''',
                '''CREATE TRIGGER IF NOT EXISTS fragments_fts_au AFTER UPDATE ON memory_fragments BEGIN
                    DELETE FROM fragments_fts WHERE rowid = old.id;
                    INSERT INTO fragments_fts(rowid, content, fragment_type)
                    VALUES (new.id, new.content, new.fragment_type);
                END;''',
            ]:
                try:
                    cursor.execute(trig_sql)
                except Exception as e:
                    logger.warning(f"FTS5 触发器创建失败: {e}")

            conn.commit()
            logger.info(f"✓ 数据库初始化完成: {self._db_path}")

        except Exception as e:
            logger.error(f"✗ 数据库初始化失败: {e}")
            raise


    
    @contextmanager
    def get_cursor(self):
        """获取数据库游标（上下文管理器）"""
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"数据库操作失败: {e}")
            raise
        finally:
            cursor.close()
    
    def execute(self, sql: str, params: tuple = ()) -> Any:
        """
        执行 SQL 语句
        
        Args:
            sql: SQL 语句
            params: 参数元组
        
        Returns:
            查询结果或最后插入的行ID
        """
        try:
            with self.get_cursor() as cursor:
                cursor.execute(sql, params)
                
                upper_sql = sql.strip().upper()
                if upper_sql.startswith('SELECT') or upper_sql.startswith('PRAGMA') or upper_sql.startswith('EXPLAIN'):
                    return cursor.fetchall()
                elif upper_sql.startswith('INSERT'):
                    return cursor.lastrowid
                else:
                    return cursor.rowcount
        except Exception as e:
            logger.error(f"✗ SQL 执行失败: {sql[:100]}. ")
            raise
    
    def create_user(self, username: str, email: Optional[str] = None) -> int:
        """创建用户"""
        try:
            with self.get_cursor() as cursor:
                cursor.execute(
                    'INSERT INTO users (username, email) VALUES (?, ?)',
                    (username, email)
                )
                return cursor.lastrowid
        except sqlite3.IntegrityError:
            # 用户已存在，返回现有用户ID
            result = self.execute('SELECT id FROM users WHERE username = ?', (username,))
            return result[0]['id'] if result else None
    
    def create_memory_variable(self, user_id: int, key: str, value: Any) -> bool:
        """创建或更新记忆变量"""
        try:
            import json
            value_str = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
            
            self.execute('''
                INSERT INTO memory_variables (user_id, key, value)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id, key) 
                DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            ''', (user_id, key, value_str))
            return True
        except Exception as e:
            logger.error(f"✗ 创建记忆变量失败: {e}")
            return False
    
    def get_memory_variable(self, user_id: int, key: str) -> Optional[Any]:
        """获取记忆变量"""
        try:
            import json
            result = self.execute(
                'SELECT value FROM memory_variables WHERE user_id = ? AND key = ?',
                (user_id, key)
            )
            
            if not result:
                return None
            
            value_str = result[0]['value']
            try:
                return json.loads(value_str)
            except (json.JSONDecodeError, TypeError):
                return value_str
        except Exception as e:
            logger.error(f"✗ 获取记忆变量失败: {e}")
            return None
    
    def create_memory_table(self, user_id: int, table_name: str, schema: Dict,
                            workspace_id: Optional[int] = None) -> bool:
        """创建记忆表（动态表结构）元数据，按 workspace 隔离。

        采用手动 upsert（先查后写）而非 ON CONFLICT，避免对唯一约束
        的硬依赖（workspace_id 可为 NULL，NULL 在唯一索引中不参与去重）。
        """
        try:
            import json
            schema_str = json.dumps(schema, ensure_ascii=False)
            
            if workspace_id is None:
                existing = self.execute(
                    'SELECT id FROM memory_tables '
                    'WHERE user_id = ? AND table_name = ? AND workspace_id IS NULL',
                    (user_id, table_name),
                )
            else:
                existing = self.execute(
                    'SELECT id FROM memory_tables '
                    'WHERE user_id = ? AND table_name = ? AND workspace_id = ?',
                    (user_id, table_name, workspace_id),
                )
            
            if existing:
                self.execute(
                    'UPDATE memory_tables SET table_schema = ?, '
                    'updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                    (schema_str, existing[0]["id"]),
                )
            else:
                self.execute(
                    'INSERT INTO memory_tables (user_id, workspace_id, table_name, table_schema) '
                    'VALUES (?, ?, ?, ?)',
                    (user_id, workspace_id, table_name, schema_str),
                )
            return True
        except Exception as e:
            logger.error(f"✗ 创建记忆表失败: {e}")
            return False
    
    def create_memory_fragment(self, user_id: int, fragment_type: str, content: str, 
                             ttl: Optional[int] = None, importance_score: float = 0.5) -> int:
        """创建记忆片段"""
        try:
            import sqlite3
            from datetime import datetime, timedelta
            
            expires_at = None
            if ttl:
                expires_at = datetime.now() + timedelta(seconds=ttl)
            
            fragment_id = self.execute('''
                INSERT INTO memory_fragments (user_id, fragment_type, content, ttl, importance_score, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (user_id, fragment_type, content, ttl, importance_score, expires_at))
            
            return fragment_id
        except Exception as e:
            logger.error(f"✗ 创建记忆片段失败: {e}")
            return None
    
    def get_memory_fragments(self, user_id: int, fragment_type: Optional[str] = None, 
                           limit: int = 100) -> List[Dict]:
        """获取记忆片段（自动过滤过期数据）"""
        try:
            from datetime import datetime
            
            # 先删除过期数据
            self.execute('''
                DELETE FROM memory_fragments 
                WHERE expires_at IS NOT NULL AND expires_at < ?
            ''', (datetime.now(),))
            
            # 查询数据
            if fragment_type:
                result = self.execute('''
                    SELECT * FROM memory_fragments 
                    WHERE user_id = ? AND fragment_type = ?
                    ORDER BY importance_score DESC, created_at DESC
                    LIMIT ?
                ''', (user_id, fragment_type, limit))
            else:
                result = self.execute('''
                    SELECT * FROM memory_fragments 
                    WHERE user_id = ?
                    ORDER BY importance_score DESC, created_at DESC
                    LIMIT ?
                ''', (user_id, limit))
            
            return [dict(row) for row in result] if result else []
        except Exception as e:
            logger.error(f"✗ 获取记忆片段失败: {e}")
            return []
    
    def close(self):
        """关闭数据库连接"""
        if hasattr(self._local, 'connection'):
            self._local.connection.close()
            del self._local.connection
            logger.info("✓ 数据库连接已关闭")


# 全局客户端实例（按 DATABASE_URL 选择后端，惰性单例）
_db_client = None
_db_client_lock = threading.Lock()

_PG_PREFIXES = ("postgres://", "postgresql://", "postgres+", "postgresql+")


def _create_db_client():
    """根据 settings.DATABASE_URL 选择数据库后端。

    - postgres/postgresql -> PostgresClient（生产多副本共享存储）
    - 其它（含空值 / sqlite://）-> SQLiteClient（开发 / 测试）
    """
    database_url = None
    try:
        from app.core.config import get_settings
        database_url = getattr(get_settings(), "DATABASE_URL", None)
    except Exception as e:
        logger.warning(f"读取 DATABASE_URL 失败，回退 SQLite: {e}")

    if database_url and database_url.strip().lower().startswith(_PG_PREFIXES):
        from app.core.pg_client import PostgresClient
        logger.info("使用 PostgreSQL 数据库后端")
        return PostgresClient(database_url)

    logger.info("使用 SQLite 数据库后端")
    return SQLiteClient()


def get_db_client():
    """获取数据库客户端实例（惰性单例，按 DATABASE_URL 选择 SQLite / PostgreSQL）。"""
    global _db_client
    if _db_client is None:
        with _db_client_lock:
            if _db_client is None:
                _db_client = _create_db_client()
    return _db_client


def test_db_connection():
    """测试数据库连接和基本操作"""
    print("\n" + "="*60)
    print("测试 SQLite 数据库连接和 CRUD 操作")
    print("="*60 + "\n")
    
    client = get_db_client()
    
    # 测试创建用户
    print("1. 测试创建用户...")
    # 先检查用户是否已存在
    existing_user = client.execute('SELECT id FROM users WHERE username = ?', ("鑫海",))
    if existing_user:
        user_id = existing_user[0]['id']
        print(f"   ✓ 用户已存在，ID: {user_id}")
    else:
        user_id = client.create_user("鑫海", "xinhai@example.com")
        print(f"   ✓ 创建用户 ID: {user_id}")
    
    # 测试记忆变量 CRUD
    print("\n2. 测试记忆变量 CRUD...")
    client.create_memory_variable(user_id, "user_name", "鑫海")
    client.create_memory_variable(user_id, "user_role", "PM")
    client.create_memory_variable(user_id, "user_projects", ["源启·智能体工厂", "Agent星图"])
    
    name = client.get_memory_variable(user_id, "user_name")
    role = client.get_memory_variable(user_id, "user_role")
    projects = client.get_memory_variable(user_id, "user_projects")
    print(f"   ✓ 用户名: {name}")
    print(f"   ✓ 用户角色: {role}")
    print(f"   ✓ 用户项目: {projects}")
    
    # 测试记忆表创建
    print("\n3. 测试记忆表结构定义...")
    table_schema = {
        "fields": [
            {"name": "project_name", "type": "TEXT"},
            {"name": "负责人", "type": "TEXT"},
            {"name": "status", "type": "TEXT"}
        ]
    }
    client.create_memory_table(user_id, "projects", table_schema)
    print(f"   ✓ 创建记忆表: projects")
    print(f"   ✓ 表结构: {table_schema}")
    
    # 测试记忆片段 CRUD
    print("\n4. 测试记忆片段 CRUD...")
    fragment_id = client.create_memory_fragment(
        user_id, 
        "preference", 
        "我喜欢极简设计风格", 
        ttl=30*24*3600,  # 30天过期
        importance_score=0.9
    )
    print(f"   ✓ 创建记忆片段 ID: {fragment_id}")
    
    fragments = client.get_memory_fragments(user_id, fragment_type="preference")
    print(f"   ✓ 查询记忆片段: {len(fragments)} 条")
    for frag in fragments[:3]:
        print(f"      - {frag['content']} (重要性: {frag['importance_score']})")
    
    # 性能测试
    print("\n5. 性能测试（1000次读写）...")
    import time
    start = time.time()
    for i in range(1000):
        client.create_memory_variable(user_id, f"perf:key:{i}", f"value_{i}")
        client.get_memory_variable(user_id, f"perf:key:{i}")
    elapsed = time.time() - start
    print(f"   ✓ 1000次读写耗时: {elapsed:.3f}秒")
    print(f"   ✓ 平均延迟: {elapsed/1000*1000:.2f}毫秒")
    
    print("\n" + "="*60)
    print("✅ SQLite 数据库测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    test_db_connection()
