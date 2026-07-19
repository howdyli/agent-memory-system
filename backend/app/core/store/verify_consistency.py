"""Store 层端到端验证脚本（Phase 1）。

针对现有 SQLite 开发库（db_client 已建好 17 张核心表）做三项检查：
1. SQLAlchemyStore CRUD：create / get / query / update / delete / count 全链路。
2. ChromaVectorStore 基本可用性：add / search / get / delete / count。
3. Store 与 legacy db_client 的一致性：同一数据在两套读取路径下返回一致。

运行：
    cd backend
    .venv/bin/python -m app.core.store.verify_consistency
"""
from __future__ import annotations

import logging
import sys
import time

from app.core.db_client import db_client as sqlite_client
from app.core.store import get_relational_store, get_vector_store
from app.models.orm import MemoryFragment, MemoryVariable, User

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

TEST_USER_ID = 999_001


def _banner(msg: str) -> None:
    log.info("\n" + "=" * 60)
    log.info(msg)
    log.info("=" * 60)


def _check(label: str, condition: bool, detail: str = "") -> None:
    flag = "✅" if condition else "❌"
    log.info(f"  {flag} {label}" + (f"  ({detail})" if detail else ""))
    if not condition:
        raise AssertionError(f"check failed: {label}")


def test_relational_crud() -> None:
    _banner("1) SQLAlchemyStore CRUD（MemoryVariable）")
    store = get_relational_store()
    _check("ping()", store.ping())

    # 准备：清理测试用户的遗留数据
    stale = store.query(MemoryVariable, filters={"user_id": TEST_USER_ID})
    for row in stale:
        store.delete(MemoryVariable, row.id)

    # create
    row = MemoryVariable(user_id=TEST_USER_ID, key="phase1_smoke", value="v1")
    created = store.create(row)
    _check("create() 返回自增 id", created.id is not None and created.id > 0, f"id={created.id}")

    # get
    fetched = store.get(MemoryVariable, created.id)
    _check("get() 返回非 None", fetched is not None)
    _check("get() value 一致", fetched.value == "v1")

    # update
    updated = store.update(MemoryVariable, created.id, {"value": "v2"})
    _check("update() 成功", updated is not None and updated.value == "v2")

    # query
    rows = store.query(MemoryVariable, filters={"user_id": TEST_USER_ID})
    _check("query() 命中 1 条", len(rows) == 1, f"count={len(rows)}")

    # count
    _check("count() 返回 1", store.count(MemoryVariable, filters={"user_id": TEST_USER_ID}) == 1)

    # delete
    _check("delete() 返回 True", store.delete(MemoryVariable, created.id))
    _check("删除后 get() 返回 None", store.get(MemoryVariable, created.id) is None)


def test_relational_existing_data() -> None:
    _banner("2) 现有数据 ORM 查询（跨 dialect 兼容）")
    store = get_relational_store()
    users = store.query(User, limit=3)
    _check("users 表至少 1 行", len(users) >= 1, f"count={len(users)}")
    fragments = store.query(MemoryFragment, limit=5, order_by="created_at", desc=True)
    _check("memory_fragments 有数据", len(fragments) > 0, f"count={len(fragments)}")
    total = store.count(MemoryFragment)
    _check("count(memory_fragments) > 0", total > 0, f"total={total}")


def test_legacy_consistency() -> None:
    _banner("3) Store vs legacy SQLiteClient 一致性")
    store = get_relational_store()

    # 写入一条临时数据（通过 Store 路径）
    row = MemoryVariable(user_id=TEST_USER_ID, key="consistency_key", value="hello")
    created = store.create(row)

    # 双读路径：
    #   - Store 路径：ORM SELECT
    #   - Legacy 路径：SQLiteClient.execute 原生 SQL（非 Redis）
    via_store = store.query(MemoryVariable, filters={"user_id": TEST_USER_ID, "key": "consistency_key"})
    via_legacy = sqlite_client.get_memory_variable(TEST_USER_ID, "consistency_key")

    _check("Store 路径读到 1 条", len(via_store) == 1)
    _check("legacy SQLite 路径读到值", via_legacy is not None, f"value={via_legacy!r}")
    _check("双路径 value 一致", via_store[0].value == str(via_legacy),
           f"store={via_store[0].value!r} legacy={via_legacy!r}")

    # 清理（走 legacy 路径）
    sqlite_client.execute("DELETE FROM memory_variables WHERE user_id = ? AND key = ?",
                          (TEST_USER_ID, "consistency_key"))


def test_vector_store() -> None:
    _banner("4) ChromaVectorStore 基本可用性")
    vs = get_vector_store()
    _check("ping()", vs.ping())

    text = "Phase 1 验证向量存储可用性"
    doc_id = vs.add(text=text, metadata={"user_id": str(TEST_USER_ID), "phase": "1"})
    _check("add() 返回 doc_id", doc_id is not None, f"id={doc_id}")

    # Chroma 写入到可检索有延迟，等 200ms 再查
    time.sleep(0.2)
    got = vs.get(doc_id)
    _check("get() 返回非 None", got is not None)

    results = vs.search(query_text="Phase 1 向量存储", n_results=3,
                        where={"user_id": str(TEST_USER_ID)})
    _check("search() 命中 ≥ 1", len(results) >= 1, f"hits={len(results)}")

    _check("delete() 返回 True", vs.delete(doc_id))
    _check("删除后 get() 为 None", vs.get(doc_id) is None)


def main() -> int:
    _banner("Store 层端到端验证（Phase 1）")
    tests = [
        test_relational_crud,
        test_relational_existing_data,
        test_legacy_consistency,
        test_vector_store,
    ]
    failures = 0
    for fn in tests:
        try:
            fn()
        except Exception as e:
            failures += 1
            log.exception(f"✗ {fn.__name__} 失败: {e}")

    _banner(f"验证完成：{'✅ 全部通过' if failures == 0 else f'❌ {failures} 项失败'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
