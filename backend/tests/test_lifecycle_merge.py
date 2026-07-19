"""
生命周期合并与冲突检测 - 全面功能测试

覆盖场景:
1. 重复片段检测 (find_duplicates) - bigram Jaccard 相似度
2. 重复片段合并 (merge_memories) - 合并 + 审计日志
3. 记忆值冲突检测 (detect_conflicts) - key 值比对
4. 冲突记录持久化 & 列表查询
5. 冲突解决 (resolve_conflict)
6. 全链路：创建 → 检测 → 合并/解决 → 审计日志检验
"""
import pytest
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app.main import app


# ============================================================
# 测试用户
# ============================================================
TEST_USER = {
    "username": "lifecycle_test_user",
    "password": "TestPass999!",
    "email": "lifecycle@test.com"
}


@pytest.fixture(scope="module")
def client():
    """测试客户端"""
    return TestClient(app)


@pytest.fixture(scope="module")
def auth(client):
    """注册 + 登录，返回 auth headers"""
    # 先尝试注册（可能已存在则忽略）
    client.post("/api/v1/auth/register", json=TEST_USER)
    # 登录
    resp = client.post("/api/v1/auth/login", json={
        "username": TEST_USER["username"],
        "password": TEST_USER["password"]
    })
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    token = resp.json().get("access_token", "")
    assert token, "未获取到 token"
    return {"Authorization": f"Bearer {token}"}


# ============================================================
# 工具函数
# ============================================================

def _create_fragment(client, auth, content: str, ftype: str = "info"):
    """创建记忆片段，返回片段 ID"""
    resp = client.post(
        "/api/v1/memory/fragments/",
        json={"fragment_type": ftype, "content": content, "importance_score": 0.8},
        headers=auth,
    )
    assert resp.status_code == 200, f"创建片段失败 ({resp.status_code}): {resp.text}"
    data = resp.json()
    return data.get("fragment_id")


def _set_variable(client, auth, key: str, value: str):
    """设置记忆变量"""
    resp = client.post(
        "/api/v1/memory/variables",
        json={"key": key, "value": value},
        headers=auth,
    )
    assert resp.status_code == 200, f"设置变量失败: {resp.text}"
    return resp.json()


def _cleanup_test_data(client, auth):
    """清理测试数据（删除测试用户的片段）"""
    # 获取所有片段
    resp = client.get("/api/v1/memory/fragments/?limit=200", headers=auth)
    if resp.status_code == 200:
        fragments = resp.json().get("fragments", [])
        for f in fragments:
            client.delete(f"/api/v1/memory/fragments/{f['id']}", headers=auth)
    # 清理变量
    resp = client.get("/api/v1/memory/variables", headers=auth)
    if resp.status_code == 200:
        variables = resp.json().get("variables", {})
        for key in variables:
            client.delete(f"/api/v1/memory/variables/{key}", headers=auth)


# ============================================================
# Part 1: 重复片段检测与合并
# ============================================================

