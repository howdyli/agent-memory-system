"""
G3 Variables 数据库备份单测

覆盖：
- set → 备份表 upsert（expires_at 正确 / ttl=None → NULL / 重复 set 幂等）
- delete / clear / update_ttl → 备份表镜像
- DB 写失败 → Redis 主路径不受影响（set 仍返回 True，仅 WARNING）
- restore：跳过 Redis 已存在 key、跳过过期记录、正常恢复（TTL / 索引正确）
- 开关 VARIABLES_DB_BACKUP_ENABLED=False → 双写与恢复全部短路
- POST /variables/restore-backup 与 GET /variables/backup-stats 端点

Redis 沿用仓库全局客户端（本地无真实 Redis 时自动降级 fakeredis）。
"""
import json
import logging
from datetime import timedelta

import pytest

from app.core.config import get_settings
from app.core.db_client import get_db_client
from app.core.redis_client import get_redis_client
from app.services import memory_variable_service as mvs
from app.services.memory_variable_service import (
    clear_memory_variables,
    delete_memory_variable,
    get_memory_variable,
    list_memory_variables,
    restore_variables_from_backup,
    get_variables_backup_stats,
    set_memory_variable,
    update_variable_ttl,
)

USER_ID = 999


# ============================================================
# 工具函数与夹具
# ============================================================

def _rows(key=None):
    """读取测试用户的备份表行（dict 列表）。"""
    db = get_db_client()
    mvs._ensure_backup_table(db)
    if key is not None:
        rows = db.execute(
            "SELECT * FROM memory_variables_backup WHERE user_id = ? AND key = ?",
            (USER_ID, key),
        )
    else:
        rows = db.execute(
            "SELECT * FROM memory_variables_backup WHERE user_id = ?",
            (USER_ID,),
        )
    return [dict(r) for r in rows or []]


def _insert_backup_row(key, value, ttl_seconds=None, expires_at=None,
                       workspace_id=0, session_id=""):
    """直接向备份表插入记录（模拟 Redis 数据已丢失的场景）。"""
    db = get_db_client()
    mvs._ensure_backup_table(db)
    db.execute(
        "INSERT INTO memory_variables_backup "
        "(user_id, workspace_id, session_id, key, value, ttl_seconds, expires_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (USER_ID, workspace_id, session_id, key, value, ttl_seconds, expires_at),
    )


def _cleanup():
    """清理测试用户的备份表行与 Redis 变量键。"""
    try:
        db = get_db_client()
        mvs._ensure_backup_table(db)
        db.execute("DELETE FROM memory_variables_backup WHERE user_id = ?", (USER_ID,))
    except Exception:
        pass
    try:
        conn = get_redis_client().get_connection()
        for pattern in (f"memory:var:{USER_ID}:*", f"memory:var:index:{USER_ID}*"):
            for k in conn.keys(pattern):
                conn.delete(k)
    except Exception:
        pass


@pytest.fixture(autouse=True)
def _clean_state():
    """每个用例前后重置 settings 缓存并清理备份表 / Redis 状态。"""
    get_settings.cache_clear()
    _cleanup()
    yield
    get_settings.cache_clear()
    _cleanup()


# ============================================================
# 双写：set / delete / clear / update_ttl
# ============================================================

@pytest.mark.unit
def test_set_writes_backup_row_with_expires_at():
    """set 带 TTL：备份表 1 行，ttl_seconds / expires_at / value 正确，哨兵归一。"""
    assert set_memory_variable(USER_ID, "bk_set", {"a": 1}, ttl=3600) is True

    rows = _rows("bk_set")
    assert len(rows) == 1
    row = rows[0]
    assert row["ttl_seconds"] == 3600
    assert json.loads(row["value"]) == {"a": 1}
    # 哨兵归一：无 workspace → 0，无 session → ''
    assert row["workspace_id"] == 0
    assert row["session_id"] == ""
    # expires_at ≈ now + 3600（允许 60s 容差）
    exp = mvs._parse_backup_timestamp(row["expires_at"])
    delta = (exp - mvs._utcnow_naive()).total_seconds()
    assert 3540 <= delta <= 3660


