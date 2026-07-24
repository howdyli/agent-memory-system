"""
存储增长基准评测套件（L3）

量化记忆存储的空间成本，供用户容量规划。

测量方法：
    对每个规模（1K/10K/100K 片段）：
    1. 记录基线存储大小（SQLite 文件 / ChromaDB 目录 / Redis 内存）
    2. 创建 N 条记忆片段（含向量嵌入）
    3. 触发 VACUUM / persist，再次测量
    4. 计算增量与每条记忆平均成本

输出：
    - 存储成本表：每 10K 记忆 ≈ X MB SQLite + Y MB ChromaDB
    - 增长曲线数据（供报告绘制）

环境变量：
    PERF_STORAGE_SCALES  规模列表（默认 "1000,10000"，100K 需手动启用）

用法：
    python -m app.benchmarks.runner run --suite storage_growth
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite
from app.benchmarks.performance._utils import (
    PERF_USER_ID,
    cleanup_user_data,
    directory_size,
    file_size,
    seed_fragments,
)

logger = logging.getLogger(__name__)

# 目标：每条记忆平均存储成本 < 5KB（SQLite + ChromaDB 合计）
TARGETS = {
    "avg_bytes_per_fragment": 5_000.0,
}

_DEFAULT_SCALES = [1_000, 10_000]


@register_suite
class StorageGrowthBenchmarkSuite(BenchmarkSuite):
    """L3 存储增长基准评测套件。"""

    name = "storage_growth"
    level = "L3"
    requires_llm = False
    requires_external = False
    description = "记忆存储空间成本（SQLite/ChromaDB/Redis 增量与每条平均成本）"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.user_id: int = self.config.get("user_id", PERF_USER_ID)
        # 规模列表
        cfg_scales = self.config.get("scales")
        if cfg_scales:
            self.scales = [int(s) for s in str(cfg_scales).split(",")]
        else:
            env_s = os.getenv("PERF_STORAGE_SCALES", "")
            self.scales = (
                [int(s) for s in env_s.split(",")] if env_s else list(_DEFAULT_SCALES)
            )
        self.results: List[Dict[str, Any]] = []
        # 存储路径
        self._sqlite_path = self._resolve_sqlite_path()
        self._chroma_dir = self._resolve_chroma_dir()

    def _resolve_sqlite_path(self) -> str:
        """从 settings 解析 SQLite 文件路径。"""
        try:
            from app.core.config import get_settings
            url = get_settings().DATABASE_URL
            if url.startswith("sqlite:///"):
                return url.replace("sqlite:///", "", 1)
        except Exception:
            pass
        return "agent_memory.db"

    def _resolve_chroma_dir(self) -> str:
        """从 settings 解析 ChromaDB 持久化目录。"""
        try:
            from app.core.config import get_settings
            return get_settings().CHROMA_PERSIST_DIR
        except Exception:
            return "./chromadb_data"

    def setup(self) -> None:
        """清理残留数据。"""
        cleanup_user_data([self.user_id])

    def run(self) -> Dict[str, Any]:
        """对每个规模测量存储增量。"""
        self.results = []
        for scale in self.scales:
            self._measure_scale(scale)
        return {
            "scales": self.scales,
            "sqlite_path": self._sqlite_path,
            "chroma_dir": self._chroma_dir,
            "measurements": self.results,
        }

    def _measure_scale(self, count: int) -> None:
        """测量指定规模下的存储增长。"""
        logger.info(f"[storage_growth] 测量规模 {count} 片段")

        # 清理本用户数据，确保从 0 开始
        cleanup_user_data([self.user_id])

        # 基线测量
        baseline = self._snapshot_storage()

        # 写入 N 条片段
        written = seed_fragments(self.user_id, count)

        # 触发 SQLite VACUUM 回收空间（更准确反映实际占用）
        try:
            from app.core.db_client import get_db_client
            db = get_db_client()
            db.execute("VACUUM")
        except Exception as e:
            logger.debug(f"VACUUM 失败: {e}")

        # 写入后测量
        after = self._snapshot_storage()

        # 计算增量
        delta_sqlite = after["sqlite_bytes"] - baseline["sqlite_bytes"]
        delta_chroma = after["chroma_bytes"] - baseline["chroma_bytes"]
        delta_redis = after["redis_bytes"] - baseline["redis_bytes"]
        total_delta = delta_sqlite + delta_chroma
        avg_per_fragment = total_delta / written if written > 0 else 0.0

        result = {
            "scale": count,
            "actual_written": written,
            "baseline": baseline,
            "after": after,
            "delta_sqlite_bytes": delta_sqlite,
            "delta_chroma_bytes": delta_chroma,
            "delta_redis_bytes": delta_redis,
            "total_delta_bytes": total_delta,
            "avg_bytes_per_fragment": round(avg_per_fragment, 1),
            "avg_kb_per_fragment": round(avg_per_fragment / 1024, 2),
            "delta_sqlite_kb": round(delta_sqlite / 1024, 1),
            "delta_chroma_kb": round(delta_chroma / 1024, 1),
        }
        self.results.append(result)
        logger.info(
            f"[storage_growth] {count} 片段: "
            f"SQLite +{delta_sqlite / 1024:.0f}KB, "
            f"ChromaDB +{delta_chroma / 1024:.0f}KB, "
            f"avg={avg_per_fragment / 1024:.2f}KB/条"
        )

    def _snapshot_storage(self) -> Dict[str, int]:
        """拍摄当前存储快照（SQLite / ChromaDB / Redis）。"""
        snapshot = {
            "sqlite_bytes": file_size(self._sqlite_path),
            "chroma_bytes": directory_size(self._chroma_dir),
            "redis_bytes": 0,
        }
        # Redis 内存占用
        try:
            from app.core.cache_client import get_redis_client
            redis = get_redis_client()
            info = redis.info(section="memory")
            snapshot["redis_bytes"] = int(info.get("used_memory", 0))
        except Exception:
            pass
        return snapshot

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """取最大规模的每条平均成本判定通过。"""
        measurements = raw_results.get("measurements", [])
        if not measurements:
            return BenchmarkResult(
                suite_name=self.name, level=self.level,
                metrics={"avg_bytes_per_fragment": 0.0},
                targets=TARGETS, passed=False,
                details=[{"error": "无存储测量结果"}], raw=raw_results,
            )

        # 取最大规模的结果
        max_m = max(measurements, key=lambda x: x.get("scale", 0))
        avg_bytes = max_m.get("avg_bytes_per_fragment", 0.0)

        result = BenchmarkResult(
            suite_name=self.name,
            level=self.level,
            metrics={"avg_bytes_per_fragment": avg_bytes},
            targets=TARGETS,
            details=measurements,
            raw=raw_results,
        )
        # 存储成本：越低越好，手动判定
        result.passed = avg_bytes <= TARGETS["avg_bytes_per_fragment"]
        return result

    def teardown(self) -> None:
        """清理测试数据。"""
        cleanup_user_data([self.user_id])
