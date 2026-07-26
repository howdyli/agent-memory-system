"""
时序失效测试（W2-F2.2）

验证内容：
1. memory_fragments 时序字段迁移（valid_from / valid_until）
2. infer_validity_window 时间推断规则引擎
3. apply_validity_to_fragment 写入时序窗口
4. scan_and_expire 定时扫描标记 expired
5. get_valid_fragments_at 时刻有效性查询
6. 召回引擎过滤 expired 记忆
"""
from datetime import datetime, timedelta

import pytest

from app.core.db_client import get_db_client
from app.services.memory_fragment_service import create_fragment
from app.services.temporal_inference_service import (
    EXPIRED_STATUS,
    apply_validity_to_fragment,
    get_valid_fragments_at,
    infer_validity_window,
    scan_and_expire,
)

TEST_USER_ID = 9911

# 固定基准时间：2026-07-22 周三 10:00
BASE = datetime(2026, 7, 22, 10, 0, 0)


@pytest.fixture(autouse=True)
def _clean_fragments():
    """每个测试前后清理测试数据。"""
    db = get_db_client()
    # 确保测试用户存在（满足 memory_fragments 外键约束）
    try:
        db.execute(
            "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)",
            (TEST_USER_ID, f"w2_temporal_test_{TEST_USER_ID}"),
        )
    except Exception:
        pass
    try:
        db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (TEST_USER_ID,))
    except Exception:
        pass
    yield
    try:
        db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (TEST_USER_ID,))
    except Exception:
        pass


def _create_test_fragment(content: str, **kwargs) -> int:
    result = create_fragment(
        user_id=TEST_USER_ID,
        fragment_type=kwargs.pop("fragment_type", "plan"),
        content=content,
        **kwargs,
    )
    assert result["success"], result.get("error")
    return result["fragment_id"]


# ============================================================
# 1. 迁移：时序字段存在
# ============================================================

@pytest.mark.unit
class TestTemporalSchema:
    """memory_fragments 应包含 valid_from / valid_until 列。"""

    def test_temporal_columns_exist(self):
        db = get_db_client()
        rows = db.execute("PRAGMA table_info(memory_fragments)")
        columns = {r["name"] for r in rows}
        assert "valid_from" in columns
        assert "valid_until" in columns


# ============================================================
# 2. 时间推断规则引擎
# ============================================================

@pytest.mark.unit
class TestInferValidityWindow:
    """infer_validity_window 规则推断。"""

    def test_today(self):
        _, until = infer_validity_window("今天下午 3 点开会", created_at=BASE)
        assert until == datetime(2026, 7, 22, 23, 59, 59)

    def test_today_english(self):
        _, until = infer_validity_window("meeting today at 3pm", created_at=BASE)
        assert until == datetime(2026, 7, 22, 23, 59, 59)

    def test_tomorrow(self):
        _, until = infer_validity_window("明天提交周报", created_at=BASE)
        assert until == datetime(2026, 7, 23, 23, 59, 59)

    def test_day_after_tomorrow(self):
        _, until = infer_validity_window("后天出差", created_at=BASE)
        assert until == datetime(2026, 7, 24, 23, 59, 59)

    def test_next_week(self):
        """BASE 是周三，下周三 = 7-29。"""
        _, until = infer_validity_window("下周三和客户评审", created_at=BASE)
        assert until == datetime(2026, 7, 29, 23, 59, 59)

    def test_next_week_english(self):
        """next friday = 下周五 7-31。"""
        _, until = infer_validity_window("demo next Friday", created_at=BASE)
        assert until == datetime(2026, 7, 31, 23, 59, 59)

    def test_this_week_future_day(self):
        """本周五（未过）= 7-24。"""
        _, until = infer_validity_window("本周五之前完成设计稿", created_at=BASE)
        assert until == datetime(2026, 7, 24, 23, 59, 59)

    def test_this_week_passed_day_rolls_over(self):
        """周一已过 → 顺延到下周一 7-27。"""
        _, until = infer_validity_window("周一例会纪要", created_at=BASE)
        assert until == datetime(2026, 7, 27, 23, 59, 59)

    def test_this_month(self):
        _, until = infer_validity_window("本月完成上线", created_at=BASE)
        assert until == datetime(2026, 7, 31, 23, 59, 59)

    def test_explicit_date(self):
        _, until = infer_validity_window("8月15日发布 v2.0", created_at=BASE)
        assert until == datetime(2026, 8, 15, 23, 59, 59)

    def test_explicit_date_passed_rolls_to_next_year(self):
        """1月5日已过 → 视为明年。"""
        _, until = infer_validity_window("1月5日年会", created_at=BASE)
        assert until == datetime(2027, 1, 5, 23, 59, 59)

    def test_invalid_date_ignored(self):
        """2月30日为无效日期 → 不推断。"""
        _, until = infer_validity_window("2月30日的会议", created_at=BASE)
        assert until is None

    def test_no_time_marker_permanent(self):
        """无时间标记 → 永久有效。"""
        valid_from, until = infer_validity_window("用户喜欢 Python", created_at=BASE)
        assert valid_from == BASE
        assert until is None


# ============================================================
# 3. 写入时序窗口
# ============================================================