class TestDuplicateDetection:
    """重复记忆片段检测场景"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, client, auth):
        _cleanup_test_data(client, auth)
        yield
        _cleanup_test_data(client, auth)

    def test_find_duplicates_high_similarity(self, client, auth):
        """测试1: 高度相似的内容应被检测为重复"""
        # 创建两个高度相似的片段
        id1 = _create_fragment(client, auth, "用户在腾讯工作，负责后端开发")
        id2 = _create_fragment(client, auth, "用户在腾讯上班，负责后端开发工作")
        assert id1 and id2, "片段创建失败"

        # 用其中一个内容检测重复（threshold 设低一些，因为 bigram 相似度约 0.38）
        resp = client.post(
            "/api/v1/memory/lifecycle/duplicates/find",
            json={"content": "用户在腾讯工作，负责后端开发", "threshold": 0.3, "limit": 10},
            headers=auth,
        )
        assert resp.status_code == 200, f"API 失败: {resp.text}"
        data = resp.json()
        print(f"\n[重复检测] 相似内容结果: {json.dumps(data, ensure_ascii=False, indent=2)}")

        assert data.get("success") is True, f"检测失败: {data}"
        assert data.get("count", 0) >= 1, f"应至少检测到 1 个重复, 实际: {data}"
        # 验证相似度字段
        dup = data["duplicates"][0]
        assert "id" in dup, f"缺失 id 字段: {dup}"
        assert "similarity" in dup, f"缺失 similarity 字段: {dup}"
        assert dup["similarity"] >= 0.3, f"相似度过低: {dup['similarity']}"

    def test_find_duplicates_no_match(self, client, auth):
        """测试2: 不相关的内容不应被检测为重复"""
        _create_fragment(client, auth, "用户在腾讯工作，负责后端开发")

        resp = client.post(
            "/api/v1/memory/lifecycle/duplicates/find",
            json={"content": "今天天气很好，适合出去散步", "threshold": 0.85, "limit": 10},
            headers=auth,
        )
        data = resp.json()
        print(f"\n[不重复检测] 结果: {json.dumps(data, ensure_ascii=False, indent=2)}")

        assert data.get("success") is True
        assert data.get("count", 0) == 0, f"不应检测到重复, 实际: {data}"

    def test_find_duplicates_threshold_filtering(self, client, auth):
        """测试3: 不同阈值过滤效果"""
        _create_fragment(client, auth, "用户喜欢打篮球")
        _create_fragment(client, auth, "用户喜欢打")

        # 搜索 "用户喜欢打篮球"，它自己相似度=1.0 必然匹配
        resp_high = client.post(
            "/api/v1/memory/lifecycle/duplicates/find",
            json={"content": "用户喜欢打篮球", "threshold": 0.99, "limit": 10},
            headers=auth,
        )
        d_high = resp_high.json()
        # 即使 0.99 阈值也会匹配到自己（相似度 1.0）
        ids_99 = [d["id"] for d in d_high.get("duplicates", [])]
        print(f"\n[阈值 0.99] 匹配 {d_high.get('count', 0)} 个, IDs: {ids_99}")

        # 搜索 "腾讯在哪个城市" 与已有内容完全不同
        resp_low = client.post(
            "/api/v1/memory/lifecycle/duplicates/find",
            json={"content": "腾讯在哪个城市", "threshold": 0.3, "limit": 10},
            headers=auth,
        )
        d_low = resp_low.json()
        print(f"[阈值 0.3, 无关内容] 匹配 {d_low.get('count', 0)} 个")

        assert d_high.get("count", 0) >= 1, f"高阈值应至少匹配到自身: {d_high}"
        assert d_low.get("count", 0) == 0, f"无关内容不应匹配: {d_low}"


class TestMergeMemories:
    """重复片段合并场景"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, client, auth):
        _cleanup_test_data(client, auth)
        yield
        _cleanup_test_data(client, auth)

    def test_merge_two_fragments(self, client, auth):
        """测试4: 合并两条重复片段"""
        # 创建两条高度相似的片段
        id1 = _create_fragment(client, auth, "用户在腾讯工作，负责后端开发")
        id2 = _create_fragment(client, auth, "用户在腾讯工作，负责后端开发工作")
        assert id1 and id2, "片段创建失败"
        print(f"\n[合并测试] 片段 A ID: {id1}, 片段 B ID: {id2}")

        # 合并 - 保留第一条内容
        merged_content = "用户在腾讯工作，主要负责后端开发"
        resp = client.post(
            "/api/v1/memory/lifecycle/duplicates/merge",
            json={
                "source_ids": [id1, id2],
                "target_content": merged_content,
                "target_type": "info",
            },
            headers=auth,
        )
        assert resp.status_code == 200, f"合并失败: {resp.text}"
        data = resp.json()
        print(f"[合并结果] {json.dumps(data, ensure_ascii=False, indent=2)}")
        assert data.get("success") is True, f"合并未成功: {data}"

        # 验证合并日志
        resp_log = client.get("/api/v1/memory/lifecycle/merge-log?limit=10", headers=auth)
        assert resp_log.status_code == 200
        logs = resp_log.json().get("logs", [])
        print(f"[合并日志] 共 {len(logs)} 条")
        assert len(logs) >= 1, "应至少有一条合并日志"

        # 验证 fragment A 内容已更新
        resp_get = client.get(f"/api/v1/memory/fragments/{id1}", headers=auth)
        assert resp_get.status_code == 200
        updated = resp_get.json().get("fragment", {})
        print(f"[更新后内容] {updated.get('content', '')}")
        assert updated.get("content") == merged_content, f"内容未更新: {updated}"

    def test_merge_less_than_two_fails(self, client, auth):
        """测试5: 少于 2 条时应返回错误"""
        id1 = _create_fragment(client, auth, "单条测试内容")
        resp = client.post(
            "/api/v1/memory/lifecycle/duplicates/merge",
            json={"source_ids": [id1], "target_content": "合并内容", "target_type": "info"},
            headers=auth,
        )
        data = resp.json()
        print(f"\n[单条合并测试] 期望错误, 结果: {data}")
        assert resp.status_code == 400, f"单条合并应返回 400, 实际: {resp.status_code}"
        detail = resp.json().get("message") or resp.json().get("detail", "")
        print(f"\n[单条合并测试] 期望错误, 结果: {resp.json()}")
        assert "至少需要 2 条" in detail, f"错误信息不符: {detail}"


