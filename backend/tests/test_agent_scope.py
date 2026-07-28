"""
P1 R-12 跨 Agent 共享记忆作用域测试

覆盖：agents CRUD（重名 409、跨 workspace 隔离）、create_fragment 带 agent_id/scope、
召回过滤（不传 agent_id 零回归 / private 隔离 / shared 全可见）、
删除 agent 后 private→shared、vector_outbox 携带 agent 元数据。
"""
import pytest

from app.core.db_client import get_db_client
from app.services import agent_registry_service as ars
from app.services import memory_fragment_service as mfs
from app.services.memory_fragment_service import (
    create_fragment,
    apply_agent_scope_filter,
    _fragment_visible_to_agent,
)

TEST_USER_ID = 999


@pytest.fixture(autouse=True)
def _cleanup_agents():
    yield
    db = get_db_client()
    try:
        db.execute('DELETE FROM agents WHERE user_id = ?', (TEST_USER_ID,))
        db.execute("DELETE FROM agents WHERE name LIKE 'test-agent%'")
    except Exception:
        pass


# ============================================================
# Agent 注册服务
# ============================================================

class TestAgentRegistry:

    def test_register_and_get(self, db):
        result = ars.register_agent(TEST_USER_ID, "test-agent-a", description="召回助手")
        assert result["success"]
        agent_id = result["agent"]["id"]

        got = ars.get_agent(agent_id, TEST_USER_ID)
        assert got["success"]
        assert got["agent"]["name"] == "test-agent-a"
        assert got["agent"]["description"] == "召回助手"

    def test_duplicate_name_rejected(self, db):
        assert ars.register_agent(TEST_USER_ID, "test-agent-dup")["success"]
        dup = ars.register_agent(TEST_USER_ID, "test-agent-dup")
        assert not dup["success"]
        assert dup.get("duplicate") is True

    def test_empty_name_rejected(self, db):
        result = ars.register_agent(TEST_USER_ID, "   ")
        assert not result["success"]

    def test_workspace_isolation(self, db):
        """workspace A 的 agent 在 workspace B 不可见"""
        result = ars.register_agent(TEST_USER_ID, "test-agent-ws", workspace_id=11111)
        assert result["success"]
        agent_id = result["agent"]["id"]

        assert ars.get_agent(agent_id, TEST_USER_ID, workspace_id=11111)["success"]
        other = ars.get_agent(agent_id, TEST_USER_ID, workspace_id=22222)
        assert not other["success"] and other.get("not_found")
        # 个人空间（workspace=None）也看不到
        assert not ars.get_agent(agent_id, TEST_USER_ID, workspace_id=None)["success"]
        # 同名可在不同 workspace 注册
        assert ars.register_agent(TEST_USER_ID, "test-agent-ws", workspace_id=22222)["success"]

    def test_validate_agent_in_workspace(self, db):
        result = ars.register_agent(TEST_USER_ID, "test-agent-v")
        agent_id = result["agent"]["id"]
        assert ars.validate_agent_in_workspace(agent_id, TEST_USER_ID, None)
        assert not ars.validate_agent_in_workspace(agent_id, TEST_USER_ID, 33333)
        assert not ars.validate_agent_in_workspace(99999999, TEST_USER_ID, None)


# ============================================================
# 写入路径：agent_id / scope
# ============================================================

