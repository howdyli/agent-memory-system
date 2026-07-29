"""
睡眠期记忆巩固服务（Sleep-time Compute, P1 R-11）

在维护周期内对语义相近的活跃记忆聚簇，调用 LLM 合并/压缩为单条巩固记忆：
- 聚簇：基于 ChromaDB 向量相似度贪心合并（阈值 CONSOLIDATION_SIMILARITY_THRESHOLD）
- 巩固：LLM 输出严格 JSON（合并重复信息、保留全部独有事实与时间信息、不得虚构）
- 写入：新建巩固记忆（跳过矛盾检测），旧记忆标记 superseded 并写入演变链与合并日志
- 降级：LLM 不可用/返回 mock/JSON 解析失败时跳过该簇，不做规则式改写
"""
import logging
import json
import re
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.chromadb_client import get_chromadb_client
from app.core.config import get_settings


# ============================================================
# consolidation_runs 审计表
# ============================================================

def _ensure_consolidation_table():
    """创建巩固运行审计表（与 memory_evolution 同款模式）"""
    db = get_db_client()
    db.execute('''CREATE TABLE IF NOT EXISTS consolidation_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        workspace_id INTEGER,
        started_at TIMESTAMP,
        finished_at TIMESTAMP,
        clusters_found INTEGER DEFAULT 0,
        clusters_consolidated INTEGER DEFAULT 0,
        fragments_superseded INTEGER DEFAULT 0,
        skipped_reason_stats TEXT,
        status TEXT DEFAULT 'completed',
        "trigger" TEXT DEFAULT 'manual',
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    try:
        db.execute('CREATE INDEX IF NOT EXISTS idx_consolidation_runs_user ON consolidation_runs(user_id)')
    except Exception:
        pass


# ============================================================
# 候选聚簇
# ============================================================

def _get_candidates(user_id: int, workspace_id: Optional[int]) -> List[Dict[str, Any]]:
    """获取巩固候选记忆：active 且创建超过 CONSOLIDATION_MIN_AGE_HOURS 的片段"""
    settings = get_settings()
    db = get_db_client()
    cutoff = (datetime.now() - timedelta(hours=settings.CONSOLIDATION_MIN_AGE_HOURS)).isoformat()

    conditions = [
        "user_id = ?",
        "(lifecycle_status IS NULL OR lifecycle_status = 'active')",
        "created_at <= ?",
    ]
    params: List[Any] = [user_id, cutoff]
    if workspace_id is not None:
        conditions.append("workspace_id = ?")
        params.append(workspace_id)

    rows = db.execute(
        f'''SELECT * FROM memory_fragments
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at ASC
            LIMIT ?''',
        tuple(params + [settings.CONSOLIDATION_MAX_CANDIDATES_PER_RUN])
    )
    return [dict(r) for r in rows] if rows else []


def _find_clusters(user_id: int, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """基于向量相似度贪心聚簇（已入簇的跳过；同类型且同 workspace 才合并）

    Returns:
        簇列表，每个簇为 {"fragments": [...], "similarities": {fragment_id: sim}}
    """
    settings = get_settings()
    chroma = get_chromadb_client()
    if chroma is None or not candidates:
        return []

    candidate_map = {c["id"]: c for c in candidates}
    clustered_ids = set()
    clusters = []

    for seed in candidates:
        if seed["id"] in clustered_ids:
            continue
        if len(clusters) >= settings.CONSOLIDATION_MAX_CLUSTERS_PER_RUN:
            break
        try:
            results = chroma.search_embeddings(
                query_text=seed["content"],
                n_results=20,
                where={"user_id": str(user_id)}
            )
        except Exception as e:
            logger.warning(f"⚠️  巩固聚簇向量检索失败（跳过种子 {seed['id']}）: {e}")
            continue

        members = [seed]
        similarities = {seed["id"]: 1.0}
        for r in results or []:
            similarity = r.get("similarity")
            if similarity is None or similarity < settings.CONSOLIDATION_SIMILARITY_THRESHOLD:
                continue
            metadata = r.get("metadata", {})
            try:
                fid = int(metadata.get("fragment_id", 0))
            except (TypeError, ValueError):
                continue
            if fid == seed["id"] or fid in clustered_ids:
                continue
            frag = candidate_map.get(fid)
            if frag is None:
                continue  # 非候选（superseded/cold/太新）不参与巩固
            if frag["fragment_type"] != seed["fragment_type"]:
                continue
            if frag.get("workspace_id") != seed.get("workspace_id"):
                continue
            members.append(frag)
            similarities[fid] = round(float(similarity), 4)

        if len(members) >= settings.CONSOLIDATION_MIN_CLUSTER_SIZE:
            for m in members:
                clustered_ids.add(m["id"])
            clusters.append({"fragments": members, "similarities": similarities})

    return clusters


# ============================================================
# LLM 巩固
# ============================================================

_CONSOLIDATION_PROMPT = """你是一个记忆巩固助手。以下是同一用户的多条语义相近的记忆片段，请将它们合并压缩为一条巩固记忆。

要求：
1. 合并重复信息，压缩冗余表述
2. 保留全部独有事实与时间信息，不得丢失细节
3. 不得虚构任何原文中不存在的信息
4. 输出严格 JSON（不要任何额外说明文字）：
{"consolidated_content": "巩固后的记忆内容", "fragment_type": "记忆类型", "rationale": "巩固理由（一句话）"}

记忆片段：
"""


def _parse_llm_json(content: str) -> Optional[Dict[str, Any]]:
    """解析 LLM 返回的 JSON，带 ```json 代码块正则提取兜底"""
    if not content:
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        pass
    # 兜底：提取 ```json ... ``` 或第一个 {...} 块
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
    if not match:
        match = re.search(r'(\{.*\})', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except (json.JSONDecodeError, TypeError):
            return None
    return None


def _consolidate_cluster_with_llm(user_id: int, cluster: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """调用 LLM 巩固单个簇。mock/解析失败/字段缺失返回 None（降级跳过）"""
    from app.services.llm_backend_service import llm_chat

    fragments = cluster["fragments"]
    lines = []
    for f in fragments:
        lines.append(f"- [类型: {f['fragment_type']}, 创建于: {f.get('created_at', '未知')}] {f['content']}")
    prompt = _CONSOLIDATION_PROMPT + "\n".join(lines)

    try:
        response = llm_chat(user_id, [
            {"role": "system", "content": "你是一个严谨的记忆巩固助手，只输出 JSON。"},
            {"role": "user", "content": prompt}
        ])
    except Exception as e:
        logger.warning(f"⚠️  巩固 LLM 调用异常: {e}")
        return None

    if not response.get("success") or response.get("mock"):
        return None

    parsed = _parse_llm_json(response.get("content", ""))
    if not parsed or not parsed.get("consolidated_content"):
        return None
    return parsed


# ============================================================
# 主流程
# ============================================================

def run_consolidation(user_id: int,
                      workspace_id: Optional[int] = None,
                      dry_run: bool = False,
                      trigger: str = "manual") -> Dict[str, Any]:
    """
    执行睡眠期记忆巩固

    Args:
        user_id: 用户 ID
        workspace_id: workspace ID（None 表示不限）
        dry_run: 仅返回聚簇预览，不调 LLM、不写库
        trigger: 触发来源（scheduled / manual）

    Returns:
        巩固结果统计
    """
    try:
        settings = get_settings()
        started_at = datetime.now().isoformat()

        if not settings.CONSOLIDATION_ENABLED:
            return {"success": True, "skipped": True, "reason": "CONSOLIDATION_ENABLED=False"}

        candidates = _get_candidates(user_id, workspace_id)
        clusters = _find_clusters(user_id, candidates)

        if dry_run:
            # 预览模式：只返回簇成员与相似度
            preview = []
            for c in clusters:
                preview.append({
                    "fragment_ids": [f["id"] for f in c["fragments"]],
                    "fragments": [
                        {"id": f["id"], "content": f["content"],
                         "fragment_type": f["fragment_type"],
                         "similarity": c["similarities"].get(f["id"])}
                        for f in c["fragments"]
                    ],
                })
            return {
                "success": True,
                "dry_run": True,
                "candidates": len(candidates),
                "clusters_found": len(clusters),
                "clusters": preview,
            }

        from app.services.memory_fragment_service import create_fragment
        from app.services.contradiction_service import _mark_superseded, _record_evolution

        db = get_db_client()
        consolidated = 0
        superseded_total = 0
        skipped_stats: Dict[str, int] = {}

        for cluster in clusters:
            fragments = cluster["fragments"]
            llm_result = _consolidate_cluster_with_llm(user_id, cluster)
            if llm_result is None:
                skipped_stats["llm_unavailable_or_parse_failed"] = \
                    skipped_stats.get("llm_unavailable_or_parse_failed", 0) + 1
                continue

            source_ids = [f["id"] for f in fragments]
            max_importance = max(float(f.get("importance_score") or 0.5) for f in fragments)
            fragment_type = llm_result.get("fragment_type") or fragments[0]["fragment_type"]
            cluster_workspace = fragments[0].get("workspace_id")

            # 新建巩固记忆（跳过矛盾检测，避免 superseded 级联）
            create_result = create_fragment(
                user_id=user_id,
                fragment_type=fragment_type,
                content=llm_result["consolidated_content"],
                importance_score=max_importance,
                metadata={
                    "consolidated_from": source_ids,
                    "rationale": llm_result.get("rationale", ""),
                },
                workspace_id=cluster_workspace,
                skip_contradiction=True,
            )
            if not create_result.get("success"):
                skipped_stats["create_failed"] = skipped_stats.get("create_failed", 0) + 1
                continue
            new_fragment_id = create_result["fragment_id"]

            # 旧记忆标记 superseded + 写入演变链
            observed_at = datetime.now().isoformat()
            for f in fragments:
                _mark_superseded(user_id, f["id"])
                _record_evolution(
                    user_id=user_id,
                    workspace_id=cluster_workspace,
                    entity_type="fragment",
                    entity_key=None,
                    old_fragment_id=f["id"],
                    new_fragment_id=new_fragment_id,
                    old_value=f["content"],
                    new_value=llm_result["consolidated_content"],
                    detection_method="consolidation",
                    similarity_score=cluster["similarities"].get(f["id"]),
                    change_reason="sleep_time_consolidation",
                    observed_at=observed_at,
                )
                superseded_total += 1

            # 写入合并审计日志（与既有 merge_log 口径一致）
            sims = [s for fid, s in cluster["similarities"].items() if fid != fragments[0]["id"]]
            avg_sim = round(sum(sims) / len(sims), 4) if sims else 1.0
            db.execute(
                '''INSERT INTO memory_merge_log
                   (user_id, memory_type, source_ids, target_id,
                    merge_type, merge_action, similarity_score,
                    old_value, new_value, operator, resolved)
                   VALUES (?, 'fragment', ?, ?, 'consolidation', 'consolidated',
                    ?, ?, ?, 'system', 1)''',
                (user_id, json.dumps(source_ids), str(new_fragment_id),
                 avg_sim,
                 json.dumps([f["content"] for f in fragments], ensure_ascii=False),
                 llm_result["consolidated_content"])
            )
            consolidated += 1

        # 运行审计
        _ensure_consolidation_table()
        db.execute(
            '''INSERT INTO consolidation_runs
               (user_id, workspace_id, started_at, finished_at,
                clusters_found, clusters_consolidated, fragments_superseded,
                skipped_reason_stats, status, "trigger")
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'completed', ?)''',
            (user_id, workspace_id, started_at, datetime.now().isoformat(),
             len(clusters), consolidated, superseded_total,
             json.dumps(skipped_stats), trigger)
        )

        logger.info(f"✓ 记忆巩固完成: user={user_id}, 簇 {len(clusters)}, "
                    f"巩固 {consolidated}, superseded {superseded_total}, 跳过 {skipped_stats}")
        return {
            "success": True,
            "candidates": len(candidates),
            "clusters_found": len(clusters),
            "clusters_consolidated": consolidated,
            "fragments_superseded": superseded_total,
            "skipped_reason_stats": skipped_stats,
            "trigger": trigger,
        }

    except Exception as e:
        logger.error(f"✗ 记忆巩固失败: {e}")
        return {"success": False, "error": str(e)}


def run_scheduled_consolidation() -> Dict[str, Any]:
    """调度入口：对所有存在候选记忆的用户执行巩固（供 run_maintenance_now 调用）"""
    try:
        settings = get_settings()
        if not settings.CONSOLIDATION_ENABLED:
            return {"success": True, "skipped": True, "reason": "CONSOLIDATION_ENABLED=False"}

        db = get_db_client()
        cutoff = (datetime.now() - timedelta(hours=settings.CONSOLIDATION_MIN_AGE_HOURS)).isoformat()
        rows = db.execute(
            '''SELECT DISTINCT user_id FROM memory_fragments
               WHERE (lifecycle_status IS NULL OR lifecycle_status = 'active')
               AND created_at <= ?''',
            (cutoff,)
        )
        user_ids = [r["user_id"] for r in rows] if rows else []

        results = []
        for uid in user_ids:
            result = run_consolidation(uid, trigger="scheduled")
            results.append({"user_id": uid, **{k: v for k, v in result.items() if k != "success"}})

        return {"success": True, "users_processed": len(user_ids), "results": results}
    except Exception as e:
        logger.error(f"✗ 调度巩固失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 查询接口（history / statistics）
# ============================================================

def get_consolidation_history(user_id: int, limit: int = 20, offset: int = 0) -> Dict[str, Any]:
    """分页查询巩固运行历史"""
    try:
        _ensure_consolidation_table()
        db = get_db_client()
        rows = db.execute(
            '''SELECT * FROM consolidation_runs WHERE user_id = ?
               ORDER BY id DESC LIMIT ? OFFSET ?''',
            (user_id, limit, offset)
        )
        runs = [dict(r) for r in rows] if rows else []
        count_rows = db.execute(
            'SELECT COUNT(*) as total FROM consolidation_runs WHERE user_id = ?',
            (user_id,)
        )
        total = count_rows[0]["total"] if count_rows else 0
        return {"success": True, "runs": runs, "count": len(runs), "total": total}
    except Exception as e:
        logger.error(f"✗ 查询巩固历史失败: {e}")
        return {"success": False, "error": str(e)}


def get_consolidation_statistics(user_id: int) -> Dict[str, Any]:
    """巩固统计：累计巩固条数、节省条数、按 trigger 分布"""
    try:
        _ensure_consolidation_table()
        db = get_db_client()
        rows = db.execute(
            '''SELECT COUNT(*) as total_runs,
                      COALESCE(SUM(clusters_consolidated), 0) as total_consolidated,
                      COALESCE(SUM(fragments_superseded), 0) as total_superseded
               FROM consolidation_runs WHERE user_id = ?''',
            (user_id,)
        )
        stats = dict(rows[0]) if rows else {"total_runs": 0, "total_consolidated": 0, "total_superseded": 0}

        # 节省条数 = superseded 总数 - 新建巩固记忆数
        stats["fragments_saved"] = max(
            0, (stats.get("total_superseded") or 0) - (stats.get("total_consolidated") or 0))

        trigger_rows = db.execute(
            '''SELECT "trigger", COUNT(*) as cnt FROM consolidation_runs
               WHERE user_id = ? GROUP BY "trigger"''',
            (user_id,)
        )
        stats["by_trigger"] = {r["trigger"]: r["cnt"] for r in trigger_rows} if trigger_rows else {}

        return {"success": True, "statistics": stats}
    except Exception as e:
        logger.error(f"✗ 查询巩固统计失败: {e}")
        return {"success": False, "error": str(e)}