# ============================================================
# Part 2: 记忆值冲突检测
# ============================================================

class TestConflictDetection:
    """记忆值冲突检测场景"""

    def test_conflict_different_values(self, client, auth):
        """测试6: 同一 key 不同值应触发冲突检测"""
        # 先设置变量
        _set_variable(client, auth, "user_company", "腾讯")

        # 检测冲突
        resp = client.post(
            "/api/v1/memory/lifecycle/conflicts/detect",
            json={"key": "user_company", "new_value": "阿里"},
            headers=auth,
        )
        assert resp.status_code == 200, f"冲突检测API失败: {resp.text}"
        data = resp.json()
        print(f"\n[冲突检测] key='user_company' 腾讯→阿里: {json.dumps(data, ensure_ascii=False, indent=2)}")

        assert data.get("success") is True
        assert data.get("conflict") is True, "应检测到冲突"
        assert data.get("existing_value") == "腾讯", f"现有值错误: {data}"
        assert data.get("new_value") == "阿里", f"新值错误: {data}"

    def test_conflict_same_value_no_conflict(self, client, auth):
        """测试7: 相同值不应触发冲突"""
        _set_variable(client, auth, "user_city", "北京")

        resp = client.post(
            "/api/v1/memory/lifecycle/conflicts/detect",
            json={"key": "user_city", "new_value": "北京"},
            headers=auth,
        )
        data = resp.json()
        print(f"\n[相同值检测] 期望无冲突: {data}")
        assert data.get("conflict") is False, "相同值不应检测为冲突"

    def test_conflict_no_existing_value(self, client, auth):
        """测试8: 无现有值时不应触发冲突"""
        resp = client.post(
            "/api/v1/memory/lifecycle/conflicts/detect",
            json={"key": "nonexistent_key_xyz", "new_value": "新值"},
            headers=auth,
        )
        data = resp.json()
        print(f"\n[无现有值] 期望无冲突: {data}")
        assert data.get("conflict") is False, "无现有值时不应检测为冲突"


# ============================================================
# Part 3: 冲突持久化与解决
# ============================================================