class TestFragmentAgentScope:

    def test_create_fragment_with_agent(self, db):
        agent_id = ars.register_agent(TEST_USER_ID, "test-agent-w")["agent"]["id"]
        result = create_fragment(
            TEST_USER_ID, "info", "agent 私有记忆",
            agent_id=agent_id, scope="private", skip_contradiction=True)
        assert result["success"]

        rows = db.execute('SELECT agent_id, scope FROM memory_fragments WHERE id = ?',
                          (result["fragment_id"],))
        row = dict(rows[0])
        assert row["agent_id"] == agent_id
        assert row["scope"] == "private"

    def test_default_scope_shared(self, db):
        """默认 shared，agent_id 为 NULL（现状调用零变化）"""
        result = create_fragment(TEST_USER_ID, "info", "普通记忆", skip_contradiction=True)
        rows = db.execute('SELECT agent_id, scope FROM memory_fragments WHERE id = ?',
                          (result["fragment_id"],))
        row = dict(rows[0])
        assert row["agent_id"] is None
        assert row["scope"] == "shared"

    def test_invalid_scope_falls_back_to_shared(self, db):
        result = create_fragment(
            TEST_USER_ID, "info", "非法 scope 记忆",
            scope="secret", skip_contradiction=True)
        rows = db.execute('SELECT scope FROM memory_fragments WHERE id = ?',
                          (result["fragment_id"],))
        assert dict(rows[0])["scope"] == "shared"

    def test_vector_outbox_carries_agent_metadata(self, db, monkeypatch):
        """Chroma 不可用时 outbox 保留 agent_id/scope 列"""
        monkeypatch.setattr(mfs, "get_chromadb_client", lambda: None)
        agent_id = ars.register_agent(TEST_USER_ID, "test-agent-ob")["agent"]["id"]
        result = create_fragment(
            TEST_USER_ID, "info", "outbox 元数据测试",
            agent_id=agent_id, scope="private", skip_contradiction=True)
        assert result["success"]

        rows = db.execute('SELECT agent_id, scope FROM vector_outbox WHERE fragment_id = ?',
                          (result["fragment_id"],))
        assert rows
        row = dict(rows[0])
        assert row["agent_id"] == agent_id
        assert row["scope"] == "private"


# ============================================================
# 召回过滤
# ============================================================

class TestRecallScopeFilter:

    def _seed_memories(self, db):
        agent_a = ars.register_agent(TEST_USER_ID, "test-agent-A")["agent"]["id"]
        agent_b = ars.register_agent(TEST_USER_ID, "test-agent-B")["agent"]["id"]
        shared = create_fragment(TEST_USER_ID, "info", "共享记忆",
                                 skip_contradiction=True)["fragment_id"]
        priv_a = create_fragment(TEST_USER_ID, "info", "A 的私有记忆",
                                 agent_id=agent_a, scope="private",
                                 skip_contradiction=True)["fragment_id"]
        priv_b = create_fragment(TEST_USER_ID, "info", "B 的私有记忆",
                                 agent_id=agent_b, scope="private",
                                 skip_contradiction=True)["fragment_id"]
        memories = [{"id": shared}, {"id": priv_a}, {"id": priv_b}]
        return agent_a, agent_b, shared, priv_a, priv_b, memories

    def test_no_agent_id_no_filtering(self, db):
        """不传 agent_id：行为与现状一致（回归）"""
        *_, memories = self._seed_memories(db)
        assert apply_agent_scope_filter(memories, TEST_USER_ID, None) == memories

    def test_agent_cannot_see_other_private(self, db):
        """agent A 看不到 agent B 的 private，shared 可见"""
        agent_a, agent_b, shared, priv_a, priv_b, memories = self._seed_memories(db)

        seen_a = {m["id"] for m in apply_agent_scope_filter(memories, TEST_USER_ID, agent_a)}
        assert seen_a == {shared, priv_a}

        seen_b = {m["id"] for m in apply_agent_scope_filter(memories, TEST_USER_ID, agent_b)}
        assert seen_b == {shared, priv_b}

    def test_null_scope_treated_as_shared(self, db):
        """存量记忆 scope=NULL 按 shared 处理"""
        agent_a = ars.register_agent(TEST_USER_ID, "test-agent-null")["agent"]["id"]
        fid = create_fragment(TEST_USER_ID, "info", "存量记忆",
                              skip_contradiction=True)["fragment_id"]
        db.execute('UPDATE memory_fragments SET scope = NULL WHERE id = ?', (fid,))

        filtered = apply_agent_scope_filter([{"id": fid}], TEST_USER_ID, agent_a)
        assert [m["id"] for m in filtered] == [fid]

    def test_visible_helper_rules(self):
        assert _fragment_visible_to_agent({"scope": "shared", "agent_id": 1}, 2)
        assert _fragment_visible_to_agent({"scope": None, "agent_id": None}, 2)
        assert _fragment_visible_to_agent({"scope": "private", "agent_id": 2}, 2)
        assert not _fragment_visible_to_agent({"scope": "private", "agent_id": 1}, 2)

    def test_semantic_search_signature_accepts_agent(self, db):
        """search_fragments_by_semantic 接受 agent_id（Chroma 不可用时优雅返回）"""
        result = mfs.search_fragments_by_semantic(
            TEST_USER_ID, "任意查询", agent_id=12345)
        assert result["success"]


