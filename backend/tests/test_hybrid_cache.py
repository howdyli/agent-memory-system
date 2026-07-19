"""
混合搜索：查询缓存 / 分页 / 子搜索贡献度分析 测试（P1）

使用 fakeredis + monkeypatch 隔离语义/BM25 检索与 DB，不依赖真实向量库或 LLM。
"""
import pytest

from app.core.redis_client import get_redis_client
from app.services import hybrid_search_service as hss


TEST_USER = 970001


class _FakeDB:
    """最小 DB 桩：仅支持 SELECT * FROM memory_fragments WHERE id = ?。"""

    def __init__(self, fragments):
        # id -> fragment dict
        self._by_id = {int(f["id"]): f for f in fragments}

    def execute(self, sql, params=None):
        if params and "memory_fragments WHERE id" in sql:
            fid = int(params[0])
            frag = self._by_id.get(fid)
            return [dict(frag)] if frag else []
        return []


def _install_mocks(monkeypatch, semantic_ids, bm25_ids, fragments):
    """安装语义/BM25/DB/rerank/boost/recency 桩，返回调用计数器。"""
    counters = {"semantic": 0, "bm25": 0}

    def fake_semantic(user_id, query, top_k=None, threshold=None):
        counters["semantic"] += 1
        return {
            "success": True,
            "fragments": [
                {"id": fid, "similarity": sim} for fid, sim in semantic_ids
            ],
        }

    def fake_bm25(query, user_id, top_k=None):
        counters["bm25"] += 1
        return {
            "success": True,
            "fragments": [{"id": fid, "bm25_score": sc} for fid, sc in bm25_ids],
        }

    fake_db = _FakeDB(fragments)

    monkeypatch.setattr(hss, "search_fragments_by_semantic", fake_semantic)
    monkeypatch.setattr(hss, "search_bm25", fake_bm25)
    monkeypatch.setattr(hss, "get_db_client", lambda: fake_db)
    monkeypatch.setattr(hss, "rerank_with_llm", lambda **kw: [])
    monkeypatch.setattr(hss, "compute_entity_boost", lambda query, frag: 0.0)
    monkeypatch.setattr(hss, "compute_recency_score", lambda frag: 0.0)
    return counters


def _reset_config_state():
    """清空模块内配置缓存与 redis 中的配置/查询缓存，回到默认版本。"""
    hss._memory_config_cache = None
    redis = get_redis_client()
    if redis:
        conn = redis.get_connection()
        try:
            conn.flushdb()
        except Exception:
            redis.delete("hybrid_search_config")


@pytest.fixture(autouse=True)
def _clean_state():
    _reset_config_state()
    yield
    _reset_config_state()


def _make_fragments(ids):
    return [
        {"id": fid, "content": f"内容 {fid}", "fragment_type": "info", "importance": 0.5}
        for fid in ids
    ]


# ============================================================
# 查询缓存
# ============================================================

def test_cache_miss_then_hit(monkeypatch):
    """首次未命中缓存，二次命中且不再触发子检索。"""
    frags = _make_fragments([1, 2, 3])
    counters = _install_mocks(
        monkeypatch,
        semantic_ids=[(1, 0.9), (2, 0.8), (3, 0.7)],
        bm25_ids=[],
        fragments=frags,
    )

    r1 = hss.hybrid_search(TEST_USER, "查询")
    assert r1["success"] is True
    assert r1["cache_hit"] is False
    assert counters["semantic"] == 1

    r2 = hss.hybrid_search(TEST_USER, "查询")
    assert r2["success"] is True
    assert r2["cache_hit"] is True
    # 命中缓存不应再调用语义检索
    assert counters["semantic"] == 1
    assert [f["id"] for f in r2["fragments"]] == [f["id"] for f in r1["fragments"]]