@pytest.mark.unit
class TestApplyValidity:
    """apply_validity_to_fragment 应把推断结果写入 DB。"""

    def test_apply_writes_valid_until(self):
        fid = _create_test_fragment("明天下午和张三开会")
        result = apply_validity_to_fragment(fid, TEST_USER_ID, "明天下午和张三开会", created_at=BASE)
        assert result["success"] is True
        assert result["valid_until"] == datetime(2026, 7, 23, 23, 59, 59).isoformat()

        db = get_db_client()
        rows = db.execute("SELECT valid_from, valid_until FROM memory_fragments WHERE id = ?", (fid,))
        assert rows[0]["valid_until"] == result["valid_until"]
        assert rows[0]["valid_from"] == BASE.isoformat()

    def test_apply_permanent_leaves_null(self):
        fid = _create_test_fragment("用户偏好深色主题", fragment_type="preference")
        result = apply_validity_to_fragment(fid, TEST_USER_ID, "用户偏好深色主题", created_at=BASE)
        assert result["success"] is True
        assert result["valid_until"] is None

        db = get_db_client()
        rows = db.execute("SELECT valid_until FROM memory_fragments WHERE id = ?", (fid,))
        assert rows[0]["valid_until"] is None


# ============================================================
# 4. 定时扫描标记 expired
# ============================================================

@pytest.mark.unit
class TestScanAndExpire:
    """scan_and_expire 应标记已过 valid_until 的 active 记忆。"""

    def test_expired_fragment_marked(self):
        fid = _create_test_fragment("今天下午开会")
        apply_validity_to_fragment(fid, TEST_USER_ID, "今天下午开会", created_at=BASE)

        # 次日凌晨扫描 → 已过期
        result = scan_and_expire(user_id=TEST_USER_ID, now=BASE + timedelta(days=1))
        assert result["success"] is True
        assert fid in result["expired_ids"]

        db = get_db_client()
        rows = db.execute("SELECT lifecycle_status FROM memory_fragments WHERE id = ?", (fid,))
        assert rows[0]["lifecycle_status"] == EXPIRED_STATUS

    def test_future_valid_until_not_expired(self):
        fid = _create_test_fragment("下周三评审")
        apply_validity_to_fragment(fid, TEST_USER_ID, "下周三评审", created_at=BASE)

        result = scan_and_expire(user_id=TEST_USER_ID, now=BASE + timedelta(hours=1))
        assert result["success"] is True
        assert fid not in result["expired_ids"]

    def test_permanent_fragment_never_expires(self):
        fid = _create_test_fragment("用户喜欢 Python", fragment_type="preference")
        apply_validity_to_fragment(fid, TEST_USER_ID, "用户喜欢 Python", created_at=BASE)

        result = scan_and_expire(user_id=TEST_USER_ID, now=BASE + timedelta(days=365))
        assert fid not in result["expired_ids"]

    def test_non_active_fragment_skipped(self):
        """非 active（如 superseded）不重复标记。"""
        fid = _create_test_fragment("今天下午开会")
        apply_validity_to_fragment(fid, TEST_USER_ID, "今天下午开会", created_at=BASE)
        db = get_db_client()
        db.execute(
            "UPDATE memory_fragments SET lifecycle_status = 'superseded' WHERE id = ?",
            (fid,),
        )

        result = scan_and_expire(user_id=TEST_USER_ID, now=BASE + timedelta(days=1))
        assert fid not in result["expired_ids"]


# ============================================================
# 5. 时刻有效性查询
# ============================================================

@pytest.mark.unit
class TestValidFragmentsAt:
    """get_valid_fragments_at 应按 valid_from/valid_until 窗口过滤。"""

    def test_valid_at_within_window(self):
        fid = _create_test_fragment("明天提交周报")
        apply_validity_to_fragment(fid, TEST_USER_ID, "明天提交周报", created_at=BASE)

        # 明天中午仍在窗口内
        result = get_valid_fragments_at(TEST_USER_ID, BASE + timedelta(days=1, hours=2))
        assert result["success"] is True
        assert fid in [f["id"] for f in result["fragments"]]

    def test_valid_at_after_window_excluded(self):
        fid = _create_test_fragment("明天提交周报")
        apply_validity_to_fragment(fid, TEST_USER_ID, "明天提交周报", created_at=BASE)

        # 3 天后已出窗口
        result = get_valid_fragments_at(TEST_USER_ID, BASE + timedelta(days=3))
        assert fid not in [f["id"] for f in result["fragments"]]

    def test_permanent_always_valid(self):
        fid = _create_test_fragment("用户喜欢 Python", fragment_type="preference")
        apply_validity_to_fragment(fid, TEST_USER_ID, "用户喜欢 Python", created_at=BASE)

        result = get_valid_fragments_at(TEST_USER_ID, BASE + timedelta(days=365))
        assert fid in [f["id"] for f in result["fragments"]]


# ============================================================
# 6. 召回过滤 expired
# ============================================================

@pytest.mark.unit
class TestRecallFiltersExpired:
    """expired 记忆不应进入召回结果。"""

    def test_recall_engine_excludes_expired(self, monkeypatch):
        from app.services import recall_engine as re_mod

        fake_fragments = [
            {
                "id": 1, "content": "项目 Alpha 的负责人是李雷",
                "fragment_type": "info", "similarity": 0.9,
                "lifecycle_status": "active", "importance_score": 0.6,
            },
            {
                "id": 2, "content": "项目 Alpha 今天下午过评审",
                "fragment_type": "plan", "similarity": 0.85,
                "lifecycle_status": "expired", "importance_score": 0.6,
            },
        ]
        monkeypatch.setattr(
            re_mod, "search_fragments_by_semantic",
            lambda **kwargs: {"success": True, "fragments": fake_fragments},
        )

        engine = re_mod.RecallEngine()
        result = engine.recall(
            user_id=TEST_USER_ID,
            query="项目 Alpha",
            use_hybrid_search=False,
            update_lifecycle=False,
            record_traces=False,
        )
        recalled_ids = [m.get("id") for m in result.memories]
        assert 2 not in recalled_ids
        assert 1 in recalled_ids