class TestConflictPersistence:
    """冲突持久化与解决流程 (依赖 detect_conflicts 现在已持久化到 merge_log)"""

    def test_conflict_persistence_and_list(self, client, auth):
        """
        测试9: 冲突持久化 → 列表查询

        detect_conflicts 现在会自动将冲突记录写入 memory_merge_log 表。
        list_pending_conflicts 应从 merge_log 中查到该记录。
        """
        # Step 1: 设置变量
        _set_variable(client, auth, "user_job", "工程师")

        # Step 2: 检测冲突（应自动持久化）
        resp = client.post(
            "/api/v1/memory/lifecycle/conflicts/detect",
            json={"key": "user_job", "new_value": "产品经理"},
            headers=auth,
        )
        detect_data = resp.json()
        print(f"\n[检测冲突] user_job: {json.dumps(detect_data, ensure_ascii=False, indent=2)}")

        assert detect_data.get("success") is True
        assert detect_data.get("conflict") is True, "应检测到冲突"
        assert detect_data.get("conflict_id") is not None, f"应返回 conflict_id: {detect_data}"
        conflict_id = detect_data["conflict_id"]
        print(f"✓ 冲突已持久化, conflict_id={conflict_id}")

        # Step 3: 查询待处理冲突
        resp_list = client.get("/api/v1/memory/lifecycle/conflicts", headers=auth)
        assert resp_list.status_code == 200
        list_data = resp_list.json()
        print(f"[待处理冲突列表] {json.dumps(list_data, ensure_ascii=False, indent=2)}")

        assert list_data.get("count", 0) >= 1, f"应至少有一个待处理冲突: {list_data}"
        found = any(c.get("id") == conflict_id for c in list_data.get("conflicts", []))
        assert found, f"冲突记录 {conflict_id} 应该在列表中"

        # Step 4: 验证冲突记录的字段
        conflict_record = next(c for c in list_data["conflicts"] if c["id"] == conflict_id)
        assert conflict_record.get("old_value") == "工程师", f"old_value 错误: {conflict_record}"
        assert conflict_record.get("new_value") == "产品经理", f"new_value 错误: {conflict_record}"
        assert conflict_record.get("merge_type") == "conflict", f"merge_type 错误: {conflict_record}"
        assert conflict_record.get("resolved") == 0, f"应未解决: {conflict_record}"
        print(f"✓ 冲突记录字段验证通过")

    def test_resolve_conflict(self, client, auth):
        """
        测试10: 冲突解决

        流程:
        1. 设置变量 → detect_conflicts（持久化）
        2. resolve_conflict → 标记为已解决
        3. list_pending_conflicts → 不再出现
        """
        # Step 1: 设置 + 检测 + 持久化
        _set_variable(client, auth, "user_school", "北京大学")
        resp = client.post(
            "/api/v1/memory/lifecycle/conflicts/detect",
            json={"key": "user_school", "new_value": "清华大学"},
            headers=auth,
        )
        conflict_id = resp.json().get("conflict_id")
        assert conflict_id is not None
        print(f"\n[冲突解决] 冲突 ID={conflict_id}")

        # Step 2: 解决冲突
        resp_resolve = client.post(
            "/api/v1/memory/lifecycle/conflicts/resolve",
            json={"conflict_id": conflict_id, "resolution": "keep_new"},
            headers=auth,
        )
        assert resp_resolve.status_code == 200, f"解决冲突失败: {resp_resolve.text}"
        resolve_data = resp_resolve.json()
        print(f"[解决结果] {resolve_data}")
        assert resolve_data.get("success") is True, f"解决未成功: {resolve_data}"

        # Step 3: 确认冲突已从待处理列表中移除
        resp_list = client.get("/api/v1/memory/lifecycle/conflicts", headers=auth)
        list_data = resp_list.json()
        remaining = [c for c in list_data.get("conflicts", []) if c["id"] == conflict_id and not c.get("resolved")]
        assert len(remaining) == 0, f"冲突 {conflict_id} 仍为待处理: {list_data}"
        print(f"✓ 冲突 {conflict_id} 已解决，不再出现在待处理列表中")

    def test_resolve_nonexistent_conflict(self, client, auth):
        """测试11: 解决不存在的冲突应返回错误"""
        resp = client.post(
            "/api/v1/memory/lifecycle/conflicts/resolve",
            json={"conflict_id": 999999, "resolution": "keep_new"},
            headers=auth,
        )
        print(f"\n[不存在冲突] 期望 400: {resp.status_code} {resp.json()}")
        assert resp.status_code == 400, f"应返回 400: {resp.status_code}"
        detail = resp.json().get("message") or resp.json().get("detail", "")
        assert "未找到" in detail, f"错误信息不符: {detail}"


# ============================================================
# Part 4: 端到端场景
# ============================================================