def test_cache_version_invalidation(monkeypatch):
    """update_config 自增 cache_version 后旧缓存失效，重新计算。"""
    frags = _make_fragments([1, 2, 3])
    counters = _install_mocks(
        monkeypatch,
        semantic_ids=[(1, 0.9), (2, 0.8), (3, 0.7)],
        bm25_ids=[],
        fragments=frags,
    )

    hss.hybrid_search(TEST_USER, "查询")   # miss -> 写缓存
    hss.hybrid_search(TEST_USER, "查询")   # hit
    assert counters["semantic"] == 1

    v_before = int(hss.get_config()["cache_version"])
    hss.update_config({"alpha": 0.5})
    v_after = int(hss.get_config()["cache_version"])
    assert v_after == v_before + 1

    # 版本变更后应重新计算（新键未命中）
    r = hss.hybrid_search(TEST_USER, "查询")
    assert r["cache_hit"] is False
    assert counters["semantic"] == 2


def test_cache_empty_candidates(monkeypatch):
    """空候选池也应成功返回并可被缓存。"""
    counters = _install_mocks(
        monkeypatch, semantic_ids=[], bm25_ids=[], fragments=[]
    )
    r1 = hss.hybrid_search(TEST_USER, "无结果")
    assert r1["success"] is True
    assert r1["total"] == 0
    assert r1["count"] == 0
    assert r1["has_more"] is False

    r2 = hss.hybrid_search(TEST_USER, "无结果")
    assert r2["cache_hit"] is True
    assert counters["semantic"] == 1


# ============================================================
# 分页
# ============================================================

def test_pagination_offset_limit(monkeypatch):
    """在候选池上按 offset/limit 切片，total/has_more 正确。"""
    frags = _make_fragments([1, 2, 3, 4, 5])
    _install_mocks(
        monkeypatch,
        semantic_ids=[(1, 0.9), (2, 0.8), (3, 0.7), (4, 0.6), (5, 0.5)],
        bm25_ids=[],
        fragments=frags,
    )

    page1 = hss.hybrid_search(TEST_USER, "分页", offset=0, limit=2)
    assert page1["total"] == 5
    assert page1["count"] == 2
    assert page1["has_more"] is True
    assert page1["offset"] == 0
    assert page1["limit"] == 2

    page_last = hss.hybrid_search(TEST_USER, "分页", offset=4, limit=2)
    assert page_last["count"] == 1
    assert page_last["has_more"] is False

    page_over = hss.hybrid_search(TEST_USER, "分页", offset=10, limit=2)
    assert page_over["count"] == 0
    assert page_over["has_more"] is False


# ============================================================
# 子搜索贡献度分析
# ============================================================

def test_analyze_search_overlap_and_sensitivity(monkeypatch):
    """analyze_search 返回重叠统计与权重敏感度。"""
    frags = _make_fragments([1, 2, 3, 4])
    _install_mocks(
        monkeypatch,
        semantic_ids=[(1, 0.9), (2, 0.8), (3, 0.7)],
        bm25_ids=[(2, 1.0), (3, 0.9), (4, 0.8)],
        fragments=frags,
    )

    result = hss.analyze_search(TEST_USER, "分析")
    assert result["success"] is True

    overlap = result["overlap"]
    assert overlap["semantic_total"] == 3
    assert overlap["bm25_total"] == 3
    assert overlap["semantic_only"] == 1   # {1}
    assert overlap["bm25_only"] == 1       # {4}
    assert overlap["both"] == 2            # {2,3}

    # 候选带信号分解
    assert len(result["candidates"]) >= 1
    assert "signal_breakdown" in result["candidates"][0]

    # 权重敏感度覆盖四个权重，且 rank_changes 为整数
    sens = result["weight_sensitivity"]
    for wk in ("alpha", "beta", "gamma", "delta"):
        assert wk in sens
        assert isinstance(sens[wk]["plus"]["rank_changes"], int)
        assert isinstance(sens[wk]["minus"]["rank_changes"], int)
