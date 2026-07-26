"""
冲突自动解决测试（W2-F2.3）

验证内容：
1. 策略选择规则（latest_wins / confidence_based / manual_review）
2. latest_wins：新记忆自动取代旧记忆
3. confidence_based：importance × evidence 因子比较
4. manual_review：写入 memory_conflicts 待用户确认
5. list_conflicts / resolve_conflict_manual
6. detect_contradiction 集成 ConflictResolver
"""
from datetime import datetime, timedelta

import pytest

from app.core.db_client import get_db_client
from app.services.conflict_resolution_service import (
    STRATEGY_CONFIDENCE,
    STRATEGY_LATEST_WINS,
    STRATEGY_MANUAL,
    ConflictResolver,
    _ensure_conflicts_table,
    get_conflict_resolver,
    list_conflicts,
    resolve_conflict_manual,
)
from app.services.contradiction_service import detect_contradiction

TEST_USER_ID = 9921


@pytest.fixture(autouse=True)
def _clean_tables():
    """每个测试前后清理测试数据。"""
    db = get_db_client()
    _ensure_conflicts_table()
    # 确保测试用户存在（满足 memory_fragments 外键约束）
    try:
        db.execute(
            "INSERT OR IGNORE INTO users (id, username) VALUES (?, ?)",
            (TEST_USER_ID, f"w2_conflict_test_{TEST_USER_ID}"),
        )
    except Exception:
        pass

    def _clean():
        for sql in (
            "DELETE FROM memory_conflicts WHERE user_id = ?",
            "DELETE FROM memory_evolution WHERE user_id = ?",
            "DELETE FROM memory_fragments WHERE user_id = ?",
        ):
            try:
                db.execute(sql, (TEST_USER_ID,))
            except Exception:
                pass

    _clean()
    yield
    _clean()


def _insert_fragment(
    content: str,
    importance: float = 0.5,
    lifecycle: str = "active",
    last_recalled_at: str = None,
    created_at: datetime = None,
) -> int:
    """直接插入记忆片段（绕过 create_fragment 的自动矛盾检测副作用）。"""
    db = get_db_client()
    return db.execute(
        """INSERT INTO memory_fragments
           (user_id, fragment_type, content, importance_score,
            lifecycle_status, created_at, last_recalled_at, vector_synced)
           VALUES (?, 'info', ?, ?, ?, ?, ?, 1)""",
        (
            TEST_USER_ID,
            content,
            importance,
            lifecycle,
            (created_at or datetime.now()).isoformat(),
            last_recalled_at,
        ),
    )


def _get_status(fragment_id: int) -> str:
    db = get_db_client()
    rows = db.execute(
        "SELECT lifecycle_status FROM memory_fragments WHERE id = ?", (fragment_id,)
    )
    return rows[0]["lifecycle_status"]


def _frag(fragment_id: int) -> dict:
    db = get_db_client()
    rows = db.execute("SELECT * FROM memory_fragments WHERE id = ?", (fragment_id,))
    return dict(rows[0])


# ============================================================
# 1. 策略选择规则
# ============================================================

@pytest.mark.unit
class TestStrategySelection:
    """_select_strategy 三分支规则。"""

    def setup_method(self):
        self.resolver = ConflictResolver()

    def test_both_high_importance_manual_review(self):
        """双高（≥0.9, ≥0.9）→ manual_review。"""
        strategy = self.resolver._select_strategy(
            {"importance_score": 0.95}, {"importance_score": 0.92}
        )
        assert strategy == STRATEGY_MANUAL

    def test_high_old_low_new_confidence_based(self):
        """old ≥0.9 且 new <0.7 → confidence_based。"""
        strategy = self.resolver._select_strategy(
            {"importance_score": 0.95}, {"importance_score": 0.5}
        )
        assert strategy == STRATEGY_CONFIDENCE

    def test_default_latest_wins(self):
        """普通重要性 → latest_wins。"""
        strategy = self.resolver._select_strategy(
            {"importance_score": 0.5}, {"importance_score": 0.5}
        )
        assert strategy == STRATEGY_LATEST_WINS

    def test_high_old_medium_new_latest_wins(self):
        """old ≥0.9 但 new ≥0.7（非双高）→ latest_wins。"""
        strategy = self.resolver._select_strategy(
            {"importance_score": 0.95}, {"importance_score": 0.8}
        )
        assert strategy == STRATEGY_LATEST_WINS


# ============================================================
# 2. latest_wins 策略
# ============================================================

