"""
图谱社区检测服务（P2 R-15）

对 graph_entities/graph_relationships 构建无向加权图（边权 = confidence），
用 networkx Louvain 算法发现社区，结果持久化（覆盖式）：
- graph_community_runs：每次检测的运行元信息（algorithm/resolution/modularity/规模）
- graph_communities：各社区成员（entity_ids JSON）与规则标签（度数 Top3 实体名拼接）

降级：networkx 不可用 / 图为空时返回 {"success": False, "reason": ...}，不抛异常。
"""
import logging
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.config import get_settings
from app.services.graph_memory_service import _ensure_graph_tables, _ws_sql


# ============================================================
# 社区结果表
# ============================================================

def _ensure_community_tables() -> None:
    """创建社区检测结果表（与 consolidation_runs 同款服务内建表模式）"""
    db = get_db_client()
    for sql in [
        '''CREATE TABLE IF NOT EXISTS graph_community_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            workspace_id INTEGER,
            algorithm TEXT DEFAULT 'louvain',
            resolution REAL DEFAULT 1.0,
            communities_found INTEGER DEFAULT 0,
            modularity REAL,
            node_count INTEGER DEFAULT 0,
            edge_count INTEGER DEFAULT 0,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            status TEXT DEFAULT 'completed',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''',
        '''CREATE TABLE IF NOT EXISTS graph_communities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            workspace_id INTEGER,
            run_id INTEGER NOT NULL,
            community_index INTEGER NOT NULL,
            entity_ids TEXT NOT NULL,
            entity_count INTEGER DEFAULT 0,
            label TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (run_id) REFERENCES graph_community_runs(id)
        )''',
    ]:
        try:
            db.execute(sql)
        except Exception:
            pass
    for sql in [
        'CREATE INDEX IF NOT EXISTS idx_community_runs_user ON graph_community_runs(user_id)',
        'CREATE INDEX IF NOT EXISTS idx_communities_user ON graph_communities(user_id)',
        'CREATE INDEX IF NOT EXISTS idx_communities_run ON graph_communities(run_id)',
    ]:
        try:
            db.execute(sql)
        except Exception:
            pass


# ============================================================
# 图构建
# ============================================================

def _load_graph_data(user_id: int, workspace_id: Optional[int]) -> Dict[str, Any]:
    """读取活跃实体与未过期关系（复用 _ws_sql workspace 过滤惯例）"""
    _ensure_graph_tables()
    db = get_db_client()
    ws_clause, ws_params = _ws_sql(workspace_id)

    entity_rows = db.execute(
        f'SELECT id, name, entity_type FROM graph_entities WHERE user_id = ? AND {ws_clause}',
        (user_id, *ws_params)
    )
    entities = {r["id"]: dict(r) for r in entity_rows} if entity_rows else {}

    rel_rows = db.execute(
        f'''SELECT source_entity_id, target_entity_id, confidence
            FROM graph_relationships
            WHERE user_id = ? AND {ws_clause} AND is_active = 1 AND expired_at IS NULL''',
        (user_id, *ws_params)
    )
    relationships = [dict(r) for r in rel_rows] if rel_rows else []
    return {"entities": entities, "relationships": relationships}


def _build_label(members: List[int], graph) -> str:
    """社区标签：取社区内度数最高的前 3 个实体名拼接（纯规则，不调 LLM）"""
    ranked = sorted(members, key=lambda n: graph.degree(n), reverse=True)[:3]
    names = [graph.nodes[n].get("name", str(n)) for n in ranked]
    return " / ".join(names)


# ============================================================
# 核心：社区检测
# ============================================================

