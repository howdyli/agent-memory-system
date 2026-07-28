"""
P1 R-11 睡眠期记忆巩固测试

覆盖：聚簇正确性、LLM 巩固写入（superseded + 演变链 + merge_log）、
mock/解析失败降级、dry_run、MIN_AGE 过滤、consolidation_runs 审计、API 冒烟。
"""
import json
import pytest
from datetime import datetime, timedelta

from app.core.db_client import get_db_client
from app.services import memory_consolidation_service as mcs
from app.services.memory_fragment_service import create_fragment

TEST_USER_ID = 999


# ============================================================
# 测试辅助
# ============================================================

class FakeChroma:
    """伪 ChromaDB：按预置相似度表返回搜索结果"""

    def __init__(self, similarity_map):
        # similarity_map: {seed_content: [(fragment_id, similarity), ...]}
        self.similarity_map = similarity_map

    def search_embeddings(self, query_text, n_results=10, where=None):
        results = []
        for fid, sim in self.similarity_map.get(query_text, []):
            results.append({
                "similarity": sim,
                "metadata": {"fragment_id": str(fid)},
                "document": "",
            })
        return results


def _make_fragment(content, fragment_type="info", age_hours=48, importance=0.5):
    """创建测试片段并回拨 created_at（跳过矛盾检测）"""
    result = create_fragment(
        user_id=TEST_USER_ID,
        fragment_type=fragment_type,
        content=content,
        importance_score=importance,
        skip_contradiction=True,
    )
    assert result["success"]
    fid = result["fragment_id"]
    db = get_db_client()
    old_time = (datetime.now() - timedelta(hours=age_hours)).isoformat()
    db.execute('UPDATE memory_fragments SET created_at = ? WHERE id = ?', (old_time, fid))
    return fid


def _fake_llm_success(user_id, messages, **kwargs):
    return {
        "success": True,
        "content": json.dumps({
            "consolidated_content": "用户在北京工作，是一名后端工程师，使用 Python。",
            "fragment_type": "info",
            "rationale": "合并重复的职业信息",
        }, ensure_ascii=False),
    }


def _fake_llm_mock(user_id, messages, **kwargs):
    return {"success": True, "mock": True, "content": "[Mock] 降级响应"}


@pytest.fixture(autouse=True)
def _cleanup_consolidation():
    yield
    db = get_db_client()
    try:
        db.execute('DELETE FROM consolidation_runs WHERE user_id = ?', (TEST_USER_ID,))
    except Exception:
        pass


# ============================================================
# 聚簇
# ============================================================

class TestClustering:

    def test_similar_fragments_clustered(self, db, monkeypatch):
        """相似记忆聚为一簇"""
        f1 = _make_fragment("用户在北京工作")
        f2 = _make_fragment("用户的工作地点是北京")
        fake = FakeChroma({
            "用户在北京工作": [(f2, 0.92)],
            "用户的工作地点是北京": [(f1, 0.92)],
        })
        monkeypatch.setattr(mcs, "get_chromadb_client", lambda: fake)

        candidates = mcs._get_candidates(TEST_USER_ID, None)
        clusters = mcs._find_clusters(TEST_USER_ID, candidates)
        assert len(clusters) == 1
        assert {f["id"] for f in clusters[0]["fragments"]} == {f1, f2}

    def test_independent_fragments_not_clustered(self, db, monkeypatch):
        """低相似度记忆不误合"""
        f1 = _make_fragment("用户喜欢喝咖啡")
        f2 = _make_fragment("用户养了一只猫")
        fake = FakeChroma({
            "用户喜欢喝咖啡": [(f2, 0.30)],
            "用户养了一只猫": [(f1, 0.30)],
        })
        monkeypatch.setattr(mcs, "get_chromadb_client", lambda: fake)

        clusters = mcs._find_clusters(TEST_USER_ID, mcs._get_candidates(TEST_USER_ID, None))
        assert clusters == []

    def test_different_types_not_clustered(self, db, monkeypatch):
        """类型不同的记忆即使相似也不合并"""
        f1 = _make_fragment("用户在北京工作", fragment_type="info")
        f2 = _make_fragment("用户计划在北京工作", fragment_type="plan")
        fake = FakeChroma({
            "用户在北京工作": [(f2, 0.95)],
            "用户计划在北京工作": [(f1, 0.95)],
        })
        monkeypatch.setattr(mcs, "get_chromadb_client", lambda: fake)

        clusters = mcs._find_clusters(TEST_USER_ID, mcs._get_candidates(TEST_USER_ID, None))
        assert clusters == []

    def test_min_age_filter(self, db, monkeypatch):
        """创建不足 MIN_AGE 的记忆不参与巩固"""
        f_old = _make_fragment("用户在北京工作", age_hours=48)
        f_new = _make_fragment("用户的工作地点是北京", age_hours=1)

        candidates = mcs._get_candidates(TEST_USER_ID, None)
        ids = {c["id"] for c in candidates}
        assert f_old in ids
        assert f_new not in ids


# ============================================================
# 巩固主流程
# ============================================================

