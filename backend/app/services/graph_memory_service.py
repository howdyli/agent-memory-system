"""
Graph Memory - 轻量级图记忆模块

基于 SQLite + 邻接表实现，支持：
1. 实体抽取与关系抽取
2. 图遍历查询（邻居节点）
3. 时序追踪（关系变化历史）
"""
import logging
import json
import re
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client

# ============================================================
# 支持的实体类型
# ============================================================
ENTITY_TYPES = ["person", "organization", "location", "event"]

# ============================================================
# 支持的关系类型（中英文映射）
# ============================================================
RELATION_TYPE_MAP = {
    # 同事
    "同事": "colleague",
    "colleague": "colleague",
    "collaborator": "colleague",
    # 朋友
    "朋友": "friend",
    "friend": "friend",
    # 上下级
    "上级": "superior",
    "superior": "superior",
    "领导": "superior",
    "经理": "superior",
    "老板": "superior",
    "下属": "subordinate",
    "subordinate": "subordinate",
    "下级": "subordinate",
    "汇报给": "subordinate",
    # 项目关联
    "项目": "project_member",
    "项目成员": "project_member",
    "project_member": "project_member",
    "同事关系": "project_member",
    # 其他
    "家庭成员": "family",
    "family": "family",
    "家人": "family",
    "配偶": "family",
    "同学": "classmate",
    "classmate": "classmate",
    "校友": "classmate",
    "师兄弟": "classmate",
    "师生": "mentor",
    "mentor": "mentor",
    "老师": "mentor",
    "学生": "mentee",
    "mentee": "mentee",
}


def _normalize_relation_type(relation_type: str) -> str:
    """规范化关系类型，支持中文/英文输入"""
    return RELATION_TYPE_MAP.get(relation_type, relation_type)


def _ensure_graph_tables():
    """确保图谱相关表存在（首次使用保障）"""
    db = get_db_client()
    for sql in [
        '''CREATE TABLE IF NOT EXISTS graph_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            name TEXT NOT NULL, entity_type TEXT NOT NULL,
            aliases TEXT, metadata TEXT,
            first_seen_at TIMESTAMP, last_seen_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            UNIQUE(user_id, name, entity_type)
        )''',
        '''CREATE TABLE IF NOT EXISTS graph_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            source_entity_id INTEGER NOT NULL, target_entity_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL, relation_subtype TEXT,
            properties TEXT, confidence REAL DEFAULT 0.5,
            valid_from TIMESTAMP, valid_to TIMESTAMP,
            is_active INTEGER DEFAULT 1, extraction_source TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (source_entity_id) REFERENCES graph_entities(id),
            FOREIGN KEY (target_entity_id) REFERENCES graph_entities(id)
        )''',
        '''CREATE TABLE IF NOT EXISTS graph_relationship_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            relationship_id INTEGER,
            source_entity_id INTEGER NOT NULL, target_entity_id INTEGER NOT NULL,
            relation_type TEXT NOT NULL,
            old_properties TEXT, new_properties TEXT,
            valid_from TIMESTAMP, valid_to TIMESTAMP,
            change_reason TEXT,
            changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''',
    ]:
        try:
            db.execute(sql)
        except Exception:
            pass


# ============================================================
# 1. 实体管理
# ============================================================

