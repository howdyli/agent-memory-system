"""
向量索引重建脚本（P0：Embedding 可插拔配套工具）

从 SQLite memory_fragments 表读取全部未删除片段，批量写入当前配置
Embedding 模型对应的 Chroma 集合。切换 EMBEDDING_PROVIDER/EMBEDDING_MODEL
后运行本脚本，即可在新集合中重建全量向量索引（旧集合保持不动）。

用法:
    # 预览待重建的片段数量与目标集合（不写入）
    EMBEDDING_PROVIDER=local python scripts/reindex_vectors.py --dry-run

    # 实际重建
    EMBEDDING_PROVIDER=local python scripts/reindex_vectors.py

幂等性：按 fragment_id 先删后写，重复运行不会产生重复向量。
"""
import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# 保证可从 backend 目录外执行
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("reindex_vectors")


def load_fragments(db):
    """读取全部未删除且未过期的记忆片段。"""
    now = datetime.now().isoformat()
    rows = db.execute(
        '''
        SELECT id, user_id, workspace_id, fragment_type, content,
               importance_score, expires_at
        FROM memory_fragments
        WHERE (lifecycle_status IS NULL OR lifecycle_status NOT IN ('soft_deleted', 'archived'))
          AND (expires_at IS NULL OR expires_at = '' OR expires_at > ?)
        ORDER BY id
        ''',
        (now,),
    )
    # sqlite3.Row 不支持 .get()，统一转 dict
    return [dict(r) for r in (rows or [])]


def build_metadata(row) -> dict:
    """构建与 memory_fragment_service.create_fragment 一致的向量元数据。"""
    workspace_id = row.get("workspace_id")
    return {
        "fragment_id": str(row["id"]),
        "user_id": str(row["user_id"]),
        "workspace_id": str(workspace_id) if workspace_id is not None else "",
        "fragment_type": row["fragment_type"],
        "importance_score": str(row.get("importance_score") if row.get("importance_score") is not None else 0.5),
        "expires_at": row.get("expires_at") or "",
        "vector_synced": "1",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="重建 Chroma 向量索引（按当前 Embedding 配置）")
    parser.add_argument("--dry-run", action="store_true", help="仅统计，不写入向量库")
    parser.add_argument("--progress-every", type=int, default=50, help="每 N 条输出一次进度")
    args = parser.parse_args()

    from app.core.config import get_settings
    from app.core.db_client import get_db_client
    from app.core.chromadb_client import get_chromadb_client

    settings = get_settings()
    logger.info(f"Embedding 配置: provider={settings.EMBEDDING_PROVIDER}, model={settings.EMBEDDING_MODEL}")

    db = get_db_client()
    rows = load_fragments(db)
    logger.info(f"待重建片段数: {len(rows)}")

    chroma = get_chromadb_client()
    if chroma is None or chroma.collection is None:
        logger.error("✗ ChromaDB 客户端初始化失败，无法重建索引")
        return 1
    logger.info(f"目标集合: {chroma.collection_name}（当前计数: {chroma.collection.count()}）")

    if args.dry_run:
        logger.info("--dry-run 模式，未写入任何数据")
        return 0

    start = time.time()
    ok, failed = 0, 0
    for i, row in enumerate(rows, 1):
        try:
            # 幂等：先删除该 fragment_id 在集合中的旧向量
            chroma.collection.delete(where={"fragment_id": str(row["id"])})
            chroma.add_embedding(text=row["content"], metadata=build_metadata(row))
            ok += 1
        except Exception as e:
            failed += 1
            logger.warning(f"⚠️ 片段 {row['id']} 重建失败: {e}")
        if i % args.progress_every == 0 or i == len(rows):
            elapsed = time.time() - start
            logger.info(f"进度: {i}/{len(rows)}（成功 {ok}，失败 {failed}，耗时 {elapsed:.1f}s）")

    logger.info(f"✓ 重建完成: 成功 {ok}，失败 {failed}，集合计数: {chroma.collection.count()}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