@pytest.mark.unit
def test_set_permanent_stores_null_ttl_and_expires():
    """set ttl=None（永久）：ttl_seconds / expires_at 均为 NULL。"""
    assert set_memory_variable(USER_ID, "bk_perm", "forever", ttl=None) is True

    rows = _rows("bk_perm")
    assert len(rows) == 1
    assert rows[0]["ttl_seconds"] is None
    assert rows[0]["expires_at"] is None


@pytest.mark.unit
def test_repeated_set_upsert_idempotent():
    """重复 set 同一 key：仍 1 行且 value / TTL 更新（哨兵归一保证幂等）。"""
    assert set_memory_variable(USER_ID, "bk_upsert", "v1", ttl=60) is True
    assert set_memory_variable(USER_ID, "bk_upsert", "v2", ttl=None) is True

    rows = _rows("bk_upsert")
    assert len(rows) == 1
    assert rows[0]["value"] == "v2"
    assert rows[0]["ttl_seconds"] is None
    assert rows[0]["expires_at"] is None


@pytest.mark.unit
def test_set_with_session_and_workspace_columns():
    """会话级 + workspace 变量：session_id / workspace_id 原样入列。"""
    assert set_memory_variable(USER_ID, "bk_scoped", "v", session_id="sess1",
                               ttl=60, workspace_id=5) is True

    rows = _rows("bk_scoped")
    assert len(rows) == 1
    assert rows[0]["workspace_id"] == 5
    assert rows[0]["session_id"] == "sess1"

    assert delete_memory_variable(USER_ID, "bk_scoped", session_id="sess1",
                                  workspace_id=5) is True
    assert _rows("bk_scoped") == []


@pytest.mark.unit
def test_delete_mirrors_backup():
    """delete → 备份表行同步删除。"""
    set_memory_variable(USER_ID, "bk_del", "v", ttl=60)
    assert len(_rows("bk_del")) == 1

    assert delete_memory_variable(USER_ID, "bk_del") is True
    assert _rows("bk_del") == []


@pytest.mark.unit
def test_clear_mirrors_backup():
    """clear → 同作用域备份行全部删除。"""
    set_memory_variable(USER_ID, "bk_clear_1", "v1", ttl=60)
    set_memory_variable(USER_ID, "bk_clear_2", "v2", ttl=None)
    assert len(_rows()) == 2

    assert clear_memory_variables(USER_ID) == 2
    assert _rows() == []


@pytest.mark.unit
def test_update_ttl_mirrors_backup():
    """update_variable_ttl → 备份表 ttl_seconds / expires_at 镜像更新。"""
    set_memory_variable(USER_ID, "bk_ttl", "v", ttl=60)

    # 续期到 3600s
    assert update_variable_ttl(USER_ID, "bk_ttl", ttl=3600) is True
    row = _rows("bk_ttl")[0]
    assert row["ttl_seconds"] == 3600
    exp = mvs._parse_backup_timestamp(row["expires_at"])
    delta = (exp - mvs._utcnow_naive()).total_seconds()
    assert 3540 <= delta <= 3660

    # 改为永久
    assert update_variable_ttl(USER_ID, "bk_ttl", ttl=None) is True
    row = _rows("bk_ttl")[0]
    assert row["ttl_seconds"] is None
    assert row["expires_at"] is None


# ============================================================
# best-effort：DB 故障不影响 Redis 主路径
# ============================================================

@pytest.mark.unit
def test_db_failure_does_not_break_set(monkeypatch, caplog):
    """DB 写失败：set 仍返回 True（Redis 可读），仅记 WARNING。"""
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(mvs, "get_db_client", _boom)

    with caplog.at_level(logging.WARNING, logger="app.services.memory_variable_service"):
        assert set_memory_variable(USER_ID, "bk_fail", "still-ok", ttl=60) is True

    assert get_memory_variable(USER_ID, "bk_fail") == "still-ok"
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "[variables_backup]" in r.getMessage()]
    assert warnings, "备份失败必须记录 [variables_backup] WARNING"


# ============================================================
# restore_variables_from_backup
# ============================================================

