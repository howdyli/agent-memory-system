"""
数据库核心 Schema 的单一数据源（Single Source of Truth）。

历史上 DDL 内联在 SQLiteClient._ensure_database_exists 中，导致 SQLite 与
PostgreSQL 两套 schema 无法共享、易漂移。此模块把核心表结构抽取为规范的
语句列表：

- SQLite 方言为规范源（canonical）。SQLiteClient 直接执行。
- PostgresClient 在执行前通过 pg_client._translate_sql 做方言翻译。

注意：webhooks / sessions / api_keys / workspaces / rbac 等表由各自的
service 首次使用时通过 `CREATE TABLE IF NOT EXISTS` 惰性创建，同样会经过
PostgresClient 的翻译层，因此无需在此重复声明。
"""
from typing import List

# ============================================================
# 核心表与索引（按依赖顺序）——SQLite 方言，规范源
# ============================================================
CORE_DDL: List[str] = [
    # 用户表
    '''
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE,
        password_hash TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''',
    # 记忆变量表
    '''
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
    ''',
    # 记忆表（动态表结构存储在 metadata 中）
    '''
    CREATE TABLE IF NOT EXISTS memory_tables (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        workspace_id INTEGER,
        table_name TEXT NOT NULL,
        table_schema TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, workspace_id, table_name),
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    ''',
    # 记忆片段表
    '''
    CREATE TABLE IF NOT EXISTS memory_fragments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        workspace_id INTEGER,
        fragment_type TEXT NOT NULL,
        content TEXT NOT NULL,
        embedding_id TEXT,
        ttl INTEGER,
        importance_score REAL DEFAULT 0.5,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )
    ''',
    'CREATE INDEX IF NOT EXISTS idx_memory_variables_user ON memory_variables(user_id)',
    'CREATE INDEX IF NOT EXISTS idx_memory_tables_user ON memory_tables(user_id)',
    'CREATE INDEX IF NOT EXISTS idx_memory_fragments_user ON memory_fragments(user_id)',
    'CREATE INDEX IF NOT EXISTS idx_memory_fragments_expires ON memory_fragments(expires_at)',
    # ---- Memory Lifecycle 生命周期管理 ----
    '''
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
    ''',
    'CREATE INDEX IF NOT EXISTS idx_lifecycle_user_status ON memory_lifecycle(user_id, lifecycle_status)',
    'CREATE INDEX IF NOT EXISTS idx_lifecycle_status ON memory_lifecycle(lifecycle_status)',
    # memory_delete_log: 删除操作审计日志
    '''
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
    ''',
    # memory_merge_log: 合并与冲突操作审计日志
    '''
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
    ''',
    # ---- Graph Memory 知识图谱 ----
    '''
    CREATE TABLE IF NOT EXISTS graph_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        workspace_id INTEGER,
        name TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        aliases TEXT,
        metadata TEXT,
        first_seen_at TIMESTAMP,
        last_seen_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id),
        UNIQUE(user_id, workspace_id, name, entity_type)
    )
    ''',
    'CREATE INDEX IF NOT EXISTS idx_graph_entities_user ON graph_entities(user_id, entity_type)',
    'CREATE INDEX IF NOT EXISTS idx_graph_entities_name ON graph_entities(user_id, name)',
    '''
    CREATE TABLE IF NOT EXISTS graph_relationships (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        workspace_id INTEGER,
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
    ''',
    'CREATE INDEX IF NOT EXISTS idx_graph_rels_source ON graph_relationships(source_entity_id)',
    'CREATE INDEX IF NOT EXISTS idx_graph_rels_target ON graph_relationships(target_entity_id)',
    'CREATE INDEX IF NOT EXISTS idx_graph_rels_active ON graph_relationships(user_id, relation_type, is_active)',
    '''
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
    ''',
    'CREATE INDEX IF NOT EXISTS idx_graph_history_rel ON graph_relationship_history(relationship_id)',
    'CREATE INDEX IF NOT EXISTS idx_graph_history_entity ON graph_relationship_history(source_entity_id, target_entity_id)',
    # ---- Memory Observability 观测性 ----
    '''
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
    ''',
    'CREATE INDEX IF NOT EXISTS idx_trace_user ON memory_trace_events(user_id, created_at)',
    'CREATE INDEX IF NOT EXISTS idx_trace_memory ON memory_trace_events(memory_id, event_type)',
    '''
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
    ''',
    'CREATE INDEX IF NOT EXISTS idx_snapshot_user ON memory_metrics_snapshots(user_id, snapshot_time)',
    '''
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
    ''',
    'CREATE INDEX IF NOT EXISTS idx_quality_memory ON memory_quality_evaluations(user_id, memory_id)',
    '''
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
    ''',
    'CREATE INDEX IF NOT EXISTS idx_extraction_user ON memory_extraction_triggers(user_id, created_at)',
    # ---- Performance Metrics 性能指标 ----
    '''
    CREATE TABLE IF NOT EXISTS performance_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        metric_type TEXT(32) NOT NULL,
        endpoint TEXT,
        value REAL,
        metadata TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''',
    'CREATE INDEX IF NOT EXISTS idx_perf_metrics_user_type_time ON performance_metrics(user_id, metric_type, created_at)',
    'CREATE INDEX IF NOT EXISTS idx_perf_metrics_user_time ON performance_metrics(user_id, created_at)',
    # ---- Extraction Feedback & Prompt Templates ----
    '''
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
    ''',
    'CREATE INDEX IF NOT EXISTS idx_extraction_feedback_user ON extraction_feedback(user_id, created_at)',
    '''
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
    ''',
    'CREATE INDEX IF NOT EXISTS idx_extraction_templates_user ON extraction_prompt_templates(user_id, is_active)',
]

# ============================================================
# 兼容性 ALTER（对既有旧表补列，失败即忽略）
# ============================================================
COMPAT_ALTERS: List[str] = [
    "ALTER TABLE users ADD COLUMN password_hash TEXT",
    "ALTER TABLE memory_fragments ADD COLUMN lifecycle_status TEXT DEFAULT 'active'",
    "ALTER TABLE memory_fragments ADD COLUMN last_recalled_at TIMESTAMP",
    "ALTER TABLE memory_fragments ADD COLUMN cold_at TIMESTAMP",
    # workspace 隔离：对旧表补 workspace_id 列（失败即忽略）
    "ALTER TABLE memory_tables ADD COLUMN workspace_id INTEGER",
    "ALTER TABLE memory_fragments ADD COLUMN workspace_id INTEGER",
    "ALTER TABLE graph_entities ADD COLUMN workspace_id INTEGER",
    "ALTER TABLE graph_relationships ADD COLUMN workspace_id INTEGER",
]

# ============================================================
# FTS5 全文搜索（仅 SQLite；PostgreSQL 端由 hybrid_search 走 ILIKE 兜底）
# ============================================================
FTS_TABLE_DDL: str = '''
    CREATE VIRTUAL TABLE IF NOT EXISTS fragments_fts USING fts5(
        content, fragment_type,
        tokenize='unicode61'
    )
'''

FTS_SEED_DDL: str = '''
    INSERT OR IGNORE INTO fragments_fts(rowid, content, fragment_type)
    SELECT id, content, fragment_type FROM memory_fragments
'''

FTS_TRIGGERS: List[str] = [
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
]