def ensure_entity(
    user_id: int,
    name: str,
    entity_type: str,
    aliases: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    创建或返回已有实体。

    如果 (user_id, name, entity_type) 已存在则返回现有记录，
    否则创建新实体。

    Args:
        user_id: 用户 ID
        name: 实体名称
        entity_type: 实体类型 (person, organization, location, event)
        aliases: 别名列表
        metadata: 元数据字典

    Returns:
        实体信息（含 id）
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()
        now = datetime.now().isoformat()

        # 尝试查找现有实体
        rows = db.execute(
            '''SELECT * FROM graph_entities
               WHERE user_id = ? AND name = ? AND entity_type = ?''',
            (user_id, name, entity_type)
        )

        if rows:
            entity = dict(rows[0])
            # 更新 last_seen_at
            db.execute(
                'UPDATE graph_entities SET last_seen_at = ?, updated_at = ? WHERE id = ?',
                (now, now, entity["id"])
            )
            # 合并别名
            if aliases:
                existing_aliases = set()
                try:
                    if entity.get("aliases"):
                        existing_aliases = set(json.loads(entity["aliases"]))
                except (json.JSONDecodeError, TypeError):
                    pass
                new_aliases = list(existing_aliases | set(aliases))
                if new_aliases and new_aliases != json.loads(entity.get("aliases", "[]") or "[]"):
                    db.execute(
                        'UPDATE graph_entities SET aliases = ? WHERE id = ?',
                        (json.dumps(new_aliases, ensure_ascii=False), entity["id"])
                    )
                    entity["aliases"] = json.dumps(new_aliases, ensure_ascii=False)
            entity["created"] = False
            return entity

        # 创建新实体
        aliases_json = json.dumps(aliases, ensure_ascii=False) if aliases else None
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else None

        entity_id = db.execute(
            '''INSERT INTO graph_entities
               (user_id, name, entity_type, aliases, metadata, first_seen_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (user_id, name, entity_type, aliases_json, metadata_json, now, now)
        )

        logger.info(f"✓ 创建实体: {name} ({entity_type}) id={entity_id}")
        return {
            "id": entity_id,
            "user_id": user_id,
            "name": name,
            "entity_type": entity_type,
            "aliases": aliases,
            "metadata": metadata,
            "first_seen_at": now,
            "last_seen_at": now,
            "created": True,
        }

    except Exception as e:
        logger.error(f"✗ 确保实体失败: {e}")
        return {"success": False, "error": str(e)}


def search_entities(
    user_id: int,
    query: str,
    entity_type: Optional[str] = None,
    limit: int = 20,
) -> Dict[str, Any]:
    """
    按名称模糊搜索实体。

    Args:
        user_id: 用户 ID
        query: 搜索关键词
        entity_type: 过滤实体类型（可选）
        limit: 返回数量

    Returns:
        实体列表
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()
        like_pattern = f"%{query}%"

        if entity_type:
            rows = db.execute(
                '''SELECT * FROM graph_entities
                   WHERE user_id = ? AND entity_type = ? AND name LIKE ?
                   ORDER BY last_seen_at DESC LIMIT ?''',
                (user_id, entity_type, like_pattern, limit)
            )
        else:
            rows = db.execute(
                '''SELECT * FROM graph_entities
                   WHERE user_id = ? AND name LIKE ?
                   ORDER BY last_seen_at DESC LIMIT ?''',
                (user_id, like_pattern, limit)
            )

        entities = [dict(r) for r in rows] if rows else []

        return {
            "success": True,
            "entities": entities,
            "count": len(entities),
        }

    except Exception as e:
        logger.error(f"✗ 搜索实体失败: {e}")
        return {"success": False, "error": str(e)}


def get_entity(
    user_id: int,
    entity_id: int,
) -> Dict[str, Any]:
    """
    获取实体详情（含关联关系计数）。

    Args:
        user_id: 用户 ID
        entity_id: 实体 ID

    Returns:
        实体详情
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()

        rows = db.execute(
            '''SELECT * FROM graph_entities WHERE id = ? AND user_id = ?''',
            (entity_id, user_id)
        )
        if not rows:
            return {"success": False, "error": "实体未找到"}

        entity = dict(rows[0])

        # 统计关系
        source_cnt = db.execute(
            '''SELECT COUNT(*) as cnt FROM graph_relationships
               WHERE source_entity_id = ? AND is_active = 1''',
            (entity_id,)
        )
        target_cnt = db.execute(
            '''SELECT COUNT(*) as cnt FROM graph_relationships
               WHERE target_entity_id = ? AND is_active = 1''',
            (entity_id,)
        )

        entity["relation_counts"] = {
            "as_source": source_cnt[0]["cnt"] if source_cnt else 0,
            "as_target": target_cnt[0]["cnt"] if target_cnt else 0,
            "total": (source_cnt[0]["cnt"] if source_cnt else 0)
                     + (target_cnt[0]["cnt"] if target_cnt else 0),
        }

        return {"success": True, "entity": entity}

    except Exception as e:
        logger.error(f"✗ 获取实体失败: {e}")
        return {"success": False, "error": str(e)}


def merge_entities(
    user_id: int,
    target_id: int,
    source_ids: List[int],
) -> Dict[str, Any]:
    """
    合并重复实体节点。

    将 source_ids 实体的所有关系重定向到 target_id，
    然后删除 source_ids 实体。

    Args:
        user_id: 用户 ID
        target_id: 目标实体 ID（保留）
        source_ids: 源实体 ID 列表（被合并）

    Returns:
        合并结果
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()

        if not source_ids:
            return {"success": False, "error": "源实体列表为空"}
        if target_id in source_ids:
            return {"success": False, "error": "目标实体不能在源列表中"}

        # 检查目标实体是否存在
        rows = db.execute(
            'SELECT * FROM graph_entities WHERE id = ? AND user_id = ?',
            (target_id, user_id)
        )
        if not rows:
            return {"success": False, "error": "目标实体未找到"}

        redirected = 0
        for sid in source_ids:
            # 重定向 source_entity_id
            r1 = db.execute(
                '''UPDATE graph_relationships SET source_entity_id = ?
                   WHERE source_entity_id = ? AND user_id = ?''',
                (target_id, sid, user_id)
            )
            # 重定向 target_entity_id
            r2 = db.execute(
                '''UPDATE graph_relationships SET target_entity_id = ?
                   WHERE target_entity_id = ? AND user_id = ?''',
                (target_id, sid, user_id)
            )
            # 删除重复实体
            db.execute(
                'DELETE FROM graph_entities WHERE id = ? AND user_id = ?',
                (sid, user_id)
            )
            redirected += 1

        logger.info(f"✓ 合并实体: {len(source_ids)} 条 → {target_id}")
        return {
            "success": True,
            "target_id": target_id,
            "merged_count": redirected,
            "message": f"Merged {redirected} entities into {target_id}",
        }

    except Exception as e:
        logger.error(f"✗ 合并实体失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 2. 关系管理
# ============================================================

def _record_relationship_history(
    user_id: int,
    relationship_id: int,
    source_entity_id: int,
    target_entity_id: int,
    relation_type: str,
    old_properties: Optional[str] = None,
    new_properties: Optional[str] = None,
    valid_from: Optional[str] = None,
    valid_to: Optional[str] = None,
    change_reason: str = "created",
) -> int:
    """记录关系变更历史"""
    db = get_db_client()
    return db.execute(
        '''INSERT INTO graph_relationship_history
           (user_id, relationship_id, source_entity_id, target_entity_id,
            relation_type, old_properties, new_properties,
            valid_from, valid_to, change_reason)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (user_id, relationship_id, source_entity_id, target_entity_id,
         relation_type, old_properties, new_properties,
         valid_from, valid_to, change_reason)
    )


def add_relationship(
    user_id: int,
    source_name: str,
    target_name: str,
    relation_type: str,
    source_type: str = "person",
    target_type: str = "organization",
    relation_subtype: Optional[str] = None,
    properties: Optional[Dict[str, Any]] = None,
    confidence: float = 0.5,
    valid_from: Optional[str] = None,
    extraction_source: str = "manual",
) -> Dict[str, Any]:
    """
    创建两个实体之间的关系。

    自动 ensure 两个实体，创建关系边，记录历史。

    Args:
        user_id: 用户 ID
        source_name: 源实体名称
        target_name: 目标实体名称
        relation_type: 关系类型（colleague, friend, superior, 等）
        source_type: 源实体类型
        target_type: 目标实体类型
        relation_subtype: 关系子类型描述
        properties: 关系属性字典
        confidence: 置信度 (0-1)
        valid_from: 关系生效时间（ISO 格式，默认当前）
        extraction_source: 来源

    Returns:
        创建结果
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()

        # 1. 确保两个实体存在
        src_result = ensure_entity(user_id, source_name, source_type)
        if not src_result.get("id"):
            return {"success": False, "error": f"源实体创建失败: {src_result.get('error')}"}

        tgt_result = ensure_entity(user_id, target_name, target_type)
        if not tgt_result.get("id"):
            return {"success": False, "error": f"目标实体创建失败: {tgt_result.get('error')}"}

        source_id = src_result["id"]
        target_id = tgt_result["id"]
        normalized_type = _normalize_relation_type(relation_type)

        now = datetime.now().isoformat()
        properties_json = json.dumps(properties, ensure_ascii=False) if properties else None

        # 2. 检查是否已存在同样关系（避免重复）
        existing = db.execute(
            '''SELECT * FROM graph_relationships
               WHERE user_id = ? AND source_entity_id = ?
               AND target_entity_id = ? AND relation_type = ?
               AND is_active = 1''',
            (user_id, source_id, target_id, normalized_type)
        )

        if existing:
            rel = dict(existing[0])
            logger.info(f"关系已存在: {source_name} -[{normalized_type}]-> {target_name}")
            return {
                "success": True,
                "relationship_id": rel["id"],
                "created": False,
                "message": "关系已存在",
                "source": src_result,
                "target": tgt_result,
            }

        # 3. 创建关系
        rel_id = db.execute(
            '''INSERT INTO graph_relationships
               (user_id, source_entity_id, target_entity_id, relation_type,
                relation_subtype, properties, confidence, valid_from,
                extraction_source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, source_id, target_id, normalized_type,
             relation_subtype, properties_json, confidence,
             valid_from or now, extraction_source)
        )

        # 4. 记录历史
        try:
            _record_relationship_history(
                user_id=user_id,
                relationship_id=rel_id,
                source_entity_id=source_id,
                target_entity_id=target_id,
                relation_type=normalized_type,
                new_properties=properties_json,
                valid_from=valid_from or now,
                change_reason="created",
            )
        except Exception as e:
            logger.warning(f"历史记录写入失败: {e}")

        logger.info(f"✓ 创建关系: {source_name} -[{normalized_type}]-> {target_name}")
        return {
            "success": True,
            "relationship_id": rel_id,
            "created": True,
            "relation_type": normalized_type,
            "source": src_result,
            "target": tgt_result,
            "valid_from": valid_from or now,
        }

    except Exception as e:
        logger.error(f"✗ 创建关系失败: {e}")
        return {"success": False, "error": str(e)}


def deactivate_relationship(
    user_id: int,
    relationship_id: int,
    reason: str = "ended",
) -> Dict[str, Any]:
    """
    结束一个关系。

    将 is_active 置为 0，记录 valid_to，写入历史。

    Args:
        user_id: 用户 ID
        relationship_id: 关系 ID
        reason: 结束原因

    Returns:
        操作结果
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()
        now = datetime.now().isoformat()

        rows = db.execute(
            '''SELECT * FROM graph_relationships WHERE id = ? AND user_id = ?''',
            (relationship_id, user_id)
        )
        if not rows:
            return {"success": False, "error": "关系未找到"}

        rel = dict(rows[0])

        # 更新状态
        db.execute(
            '''UPDATE graph_relationships SET is_active = 0, valid_to = ?
               WHERE id = ?''',
            (now, relationship_id)
        )

        # 记录历史
        try:
            _record_relationship_history(
                user_id=user_id,
                relationship_id=relationship_id,
                source_entity_id=rel["source_entity_id"],
                target_entity_id=rel["target_entity_id"],
                relation_type=rel["relation_type"],
                old_properties=rel.get("properties"),
                valid_from=rel.get("valid_from"),
                valid_to=now,
                change_reason=reason,
            )
        except Exception as e:
            logger.warning(f"历史记录写入失败: {e}")

        logger.info(f"✓ 结束关系: {relationship_id} ({reason})")
        return {
            "success": True,
            "message": f"Relationship {relationship_id} deactivated",
            "valid_to": now,
        }

    except Exception as e:
        logger.error(f"✗ 结束关系失败: {e}")
        return {"success": False, "error": str(e)}


def update_relationship(
    user_id: int,
    relationship_id: int,
    updates: Dict[str, Any],
) -> Dict[str, Any]:
    """
    更新关系属性。

    记录变更历史以便时序追踪。

    Args:
        user_id: 用户 ID
        relationship_id: 关系 ID
        updates: 更新字段（properties, confidence, relation_subtype 等）

    Returns:
        更新结果
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()
        now = datetime.now().isoformat()

        rows = db.execute(
            '''SELECT * FROM graph_relationships WHERE id = ? AND user_id = ?''',
            (relationship_id, user_id)
        )
        if not rows:
            return {"success": False, "error": "关系未找到"}

        rel = dict(rows[0])
        old_properties = rel.get("properties")

        # 构建 UPDATE 语句
        allowed_fields = {"properties", "confidence", "relation_subtype", "extraction_source"}
        set_parts = []
        params = []

        for field, value in updates.items():
            if field == "properties" and value is not None:
                value = json.dumps(value, ensure_ascii=False)
                set_parts.append(f"{field} = ?")
                params.append(value)
            elif field in allowed_fields:
                set_parts.append(f"{field} = ?")
                params.append(value)

        if not set_parts:
            return {"success": False, "error": "无有效更新字段"}

        params.append(relationship_id)
        db.execute(
            f'UPDATE graph_relationships SET {", ".join(set_parts)} WHERE id = ?',
            tuple(params)
        )

        # 记录历史
        try:
            _record_relationship_history(
                user_id=user_id,
                relationship_id=relationship_id,
                source_entity_id=rel["source_entity_id"],
                target_entity_id=rel["target_entity_id"],
                relation_type=rel["relation_type"],
                old_properties=old_properties,
                new_properties=updates.get("properties"),
                change_reason="updated",
            )
        except Exception as e:
            logger.warning(f"历史记录写入失败: {e}")

        logger.info(f"✓ 更新关系: {relationship_id}")
        return {"success": True, "message": f"Relationship {relationship_id} updated"}

    except Exception as e:
        logger.error(f"✗ 更新关系失败: {e}")
        return {"success": False, "error": str(e)}


def get_relationship_history(
    user_id: int,
    entity1_name: str,
    entity2_name: str,
    entity1_type: str = "person",
    entity2_type: str = "organization",
) -> Dict[str, Any]:
    """
    获取两个实体之间的关系变化历史（时序追踪）。

    例如："张三"和"A公司"之间可能经历:
    - 2024: 入职 → relation=subordinate
    - 2025: 离职 → is_active=0
    - 2025: 跳到B公司 → 新关系

    Args:
        user_id: 用户 ID
        entity1_name: 第一个实体名称
        entity2_name: 第二个实体名称
        entity1_type: 第一个实体类型
        entity2_type: 第二个实体类型

    Returns:
        关系历史列表
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()

        # 查找两个实体
        e1 = db.execute(
            '''SELECT id FROM graph_entities
               WHERE user_id = ? AND name = ? AND entity_type = ?''',
            (user_id, entity1_name, entity1_type)
        )
        e2 = db.execute(
            '''SELECT id FROM graph_entities
               WHERE user_id = ? AND name = ? AND entity_type = ?''',
            (user_id, entity2_name, entity2_type)
        )

        if not e1 or not e2:
            return {"success": True, "history": [], "message": "一方实体不存在"}

        e1_id = e1[0]["id"]
        e2_id = e2[0]["id"]

        # 先从 history 表获取完整时序
        history_rows = db.execute(
            '''SELECT h.*, r.is_active as current_active
               FROM graph_relationship_history h
               LEFT JOIN graph_relationships r ON h.relationship_id = r.id
               WHERE h.user_id = ?
               AND ((h.source_entity_id = ? AND h.target_entity_id = ?)
                    OR (h.source_entity_id = ? AND h.target_entity_id = ?))
               ORDER BY h.changed_at ASC''',
            (user_id, e1_id, e2_id, e2_id, e1_id)
        )

        history = [dict(r) for r in history_rows] if history_rows else []

        # 如果 history 为空，从当前关系构建
        if not history:
            current = db.execute(
                '''SELECT r.*, e1.name as source_name, e2.name as target_name
                   FROM graph_relationships r
                   JOIN graph_entities e1 ON r.source_entity_id = e1.id
                   JOIN graph_entities e2 ON r.target_entity_id = e2.id
                   WHERE r.user_id = ?
                   AND ((r.source_entity_id = ? AND r.target_entity_id = ?)
                        OR (r.source_entity_id = ? AND r.target_entity_id = ?))
                   ORDER BY r.created_at DESC''',
                (user_id, e1_id, e2_id, e2_id, e1_id)
            )
            if current:
                history = [dict(r) for r in current]

        return {
            "success": True,
            "history": history,
            "count": len(history),
            "entities": {
                "entity1": {"name": entity1_name, "type": entity1_type, "id": e1_id},
                "entity2": {"name": entity2_name, "type": entity2_type, "id": e2_id},
            },
        }

    except Exception as e:
        logger.error(f"✗ 获取关系历史失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 3. 图遍历
# ============================================================

def get_neighbors(
    user_id: int,
    entity_name: Optional[str] = None,
    entity_type: str = "person",
    relation_type: Optional[str] = None,
    depth: int = 1,
    max_depth: int = 3,
    entity_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    查询指定实体的邻居节点。

    Args:
        user_id: 用户 ID
        entity_name: 实体名称（与 entity_id 二选一）
        entity_type: 实体类型
        relation_type: 过滤关系类型（如 colleague）
        depth: 遍历深度（1=直接邻居, 2=二度, ...）
        max_depth: 最大遍历深度限制
        entity_id: 实体 ID（优先使用，无需 name 查找）

    Returns:
        邻居节点列表
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()

        # 1. 查找实体 — 优先按 ID 查找，否则按 name+type 查找
        if entity_id is not None:
            rows = db.execute(
                '''SELECT id, name FROM graph_entities
                   WHERE user_id = ? AND id = ?''',
                (user_id, entity_id)
            )
            if not rows:
                return {"success": True, "neighbors": [], "message": f"实体 ID={entity_id} 不存在"}
            entity_name = rows[0]["name"]
        else:
            if not entity_name:
                return {"success": False, "error": "必须提供 entity_id 或 entity_name"}
            rows = db.execute(
                '''SELECT id FROM graph_entities
                   WHERE user_id = ? AND name = ? AND entity_type = ?''',
                (user_id, entity_name, entity_type)
            )
            if not rows:
                return {"success": True, "neighbors": [], "message": f"实体 '{entity_name}' 不存在"}

        entity_id = rows[0]["id"]
        visited = {entity_id}
        all_neighbors = []
        current_ids = [entity_id]
        actual_depth = min(depth, max_depth)

        for d in range(actual_depth):
            next_ids = []
            if not current_ids:
                break

            placeholders = ",".join("?" for _ in current_ids)

            # 双向查询
            rel_rows = db.execute(
                f'''SELECT r.*, e1.name as source_name, e1.entity_type as source_type,
                           e2.name as target_name, e2.entity_type as target_type
                    FROM graph_relationships r
                    JOIN graph_entities e1 ON r.source_entity_id = e1.id
                    JOIN graph_entities e2 ON r.target_entity_id = e2.id
                    WHERE r.user_id = ? AND r.is_active = 1
                    AND (r.source_entity_id IN ({placeholders})
                         OR r.target_entity_id IN ({placeholders}))''',
                tuple([user_id] + current_ids + current_ids)
            )

            if not rel_rows:
                break

            for rel in rel_rows:
                rel = dict(rel)
                if rel["source_entity_id"] in current_ids:
                    neighbor_id = rel["target_entity_id"]
                    neighbor_name = rel["target_name"]
                    neighbor_type = rel["target_type"]
                    direction = "outgoing"
                    rel_type = rel["relation_type"]
                else:
                    neighbor_id = rel["source_entity_id"]
                    neighbor_name = rel["source_name"]
                    neighbor_type = rel["source_type"]
                    direction = "incoming"
                    # 反转关系类型
                    reverse_map = {
                        "superior": "subordinate",
                        "subordinate": "superior",
                        "colleague": "colleague",
                        "friend": "friend",
                        "project_member": "project_member",
                        "mentor": "mentee",
                        "mentee": "mentor",
                        "classmate": "classmate",
                        "family": "family",
                    }
                    rel_type = reverse_map.get(rel["relation_type"], rel["relation_type"])

                # 按关系类型过滤
                if relation_type and rel_type != _normalize_relation_type(relation_type):
                    continue

                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    skip = False
                    for existing in all_neighbors:
                        if existing["entity_id"] == neighbor_id and existing.get("relation_type") == rel_type:
                            skip = True
                            break
                    if not skip:
                        all_neighbors.append({
                            "entity_id": neighbor_id,
                            "entity_name": neighbor_name,
                            "entity_type": neighbor_type,
                            "relation_type": rel_type,
                            "direction": direction,
                            "depth": d + 1,
                            "confidence": rel.get("confidence", 0.5),
                            "relationship_id": rel["id"],
                        })
                        if d + 1 < actual_depth:
                            next_ids.append(neighbor_id)

            current_ids = next_ids

        return {
            "success": True,
            "center": entity_name,
            "neighbors": all_neighbors,
            "count": len(all_neighbors),
            "depth": actual_depth,
        }

    except Exception as e:
        logger.error(f"✗ 图遍历失败: {e}")
        return {"success": False, "error": str(e)}


def list_relationships(
    user_id: int,
    source_name: Optional[str] = None,
    target_name: Optional[str] = None,
    relation_type: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """
    查询关系列表。

    Args:
        user_id: 用户 ID
        source_name: 源实体名称过滤
        target_name: 目标实体名称过滤
        relation_type: 关系类型过滤
        is_active: 是否仅活跃关系
        limit: 返回数量
        offset: 偏移量

    Returns:
        关系列表
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()

        conditions = ["r.user_id = ?"]
        params = [user_id]

        if source_name:
            conditions.append("e1.name LIKE ?")
            params.append(f"%{source_name}%")
        if target_name:
            conditions.append("e2.name LIKE ?")
            params.append(f"%{target_name}%")
        if relation_type:
            conditions.append("r.relation_type = ?")
            params.append(_normalize_relation_type(relation_type))
        if is_active is not None:
            conditions.append("r.is_active = ?")
            params.append(1 if is_active else 0)

        where = " AND ".join(conditions)

        rows = db.execute(
            f'''SELECT r.*, e1.name as source_name, e1.entity_type as source_type,
                       e2.name as target_name, e2.entity_type as target_type
                FROM graph_relationships r
                JOIN graph_entities e1 ON r.source_entity_id = e1.id
                JOIN graph_entities e2 ON r.target_entity_id = e2.id
                WHERE {where}
                ORDER BY r.created_at DESC
                LIMIT ? OFFSET ?''',
            tuple(params + [limit, offset])
        )

        relationships = [dict(r) for r in rows] if rows else []

        count_rows = db.execute(
            f'SELECT COUNT(*) as total FROM graph_relationships r WHERE {where}',
            tuple(params)
        )
        total = count_rows[0]["total"] if count_rows else 0

        return {
            "success": True,
            "relationships": relationships,
            "count": len(relationships),
            "total": total,
        }

    except Exception as e:
        logger.error(f"✗ 查询关系失败: {e}")
        return {"success": False, "error": str(e)}


def get_entity_graph_text(
    user_id: int,
    center_entity: str,
    entity_type: str = "person",
    max_depth: int = 2,
) -> str:
    """
    构建以某实体为中心的图谱文本表示（用于 LLM 上下文注入）。

    Args:
        user_id: 用户 ID
        center_entity: 中心实体名称
        entity_type: 中心实体类型
        max_depth: 最大遍历深度

    Returns:
        格式化的图谱文本
    """
    result = get_neighbors(
        user_id=user_id,
        entity_name=center_entity,
        entity_type=entity_type,
        depth=max_depth,
    )

    if not result.get("success") or not result.get("neighbors"):
        return ""

    lines = []
    lines.append(f"[知识图谱 - 以 {center_entity} 为中心]")

    # 按深度分组
    by_depth = {}
    for nb in result["neighbors"]:
        d = nb.get("depth", 1)
        by_depth.setdefault(d, []).append(nb)

    for d in sorted(by_depth.keys()):
        if d == 1:
            lines.append(f"  直接关系 ({center_entity}):")
        else:
            lines.append(f"  间接关系 (深度 {d}):")
        for nb in by_depth[d]:
            rel_label = {
                "colleague": "同事",
                "friend": "朋友",
                "superior": "上级",
                "subordinate": "下属",
                "project_member": "项目同事",
                "family": "家人",
                "classmate": "同学",
                "mentor": "导师",
                "mentee": "学生",
            }.get(nb["relation_type"], nb["relation_type"])

            direction = "→" if nb["direction"] == "outgoing" else "←"
            lines.append(
                f"    - {center_entity} {direction} {nb['entity_name']} "
                f"({rel_label}, 置信度: {nb.get('confidence', 0.5):.2f})"
            )

    return "\n".join(lines)


# ============================================================
# 4. 实体与关系抽取
# ============================================================

# LLM 抽取系统提示词
EXTRACTION_SYSTEM_PROMPT = """你是一个实体与关系抽取引擎。
从对话文本中识别人名、组织、地点、事件及其关系。

返回 JSON 格式（不要包含其他文字）：
{
  "entities": [
    {"name": "实体名称", "type": "person|organization|location|event", "aliases": ["别名1"]}
  ],
  "relationships": [
    {
      "source": "源实体名称",
      "target": "目标实体名称",
      "relation": "关系类型（colleague|superior|subordinate|friend|project_member|classmate|family）",
      "confidence": 0.9
    }
  ]
}"""


def extract_entities_from_text(
    user_id: int,
    text: str,
) -> Dict[str, Any]:
    """
    从文本中批量抽取实体和关系。

    优先使用 LLM，不可用时回退到 regex 方案。

    Args:
        user_id: 用户 ID
        text: 输入文本

    Returns:
        抽取结果（entities + relationships）
    """
    result = {
        "entities": [],
        "relationships": [],
        "extraction_method": "unknown",
    }

    # 尝试 LLM 抽取
    llm_success = False
    try:
        from app.services.llm_backend_service import llm_chat

        msg = [
            {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ]
        response = llm_chat(user_id=user_id, messages=msg, temperature=0.1)
        if response.get("success") and response.get("content"):
            import json as _json
            content = response["content"]
            # 提取 JSON
            code_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', content, re.DOTALL)
            if code_match:
                content = code_match.group(1).strip()
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                parsed = _json.loads(json_match.group(0))
                result["entities"] = parsed.get("entities", [])
                result["relationships"] = parsed.get("relationships", [])
                result["extraction_method"] = "llm"
                llm_success = True
    except Exception as e:
        logger.debug(f"LLM 抽取失败，回退 regex: {e}")

    # 回退：regex 方案
    if not llm_success:
        result["extraction_method"] = "regex"

        # 使用 ContextCompressor 的 EntityGraphTraverser
        try:
            from app.services.context_compressor import EntityGraphTraverser
            raw_entities = EntityGraphTraverser.extract_entities(text)
            for ent in raw_entities:
                result["entities"].append({
                    "name": ent,
                    "type": _guess_entity_type(ent, text),
                    "aliases": [],
                })
        except Exception:
            # 最终回退：简单正则
            # 人名：2-4 字中文
            people = re.findall(r'([\u4e00-\u9fff]{2,4})(?:是|叫|说|问|告诉|通知|联系|找)', text)
            for p in set(people):
                result["entities"].append({"name": p, "type": "person", "aliases": []})

            # 组织：XX公司/集团/学院/医院
            orgs = re.findall(r'([\u4e00-\u9fff]{2,10}(?:公司|集团|学院|医院|大学|银行|机构|部门|团队|项目组))', text)
            for o in set(orgs):
                result["entities"].append({"name": o, "type": "organization", "aliases": []})

            # 地点
            locs = re.findall(r'在([\u4e00-\u9fff]{2,6}(?:市|区|省|路|街|大厦|广场|中心))', text)
            for l in set(locs):
                result["entities"].append({"name": l, "type": "location", "aliases": []})

        # 尝试从文本中推断关系
        # "XX的同事"、"XX的朋友"
        rel_patterns = [
            (r'([\u4e00-\u9fff]{2,4})的同事([\u4e00-\u9fff]{2,4})', "colleague"),
            (r'([\u4e00-\u9fff]{2,4})的朋友([\u4e00-\u9fff]{2,4})', "friend"),
            (r'([\u4e00-\u9fff]{2,4})的领导([\u4e00-\u9fff]{2,4})', "superior"),
            (r'([\u4e00-\u9fff]{2,4})的下属([\u4e00-\u9fff]{2,4})', "subordinate"),
        ]
        for pattern, rel_type in rel_patterns:
            matches = re.findall(pattern, text)
            for src, tgt in matches:
                result["relationships"].append({
                    "source": src,
                    "target": tgt,
                    "relation": rel_type,
                    "confidence": 0.6,
                })

        # 去重
        seen_entities = set()
        unique_entities = []
        for e in result["entities"]:
            key = (e["name"], e["type"])
            if key not in seen_entities:
                seen_entities.add(key)
                unique_entities.append(e)
        result["entities"] = unique_entities

    # 批量存入数据库
    stored = {"entities": 0, "relationships": 0}
    for ent in result["entities"]:
        try:
            ensure_entity(
                user_id=user_id,
                name=ent["name"],
                entity_type=ent["type"],
                aliases=ent.get("aliases"),
            )
            stored["entities"] += 1
        except Exception as e:
            logger.debug(f"实体存储失败: {e}")

    for rel in result["relationships"]:
        try:
            add_relationship(
                user_id=user_id,
                source_name=rel["source"],
                target_name=rel["target"],
                relation_type=rel["relation"],
                source_type="person",
                target_type=_guess_entity_type(rel["target"], text),
                confidence=rel.get("confidence", 0.5),
                extraction_source="auto_extract",
            )
            stored["relationships"] += 1
        except Exception as e:
            logger.debug(f"关系存储失败: {e}")

    result["stored"] = stored
    return result


def _guess_entity_type(name: str, context: str = "") -> str:
    """
    猜测实体类型。

    Args:
        name: 实体名称
        context: 上下文文本

    Returns:
        猜测的实体类型
    """
    # 2-4 字中文通常是人名
    if re.match(r'^[\u4e00-\u9fff]{2,4}$', name):
        return "person"

    # 含公司/集团/学院等后缀的是组织
    if re.search(r'(公司|集团|学院|医院|大学|银行|机构|部门|团队|项目组|有限公司)$', name):
        return "organization"

    # 含市/区/省等后缀的是地点
    if re.search(r'(市|区|省|路|街|大厦|广场|中心)$', name):
        return "location"

    return "organization"


# ============================================================
# 5. 自然语言图查询
# ============================================================

def query_graph(
    user_id: int,
    query: str,
) -> Dict[str, Any]:
    """
    自然语言图查询。

    解析 NL 查询并路由到对应的图操作：
    - "张三的同事" → get_neighbors(type=colleague)
    - "张三和李四的关系" → get_relationship_history
    - "张三的经历" → get_entity_graph_text
    - "找张三" → search_entities

    Args:
        user_id: 用户 ID
        query: 自然语言查询

    Returns:
        查询结果
    """
    try:
        # 1. 提取查询中的实体名
        entities = _extract_query_entities(query)

        if not entities:
            return {"success": True, "result": "未识别到实体", "query_type": "unknown"}

        # 2. 判断查询类型
        query_type = _classify_query(query)

        if query_type == "neighbors":
            # "张三的同事/朋友/领导"
            relation_type = _extract_relation_type(query)
            return get_neighbors(
                user_id=user_id,
                entity_name=entities[0],
                entity_type="person",
                relation_type=relation_type,
            )

        elif query_type == "history":
            # "张三和李四的关系"、"张三在A公司的经历"
            if len(entities) >= 2:
                return get_relationship_history(
                    user_id=user_id,
                    entity1_name=entities[0],
                    entity2_name=entities[1],
                )
            else:
                graph_text = get_entity_graph_text(
                    user_id=user_id,
                    center_entity=entities[0],
                    max_depth=2,
                )
                return {
                    "success": True,
                    "result": graph_text or f"未找到 '{entities[0]}' 的关系信息",
                    "query_type": "history",
                    "entity": entities[0],
                }

        elif query_type == "graph":
            # "张三的经历"、"关于张三的关系网"
            graph_text = get_entity_graph_text(
                user_id=user_id,
                center_entity=entities[0],
                max_depth=2,
            )
            return {
                "success": True,
                "result": graph_text or f"未找到 '{entities[0]}' 的关系信息",
                "query_type": "graph",
                "entity": entities[0],
            }

        else:
            # 默认：搜索实体
            return search_entities(user_id=user_id, query=entities[0])

    except Exception as e:
        logger.error(f"✗ 图查询失败: {e}")
        return {"success": False, "error": str(e)}


def _extract_query_entities(text: str) -> List[str]:
    """
    从自然语言查询中提取实体名称。
    使用 EntityGraphTraverser.extract_entities 作为主力，
    回退到简单正则。
    """
    try:
        from app.services.context_compressor import EntityGraphTraverser
        result = EntityGraphTraverser.extract_entities(text)
        if result:
            return result
    except Exception:
        pass

    # 回退：提取引号内或 2-4 字中文名词前的实体
    quoted = re.findall(r'[""」](.+?)[""「]', text)
    if quoted:
        return quoted
    # "XX的YY" 中提取 XX
    names = re.findall(r'([\u4e00-\u9fff]{2,4})(?:的同事|的朋友|的领导|的下属|和|与|跟)', text)
    if names:
        return list(set(names))
    # "XX公司/集团"等
    orgs = re.findall(r'([\u4e00-\u9fff]{2,10}(?:公司|集团|学院))', text)
    if orgs:
        return orgs
    return []


def _classify_query(query: str) -> str:
    """
    分类查询类型。

    Returns:
        'neighbors': 查询邻居（"张三的同事"）
        'history': 历史关系（"张三和李四的关系"）
        'graph': 图展示（"张三的经历"）
        'search': 搜索（默认）
    """
    if re.search(r'(的同事|的朋友|的领导|的下属|认识谁|认识什么人)', query):
        return "neighbors"
    if re.search(r'(的关系|的经历|的历程|的关系史|变化)', query):
        return "history"
    if re.search(r'(的关系网|的圈子|的图谱|关于.*的关系)', query):
        return "graph"
    return "search"


def _extract_relation_type(query: str) -> Optional[str]:
    """
    从查询中提取关系类型。

    Args:
        query: 查询文本

    Returns:
        关系类型或 None
    """
    mapping = {
        r'同事': "colleague",
        r'朋友': "friend",
        r'领导': "superior",
        r'上级': "superior",
        r'下属': "subordinate",
        r'下级': "subordinate",
        r'同学': "classmate",
        r'家人': "family",
    }
    for pattern, rel_type in mapping.items():
        if re.search(pattern, query):
            return rel_type
    return None


# ============================================================
# 测试
# ============================================================

def test_graph_memory():
    """测试图谱记忆模块"""
    from datetime import timedelta

    print("\n" + "=" * 60)
    print("测试 Graph Memory 模块")
    print("=" * 60 + "\n")

    test_user_id = 998
    _ensure_graph_tables()
    db = get_db_client()

    # 清理
    for tbl in ['graph_entities', 'graph_relationships', 'graph_relationship_history']:
        db.execute(f'DELETE FROM {tbl} WHERE user_id = ?', (test_user_id,))
    print("  清理完成\n")

    # ================================================================
    # 1. 实体管理
    # ================================================================
    print("--- 1. 实体管理 ---\n")

    print("1.1 创建实体...")
    r = ensure_entity(test_user_id, "张三", "person", aliases=["三哥"])
    assert r.get("id"), f"创建实体失败: {r}"
    zhang_san_id = r["id"]
    print(f"  张三 → id={zhang_san_id}, created={r.get('created')}")

    r2 = ensure_entity(test_user_id, "张三", "person")  # 重复创建
    assert r2.get("id") == zhang_san_id
    print(f"  重复创建张三 → id={r2['id']}, created={r2.get('created')} (预期 False)")

    ensure_entity(test_user_id, "李四", "person")
    ensure_entity(test_user_id, "王五", "person")
    ensure_entity(test_user_id, "A公司", "organization")
    ensure_entity(test_user_id, "B公司", "organization")
    ensure_entity(test_user_id, "北京", "location")
    print("  ✓ 实体创建完成\n")

    print("1.2 搜索实体...")
    r = search_entities(test_user_id, "张三")
    assert r["count"] > 0
    print(f"  搜索 '张三': {r['count']} 条")

    r = search_entities(test_user_id, "公司", entity_type="organization")
    assert r["count"] == 2
    print(f"  搜索 '公司' (organization): {r['count']} 条")
    print("  ✓ 搜索完成\n")

    print("1.3 获取实体详情...")
    r = get_entity(test_user_id, zhang_san_id)
    assert r["success"]
    print(f"  张三: {r['entity']['name']}, 关系数={r['entity']['relation_counts']}")
    print("  ✓ 实体详情完成\n")

    # ================================================================
    # 2. 关系管理
    # ================================================================
    print("--- 2. 关系管理 ---\n")

    print("2.1 创建关系...")
    r = add_relationship(
        test_user_id, "张三", "李四", "colleague",
        source_type="person", target_type="person",
        confidence=0.9, extraction_source="test",
    )
    assert r["success"]
    rel_id_1 = r["relationship_id"]
    print(f"  张三 -[同事]-> 李四: rel_id={rel_id_1}")

    r = add_relationship(test_user_id, "张三", "A公司", "subordinate",
                          source_type="person", target_type="organization",
                          confidence=0.95, extraction_source="test")
    assert r["success"]
    rel_id_2 = r["relationship_id"]
    print(f"  张三 -[下属]-> A公司: rel_id={rel_id_2}")

    r = add_relationship(test_user_id, "张三", "王五", "friend",
                          source_type="person", target_type="person")
    assert r["success"]
    print(f"  张三 -[朋友]-> 王五: rel_id={r['relationship_id']}")

    r = add_relationship(test_user_id, "李四", "A公司", "colleague",
                          source_type="person", target_type="organization")
    assert r["success"]
    print(f"  李四 -[同事]-> A公司: rel_id={r['relationship_id']}")
    print("  ✓ 关系创建完成\n")

    print("2.2 关系去重...")
    r = add_relationship(test_user_id, "张三", "李四", "colleague",
                          source_type="person", target_type="person")
    assert r["created"] is False
    print(f"  重复添加: created={r.get('created')} (预期 False)")
    print("  ✓ 去重完成\n")

    print("2.3 结束关系...")
    r = deactivate_relationship(test_user_id, rel_id_2, reason="张三离开A公司")
    assert r["success"]
    print(f"  张三离职A公司: {r['message']}")
    print("  ✓ 关系终止完成\n")

    print("2.4 查询关系列表...")
    r = list_relationships(test_user_id)
    assert r["success"]
    print(f"  全部关系: {r['count']} 条")
    r = list_relationships(test_user_id, relation_type="colleague")
    print(f"  同事关系: {r['count']} 条")
    assert r["count"] >= 1
    print("  ✓ 关系查询完成\n")

    # ================================================================
    # 3. 图遍历
    # ================================================================
    print("--- 3. 图遍历 ---\n")

    print("3.1 直接邻居查询...")
    r = get_neighbors(test_user_id, "张三", relation_type="colleague")
    assert r["success"]
    print(f"  张三的同事: {r['count']} 人")
    for nb in r["neighbors"]:
        print(f"    - {nb['entity_name']} ({nb['relation_type']})")
    assert any(nb["entity_name"] == "李四" for nb in r["neighbors"])
    print("  ✓ 邻居查询完成\n")

    print("3.2 所有关系查询...")
    r = get_neighbors(test_user_id, "张三")
    print(f"  张三的所有关系: {r['count']} 条")
    for nb in r["neighbors"]:
        print(f"    - {nb['entity_name']} ({nb['relation_type']})")
    print("  ✓ 全部关系完成\n")

    print("3.3 间接邻居...")
    # 李四和A公司是同事（同事关系A公司），张三和李四是同事，所以张三-李四-A公司构成二度关系
    r = get_neighbors(test_user_id, "张三", depth=2)
    print(f"  张三的二度关系: {r['count']} 条")
    print("  ✓ 间接邻居完成\n")

    print("3.4 图谱文本表示...")
    text = get_entity_graph_text(test_user_id, "张三")
    print(f"  图谱文本:\n{text}")
    assert "张三" in text
    print("  ✓ 图谱文本完成\n")

    # ================================================================
    # 4. 时序追踪
    # ================================================================
    print("--- 4. 时序追踪 ---\n")

    print("4.1 关系变更历史...")
    r = get_relationship_history(test_user_id, "张三", "A公司")
    assert r["success"]
    print(f"  张三-A公司 历史记录: {r['count']} 条")
    for h in r["history"]:
        reason = h.get("change_reason", "current")
        active = h.get("current_active", h.get("is_active", 1))
        print(f"    - {h.get('relation_type')} (reason={reason}, active={active})")
    print("  ✓ 时序追踪完成\n")

    print("4.2 关系切换测试（张三从A公司到B公司）...")
    # 模拟时序场景
    add_relationship(test_user_id, "张三", "B公司", "subordinate",
                     source_type="person", target_type="organization",
                     confidence=0.9, extraction_source="test")
    print("  张三加入B公司")

    r = get_relationship_history(test_user_id, "张三", "B公司")
    print(f"  张三-B公司 历史: {r['count']} 条")
    assert r["count"] >= 1

    # 查看张三的所有公司经历
    r = get_neighbors(test_user_id, "张三", relation_type="subordinate")
    print(f"  张三的下属关系（公司）: {r['count']} 家")
    for nb in r["neighbors"]:
        active = "在职" if nb.get("relation_type") else "历史"
        print(f"    - {nb['entity_name']}")
    print("  ✓ 时序模拟完成\n")

    # ================================================================
    # 5. 实体抽取
    # ================================================================
    print("--- 5. 实体与关系抽取 ---\n")

    print("5.1 文本实体抽取 (regex回退)...")
    text = "张三在北京的A公司工作，他的同事是李四，领导是王五。"
    r = extract_entities_from_text(test_user_id, text)
    print(f"  抽取方法: {r.get('extraction_method')}")
    print(f"  实体数: {len(r['entities'])}")
    for e in r["entities"]:
        print(f"    - {e['name']} ({e['type']})")
    print(f"  关系数: {len(r['relationships'])}")
    for rel in r["relationships"]:
        print(f"    - {rel['source']} -[{rel['relation']}]-> {rel['target']}")
    print(f"  存储: entities={r['stored']['entities']}, rels={r['stored']['relationships']}")
    print("  ✓ 实体抽取完成\n")

    # ================================================================
    # 6. 自然语言查询
    # ================================================================
    print("--- 6. 自然语言图查询 ---\n")

    print("6.1 查询邻居...")
    r = query_graph(test_user_id, "张三的同事")
    if r.get("success"):
        print(f"  '张三的同事': {r.get('count', 0)} 人")
    print("  ✓ NL查询完成\n")

    print("6.2 查询关系历史...")
    r = query_graph(test_user_id, "张三和A公司的关系")
    if r.get("success"):
        print(f"  '张三和A公司的关系': {r.get('count', 0)} 条记录")
    print("  ✓ NL历史查询完成\n")

    print("6.3 搜索实体...")
    r = query_graph(test_user_id, "找张三")
    if r.get("success") and r.get("entities"):
        print(f"  '找张三': {r.get('count', 0)} 条")
    print("  ✓ NL搜索查询完成\n")

    # 清理
    print("--- 清理测试数据 ---")
    for tbl in ['graph_entities', 'graph_relationships', 'graph_relationship_history']:
        db.execute(f'DELETE FROM {tbl} WHERE user_id = ?', (test_user_id,))
    print("  清理完成")

    print("\n" + "=" * 60)
    print("✅ Graph Memory 模块测试完成！")
    print("=" * 60 + "\n")

    return True


def _levenshtein_distance(s1: str, s2: str) -> int:
    """计算两个字符串的 Levenshtein 编辑距离"""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row
    return prev_row[-1]


def detect_duplicate_entities(
    user_id: int,
    threshold: int = 3,
) -> Dict[str, Any]:
    """
    检测相似实体（基于名称编辑距离）。

    Args:
        user_id: 用户 ID
        threshold: Levenshtein 距离阈值，< threshold 认为是候选重复

    Returns:
        候选重复实体对列表
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()

        rows = db.execute(
            '''SELECT id, name, entity_type FROM graph_entities
               WHERE user_id = ? ORDER BY name''',
            (user_id,)
        )
        entities = [dict(r) for r in rows] if rows else []

        duplicates = []
        seen_pairs = set()
        for i, e1 in enumerate(entities):
            for e2 in entities[i + 1:]:
                # 只比较同类型实体
                if e1["entity_type"] != e2["entity_type"]:
                    continue
                dist = _levenshtein_distance(e1["name"], e2["name"])
                if dist < threshold and dist > 0:
                    pair_key = (min(e1["id"], e2["id"]), max(e1["id"], e2["id"]))
                    if pair_key not in seen_pairs:
                        seen_pairs.add(pair_key)
                        max_len = max(len(e1["name"]), len(e2["name"]), 1)
                        similarity = round(1 - dist / max_len, 3)
                        duplicates.append({
                            "entity_a": {"id": e1["id"], "name": e1["name"], "entity_type": e1["entity_type"]},
                            "entity_b": {"id": e2["id"], "name": e2["name"], "entity_type": e2["entity_type"]},
                            "distance": dist,
                            "similarity": similarity,
                        })

        duplicates.sort(key=lambda x: x["similarity"], reverse=True)

        return {
            "success": True,
            "duplicates": duplicates,
            "count": len(duplicates),
            "threshold": threshold,
        }
    except Exception as e:
        logger.error(f"✗ 检测重复实体失败: {e}")
        return {"success": False, "error": str(e)}


def delete_entity(
    user_id: int,
    entity_id: int,
) -> Dict[str, Any]:
    """
    删除实体及其所有关系。

    Args:
        user_id: 用户 ID
        entity_id: 实体 ID

    Returns:
        删除结果
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()

        rows = db.execute(
            'SELECT * FROM graph_entities WHERE id = ? AND user_id = ?',
            (entity_id, user_id)
        )
        if not rows:
            return {"success": False, "error": "实体未找到"}

        # 删除关系
        db.execute(
            'DELETE FROM graph_relationships WHERE (source_entity_id = ? OR target_entity_id = ?) AND user_id = ?',
            (entity_id, entity_id, user_id)
        )
        # 删除实体
        db.execute(
            'DELETE FROM graph_entities WHERE id = ? AND user_id = ?',
            (entity_id, user_id)
        )

        logger.info(f"✓ 删除实体: id={entity_id}")
        return {"success": True, "message": f"Entity {entity_id} deleted"}
    except Exception as e:
        logger.error(f"✗ 删除实体失败: {e}")
        return {"success": False, "error": str(e)}


def update_entity(
    user_id: int,
    entity_id: int,
    name: Optional[str] = None,
    entity_type: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    更新实体信息。

    Args:
        user_id: 用户 ID
        entity_id: 实体 ID
        name: 新名称
        entity_type: 新类型
        metadata: 新元数据

    Returns:
        更新结果
    """
    try:
        _ensure_graph_tables()
        db = get_db_client()

        rows = db.execute(
            'SELECT * FROM graph_entities WHERE id = ? AND user_id = ?',
            (entity_id, user_id)
        )
        if not rows:
            return {"success": False, "error": "实体未找到"}

        set_parts = []
        params = []
        if name is not None:
            set_parts.append("name = ?")
            params.append(name)
        if entity_type is not None:
            set_parts.append("entity_type = ?")
            params.append(entity_type)
        if metadata is not None:
            set_parts.append("metadata = ?")
            params.append(json.dumps(metadata, ensure_ascii=False))

        if not set_parts:
            return {"success": False, "error": "无有效更新字段"}

        set_parts.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(entity_id)

        db.execute(
            f'UPDATE graph_entities SET {", ".join(set_parts)} WHERE id = ?',
            tuple(params)
        )

        logger.info(f"✓ 更新实体: id={entity_id}")
        return {"success": True, "message": f"Entity {entity_id} updated"}
    except Exception as e:
        logger.error(f"✗ 更新实体失败: {e}")
        return {"success": False, "error": str(e)}


def get_graph_statistics(user_id: int) -> Dict[str, Any]:
    """
    获取用户的知识图谱统计信息。

    Returns:
        {
            "success": True,
            "entity_count": N,
            "relationship_count": N,
            "entity_types": {"person": N, ...},
            "relationship_types": {"colleague": N, ...},
            "top_entities": [{"name": ..., "entity_type": ..., "relation_count": N}, ...],
        }
    """
    try:
        db = get_db_client()

        # 实体总数
        entity_rows = db.execute(
            'SELECT COUNT(*) as cnt FROM graph_entities WHERE user_id = ?',
            (user_id,)
        )
        entity_count = dict(entity_rows[0])["cnt"] if entity_rows else 0

        # 关系总数（仅活跃）
        rel_rows = db.execute(
            '''SELECT COUNT(*) as cnt FROM graph_relationships
               WHERE user_id = ? AND (valid_to IS NULL OR valid_to > ?)''',
            (user_id, datetime.now().isoformat())
        )
        relationship_count = dict(rel_rows[0])["cnt"] if rel_rows else 0

        # 按实体类型统计
        type_rows = db.execute(
            'SELECT entity_type, COUNT(*) as cnt FROM graph_entities WHERE user_id = ? GROUP BY entity_type',
            (user_id,)
        )
        entity_types = {dict(r)["entity_type"]: dict(r)["cnt"] for r in (type_rows or [])}

        # 按关系类型统计
        rtype_rows = db.execute(
            '''SELECT relation_type, COUNT(*) as cnt FROM graph_relationships
               WHERE user_id = ? AND (valid_to IS NULL OR valid_to > ?)
               GROUP BY relation_type''',
            (user_id, datetime.now().isoformat())
        )
        relationship_types = {dict(r)["relation_type"]: dict(r)["cnt"] for r in (rtype_rows or [])}

        # Top 10 最连接的实体
        top_rows = db.execute(
            '''SELECT e.id, e.name, e.entity_type,
                      (SELECT COUNT(*) FROM graph_relationships r
                       WHERE r.user_id = e.user_id
                       AND (r.source_entity_id = e.id OR r.target_entity_id = e.id)
                       AND (r.valid_to IS NULL OR r.valid_to > ?)) as relation_count
               FROM graph_entities e
               WHERE e.user_id = ?
               ORDER BY relation_count DESC
               LIMIT 10''',
            (datetime.now().isoformat(), user_id)
        )
        top_entities = [dict(r) for r in (top_rows or [])]

        return {
            "success": True,
            "entity_count": entity_count,
            "relationship_count": relationship_count,
            "entity_types": entity_types,
            "relationship_types": relationship_types,
            "top_entities": top_entities,
        }
    except Exception as e:
        logger.error(f"✗ 获取图谱统计失败: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    test_graph_memory()