@pytest.mark.unit
def test_restore_skips_existing_redis_keys():
    """Redis 中已存在的 key 不覆盖，计入 skipped_existing。"""
    set_memory_variable(USER_ID, "bk_exist", "redis-value", ttl=3600)

    stats = restore_variables_from_backup(user_id=USER_ID)
    assert stats["scanned"] == 1
    assert stats["skipped_existing"] == 1
    assert stats["restored"] == 0
    assert get_memory_variable(USER_ID, "bk_exist") == "redis-value"


@pytest.mark.unit
def test_restore_skips_expired_rows():
    """已过期备份记录跳过，计入 skipped_expired，不写 Redis。"""
    past = (mvs._utcnow_naive() - timedelta(seconds=10)).strftime(mvs._BACKUP_TS_FORMAT)
    _insert_backup_row("bk_expired", "old", ttl_seconds=60, expires_at=past)

    stats = restore_variables_from_backup(user_id=USER_ID)
    assert stats["scanned"] == 1
    assert stats["skipped_expired"] == 1
    assert stats["restored"] == 0
    assert get_memory_variable(USER_ID, "bk_expired") is None


@pytest.mark.unit
def test_restore_missing_key_with_remaining_ttl_and_index():
    """正常记录恢复：值 / 剩余 TTL（按 expires_at 计算）/ 索引均正确。"""
    future = (mvs._utcnow_naive() + timedelta(seconds=3600)).strftime(mvs._BACKUP_TS_FORMAT)
    # ttl_seconds 故意给 7200：剩余 TTL 必须来自 expires_at 而非原始 ttl_seconds
    _insert_backup_row("bk_restore", "hello", ttl_seconds=7200, expires_at=future)

    stats = restore_variables_from_backup(user_id=USER_ID)
    assert stats["scanned"] == 1
    assert stats["restored"] == 1
    assert stats["skipped_existing"] == 0 and stats["skipped_expired"] == 0

    assert get_memory_variable(USER_ID, "bk_restore") == "hello"
    remaining = get_redis_client().ttl(mvs._build_key(USER_ID, "bk_restore"))
    assert 0 < remaining <= 3600
    # 索引已更新（list 走索引）
    assert "bk_restore" in list_memory_variables(USER_ID)


@pytest.mark.unit
def test_restore_permanent_key_has_no_ttl():
    """永久记录恢复：Redis 无过期时间（ttl == -1）。"""
    _insert_backup_row("bk_restore_perm", "forever")

    stats = restore_variables_from_backup(user_id=USER_ID)
    assert stats["restored"] == 1
    assert get_memory_variable(USER_ID, "bk_restore_perm") == "forever"
    assert get_redis_client().ttl(mvs._build_key(USER_ID, "bk_restore_perm")) == -1


@pytest.mark.unit
def test_restore_denormalizes_sentinel_scope():
    """哨兵反归一：workspace_id=0 / session_id='' 恢复为全局作用域 Redis key。"""
    _insert_backup_row("bk_restore_scope", "v", workspace_id=0, session_id="")

    stats = restore_variables_from_backup(user_id=USER_ID)
    assert stats["restored"] == 1
    # 全局作用域 key（不含 :w0: 段）可直接读取
    assert get_redis_client().exists(f"memory:var:{USER_ID}:bk_restore_scope")


# ============================================================
# 开关：VARIABLES_DB_BACKUP_ENABLED=False
# ============================================================

@pytest.mark.unit
def test_disabled_switch_skips_backup_and_restore(monkeypatch):
    """开关关闭：不写备份表；restore 短路返回全 0 + disabled 标记。"""
    monkeypatch.setenv("VARIABLES_DB_BACKUP_ENABLED", "false")
    get_settings.cache_clear()

    assert set_memory_variable(USER_ID, "bk_disabled", "v", ttl=60) is True
    assert _rows("bk_disabled") == []

    stats = restore_variables_from_backup(user_id=USER_ID)
    assert stats.get("disabled") is True
    assert stats["scanned"] == 0 and stats["restored"] == 0
    assert stats["skipped_existing"] == 0 and stats["skipped_expired"] == 0

    assert get_variables_backup_stats(user_id=USER_ID) == {
        "enabled": False, "total": 0, "active": 0
    }