@pytest.mark.unit
class TestLatestWins:
    """默认策略：新记忆取代旧记忆。"""

    def test_old_superseded(self):
        old_id = _insert_fragment("I live in New York", importance=0.5)
        new_id = _insert_fragment("I moved to San Francisco", importance=0.5)

        result = get_conflict_resolver().resolve(_frag(old_id), _frag(new_id))

        assert result.strategy == STRATEGY_LATEST_WINS
        assert result.action == "superseded_old"
        assert result.winner_id == new_id
        assert result.loser_id == old_id
        assert _get_status(old_id) == "superseded"
        assert _get_status(new_id) == "active"


# ============================================================
# 3. confidence_based 策略
# ============================================================

@pytest.mark.unit
class TestConfidenceBased:
    """importance × evidence 因子比较。"""

    def test_high_value_old_memory_kept(self):
        """高价值旧记忆（有召回实证）胜出，低可信新记忆被拒。"""
        old_id = _insert_fragment(
            "I live in New York",
            importance=0.95,
            last_recalled_at=datetime.now().isoformat(),
            created_at=datetime.now() - timedelta(days=30),
        )
        new_id = _insert_fragment("I moved to San Francisco", importance=0.5)

        result = get_conflict_resolver().resolve(_frag(old_id), _frag(new_id))

        assert result.strategy == STRATEGY_CONFIDENCE
        assert result.action == "kept_old"
        assert result.winner_id == old_id
        assert result.loser_id == new_id
        assert _get_status(old_id) == "active"
        assert _get_status(new_id) == "superseded"
        assert result.detail["old_score"] > result.detail["new_score"]

    def test_confidence_score_evidence_factors(self):
        """被召回 +0.1，近 7 天创建 +0.05。"""
        resolver = ConflictResolver()
        base = resolver._confidence_score({
            "importance_score": 0.8,
            "created_at": (datetime.now() - timedelta(days=30)).isoformat(),
        })
        recalled = resolver._confidence_score({
            "importance_score": 0.8,
            "last_recalled_at": datetime.now().isoformat(),
            "created_at": (datetime.now() - timedelta(days=30)).isoformat(),
        })
        fresh = resolver._confidence_score({
            "importance_score": 0.8,
            "created_at": datetime.now().isoformat(),
        })
        assert base == pytest.approx(0.8)
        assert recalled == pytest.approx(0.8 * 1.1)
        assert fresh == pytest.approx(0.8 * 1.05)


# ============================================================
# 4. manual_review 策略
# ============================================================

@pytest.mark.unit
class TestManualReview:
    """双高重要性 → 写入 memory_conflicts，等待用户确认。"""

    def test_pending_review_created(self):
        old_id = _insert_fragment("I live in New York", importance=0.95)
        new_id = _insert_fragment("I moved to San Francisco", importance=0.95)

        result = get_conflict_resolver().resolve(
            _frag(old_id), _frag(new_id),
            {"entity_type": "location", "detection_method": "pattern"},
        )

        assert result.strategy == STRATEGY_MANUAL
        assert result.action == "pending_review"
        assert result.conflict_id is not None
        # 双方均不变更
        assert _get_status(old_id) == "active"
        assert _get_status(new_id) == "active"

        db = get_db_client()
        rows = db.execute(
            "SELECT * FROM memory_conflicts WHERE id = ?", (result.conflict_id,)
        )
        conflict = dict(rows[0])
        assert conflict["status"] == "conflict_pending"
        assert conflict["old_fragment_id"] == old_id
        assert conflict["new_fragment_id"] == new_id
        assert conflict["entity_type"] == "location"


# ============================================================
# 5. 冲突列表与手动解决
# ============================================================

