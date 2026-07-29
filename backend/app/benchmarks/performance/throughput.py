"""
摄入吞吐基准评测套件（L3）

量化批量摄入能力（如首次集成时导入历史会话），覆盖三类场景：
    1. 批量创建记忆片段（含向量嵌入）— 10K 片段
    2. 批量导入结构化表记录 — 1K 记录
    3. 批量会话记忆抽取（LLM 抽取）— 100 段会话（需 LLM，默认跳过）

并发度梯度：1 / 4 / 8 / 16

指标：
    - 吞吐量（ops/sec）
    - 总耗时
    - 错误率
    - Outbox 积压量（跨存储一致性延迟 P95）

环境变量：
    PERF_CONCURRENCY  并发度列表（默认 "1,4,8"）
    PERF_FRAGMENT_BATCH  批量片段数（默认 1000，CI 友好）
    PERF_SKIP_LLM     跳过 LLM 抽取场景（默认 1）

用法：
    python -m app.benchmarks.runner run --suite throughput
    python -m app.benchmarks.runner run --suite throughput --concurrency 8
"""
from __future__ import annotations

import concurrent.futures
import logging
import os
import time
from typing import Any, Dict, List, Optional

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite
from app.benchmarks.performance._utils import (
    PERF_SCALE,
    PERF_USER_ID,
    PERF_WORKSPACE_ID,
    cleanup_user_data,
)

logger = logging.getLogger(__name__)

# 目标：片段创建吞吐量 ≥ 50 ops/sec（单并发，含向量嵌入）
TARGETS = {
    "fragment_throughput_ops": 50.0 / PERF_SCALE,
    "fragment_success_rate": 0.95,
    "table_record_throughput_ops": 100.0 / PERF_SCALE,
}

_DEFAULT_CONCURRENCY = [1, 4, 8]
_DEFAULT_FRAGMENT_BATCH = int(os.getenv("PERF_FRAGMENT_BATCH", "1000"))
_DEFAULT_RECORD_BATCH = int(os.getenv("PERF_RECORD_BATCH", "500"))
_SKIP_LLM = os.getenv("PERF_SKIP_LLM", "1") == "1"