class TestConsolidation:

    def _setup_cluster(self, monkeypatch):
        f1 = _make_fragment("用户在北京工作", importance=0.6)
        f2 = _make_fragment("用户是一名后端工程师，使用 Python", importance=0.8)
        fake = FakeChroma({
            "用户在北京工作": [(f2, 0.90)],
            "用户是一名后端工程师，使用 Python": [(f1, 0.90)],
        })
        monkeypatch.setattr(mcs, "get_chromadb_client", lambda: fake)
        return f1, f2

    def test_consolidation_success(self, db, monkeypatch):
        """LLM 巩固成功：新记忆创建 + 旧记忆 superseded + 演变链 + merge_log"""
        f1, f2 = self._setup_cluster(monkeypatch)
        monkeypatch.setattr("app.services.llm_backend_service.llm_chat", _fake_llm_success)

        result = mcs.run_consolidation(TEST_USER_ID)
        assert result["success"]
        assert result["clusters_consolidated"] == 1
        assert result["fragments_superseded"] == 2

        # 旧记忆 superseded
        for fid in (f1, f2):
            rows = db.execute('SELECT lifecycle_status FROM memory_fragments WHERE id = ?', (fid,))
            assert dict(rows[0])["lifecycle_status"] == "superseded"

        # 巩固记忆存在且 importance 取簇内最大值
        rows = db.execute(
            "SELECT * FROM memory_fragments WHERE user_id = ? AND lifecycle_status = 'active' "
            "AND content LIKE '%后端工程师%'", (TEST_USER_ID,))
        assert rows
        new_frag = dict(rows[0])
        assert new_frag["importance_score"] == pytest.approx(0.8)

        # 演变链 detection_method='consolidation'
        evo = db.execute(
            "SELECT * FROM memory_evolution WHERE user_id = ? AND detection_method = 'consolidation'",
            (TEST_USER_ID,))
        assert len(evo) == 2
        assert dict(evo[0])["change_reason"] == "sleep_time_consolidation"

        # merge_log merge_type='consolidation'
        logs = db.execute(
            "SELECT * FROM memory_merge_log WHERE user_id = ? AND merge_type = 'consolidation'",
            (TEST_USER_ID,))
        assert len(logs) == 1
        assert dict(logs[0])["operator"] == "system"

    def test_llm_mock_degrades_to_skip(self, db, monkeypatch):
        """LLM 返回 mock 时跳过该簇，不写库"""
        f1, f2 = self._setup_cluster(monkeypatch)
        monkeypatch.setattr("app.services.llm_backend_service.llm_chat", _fake_llm_mock)

        result = mcs.run_consolidation(TEST_USER_ID)
        assert result["success"]
        assert result["clusters_consolidated"] == 0
        assert result["skipped_reason_stats"].get("llm_unavailable_or_parse_failed") == 1

        # 旧记忆仍为 active
        rows = db.execute('SELECT lifecycle_status FROM memory_fragments WHERE id = ?', (f1,))
        assert dict(rows[0])["lifecycle_status"] == "active"

    def test_llm_invalid_json_skips(self, db, monkeypatch):
        """LLM 输出非 JSON 时降级跳过"""
        self._setup_cluster(monkeypatch)
        monkeypatch.setattr(
            "app.services.llm_backend_service.llm_chat",
            lambda uid, msgs, **kw: {"success": True, "content": "这不是 JSON"})

        result = mcs.run_consolidation(TEST_USER_ID)
        assert result["success"]
        assert result["clusters_consolidated"] == 0

    def test_dry_run_no_writes(self, db, monkeypatch):
        """dry_run 只返回聚簇预览，不调 LLM、不写库"""
        f1, f2 = self._setup_cluster(monkeypatch)

        def _fail_llm(*a, **kw):
            raise AssertionError("dry_run 不应调用 LLM")
        monkeypatch.setattr("app.services.llm_backend_service.llm_chat", _fail_llm)

        result = mcs.run_consolidation(TEST_USER_ID, dry_run=True)
        assert result["success"] and result["dry_run"]
        assert result["clusters_found"] == 1
        assert {f1, f2} == set(result["clusters"][0]["fragment_ids"])

        # 无审计行、旧记忆不变
        runs = db.execute('SELECT * FROM consolidation_runs WHERE user_id = ?', (TEST_USER_ID,))
        assert not runs
        rows = db.execute('SELECT lifecycle_status FROM memory_fragments WHERE id = ?', (f1,))
        assert dict(rows[0])["lifecycle_status"] == "active"

    def test_consolidation_run_audit(self, db, monkeypatch):
        """consolidation_runs 审计行正确记录"""
        self._setup_cluster(monkeypatch)
        monkeypatch.setattr("app.services.llm_backend_service.llm_chat", _fake_llm_success)

        result = mcs.run_consolidation(TEST_USER_ID, trigger="manual")
        assert result["success"]

        runs = db.execute('SELECT * FROM consolidation_runs WHERE user_id = ?', (TEST_USER_ID,))
        assert len(runs) == 1
        run = dict(runs[0])
        assert run["clusters_found"] == 1
        assert run["clusters_consolidated"] == 1
        assert run["fragments_superseded"] == 2
        assert run["trigger"] == "manual"
        assert run["status"] == "completed"

    def test_parse_llm_json_fallback(self):
        """JSON 解析兜底：```json 代码块提取"""
        wrapped = '好的，结果如下：\n```json\n{"consolidated_content": "abc", "fragment_type": "info"}\n```'
        parsed = mcs._parse_llm_json(wrapped)
        assert parsed["consolidated_content"] == "abc"
        assert mcs._parse_llm_json("not json at all") is None
        assert mcs._parse_llm_json("") is None


# ============================================================
# API 冒烟
# ============================================================

class TestConsolidationAPI:

    def test_run_dry_run_api(self, auth_client):
        resp = auth_client.post("/api/v1/memory/consolidation/run", json={"dry_run": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"]
        assert data.get("dry_run") is True or data.get("skipped")

    def test_history_api(self, auth_client):
        resp = auth_client.get("/api/v1/memory/consolidation/history")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"]
        assert "runs" in data

    def test_statistics_api(self, auth_client):
        resp = auth_client.get("/api/v1/memory/consolidation/statistics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"]
        assert "statistics" in data
        assert "by_trigger" in data["statistics"]