class TestEndToEnd:
    """完整端到端场景测试"""

    @pytest.fixture(autouse=True)
    def setup_teardown(self, client, auth):
        _cleanup_test_data(client, auth)
        yield
        _cleanup_test_data(client, auth)

    def test_full_merge_workflow(self, client, auth):
        """
        测试12: 完整合并工作流

        场景: 系统中有两条关于用户公司的记忆
        "用户在腾讯工作，主要负责后端开发" (已存在)
        "用户在腾讯上班，做后端开发工作" (新内容)

        流程:
        1. 创建两条片段
        2. 用新内容检测重复 → 找到已存在的相似片段
        3. 合并两条片段 → 审计日志记录
        4. 验证合并日志
        """
        # Step 1: 创建已存在的记忆
        _create_fragment(client, auth, "用户在腾讯工作，主要负责后端开发")
        print("\n[Step 1] ✓ 已创建记忆: '用户在腾讯工作，主要负责后端开发'")

        # Step 2: 创建新片段并用它检测重复
        new_content = "用户在腾讯上班，做后端开发工作"
        _create_fragment(client, auth, new_content)

        # 用其中一个内容检测重复（bigram 相似度 ~0.38, 阈值 0.3 即可命中）
        resp_find = client.post(
            "/api/v1/memory/lifecycle/duplicates/find",
            json={"content": "用户在腾讯工作，主要负责后端开发", "threshold": 0.3, "limit": 10},
            headers=auth,
        )
        find_data = resp_find.json()
        print(f"[Step 2] 检测重复: 现有记忆 vs 新内容")
        print(f"  结果: {find_data.get('count', 0)} 个重复")
        for d in find_data.get("duplicates", []):
            print(f"  → ID={d['id']}, 相似度={d['similarity']:.4f}, 内容='{d['content']}'")

        assert find_data.get("count", 0) >= 1, f"应检测到重复片段, get: {find_data}"

        # Step 3: 合并
        dup_ids = [d["id"] for d in find_data["duplicates"]]
        merged_content = "用户在腾讯工作，从事后端开发"
        resp_merge = client.post(
            "/api/v1/memory/lifecycle/duplicates/merge",
            json={
                "source_ids": dup_ids,
                "target_content": merged_content,
                "target_type": "info",
            },
            headers=auth,
        )
        merge_data = resp_merge.json()
        print(f"[Step 3] 合并结果: {json.dumps(merge_data, ensure_ascii=False, indent=2)}")
        assert merge_data.get("success") is True, f"合并失败: {merge_data}"

        # Step 4: 验证合并日志
        resp_log = client.get("/api/v1/memory/lifecycle/merge-log?limit=10", headers=auth)
        log_data = resp_log.json()
        print(f"[Step 4] 合并日志 (共 {log_data.get('count', 0)} 条):")
        for log in log_data.get("logs", [])[:3]:
            print(f"  ID={log.get('id')}, source_ids={log.get('source_ids')}, "
                  f"new_value={log.get('new_value', '')[:50]}")

        assert log_data.get("count", 0) >= 1, "应有合并审计日志"

    def test_user_company_conflict_scenario(self, client, auth):
        """
        测试13: "用户在腾讯工作" + "用户在阿里工作" 冲突场景

        场景: 用户公司记忆发生值冲突

        流程:
        1. 设置变量 user_company = "腾讯"
        2. 检测冲突: user_company → "阿里" 应触发冲突并持久化
        3. 输出冲突详情
        4. 查询待处理冲突列表包含此记录
        5. 解决冲突
        6. 确认已解决
        """
        # Step 1: 已有记忆值
        _set_variable(client, auth, "user_company", "腾讯")
        print("\n[场景: 公司冲突]")
        print("[Step 1] ✓ 已有记忆: user_company = '腾讯'")

        # Step 2: 新值检测冲突（自动持久化）
        resp = client.post(
            "/api/v1/memory/lifecycle/conflicts/detect",
            json={"key": "user_company", "new_value": "阿里"},
            headers=auth,
        )
        data = resp.json()
        print(f"[Step 2] 新值: '阿里'")
        print(f"  冲突检测: {'⚠ 冲突!' if data.get('conflict') else '✓ 无冲突'}")
        print(f"  现有值: '{data.get('existing_value', '-')}'")
        print(f"  新值:    '{data.get('new_value', '-')}'")
        print(f"  相似度: {data.get('similarity', '-')}")
        print(f"  消息: {data.get('message', '-')}")

        assert data.get("success") is True
        assert data.get("conflict") is True, "应检测到公司变更冲突"
        assert data.get("conflict_id") is not None, "应返回 conflict_id"
        conflict_id = data["conflict_id"]

        # Step 3: 查询待处理冲突列表
        resp_list = client.get("/api/v1/memory/lifecycle/conflicts", headers=auth)
        list_data = resp_list.json()
        print(f"[Step 3] 待处理冲突列表: count={list_data.get('count', 0)}")
        found = any(c.get("id") == conflict_id for c in list_data.get("conflicts", []))
        assert found, f"冲突 {conflict_id} 应在待处理列表中"
        print(f"  ✓ 冲突记录 #{conflict_id} 在列表中")

        # Step 4: 解决冲突
        resp_resolve = client.post(
            "/api/v1/memory/lifecycle/conflicts/resolve",
            json={"conflict_id": conflict_id, "resolution": "keep_new"},
            headers=auth,
        )
        assert resp_resolve.status_code == 200
        resolve_data = resp_resolve.json()
        print(f"[Step 4] 冲突已解决: {resolve_data.get('message')}")

        # Step 5: 确认已解决
        resp_list2 = client.get("/api/v1/memory/lifecycle/conflicts", headers=auth)
        list_data2 = resp_list2.json()
        still_pending = [c for c in list_data2.get("conflicts", []) if c["id"] == conflict_id and not c.get("resolved")]
        assert len(still_pending) == 0, f"冲突 {conflict_id} 仍为待处理"
        print(f"[Step 5] ✓ 冲突 {conflict_id} 已不再出现在待处理列表中")

    def test_multi_key_related_conflicts(self, client, auth):
        """测试14: 多 key 关联冲突检测"""
        _set_variable(client, auth, "user_company", "腾讯")
        _set_variable(client, auth, "user_job_title", "后端工程师")

        resp = client.post(
            "/api/v1/memory/lifecycle/conflicts/detect",
            json={"key": "user_company", "new_value": "阿里"},
            headers=auth,
        )
        data = resp.json()
        print(f"\n[多 key 关联冲突]")
        print(f"  conflict: {data.get('conflict')}")
        print(f"  关联冲突: {data.get('related_conflicts', [])}")

        assert data.get("success") is True