@register_suite
class ThroughputBenchmarkSuite(BenchmarkSuite):
    """L3 摄入吞吐基准评测套件。"""

    name = "throughput"
    level = "L3"
    requires_llm = False
    requires_external = False
    description = "批量摄入吞吐量（片段/表记录/会话抽取）+ Outbox 积压"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.user_id: int = self.config.get("user_id", PERF_USER_ID)
        self.workspace_id: Optional[int] = self.config.get(
            "workspace_id", PERF_WORKSPACE_ID
        )
        # 并发度：从 config 或环境变量
        cfg_concurrency = self.config.get("concurrency")
        if cfg_concurrency:
            self.concurrency_levels = [int(c) for c in str(cfg_concurrency).split(",")]
        else:
            env_c = os.getenv("PERF_CONCURRENCY", "")
            self.concurrency_levels = (
                [int(c) for c in env_c.split(",")] if env_c else list(_DEFAULT_CONCURRENCY)
            )
        self.fragment_batch: int = self.config.get(
            "fragment_batch", _DEFAULT_FRAGMENT_BATCH
        )
        self.record_batch: int = self.config.get(
            "record_batch", _DEFAULT_RECORD_BATCH
        )
        self.skip_llm: bool = self.config.get("skip_llm", _SKIP_LLM)
        self.results: List[Dict[str, Any]] = []

    def setup(self) -> None:
        """清理残留数据。"""
        cleanup_user_data([self.user_id])

    def run(self) -> Dict[str, Any]:
        """对每个并发度跑片段/表记录吞吐测试。"""
        self.results = []
        for concurrency in self.concurrency_levels:
            self._bench_fragment_throughput(concurrency)
            self._bench_table_record_throughput(concurrency)
        # 会话抽取（LLM）单独跑，仅单并发
        if not self.skip_llm:
            self._bench_conversation_extraction()
        else:
            self.results.append({
                "scenario": "conversation_extraction",
                "concurrency": 1,
                "skipped": True,
                "reason": "PERF_SKIP_LLM=1 或未配置 LLM",
            })
        return {
            "concurrency_levels": self.concurrency_levels,
            "fragment_batch": self.fragment_batch,
            "record_batch": self.record_batch,
            "scenarios": self.results,
        }

    # ============================================================
    # 场景 1：批量创建记忆片段（含向量嵌入）
    # ============================================================

    def _bench_fragment_throughput(self, concurrency: int) -> None:
        """并发创建 fragment_batch 条片段，测量吞吐量与 Outbox 积压。"""
        from app.services.memory_fragment_service import create_fragment

        total = self.fragment_batch
        per_worker = max(1, total // concurrency)
        counter = {"n": 0, "errors": 0}
        lock = None
        if concurrency > 1:
            import threading
            lock = threading.Lock()

        def worker(_worker_id: int) -> int:
            local_ok = 0
            for _ in range(per_worker):
                with _lock_or_none(lock):
                    idx = counter["n"]
                    counter["n"] += 1
                content = f"吞吐测试片段 #{idx} by worker {_worker_id}"
                try:
                    create_fragment(
                        user_id=self.user_id,
                        fragment_type="info",
                        content=content,
                        importance_score=0.5,
                        workspace_id=self.workspace_id,
                    )
                    local_ok += 1
                except Exception:
                    with _lock_or_none(lock):
                        counter["errors"] += 1
            return local_ok

        start = time.perf_counter()
        if concurrency == 1:
            worker(0)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                list(pool.map(worker, range(concurrency)))
        elapsed = time.perf_counter() - start

        ok = counter["n"] - counter["errors"]
        throughput = ok / elapsed if elapsed > 0 else 0.0
        error_rate = counter["errors"] / max(1, counter["n"])
        outbox_backlog = self._measure_outbox_backlog()

        result = {
            "scenario": "fragment_create",
            "concurrency": concurrency,
            "total": counter["n"],
            "succeeded": ok,
            "errors": counter["errors"],
            "error_rate": round(error_rate, 4),
            "elapsed_seconds": round(elapsed, 3),
            "throughput_ops": round(throughput, 2),
            "outbox_backlog": outbox_backlog,
        }
        self.results.append(result)
        logger.info(
            f"[throughput] fragment_create c={concurrency}: "
            f"{throughput:.1f} ops/s (errors={counter['errors']}, "
            f"outbox={outbox_backlog['pending']})"
        )

    # ============================================================
    # 场景 2：批量导入结构化表记录
    # ============================================================

    def _bench_table_record_throughput(self, concurrency: int) -> None:
        """并发导入 record_batch 条表记录。"""
        from app.services.memory_table_service import create_memory_table, add_record

        table_name = f"perf_throughput_c{concurrency}"
        fields = [
            {"name": "title", "type": "text"},
            {"name": "value", "type": "number"},
            {"name": "tag", "type": "text"},
        ]
        try:
            create_memory_table(
                user_id=self.user_id, table_name=table_name,
                fields=fields, workspace_id=self.workspace_id,
            )
        except Exception as e:
            logger.warning(f"建表失败: {e}")
            self.results.append({
                "scenario": "table_record",
                "concurrency": concurrency,
                "error": str(e),
            })
            return

        total = self.record_batch
        per_worker = max(1, total // concurrency)
        counter = {"n": 0, "errors": 0}
        lock = None
        if concurrency > 1:
            import threading
            lock = threading.Lock()

        def worker(_worker_id: int) -> int:
            local_ok = 0
            for _ in range(per_worker):
                with _lock_or_none(lock):
                    idx = counter["n"]
                    counter["n"] += 1
                try:
                    add_record(
                        user_id=self.user_id, table_name=table_name,
                        record={"title": f"record_{idx}", "value": idx, "tag": "perf"},
                        workspace_id=self.workspace_id,
                    )
                    local_ok += 1
                except Exception:
                    with _lock_or_none(lock):
                        counter["errors"] += 1
            return local_ok

        start = time.perf_counter()
        if concurrency == 1:
            worker(0)
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
                list(pool.map(worker, range(concurrency)))
        elapsed = time.perf_counter() - start

        ok = counter["n"] - counter["errors"]
        throughput = ok / elapsed if elapsed > 0 else 0.0
        error_rate = counter["errors"] / max(1, counter["n"])

        result = {
            "scenario": "table_record",
            "concurrency": concurrency,
            "table_name": table_name,
            "total": counter["n"],
            "succeeded": ok,
            "errors": counter["errors"],
            "error_rate": round(error_rate, 4),
            "elapsed_seconds": round(elapsed, 3),
            "throughput_ops": round(throughput, 2),
        }
        self.results.append(result)
        logger.info(
            f"[throughput] table_record c={concurrency}: "
            f"{throughput:.1f} ops/s (errors={counter['errors']})"
        )

    # ============================================================
    # 场景 3：批量会话记忆抽取（LLM）
    # ============================================================

    def _bench_conversation_extraction(self) -> None:
        """抽取 100 段会话的记忆（LLM 路径）。"""
        from app.services.memory_extraction_service import batch_extract_from_conversation

        sample_conversations = [
            [
                {"role": "user", "content": f"我叫测试用户{ i }, 住在城市{i}"},
                {"role": "assistant", "content": f"好的, 已记录城市{i}"},
            ]
            for i in range(100)
        ]

        start = time.perf_counter()
        ok = 0
        errors = 0
        for conv in sample_conversations:
            try:
                batch_extract_from_conversation(
                    user_id=self.user_id,
                    conversation_history=conv,
                    workspace_id=self.workspace_id,
                )
                ok += 1
            except Exception:
                errors += 1
        elapsed = time.perf_counter() - start
        throughput = ok / elapsed if elapsed > 0 else 0.0

        result = {
            "scenario": "conversation_extraction",
            "concurrency": 1,
            "total": len(sample_conversations),
            "succeeded": ok,
            "errors": errors,
            "error_rate": round(errors / max(1, len(sample_conversations)), 4),
            "elapsed_seconds": round(elapsed, 3),
            "throughput_ops": round(throughput, 2),
        }
        self.results.append(result)
        logger.info(
            f"[throughput] conversation_extraction: "
            f"{throughput:.1f} ops/s (errors={errors})"
        )

    # ============================================================
    # Outbox 积压测量
    # ============================================================

    def _measure_outbox_backlog(self) -> Dict[str, Any]:
        """测量 vector_outbox 表的积压情况。"""
        try:
            from app.core.db_client import get_db_client
            db = get_db_client()
            # 总积压量
            row = db.execute(
                "SELECT COUNT(*) as cnt FROM vector_outbox WHERE user_id = ?",
                (self.user_id,),
            )
            pending = row[0]["cnt"] if row else 0
            # 重试次数分布
            retry_rows = db.execute(
                "SELECT retry_count, COUNT(*) as cnt FROM vector_outbox "
                "WHERE user_id = ? GROUP BY retry_count",
                (self.user_id,),
            )
            retry_dist = {str(r["retry_count"]): r["cnt"] for r in (retry_rows or [])}
            return {"pending": pending, "retry_distribution": retry_dist}
        except Exception as e:
            logger.debug(f"Outbox 测量失败: {e}")
            return {"pending": -1, "error": str(e)}

    # ============================================================
    # 评估与清理
    # ============================================================

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """提取单并发片段吞吐量与错误率，对照目标判定。"""
        scenarios = raw_results.get("scenarios", [])

        # 取单并发(c=1)的片段吞吐量作为基线指标
        frag_c1 = next(
            (s for s in scenarios
             if s.get("scenario") == "fragment_create" and s.get("concurrency") == 1),
            {},
        )
        table_c1 = next(
            (s for s in scenarios
             if s.get("scenario") == "table_record" and s.get("concurrency") == 1),
            {},
        )

        frag_throughput = frag_c1.get("throughput_ops", 0.0)
        frag_error_rate = frag_c1.get("error_rate", 1.0)
        table_throughput = table_c1.get("throughput_ops", 0.0)

        # 错误率指标：用 1 - error_rate 表示（≥ 0.95 为通过）
        frag_success_rate = 1.0 - frag_error_rate

        metrics = {
            "fragment_throughput_ops": frag_throughput,
            "fragment_success_rate": frag_success_rate,
            "table_record_throughput_ops": table_throughput,
        }

        result = BenchmarkResult(
            suite_name=self.name,
            level=self.level,
            metrics=metrics,
            targets=TARGETS,
            details=scenarios,
            raw=raw_results,
        )
        result.check_targets()
        return result

    def teardown(self) -> None:
        """清理测试数据。"""
        cleanup_user_data([self.user_id])


def _lock_or_none(lock):
    """返回上下文管理器：lock 存在时加锁，否则 no-op。"""
    if lock is None:
        from contextlib import nullcontext
        return nullcontext()
    return lock