def detect_communities(user_id: int,
                       workspace_id: Optional[int] = None,
                       resolution: Optional[float] = None) -> Dict[str, Any]:
    """
    运行 Louvain 社区检测并覆盖式持久化结果

    Returns:
        {"success": True, "run_id", "communities_found", "modularity",
         "node_count", "edge_count", "communities": [...]}
    """
    try:
        settings = get_settings()
        resolution = resolution if resolution is not None else settings.COMMUNITY_DETECTION_RESOLUTION
        started_at = datetime.now().isoformat()

        try:
            import networkx as nx
        except ImportError:
            return {"success": False, "reason": "networkx 未安装，无法执行社区检测"}

        data = _load_graph_data(user_id, workspace_id)
        entities = data["entities"]
        relationships = data["relationships"]

        if not entities:
            return {"success": False, "reason": "图谱为空（无活跃实体）"}

        # 构建无向加权图（孤立实体也入图，作为节点参与但不会形成 >=2 社区）
        G = nx.Graph()
        for eid, ent in entities.items():
            G.add_node(eid, name=ent["name"], entity_type=ent["entity_type"])
        for rel in relationships:
            src, tgt = rel["source_entity_id"], rel["target_entity_id"]
            if src in entities and tgt in entities:
                weight = float(rel.get("confidence") or 0.5)
                # 平行边取最大权重
                if G.has_edge(src, tgt):
                    G[src][tgt]["weight"] = max(G[src][tgt]["weight"], weight)
                else:
                    G.add_edge(src, tgt, weight=weight)

        if G.number_of_edges() == 0:
            return {"success": False, "reason": "图谱无有效关系边，无法划分社区"}

        # Louvain 社区发现（seed 固定保证可重复）
        raw_communities = nx.community.louvain_communities(
            G, weight="weight", resolution=resolution, seed=42
        )
        modularity = nx.community.modularity(
            G, raw_communities, weight="weight", resolution=resolution
        )

        # 过滤小社区
        communities = [sorted(c) for c in raw_communities if len(c) >= settings.COMMUNITY_MIN_SIZE]
        communities.sort(key=len, reverse=True)

        # 覆盖式写入：删除该 user/workspace 旧结果 → 写新 run + communities
        _ensure_community_tables()
        db = get_db_client()
        ws_clause, ws_params = _ws_sql(workspace_id)
        db.execute(
            f'DELETE FROM graph_communities WHERE user_id = ? AND {ws_clause}',
            (user_id, *ws_params)
        )
        db.execute(
            f'DELETE FROM graph_community_runs WHERE user_id = ? AND {ws_clause}',
            (user_id, *ws_params)
        )

        run_id = db.execute(
            '''INSERT INTO graph_community_runs
               (user_id, workspace_id, algorithm, resolution, communities_found,
                modularity, node_count, edge_count, started_at, finished_at, status)
               VALUES (?, ?, 'louvain', ?, ?, ?, ?, ?, ?, ?, 'completed')''',
            (user_id, workspace_id, resolution, len(communities),
             round(float(modularity), 4), G.number_of_nodes(), G.number_of_edges(),
             started_at, datetime.now().isoformat())
        )

        community_summaries = []
        for idx, members in enumerate(communities):
            label = _build_label(members, G)
            db.execute(
                '''INSERT INTO graph_communities
                   (user_id, workspace_id, run_id, community_index, entity_ids, entity_count, label)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (user_id, workspace_id, run_id, idx,
                 json.dumps(members), len(members), label)
            )
            community_summaries.append({
                "community_index": idx,
                "entity_ids": members,
                "entity_count": len(members),
                "label": label,
            })

        logger.info(f"✓ 社区检测完成: user={user_id}, 节点 {G.number_of_nodes()}, "
                    f"边 {G.number_of_edges()}, 社区 {len(communities)}, "
                    f"modularity {modularity:.4f}")
        return {
            "success": True,
            "run_id": run_id,
            "algorithm": "louvain",
            "resolution": resolution,
            "communities_found": len(communities),
            "modularity": round(float(modularity), 4),
            "node_count": G.number_of_nodes(),
            "edge_count": G.number_of_edges(),
            "communities": community_summaries,
        }

    except Exception as e:
        logger.error(f"✗ 社区检测失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 查询接口
# ============================================================

def get_communities(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """返回最近一次 run 的社区列表（含实体详情）"""
    try:
        _ensure_community_tables()
        db = get_db_client()
        ws_clause, ws_params = _ws_sql(workspace_id)

        run_rows = db.execute(
            f'''SELECT * FROM graph_community_runs
                WHERE user_id = ? AND {ws_clause}
                ORDER BY id DESC LIMIT 1''',
            (user_id, *ws_params)
        )
        if not run_rows:
            return {"success": True, "communities": [], "count": 0,
                    "message": "尚未执行社区检测"}
        run = dict(run_rows[0])

        community_rows = db.execute(
            'SELECT * FROM graph_communities WHERE run_id = ? ORDER BY community_index ASC',
            (run["id"],)
        )
        communities = []
        for row in (community_rows or []):
            c = dict(row)
            entity_ids = json.loads(c["entity_ids"] or "[]")
            entity_details = []
            if entity_ids:
                placeholders = ",".join("?" * len(entity_ids))
                ent_rows = db.execute(
                    f'SELECT id, name, entity_type FROM graph_entities WHERE id IN ({placeholders})',
                    tuple(entity_ids)
                )
                entity_details = [dict(r) for r in ent_rows] if ent_rows else []
            communities.append({
                "community_index": c["community_index"],
                "label": c["label"],
                "entity_count": c["entity_count"],
                "entities": entity_details,
            })

        return {
            "success": True,
            "run_id": run["id"],
            "detected_at": run.get("finished_at"),
            "communities": communities,
            "count": len(communities),
        }
    except Exception as e:
        logger.error(f"✗ 查询社区失败: {e}")
        return {"success": False, "error": str(e)}


def get_community_stats(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """返回最近一次 run 的元信息（modularity、社区数、节点/边数）"""
    try:
        _ensure_community_tables()
        db = get_db_client()
        ws_clause, ws_params = _ws_sql(workspace_id)
        rows = db.execute(
            f'''SELECT * FROM graph_community_runs
                WHERE user_id = ? AND {ws_clause}
                ORDER BY id DESC LIMIT 1''',
            (user_id, *ws_params)
        )
        if not rows:
            return {"success": True, "stats": None, "message": "尚未执行社区检测"}
        run = dict(rows[0])
        return {
            "success": True,
            "stats": {
                "run_id": run["id"],
                "algorithm": run["algorithm"],
                "resolution": run["resolution"],
                "communities_found": run["communities_found"],
                "modularity": run["modularity"],
                "node_count": run["node_count"],
                "edge_count": run["edge_count"],
                "detected_at": run.get("finished_at"),
            },
        }
    except Exception as e:
        logger.error(f"✗ 查询社区统计失败: {e}")
        return {"success": False, "error": str(e)}
