"""
图谱社区检测测试（P2 R-15）

覆盖：
- 造 2 个明显分离的实体簇 → detect 得到 >=2 社区、modularity > 0、标签非空
- COMMUNITY_MIN_SIZE 过滤
- 重复 detect 覆盖旧结果
- 空图降级
- stats/查询端点
"""
import pytest
import json

USER_ID = 999
_WS = None  # 测试用 workspace_id


@pytest.fixture(autouse=True)
def cleanup_community():
    """清理社区相关表 + 测试用实体/关系"""
    yield
    from app.core.db_client import get_db_client
    db = get_db_client()
    for sql in [
        'DELETE FROM graph_communities WHERE user_id = ?',
        'DELETE FROM graph_community_runs WHERE user_id = ?',
        'DELETE FROM graph_relationships WHERE user_id = ?',
        'DELETE FROM graph_entities WHERE user_id = ?',
    ]:
        try:
            db.execute(sql, (USER_ID,))
        except Exception:
            pass


def _create_entity(db, name: str, entity_type: str = "concept") -> int:
    return db.execute(
        '''INSERT INTO graph_entities (user_id, workspace_id, name, entity_type)
           VALUES (?, ?, ?, ?)''',
        (USER_ID, _WS, name, entity_type)
    )


def _create_rel(db, src: int, tgt: int, confidence: float = 0.8):
    db.execute(
        '''INSERT INTO graph_relationships
           (user_id, workspace_id, source_entity_id, target_entity_id, relation_type, confidence, is_active)
           VALUES (?, ?, ?, ?, 'relates_to', ?, 1)''',
        (USER_ID, _WS, src, tgt, confidence)
    )


def _build_two_clusters():
    """造 2 个明显分离的实体簇（簇内密连，簇间仅一条弱边）"""
    from app.core.db_client import get_db_client
    from app.services.graph_memory_service import _ensure_graph_tables
    _ensure_graph_tables()
    db = get_db_client()

    # 簇 A: Python / FastAPI / Uvicorn（强连接）
    a1 = _create_entity(db, "Python")
    a2 = _create_entity(db, "FastAPI")
    a3 = _create_entity(db, "Uvicorn")
    _create_rel(db, a1, a2, 0.95)
    _create_rel(db, a2, a3, 0.90)
    _create_rel(db, a1, a3, 0.85)

    # 簇 B: React / TypeScript / Vite（强连接）
    b1 = _create_entity(db, "React")
    b2 = _create_entity(db, "TypeScript")
    b3 = _create_entity(db, "Vite")
    _create_rel(db, b1, b2, 0.92)
    _create_rel(db, b2, b3, 0.88)
    _create_rel(db, b1, b3, 0.80)

    # 簇间仅一条弱边
    _create_rel(db, a1, b1, 0.1)

    return {"cluster_a": [a1, a2, a3], "cluster_b": [b1, b2, b3]}


# ============================================================
# 核心测试
# ============================================================

class TestCommunityDetection:
    def test_detect_two_clusters(self):
        """2 个明显分离的簇 → >=2 社区、modularity > 0、标签非空"""
        _build_two_clusters()
        from app.services.graph_community_service import detect_communities
        result = detect_communities(USER_ID, workspace_id=_WS)
        assert result["success"] is True
        assert result["communities_found"] >= 2
        assert result["modularity"] > 0
        assert result["node_count"] == 6
        assert result["edge_count"] == 7  # 3+3+1
        for c in result["communities"]:
            assert c["label"]  # 标签非空
            assert c["entity_count"] >= 2

    def test_empty_graph(self):
        """空图 → 降级"""
        from app.services.graph_community_service import detect_communities
        result = detect_communities(USER_ID, workspace_id=_WS)
        assert result["success"] is False
        assert "空" in result.get("reason", "")

    def test_no_edges(self):
        """有实体但无关系 → 降级"""
        from app.core.db_client import get_db_client
        from app.services.graph_memory_service import _ensure_graph_tables
        _ensure_graph_tables()
        db = get_db_client()
        _create_entity(db, "Solo")
        from app.services.graph_community_service import detect_communities
        result = detect_communities(USER_ID, workspace_id=_WS)
        assert result["success"] is False
        assert "关系" in result.get("reason", "") or "边" in result.get("reason", "")

    def test_min_size_filter(self, monkeypatch):
        """COMMUNITY_MIN_SIZE 过滤：设为 10 → 6 节点的图不产生任何 >=10 社区"""
        from app.core.config import get_settings
        monkeypatch.setattr(get_settings(), "COMMUNITY_MIN_SIZE", 10)
        _build_two_clusters()
        from app.services.graph_community_service import detect_communities
        result = detect_communities(USER_ID, workspace_id=_WS)
        assert result["success"] is True
        assert result["communities_found"] == 0

    def test_detect_overwrites(self):
        """重复 detect 覆盖旧结果"""
        _build_two_clusters()
        from app.services.graph_community_service import detect_communities, get_community_stats
        detect_communities(USER_ID, workspace_id=_WS)
        detect_communities(USER_ID, workspace_id=_WS)
        stats = get_community_stats(USER_ID, workspace_id=_WS)
        assert stats["success"] is True
        # 应只有 1 个 run
        from app.core.db_client import get_db_client
        db = get_db_client()
        rows = db.execute(
            'SELECT COUNT(*) as c FROM graph_community_runs WHERE user_id = ?', (USER_ID,)
        )
        assert rows[0]["c"] == 1


# ============================================================
# 查询接口
# ============================================================

class TestCommunityQuery:
    def test_get_communities(self):
        _build_two_clusters()
        from app.services.graph_community_service import detect_communities, get_communities
        detect_communities(USER_ID, workspace_id=_WS)
        result = get_communities(USER_ID, workspace_id=_WS)
        assert result["success"] is True
        assert result["count"] >= 2
        for c in result["communities"]:
            assert "entities" in c
            assert len(c["entities"]) > 0

    def test_get_stats(self):
        _build_two_clusters()
        from app.services.graph_community_service import detect_communities, get_community_stats
        detect_communities(USER_ID, workspace_id=_WS)
        result = get_community_stats(USER_ID, workspace_id=_WS)
        assert result["success"] is True
        s = result["stats"]
        assert s["algorithm"] == "louvain"
        assert s["node_count"] == 6
        assert s["modularity"] > 0

    def test_no_run_yet(self):
        """未执行过检测 → 空结果"""
        from app.services.graph_community_service import get_communities, get_community_stats
        r1 = get_communities(USER_ID, workspace_id=_WS)
        assert r1["success"] is True
        assert r1["count"] == 0
        r2 = get_community_stats(USER_ID, workspace_id=_WS)
        assert r2["success"] is True
        assert r2["stats"] is None