# ============================================================
# restore setnx 原子竞争分支
# ============================================================

@pytest.mark.unit
def test_restore_setnx_race_skips_existing(monkeypatch):
    """
    竞争分支：exists() 返回 False（key 看似缺失），但 SET NX 返回 None/False
    （并发写入抢先创建），断言 skipped_existing += 1 且未调 expire/persist。
    """
    future = (mvs._utcnow_naive() + timedelta(seconds=3600)).strftime(mvs._BACKUP_TS_FORMAT)
    _insert_backup_row("bk_race", "race-value", ttl_seconds=3600, expires_at=future)

    redis_client = get_redis_client()
    real_conn = redis_client.get_connection()

    # Mock connection.set：nx=True 时返回 None（模拟并发竞争失败）
    original_set = real_conn.set
    call_log = []

    def mock_set(name, value, nx=False, ex=None, **kwargs):
        call_log.append({"name": name, "nx": nx, "ex": ex})
        if nx:
            return None  # key 已被并发写入抢占
        return original_set(name, value, **kwargs)

    monkeypatch.setattr(real_conn, "set", mock_set)

    # 确保 exists() 返回 False（key 在 fakeredis 中确实不存在）
    stats = restore_variables_from_backup(user_id=USER_ID)

    assert stats["scanned"] == 1
    assert stats["skipped_existing"] == 1, "SET NX 返回 None 时必须计入 skipped_existing"
    assert stats["restored"] == 0
    # 确认 nx=True 的单命令确实被调用
    assert any(c["nx"] is True for c in call_log), "必须走原子 SET NX，不能退回 exists+set"
    # key 未被写入
    assert get_memory_variable(USER_ID, "bk_race") is None


# ============================================================
# purge_expired_backup_rows 服务层
# ============================================================

@pytest.mark.unit
def test_purge_expired_backup_rows_service():
    """直接调 purge_expired_backup_rows：过期行被物理删除，未过期行保留。"""
    past = (mvs._utcnow_naive() - timedelta(seconds=60)).strftime(mvs._BACKUP_TS_FORMAT)
    future = (mvs._utcnow_naive() + timedelta(seconds=3600)).strftime(mvs._BACKUP_TS_FORMAT)

    # 2 条过期 + 1 条未过期 + 1 条永久（无 expires_at，不被清理）
    _insert_backup_row("bk_purge_exp1", "v1", ttl_seconds=60, expires_at=past)
    _insert_backup_row("bk_purge_exp2", "v2", ttl_seconds=60, expires_at=past)
    _insert_backup_row("bk_purge_active", "v3", ttl_seconds=3600, expires_at=future)
    _insert_backup_row("bk_purge_perm", "v4")  # expires_at=None

    result = mvs.purge_expired_backup_rows(user_id=USER_ID)

    assert result["purged"] == 2
    # 过期行已删除
    assert _rows("bk_purge_exp1") == []
    assert _rows("bk_purge_exp2") == []
    # 未过期行与永久行保留
    assert len(_rows("bk_purge_active")) == 1
    assert len(_rows("bk_purge_perm")) == 1


@pytest.mark.unit
def test_purge_disabled_when_switch_off(monkeypatch):
    """开关关闭时 purge_expired_backup_rows 短路返回 disabled=True。"""
    monkeypatch.setenv("VARIABLES_DB_BACKUP_ENABLED", "false")
    get_settings.cache_clear()

    past = (mvs._utcnow_naive() - timedelta(seconds=60)).strftime(mvs._BACKUP_TS_FORMAT)
    _insert_backup_row("bk_purge_dis", "v", ttl_seconds=60, expires_at=past)

    result = mvs.purge_expired_backup_rows(user_id=USER_ID)
    assert result.get("disabled") is True
    assert result["purged"] == 0
    # 行未被删除（开关关闭，函数短路）
    assert len(_rows("bk_purge_dis")) == 1


# ============================================================
# 管理端点 API
# ============================================================