if __name__ == "__main__":
    """直接运行时使用 requests 而非 pytest"""
    import requests

    BASE = "http://localhost:8000/api/v1"
    session = requests.Session()

    # 注册 + 登录
    session.post(f"{BASE}/auth/register", json=TEST_USER)
    resp = session.post(f"{BASE}/auth/login", json={
        "username": TEST_USER["username"], "password": TEST_USER["password"]
    })
    token = resp.json().get("access_token", "")
    session.headers.update({"Authorization": f"Bearer {token}"})
    print(f"✓ 登录成功, token={token[:20]}...")

    # === 测试 1: 重复检测 ===
    print("\n" + "=" * 60)
    print("测试 1: 重复片段检测")
    print("=" * 60)
    r = session.post(f"{BASE}/memory/fragments/", json={
        "fragment_type": "info", "content": "用户在腾讯工作，负责后端开发", "importance_score": 0.8
    })
    id1 = r.json().get("fragment_id")
    print(f"创建片段 1: id={id1}")

    r = session.post(f"{BASE}/memory/fragments/", json={
        "fragment_type": "info", "content": "用户在腾讯上班，做后端开发相关工作", "importance_score": 0.8
    })
    id2 = r.json().get("fragment_id")
    print(f"创建片段 2: id={id2}")

    r = session.post(f"{BASE}/memory/lifecycle/duplicates/find", json={
        "content": "用户在腾讯工作，负责后端开发", "threshold": 0.5, "limit": 10
    })
    print(f"重复检测: count={r.json().get('count', 0)}")
    for d in r.json().get("duplicates", []):
        print(f"  → id={d['id']}, sim={d['similarity']:.4f}")

    # === 测试 2: 合并 ===
    print("\n" + "=" * 60)
    print("测试 2: 重复片段合并")
    print("=" * 60)
    r = session.post(f"{BASE}/memory/lifecycle/duplicates/merge", json={
        "source_ids": [id1, id2],
        "target_content": "用户在腾讯工作，主要负责后端开发",
        "target_type": "info",
    })
    print(f"合并结果: {'✓' if r.json().get('success') else '✗'} {r.json()}")

    # === 测试 3: 冲突检测 ===
    print("\n" + "=" * 60)
    print("测试 3: 记忆值冲突检测")
    print("=" * 60)
    r = session.post(f"{BASE}/memory/variables", json={"key": "user_company", "value": "腾讯"})
    print(f"设置变量 user_company='腾讯': {r.status_code}")

    r = session.post(f"{BASE}/memory/lifecycle/conflicts/detect",
                     json={"key": "user_company", "new_value": "阿里"})
    data = r.json()
    print(f"冲突检测: conflict={data.get('conflict')}")
    print(f"  现有='{data.get('existing_value')}' → 新值='{data.get('new_value')}'")
    print(f"  消息: {data.get('message')}")

    # === 测试 4: 待处理冲突列表 ===
    print("\n" + "=" * 60)
    print("测试 4: 待处理冲突列表")
    print("=" * 60)
    r = session.get(f"{BASE}/memory/lifecycle/conflicts")
    data = r.json()
    print(f"待处理冲突: count={data.get('count', 0)}")
    for c in data.get("conflicts", []):
        print(f"  id={c['id']}, type={c.get('merge_type')}, action={c.get('merge_action')}")

    # === 测试 5: 合并审计日志 ===
    print("\n" + "=" * 60)
    print("测试 5: 合并审计日志")
    print("=" * 60)
    r = session.get(f"{BASE}/memory/lifecycle/merge-log?limit=10")
    logs = r.json().get("logs", [])
    print(f"审计日志: {len(logs)} 条")
    for log in logs[:5]:
        print(f"  id={log['id']}, type={log.get('merge_type')}, "
              f"source_ids={log.get('source_ids')}, "
              f"new_value={str(log.get('new_value', ''))[:60]}")

    print("\n" + "=" * 60)
    print("✓ 全量测试完成")
    print("=" * 60)