@pytest.mark.unit
class TestListAndManualResolve:
    """list_conflicts / resolve_conflict_manual。"""

    def _make_pending_conflict(self):
        old_id = _insert_fragment("I live in New York", importance=0.95)
        new_id = _insert_fragment("I moved to San Francisco", importance=0.95)
        result = get_conflict_resolver().resolve(_frag(old_id), _frag(new_id))
        return old_id, new_id, result.conflict_id

    def test_list_conflicts_with_fragment_details(self):
        old_id, new_id, conflict_id = self._make_pending_conflict()

        result = list_conflicts(TEST_USER_ID)
        assert result["success"] is True
        assert result["total"] == 1
        entry = result["conflicts"][0]
        assert entry["id"] == conflict_id
        assert entry["old_fragment"]["id"] == old_id
        assert entry["new_fragment"]["id"] == new_id

    def test_resolve_keep_new(self):
        old_id, new_id, conflict_id = self._make_pending_conflict()

        result = resolve_conflict_manual(TEST_USER_ID, conflict_id, "keep_new", reason="搬家了")
        assert result["success"] is True
        assert result["action_taken"] == "keep_new"
        assert result["affected_fragments"] == [old_id]
        assert _get_status(old_id) == "superseded"
        assert _get_status(new_id) == "active"

    def test_resolve_keep_old(self):
        old_id, new_id, conflict_id = self._make_pending_conflict()

        result = resolve_conflict_manual(TEST_USER_ID, conflict_id, "keep_old")
        assert result["success"] is True
        assert result["affected_fragments"] == [new_id]
        assert _get_status(old_id) == "active"
        assert _get_status(new_id) == "superseded"

    def test_resolve_keep_both(self):
        old_id, new_id, conflict_id = self._make_pending_conflict()

        result = resolve_conflict_manual(TEST_USER_ID, conflict_id, "keep_both")
        assert result["success"] is True
        assert result["affected_fragments"] == []
        assert _get_status(old_id) == "active"
        assert _get_status(new_id) == "active"

    def test_resolved_conflict_removed_from_pending(self):
        _, _, conflict_id = self._make_pending_conflict()
        resolve_conflict_manual(TEST_USER_ID, conflict_id, "keep_new")

        result = list_conflicts(TEST_USER_ID)
        assert result["total"] == 0

    def test_invalid_resolution_rejected(self):
        _, _, conflict_id = self._make_pending_conflict()
        result = resolve_conflict_manual(TEST_USER_ID, conflict_id, "invalid_option")
        assert result["success"] is False

    def test_double_resolve_rejected(self):
        _, _, conflict_id = self._make_pending_conflict()
        resolve_conflict_manual(TEST_USER_ID, conflict_id, "keep_new")
        result = resolve_conflict_manual(TEST_USER_ID, conflict_id, "keep_old")
        assert result["success"] is False

    def test_nonexistent_conflict_rejected(self):
        result = resolve_conflict_manual(TEST_USER_ID, 999999, "keep_new")
        assert result["success"] is False


# ============================================================
# 6. detect_contradiction 集成
# ============================================================

@pytest.mark.unit
class TestDetectContradictionIntegration:
    """矛盾检测应通过 ConflictResolver 策略化解决。"""

    def test_default_importance_auto_superseded(self):
        """普通重要性 → latest_wins 自动取代。"""
        old_id = _insert_fragment("I live in New York", importance=0.5)
        new_id = _insert_fragment("I moved to San Francisco", importance=0.5)

        result = detect_contradiction(
            user_id=TEST_USER_ID,
            new_content="I moved to San Francisco",
            new_fragment_id=new_id,
            enable_semantic=False,
        )

        assert result["success"] is True
        assert old_id in result["superseded_ids"]
        assert "pattern" in result["detection_methods"]
        c = result["contradictions"][0]
        assert c["resolution_strategy"] == STRATEGY_LATEST_WINS
        assert c["resolution_action"] == "superseded_old"
        assert _get_status(old_id) == "superseded"

    def test_double_high_importance_pending_review(self):
        """双高重要性 → manual_review，双方不变更。"""
        old_id = _insert_fragment("I live in New York", importance=0.95)
        new_id = _insert_fragment("I moved to San Francisco", importance=0.95)

        result = detect_contradiction(
            user_id=TEST_USER_ID,
            new_content="I moved to San Francisco",
            new_fragment_id=new_id,
            enable_semantic=False,
        )

        assert result["success"] is True
        assert result["superseded_ids"] == []
        c = result["contradictions"][0]
        assert c["resolution_strategy"] == STRATEGY_MANUAL
        assert c["resolution_action"] == "pending_review"
        assert c.get("conflict_id") is not None
        assert _get_status(old_id) == "active"

        # 待处理冲突可通过 list_conflicts 查询
        conflicts = list_conflicts(TEST_USER_ID)
        assert conflicts["total"] == 1

    def test_high_old_low_new_kept_old(self):
        """高价值旧记忆 vs 低可信新记忆 → kept_old，新记忆被拒。"""
        old_id = _insert_fragment(
            "I live in New York",
            importance=0.95,
            last_recalled_at=datetime.now().isoformat(),
        )
        new_id = _insert_fragment("I moved to San Francisco", importance=0.5)

        result = detect_contradiction(
            user_id=TEST_USER_ID,
            new_content="I moved to San Francisco",
            new_fragment_id=new_id,
            enable_semantic=False,
        )

        assert result["success"] is True
        assert result["superseded_ids"] == []
        c = result["contradictions"][0]
        assert c["resolution_strategy"] == STRATEGY_CONFIDENCE
        assert c["resolution_action"] == "kept_old"
        assert _get_status(old_id) == "active"
        assert _get_status(new_id) == "superseded"