@pytest.mark.integration
def test_restore_backup_endpoint(client, auth_headers):
    """POST /variables/restore-backup：Redis 丢 key 后可经备份恢复。"""
    resp = client.post("/api/v1/memory/variables", headers=auth_headers, json={
        "key": "bk_api_restore", "value": "v-api", "ttl": 3600,
    })
    assert resp.status_code == 200

    # 从备份表反查该用户，模拟 Redis 数据丢失
    db = get_db_client()
    rows = db.execute(
        "SELECT user_id, workspace_id, session_id FROM memory_variables_backup WHERE key = ?",
        ("bk_api_restore",),
    )
    assert rows
    row = dict(rows[0])
    redis_key = mvs._build_key(row["user_id"], "bk_api_restore",
                               row["session_id"] or None, row["workspace_id"] or None)
    get_redis_client().delete(redis_key)
    resp = client.get("/api/v1/memory/variables/bk_api_restore", headers=auth_headers)
    assert resp.status_code == 404

    resp = client.post("/api/v1/memory/variables/restore-backup", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["restored"] >= 1
    for field in ("scanned", "restored", "skipped_existing", "skipped_expired"):
        assert field in body

    resp = client.get("/api/v1/memory/variables/bk_api_restore", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["value"] == "v-api"

    # 清理（同时镜像清理备份表）
    client.delete("/api/v1/memory/variables/bk_api_restore", headers=auth_headers)


@pytest.mark.integration
def test_backup_stats_endpoint(client, auth_headers):
    """GET /variables/backup-stats：返回总行数 / 未过期行数 / 开关状态。"""
    resp = client.post("/api/v1/memory/variables", headers=auth_headers, json={
        "key": "bk_api_stats", "value": "v", "ttl": 3600,
    })
    assert resp.status_code == 200

    resp = client.get("/api/v1/memory/variables/backup-stats", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["total"] >= 1
    assert 1 <= body["active"] <= body["total"]

    client.delete("/api/v1/memory/variables/bk_api_stats", headers=auth_headers)


@pytest.mark.integration
def test_backup_purge_endpoint(client, auth_headers):
    """POST /variables/backup-purge：物理删除过期行，返回 purged 计数。"""
    past = (mvs._utcnow_naive() - timedelta(seconds=60)).strftime(mvs._BACKUP_TS_FORMAT)
    future = (mvs._utcnow_naive() + timedelta(seconds=3600)).strftime(mvs._BACKUP_TS_FORMAT)

    # 先通过 API 创建一条记录，从备份表反查真实 user_id
    resp = client.post("/api/v1/memory/variables", headers=auth_headers, json={
        "key": "bk_purge_probe", "value": "probe", "ttl": 3600,
    })
    assert resp.status_code == 200

    db = get_db_client()
    mvs._ensure_backup_table(db)
    probe_row = db.execute(
        "SELECT user_id FROM memory_variables_backup WHERE key = ?",
        ("bk_purge_probe",),
    )
    assert probe_row, "probe 行必须存在"
    uid = dict(probe_row[0])["user_id"]

    # 直接插入 2 条过期 + 1 条未过期（用同一 user_id）
    for key, exp in [("bk_purge_e1", past), ("bk_purge_e2", past), ("bk_purge_ok", future)]:
        db.execute(
            "INSERT INTO memory_variables_backup "
            "(user_id, workspace_id, session_id, key, value, ttl_seconds, expires_at) "
            "VALUES (?, 0, '', ?, 'v', 60, ?)",
            (uid, key, exp),
        )

    resp = client.post("/api/v1/memory/variables/backup-purge", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["purged"] >= 2

    # 过期行已被清理，未过期行保留
    remaining = db.execute(
        "SELECT key FROM memory_variables_backup "
        "WHERE user_id = ? AND key IN ('bk_purge_e1','bk_purge_e2','bk_purge_ok')",
        (uid,),
    )
    remaining_keys = {dict(r)["key"] for r in (remaining or [])}
    assert "bk_purge_ok" in remaining_keys
    assert "bk_purge_e1" not in remaining_keys
    assert "bk_purge_e2" not in remaining_keys

    # 清理
    client.delete("/api/v1/memory/variables/bk_purge_probe", headers=auth_headers)
    db.execute("DELETE FROM memory_variables_backup WHERE user_id = ?", (uid,))