# ============================================================
# 删除 Agent：private → shared
# ============================================================

class TestDeleteAgent:

    def test_delete_converts_private_to_shared(self, db):
        agent_id = ars.register_agent(TEST_USER_ID, "test-agent-del")["agent"]["id"]
        fid = create_fragment(TEST_USER_ID, "info", "将被共享的私有记忆",
                              agent_id=agent_id, scope="private",
                              skip_contradiction=True)["fragment_id"]

        result = ars.delete_agent(agent_id, TEST_USER_ID)
        assert result["success"]
        assert result["private_memories_shared"] == 1

        rows = db.execute('SELECT scope FROM memory_fragments WHERE id = ?', (fid,))
        assert dict(rows[0])["scope"] == "shared"
        assert not ars.get_agent(agent_id, TEST_USER_ID)["success"]


# ============================================================
# API
# ============================================================

class TestAgentsAPI:

    def test_crud_flow(self, auth_client):
        # 创建
        resp = auth_client.post("/api/v1/agents", json={"name": "test-agent-api", "description": "d"})
        assert resp.status_code == 201
        agent_id = resp.json()["agent"]["id"]

        # 重名 409
        resp = auth_client.post("/api/v1/agents", json={"name": "test-agent-api"})
        assert resp.status_code == 409

        # 列表
        resp = auth_client.get("/api/v1/agents")
        assert resp.status_code == 200
        assert any(a["id"] == agent_id for a in resp.json()["agents"])

        # 详情
        resp = auth_client.get(f"/api/v1/agents/{agent_id}")
        assert resp.status_code == 200

        # 删除
        resp = auth_client.delete(f"/api/v1/agents/{agent_id}")
        assert resp.status_code == 200

        # 删除后 404
        resp = auth_client.get(f"/api/v1/agents/{agent_id}")
        assert resp.status_code == 404

    def test_search_with_foreign_agent_403(self, auth_client):
        """召回 API 传入不属于当前 workspace 的 agent_id → 403"""
        resp = auth_client.post(
            "/api/v1/memory/recall/search",
            json={"query": "test", "agent_id": 99999999})
        assert resp.status_code == 403

    def test_search_with_own_agent_ok(self, auth_client):
        resp = auth_client.post("/api/v1/agents", json={"name": "test-agent-search"})
        assert resp.status_code == 201
        agent_id = resp.json()["agent"]["id"]

        resp = auth_client.post(
            "/api/v1/memory/recall/search",
            json={"query": "任意查询", "agent_id": agent_id})
        assert resp.status_code == 200
        assert resp.json()["success"]

    def test_create_fragment_api_with_scope(self, auth_client):
        resp = auth_client.post("/api/v1/agents", json={"name": "test-agent-frag"})
        agent_id = resp.json()["agent"]["id"]

        resp = auth_client.post(
            "/api/v1/memory/fragments",
            json={"fragment_type": "info", "content": "API 私有记忆",
                  "agent_id": agent_id, "scope": "private"})
        assert resp.status_code == 201
        fragment_id = resp.json()["fragment_id"]

        db = get_db_client()
        rows = db.execute('SELECT agent_id, scope FROM memory_fragments WHERE id = ?', (fragment_id,))
        row = dict(rows[0])
        assert row["agent_id"] == agent_id
        assert row["scope"] == "private"
        # 清理（非 999 用户的数据不会被 conftest 清理）
        db.execute('DELETE FROM memory_fragments WHERE id = ?', (fragment_id,))
