"""
矛盾检测与调和引擎（R-05 收尾）

核心能力：
1. **memory_evolution 演变记录表**：记录每次"新事实取代旧事实"的事件，形成演变链
2. **语义矛盾检测**：在模式匹配（location/org/title/status）基础上，使用 ChromaDB
   向量相似度检测更广泛的语义冲突（如偏好变化、观点更新等）
3. **演变链追溯**：查询某事实的完整演变历史（v1 → v2 → v3 ...）

设计原则：
- 模式匹配优先（快速、精准），语义检测兜底（覆盖面广）
- 所有矛盾事件均写入 memory_evolution 表，支持完整审计
- 旧记忆标记为 "superseded"，召回时自动过滤
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.db_client import get_db_client

logger = logging.getLogger(__name__)

_SUPERSEDED_STATUS = "superseded"

# 复用 advanced_recall 的模式匹配能力
from app.services.advanced_recall import (
    _extract_updatable_entity,
    _text_similarity,
)


# ============================================================
# 1. memory_evolution 表初始化
# ============================================================

def _ensure_evolution_table() -> None:
    """确保 memory_evolution 演变记录表存在。"""
    db = get_db_client()
    try:
        db.execute('''CREATE TABLE IF NOT EXISTS memory_evolution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            workspace_id INTEGER,
            entity_type TEXT NOT NULL,
            entity_key TEXT,
            old_fragment_id INTEGER,
            new_fragment_id INTEGER,
            old_value TEXT,
            new_value TEXT,
            detection_method TEXT NOT NULL DEFAULT 'pattern',
            similarity_score REAL,
            change_reason TEXT,
            observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        # 索引：按用户+实体类型查询演变链
        try:
            db.execute(
                'CREATE INDEX IF NOT EXISTS idx_evolution_user_entity '
                'ON memory_evolution(user_id, entity_type, entity_key)'
            )
        except Exception:
            pass
        try:
            db.execute(
                'CREATE INDEX IF NOT EXISTS idx_evolution_fragment '
                'ON memory_evolution(user_id, old_fragment_id, new_fragment_id)'
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"创建 memory_evolution 表失败: {e}")


# ============================================================
# 2. 矛盾检测（模式匹配 + 语义检测）
# ============================================================

def detect_contradiction(
    user_id: int,
    new_content: str,
    new_fragment_id: Optional[int] = None,
    workspace_id: Optional[int] = None,
    pattern_threshold: float = 0.6,
    semantic_threshold: float = 0.75,
    enable_semantic: bool = True,
) -> Dict[str, Any]:
    """检测新记忆是否与已有记忆构成矛盾，并记录演变链。

    检测策略（两阶段）：
    1. **模式匹配阶段**：提取可更新实体（location/org/title/status），
       查找同类型不同值的旧记忆 → 标记 superseded + 写入演变记录
    2. **语义检测阶段**：使用 ChromaDB 向量搜索查找高相似度但内容冲突的记忆
       （覆盖偏好变化、观点更新等模式匹配无法覆盖的场景）

    Args:
        user_id: 用户 ID
        new_content: 新记忆内容
        new_fragment_id: 新记忆片段 ID（如有，用于演变链记录）
        workspace_id: workspace ID
        pattern_threshold: 模式匹配阶段的文本相似度阈值
        semantic_threshold: 语义检测阶段的向量相似度阈值
        enable_semantic: 是否启用语义检测（可关闭以提升性能）

    Returns:
        {
            "success": bool,
            "contradictions": List[Dict],  # 检测到的矛盾列表
            "superseded_ids": List[int],   # 被标记为过时的记忆 ID
            "detection_methods": List[str], # 使用的检测方法
            "evolution_records": List[int], # 写入的演变记录 ID
        }
    """
    try:
        _ensure_evolution_table()
        contradictions: List[Dict[str, Any]] = []
        superseded_ids: List[int] = []
        detection_methods: List[str] = []
        evolution_record_ids: List[int] = []
        now = datetime.now().isoformat()

        # ---- 阶段 1: 模式匹配检测 ----
        entity = _extract_updatable_entity(new_content)
        if entity:
            update_type, new_value = entity
            pattern_results = _detect_pattern_contradiction(
                user_id=user_id,
                new_content=new_content,
                new_fragment_id=new_fragment_id,
                update_type=update_type,
                new_value=new_value,
                workspace_id=workspace_id,
                threshold=pattern_threshold,
                now=now,
            )
            if pattern_results["superseded_ids"]:
                contradictions.extend(pattern_results["contradictions"])
                superseded_ids.extend(pattern_results["superseded_ids"])
                detection_methods.append("pattern")
                evolution_record_ids.extend(pattern_results["evolution_records"])

        # ---- 阶段 2: 语义检测（兜底，仅当模式匹配未发现矛盾时启用）----
        if enable_semantic and not superseded_ids:
            semantic_results = _detect_semantic_contradiction(
                user_id=user_id,
                new_content=new_content,
                new_fragment_id=new_fragment_id,
                workspace_id=workspace_id,
                threshold=semantic_threshold,
                now=now,
            )
            if semantic_results["superseded_ids"]:
                contradictions.extend(semantic_results["contradictions"])
                superseded_ids.extend(semantic_results["superseded_ids"])
                detection_methods.append("semantic")
                evolution_record_ids.extend(semantic_results["evolution_records"])

        if superseded_ids:
            logger.info(
                f"✓ 矛盾检测: 方法={detection_methods}, "
                f"标记 {len(superseded_ids)} 条旧记忆为过时"
            )

        return {
            "success": True,
            "contradictions": contradictions,
            "superseded_ids": superseded_ids,
            "detection_methods": detection_methods,
            "evolution_records": evolution_record_ids,
        }

    except Exception as e:
        logger.error(f"矛盾检测失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "contradictions": [],
            "superseded_ids": [],
            "detection_methods": [],
            "evolution_records": [],
        }


def _detect_pattern_contradiction(
    user_id: int,
    new_content: str,
    new_fragment_id: Optional[int],
    update_type: str,
    new_value: str,
    workspace_id: Optional[int],
    threshold: float,
    now: str,
) -> Dict[str, Any]:
    """模式匹配检测：查找同类型不同值的旧记忆。

    同实体类型 + 不同值即为矛盾（entity_type 匹配已是强信号，
    不再要求文本相似度达标，threshold 仅用于记录相似度分数）。
    """
    db = get_db_client()
    contradictions: List[Dict[str, Any]] = []
    superseded_ids: List[int] = []
    evolution_records: List[int] = []

    rows = db.execute(
        """SELECT id, content, lifecycle_status FROM memory_fragments
           WHERE user_id = ? AND lifecycle_status = 'active'
           ORDER BY created_at DESC""",
        (user_id,),
    )

    for row in (rows or []):
        old_content = row["content"]
        if row["id"] == new_fragment_id:
            continue
        old_entity = _extract_updatable_entity(old_content)
        if not old_entity:
            continue
        old_type, old_value = old_entity

        # 同类型但不同值 → 矛盾（entity_type 匹配是强信号，无需文本相似度过滤）
        if old_type == update_type and old_value.lower() != new_value.lower():
            similarity = _text_similarity(old_content, new_content)
            superseded_ids.append(row["id"])
            contradictions.append({
                "old_fragment_id": row["id"],
                "old_content": old_content,
                "new_value": new_value,
                "old_value": old_value,
                "entity_type": update_type,
                "detection_method": "pattern",
                "similarity_score": similarity,
            })

    # 标记 + 写入演变记录
    for c in contradictions:
        _mark_superseded(user_id, c["old_fragment_id"])
        eid = _record_evolution(
            user_id=user_id,
            workspace_id=workspace_id,
            entity_type=c["entity_type"],
            entity_key=c["entity_type"],  # 模式匹配以类型为 key
            old_fragment_id=c["old_fragment_id"],
            new_fragment_id=new_fragment_id,
            old_value=c["old_value"],
            new_value=c["new_value"],
            detection_method="pattern",
            similarity_score=c["similarity_score"],
            change_reason=f"{c['entity_type']}_update",
            observed_at=now,
        )
        evolution_records.append(eid)

    return {
        "contradictions": contradictions,
        "superseded_ids": superseded_ids,
        "evolution_records": evolution_records,
    }


def _detect_semantic_contradiction(
    user_id: int,
    new_content: str,
    new_fragment_id: Optional[int],
    workspace_id: Optional[int],
    threshold: float,
    now: str,
) -> Dict[str, Any]:
    """语义检测：使用向量相似度查找高相似度但内容冲突的记忆。

    策略：搜索与新内容语义相似的旧记忆，若相似度高但文本不同（值不同），
    则判定为语义矛盾。适用于偏好变化、观点更新等无法用正则覆盖的场景。
    """
    contradictions: List[Dict[str, Any]] = []
    superseded_ids: List[int] = []
    evolution_records: List[int] = []

    try:
        from app.core.chromadb_client import get_chromadb_client
        chroma = get_chromadb_client()
        if chroma is None:
            return {"contradictions": [], "superseded_ids": [], "evolution_records": []}

        # 向量搜索：找语义相似的记忆
        where = {"user_id": str(user_id)}
        results = chroma.search_embeddings(new_content, n_results=10, where=where)

        db = get_db_client()
        for r in results:
            similarity = r.get("similarity")
            if similarity is None or similarity < threshold:
                continue

            # 从 metadata 获取 fragment_id
            frag_id_str = r.get("metadata", {}).get("fragment_id")
            if not frag_id_str:
                continue
            try:
                old_fragment_id = int(frag_id_str)
            except (ValueError, TypeError):
                continue

            if old_fragment_id == new_fragment_id:
                continue

            # 检查旧记忆是否仍为 active
            rows = db.execute(
                "SELECT id, content, lifecycle_status FROM memory_fragments WHERE id = ? AND user_id = ?",
                (old_fragment_id, user_id),
            )
            if not rows or rows[0]["lifecycle_status"] != "active":
                continue

            old_content = rows[0]["content"]
            # 高语义相似但文本不完全相同（排除完全重复）→ 潜在矛盾
            if old_content.strip().lower() == new_content.strip().lower():
                continue

            # 排除已被模式匹配覆盖的类型（避免重复检测）
            old_entity = _extract_updatable_entity(old_content)
            new_entity = _extract_updatable_entity(new_content)
            if old_entity and new_entity and old_entity[0] == new_entity[0]:
                continue  # 模式匹配已处理

            # 语义矛盾：提取差异值
            old_value = old_content[:200]
            new_value = new_content[:200]

            superseded_ids.append(old_fragment_id)
            contradictions.append({
                "old_fragment_id": old_fragment_id,
                "old_content": old_content,
                "new_value": new_value,
                "old_value": old_value,
                "entity_type": "semantic",
                "detection_method": "semantic",
                "similarity_score": similarity,
            })

        # 标记 + 写入演变记录
        for c in contradictions:
            _mark_superseded(user_id, c["old_fragment_id"])
            eid = _record_evolution(
                user_id=user_id,
                workspace_id=workspace_id,
                entity_type="semantic",
                entity_key=_extract_semantic_key(new_content),
                old_fragment_id=c["old_fragment_id"],
                new_fragment_id=new_fragment_id,
                old_value=c["old_value"],
                new_value=c["new_value"],
                detection_method="semantic",
                similarity_score=c["similarity_score"],
                change_reason="semantic_contradiction",
                observed_at=now,
            )
            evolution_records.append(eid)

    except Exception as e:
        logger.warning(f"语义矛盾检测失败（降级跳过）: {e}")

    return {
        "contradictions": contradictions,
        "superseded_ids": superseded_ids,
        "evolution_records": evolution_records,
    }


def _extract_semantic_key(content: str) -> str:
    """从内容中提取语义 key（用于分组演变链）。

    取内容前 50 字符作为 key，使语义相近的记忆归入同一演变链。
    """
    # 去除常见动词/代词，取核心名词短语
    cleaned = re.sub(r'\b(?:I|i|my|the|a|an|is|am|are|was|were|have|has|had|do|does|did|will|would|can|could|should|to|in|at|on|for|of|with|from|by)\b', '', content)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned[:50] if cleaned else content[:50]


# ============================================================
# 3. 演变链追溯
# ============================================================

def get_evolution_chain(
    user_id: int,
    entity_type: str,
    entity_key: Optional[str] = None,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """追溯某事实的完整演变链。

    返回按时间排序的演变记录列表，展示事实如何从 v1 → v2 → v3 变化。

    Args:
        user_id: 用户 ID
        entity_type: 实体类型（location/organization/title/status/semantic）
        entity_key: 实体 key（为空则返回该类型的所有演变记录）
        workspace_id: workspace ID

    Returns:
        {
            "success": bool,
            "entity_type": str,
            "entity_key": str,
            "chain": List[Dict],  # 按时间排序的演变记录
            "total_versions": int,
        }
    """
    try:
        _ensure_evolution_table()
        db = get_db_client()

        conditions = ["user_id = ?", "entity_type = ?"]
        params: List[Any] = [user_id, entity_type]

        if entity_key:
            conditions.append("entity_key = ?")
            params.append(entity_key)

        rows = db.execute(
            f"""SELECT * FROM memory_evolution
                WHERE {' AND '.join(conditions)}
                ORDER BY observed_at ASC""",
            tuple(params),
        )

        chain = []
        for row in (rows or []):
            entry = dict(row)
            if entry.get("similarity_score") is not None:
                entry["similarity_score"] = round(entry["similarity_score"], 4)
            chain.append(entry)

        logger.info(
            f"✓ 演变链查询: entity_type={entity_type}, "
            f"entity_key={entity_key}, versions={len(chain)}"
        )
        return {
            "success": True,
            "entity_type": entity_type,
            "entity_key": entity_key or "",
            "chain": chain,
            "total_versions": len(chain),
        }

    except Exception as e:
        logger.error(f"演变链查询失败: {e}")
        return {"success": False, "error": str(e), "chain": [], "total_versions": 0}


def get_evolution_history(
    user_id: int,
    fragment_id: int,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """查询某个记忆片段相关的演变历史（作为旧或新事实参与的所有演变事件）。

    Args:
        user_id: 用户 ID
        fragment_id: 记忆片段 ID
        workspace_id: workspace ID

    Returns:
        {
            "success": bool,
            "fragment_id": int,
            "as_superseded": List[Dict],  # 该片段被取代的事件
            "as_successor": List[Dict],   # 该片段取代他人的事件
        }
    """
    try:
        _ensure_evolution_table()
        db = get_db_client()

        # 作为被取代的旧事实
        old_rows = db.execute(
            """SELECT * FROM memory_evolution
               WHERE user_id = ? AND old_fragment_id = ?
               ORDER BY observed_at ASC""",
            (user_id, fragment_id),
        )
        as_superseded = [dict(r) for r in (old_rows or [])]

        # 作为新事实（取代了旧事实）
        new_rows = db.execute(
            """SELECT * FROM memory_evolution
               WHERE user_id = ? AND new_fragment_id = ?
               ORDER BY observed_at ASC""",
            (user_id, fragment_id),
        )
        as_successor = [dict(r) for r in (new_rows or [])]

        return {
            "success": True,
            "fragment_id": fragment_id,
            "as_superseded": as_superseded,
            "as_successor": as_successor,
        }

    except Exception as e:
        logger.error(f"演变历史查询失败: {e}")
        return {"success": False, "error": str(e)}


def get_evolution_statistics(
    user_id: int,
    workspace_id: Optional[int] = None,
) -> Dict[str, Any]:
    """获取用户的演变统计信息。"""
    try:
        _ensure_evolution_table()
        db = get_db_client()

        rows = db.execute(
            """SELECT entity_type, detection_method, COUNT(*) as count
               FROM memory_evolution
               WHERE user_id = ?
               GROUP BY entity_type, detection_method""",
            (user_id,),
        )

        by_type: Dict[str, int] = {}
        by_method: Dict[str, int] = {}
        total = 0
        for row in (rows or []):
            et = row["entity_type"]
            dm = row["detection_method"]
            cnt = row["count"]
            by_type[et] = by_type.get(et, 0) + cnt
            by_method[dm] = by_method.get(dm, 0) + cnt
            total += cnt

        return {
            "success": True,
            "total_evolutions": total,
            "by_entity_type": by_type,
            "by_detection_method": by_method,
        }

    except Exception as e:
        logger.error(f"演变统计查询失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 4. 内部辅助函数
# ============================================================

def _mark_superseded(user_id: int, fragment_id: int) -> None:
    """将记忆片段标记为 superseded。"""
    db = get_db_client()
    db.execute(
        """UPDATE memory_fragments
           SET lifecycle_status = ?
           WHERE id = ? AND user_id = ?""",
        (_SUPERSEDED_STATUS, fragment_id, user_id),
    )


def _record_evolution(
    user_id: int,
    workspace_id: Optional[int],
    entity_type: str,
    entity_key: Optional[str],
    old_fragment_id: Optional[int],
    new_fragment_id: Optional[int],
    old_value: Optional[str],
    new_value: Optional[str],
    detection_method: str,
    similarity_score: Optional[float],
    change_reason: str,
    observed_at: str,
) -> int:
    """写入一条演变记录，返回记录 ID。"""
    _ensure_evolution_table()
    db = get_db_client()
    return db.execute(
        """INSERT INTO memory_evolution
           (user_id, workspace_id, entity_type, entity_key,
            old_fragment_id, new_fragment_id, old_value, new_value,
            detection_method, similarity_score, change_reason, observed_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, workspace_id, entity_type, entity_key,
         old_fragment_id, new_fragment_id, old_value, new_value,
         detection_method, similarity_score, change_reason, observed_at),
    )
