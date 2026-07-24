"""L3 性能基准共享工具。

提供计时、分位数计算、数据预置与清理等通用函数，供 latency / throughput /
load_test / storage_growth 四个套件复用。
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 默认采样次数：CI 友好，可通过环境变量 PERF_ITERATIONS 放大
DEFAULT_ITERATIONS = int(os.getenv("PERF_ITERATIONS", "100"))
# 全局放宽系数（慢机器可通过环境变量调大，如 CI 设为 2.0）
PERF_SCALE = float(os.getenv("PERF_SCALE", "1.0"))
# 默认测试用户 ID（与 conftest 测试用户错开，避免干扰）
PERF_USER_ID = int(os.getenv("PERF_USER_ID", "9800"))
PERF_WORKSPACE_ID: Optional[int] = (
    int(v) if (v := os.getenv("PERF_WORKSPACE_ID", "")) else None
)


# ============================================================
# 分位数与计时
# ============================================================

def percentile(samples: List[float], pct: float) -> float:
    """计算百分位数（samples 单位：毫秒）。

    使用最近秩方法（nearest-rank），与 test_performance_baselines 保持一致。
    """
    if not samples:
        return 0.0
    ordered = sorted(samples)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * len(ordered))) - 1))
    return ordered[k]


def measure(fn: Callable[[], Any], iterations: int = DEFAULT_ITERATIONS,
            warmup: Optional[int] = None) -> Tuple[List[float], List[Any]]:
    """执行 fn 若干次，返回 (每次耗时毫秒列表, 每次返回值列表)。

    Args:
        fn: 被测函数
        iterations: 正式采样次数
        warmup: 预热次数（默认 iterations 的 10%，上限 20）

    Note:
        服务层函数（如 create_fragment）通常用 try/except 包裹并返回
        {"success": False, "error": ...} 而非抛异常。为避免把"快速失败"
        误测为"真实性能"，本函数将这类返回也标记为 _error，以便上层
        正确统计错误率。
    """
    if warmup is None:
        warmup = min(20, max(3, iterations // 10))
    for _ in range(warmup):
        try:
            fn()
        except Exception:
            pass  # 预热失败不中断
    samples: List[float] = []
    returns: List[Any] = []
    for _ in range(iterations):
        start = time.perf_counter()
        try:
            ret = fn()
            # 检测服务层的"静默失败"：返回字典且 success=False 视为错误
            if isinstance(ret, dict) and ret.get("success") is False:
                returns.append({"_error": ret.get("error", "success=False"),
                                "_silent": True})
            else:
                returns.append(ret)
        except Exception as e:
            returns.append({"_error": str(e)})
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples, returns


def summarize_latency(samples: List[float]) -> Dict[str, float]:
    """将原始延迟样本汇总为 P50/P95/P99/mean/min/max。"""
    if not samples:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0,
                "min": 0.0, "max": 0.0, "count": 0}
    return {
        "p50": round(percentile(samples, 50), 3),
        "p95": round(percentile(samples, 95), 3),
        "p99": round(percentile(samples, 99), 3),
        "mean": round(sum(samples) / len(samples), 3),
        "min": round(min(samples), 3),
        "max": round(max(samples), 3),
        "count": len(samples),
    }


# ============================================================
# 数据规模与预置
# ============================================================

# 数据规模梯度（片段数）
SCALE_OPTIONS = {
    "small": 1_000,   # 1K — CI 友好
    "medium": 10_000,  # 10K — nightly
    "large": 100_000,  # 100K — release/手动
}

# 别名：1k/10k/100k
SCALE_ALIASES = {
    "1k": "small", "10k": "medium", "100k": "large",
    "1000": "small", "10000": "medium", "100000": "large",
}


def resolve_scale(scale: str) -> int:
    """将规模标识解析为片段数。"""
    key = scale.lower()
    if key in SCALE_OPTIONS:
        return SCALE_OPTIONS[key]
    if key in SCALE_ALIASES:
        return SCALE_OPTIONS[SCALE_ALIASES[key]]
    raise ValueError(f"未知数据规模: {scale}，可选: {list(SCALE_OPTIONS.keys())}/{list(SCALE_ALIASES.keys())}")


def seed_fragments(user_id: int, count: int,
                   workspace_id: Optional[int] = None,
                   batch_size: int = 500) -> int:
    """预置 N 条记忆片段（含向量嵌入），返回实际写入数。

    使用 service 层 create_fragment 确保向量同步写入 ChromaDB。
    批量提交以降低开销。
    """
    ensure_perf_user(user_id)
    from app.services.memory_fragment_service import create_fragment

    written = 0
    topics = ["编程", "旅行", "美食", "健康", "阅读", "音乐", "运动", "科技", "工作", "学习"]
    for i in range(count):
        topic = topics[i % len(topics)]
        content = (
            f"性能测试记忆 #{i}：用户在 {topic} 领域的第 {i % 100} 条记录。"
            f"涉及关键词 perf_{i % 50} 与编号 {i}。"
        )
        try:
            result = create_fragment(
                user_id=user_id,
                fragment_type="info",
                content=content,
                importance_score=0.5 + (i % 5) * 0.05,
                workspace_id=workspace_id,
            )
            if isinstance(result, dict) and result.get("success", True):
                written += 1
            else:
                logger.debug(f"预置片段 {i} 失败: {result}")
        except Exception as e:
            logger.debug(f"预置片段 {i} 异常: {e}")
        if written > 0 and written % batch_size == 0:
            logger.info(f"  已预置 {written}/{count} 片段")
    return written


def seed_graph(user_id: int, entity_count: int = 50,
               workspace_id: Optional[int] = None) -> int:
    """预置图谱实体与关系，返回实体数。"""
    ensure_perf_user(user_id)
    from app.services.graph_memory_service import ensure_entity, add_relationship

    created = 0
    for i in range(entity_count):
        try:
            ensure_entity(
                user_id=user_id,
                name=f"perf_entity_{i}",
                entity_type="person",
                workspace_id=workspace_id,
            )
            created += 1
            # 建立关系（与前一个实体）
            if i > 0:
                try:
                    add_relationship(
                        user_id=user_id,
                        source_name=f"perf_entity_{i - 1}",
                        target_name=f"perf_entity_{i}",
                        relation_type="colleague",
                        source_type="person",
                        target_type="person",
                        workspace_id=workspace_id,
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"预置实体 {i} 失败: {e}")
    return created


# ============================================================
# 清理
# ============================================================

def ensure_perf_user(user_id: int) -> bool:
    """确保测试用户存在（避免外键约束失败）。

    create_fragment 等服务层操作要求 user_id 在 users 表中存在，
    否则 FOREIGN KEY constraint failed 会导致静默失败。
    """
    try:
        from app.core.db_client import get_db_client
        db = get_db_client()
        rows = db.execute("SELECT id FROM users WHERE id = ?", (user_id,))
        if rows:
            return True
        db.execute(
            "INSERT INTO users (id, username, email) VALUES (?, ?, ?)",
            (user_id, f"perf_user_{user_id}", f"perf_{user_id}@benchmark.local"),
        )
        logger.info(f"✓ 创建性能测试用户 id={user_id}")
        return True
    except Exception as e:
        logger.warning(f"创建性能测试用户失败: {e}")
        return False


def cleanup_user_data(user_ids: List[int]) -> None:
    """清理指定用户的所有记忆数据（多表 + ChromaDB 集合）。"""
    try:
        from app.core.db_client import get_db_client
        db = get_db_client()
        for uid in user_ids:
            try:
                db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (uid,))
            except Exception:
                pass
            try:
                db.execute("DELETE FROM memory_variables WHERE user_id = ?", (uid,))
            except Exception:
                pass
            try:
                db.execute("DELETE FROM memory_tables WHERE user_id = ?", (uid,))
            except Exception:
                pass
            # 注意：实际表名为 graph_entities / graph_relationships（无 memory_ 前缀）
            for table in ("graph_relationships", "graph_entities",
                          "graph_relationship_history"):
                try:
                    db.execute(f"DELETE FROM {table} WHERE user_id = ?", (uid,))
                except Exception:
                    pass
            try:
                db.execute("DELETE FROM memory_search_keys WHERE user_id = ?", (uid,))
            except Exception:
                pass
            try:
                db.execute("DELETE FROM vector_outbox WHERE user_id = ?", (uid,))
            except Exception:
                pass
            try:
                db.execute("DELETE FROM memory_lifecycle WHERE user_id = ?", (uid,))
            except Exception:
                pass
            try:
                db.execute("DELETE FROM memory_delete_log WHERE user_id = ?", (uid,))
            except Exception:
                pass
            try:
                db.execute("DELETE FROM memory_merge_log WHERE user_id = ?", (uid,))
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"清理 DB 失败: {e}")

    # 清理 ChromaDB 向量（按 user_id 元数据过滤）
    try:
        from app.core.chromadb_client import get_chromadb_client
        chroma = get_chromadb_client()
        if chroma and chroma.collection is not None:
            for uid in user_ids:
                try:
                    chroma.collection.delete(where={"user_id": str(uid)})
                except Exception:
                    pass
    except Exception:
        pass

    # 清理 Redis 缓存
    try:
        from app.core.cache_client import get_redis_client
        redis = get_redis_client()
        for uid in user_ids:
            pattern = f"mem:{uid}:*"
            for key in redis.keys(pattern):
                redis.delete(key)
    except Exception:
        pass


# ============================================================
# 存储大小测量
# ============================================================

def directory_size(path: str) -> int:
    """递归计算目录总大小（字节）。"""
    total = 0
    if not os.path.exists(path):
        return 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            try:
                fp = os.path.join(dirpath, f)
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total


def file_size(path: str) -> int:
    """获取文件大小（字节），不存在返回 0。"""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
