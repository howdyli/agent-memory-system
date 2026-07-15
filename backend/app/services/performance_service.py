"""
性能优化服务

提供缓存优化、查询优化、并发性能优化和监控
"""
import logging
import json
import time
import hashlib
import asyncio
from typing import Optional, Dict, Any, List, Callable
from functools import wraps
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.redis_client import get_redis_client


# ============================================================
# 缓存服务
# ============================================================

class CacheService:
    """多层级缓存服务"""

    def __init__(self):
        self._redis = None
        self._local_cache: Dict[str, Any] = {}
        self._cache_stats = {"hits": 0, "misses": 0, "sets": 0, "deletes": 0}

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    def _make_key(self, namespace: str, key: str) -> str:
        return f"cache:{namespace}:{key}"

    def get(self, namespace: str, key: str) -> Optional[Any]:
        """从缓存获取数据"""
        full_key = self._make_key(namespace, key)

        # 1. 先查本地缓存
        if full_key in self._local_cache:
            entry = self._local_cache[full_key]
            if entry["expires_at"] is None or time.time() < entry["expires_at"]:
                self._cache_stats["hits"] += 1
                return entry["value"]
            else:
                del self._local_cache[full_key]

        # 2. 查 Redis
        try:
            data = self.redis.get(full_key)
            if data:
                value = json.loads(data)
                # 回填本地缓存
                self._local_cache[full_key] = {"value": value, "expires_at": time.time() + 60}
                self._cache_stats["hits"] += 1
                return value
        except Exception:
            pass

        self._cache_stats["misses"] += 1
        return None

    def set(self, namespace: str, key: str, value: Any, ttl: int = 300):
        """设置缓存"""
        full_key = self._make_key(namespace, key)
        value_str = json.dumps(value, ensure_ascii=False, default=str)

        # 写入 Redis
        try:
            self.redis.setex(full_key, ttl, value_str)
        except Exception:
            pass

        # 写入本地缓存
        self._local_cache[full_key] = {"value": value, "expires_at": time.time() + min(ttl, 60)}
        self._cache_stats["sets"] += 1

    def delete(self, namespace: str, key: str):
        """删除缓存"""
        full_key = self._make_key(namespace, key)

        if full_key in self._local_cache:
            del self._local_cache[full_key]

        try:
            self.redis.delete(full_key)
        except Exception:
            pass

        self._cache_stats["deletes"] += 1

    def clear_namespace(self, namespace: str):
        """清空指定命名空间的缓存"""
        prefix = self._make_key(namespace, "")
        keys_to_del = [k for k in self._local_cache if k.startswith(prefix)]
        for k in keys_to_del:
            del self._local_cache[k]

        try:
            for key in self.redis.keys(f"{prefix}*"):
                self.redis.delete(key)
        except Exception:
            pass

    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计"""
        total = self._cache_stats["hits"] + self._cache_stats["misses"]
        hit_rate = (self._cache_stats["hits"] / total * 100) if total > 0 else 0
        return {
            **self._cache_stats,
            "hit_rate": round(hit_rate, 2),
            "local_cache_size": len(self._local_cache)
        }


# 全局缓存实例
_cache_service = CacheService()


def get_cache() -> CacheService:
    return _cache_service


def cached(namespace: str, ttl: int = 300):
    """
    缓存装饰器

    Args:
        namespace: 缓存命名空间
        ttl: 缓存过期时间（秒）
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key = hashlib.md5(
                f"{func.__name__}:{args}:{sorted(kwargs.items())}".encode()
            ).hexdigest()

            # 尝试从缓存获取
            cached_value = _cache_service.get(namespace, cache_key)
            if cached_value is not None:
                logger.debug(f"✓ 缓存命中: {namespace}:{func.__name__}")
                return cached_value

            # 执行函数
            result = func(*args, **kwargs)

            # 写入缓存
            _cache_service.set(namespace, cache_key, result, ttl)
            return result

        return wrapper
    return decorator


# ============================================================
# LLM 响应缓存
# ============================================================

def cache_llm_response(user_id: int, prompt_hash: str, response: Any, ttl: int = 3600):
    """缓存 LLM 响应"""
    _cache_service.set(f"llm:{user_id}", prompt_hash, response, ttl)


def get_cached_llm_response(user_id: int, prompt_hash: str) -> Optional[Any]:
    """获取缓存的 LLM 响应"""
    return _cache_service.get(f"llm:{user_id}", prompt_hash)


def compute_prompt_hash(messages: List[Dict[str, str]]) -> str:
    """计算消息列表的哈希值"""
    prompt_str = json.dumps(messages, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(prompt_str.encode()).hexdigest()


# ============================================================
# 数据库查询优化
# ============================================================

def _ensure_perf_tables():
    """确保性能表存在"""
    db = get_db_client()
    db.execute('''
        CREATE TABLE IF NOT EXISTS query_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            query_type TEXT,
            table_name TEXT,
            execution_time_ms REAL,
            row_count INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS index_suggestions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT,
            column_name TEXT,
            suggestion TEXT,
            priority TEXT DEFAULT 'medium',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')


def log_query(user_id: int, query_type: str, table_name: str,
              execution_time_ms: float, row_count: int = 0):
    """记录查询日志（用于分析慢查询）"""
    try:
        _ensure_perf_tables()
        db = get_db_client()
        db.execute(
            'INSERT INTO query_logs (user_id, query_type, table_name, execution_time_ms, row_count) VALUES (?, ?, ?, ?, ?)',
            (user_id, query_type, table_name, execution_time_ms, row_count)
        )
    except Exception:
        pass


def get_slow_queries(limit: int = 20, threshold_ms: float = 100) -> Dict[str, Any]:
    """获取慢查询列表"""
    try:
        _ensure_perf_tables()
        db = get_db_client()
        rows = db.execute(
            'SELECT * FROM query_logs WHERE execution_time_ms > ? ORDER BY execution_time_ms DESC LIMIT ?',
            (threshold_ms, limit)
        )
        queries = [dict(row) for row in rows] if rows else []
        return {"success": True, "slow_queries": queries, "count": len(queries)}
    except Exception as e:
        return {"success": False, "error": str(e)}


def analyze_indexes() -> Dict[str, Any]:
    """分析索引使用情况并给出优化建议"""
    try:
        _ensure_perf_tables()
        db = get_db_client()

        # 获取所有表
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'query_logs' AND name NOT LIKE 'index_suggestions'"
        )

        suggestions = []
        for table_row in tables:
            table_name = dict(table_row)["name"]

            # 获取表结构
            columns = db.execute(f"PRAGMA table_info({table_name})")
            col_names = [dict(c)["name"] for c in columns] if columns else []

            # 获取现有索引
            indexes = db.execute(f"PRAGMA index_list({table_name})")
            indexed_cols = set()
            if indexes:
                for idx in indexes:
                    idx_info = dict(idx)
                    idx_cols = db.execute(f"PRAGMA index_info({idx_info['name']})")
                    if idx_cols:
                        for ic in idx_cols:
                            indexed_cols.add(dict(ic)["name"])

            # 检查查询日志中该表的查询模式
            query_logs = db.execute(
                'SELECT query_type, COUNT(*) as count, AVG(execution_time_ms) as avg_time FROM query_logs WHERE table_name = ? GROUP BY query_type',
                (table_name,)
            )

            if query_logs:
                for log in query_logs:
                    log_dict = dict(log)
                    if log_dict["avg_time"] and log_dict["avg_time"] > 50:
                        suggestions.append({
                            "table": table_name,
                            "query_type": log_dict["query_type"],
                            "avg_time_ms": round(log_dict["avg_time"], 2),
                            "query_count": log_dict["count"],
                            "suggestion": f"Consider adding index on frequently queried columns in {table_name}"
                        })

        return {
            "success": True,
            "suggestions": suggestions,
            "table_count": len(tables) if tables else 0,
            "analyzed_tables": [dict(t)["name"] for t in tables] if tables else []
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# 并发性能优化
# ============================================================

def batch_process(items: List[Any], processor: Callable, batch_size: int = 100) -> Dict[str, Any]:
    """
    批量处理数据

    Args:
        items: 待处理数据列表
        processor: 处理函数
        batch_size: 每批大小

    Returns:
        处理结果
    """
    results = []
    errors = []
    total = len(items)

    for i in range(0, total, batch_size):
        batch = items[i:i + batch_size]
        for item in batch:
            try:
                result = processor(item)
                results.append(result)
            except Exception as e:
                errors.append({"item": str(item)[:100], "error": str(e)})

    return {
        "success": True,
        "processed": len(results),
        "errors": len(errors),
        "results": results,
        "error_details": errors[:10]  # 最多返回10个错误详情
    }


# ============================================================
# 性能监控
# ============================================================

def get_performance_stats() -> Dict[str, Any]:
    """获取性能统计"""
    try:
        _ensure_perf_tables()
        db = get_db_client()

        # 查询统计
        total_queries = db.execute('SELECT COUNT(*) as count FROM query_logs')
        total = dict(total_queries[0])["count"] if total_queries else 0

        avg_time = db.execute('SELECT AVG(execution_time_ms) as avg FROM query_logs')
        avg = round(dict(avg_time[0])["avg"], 2) if avg_time and avg_time[0]["avg"] else 0

        max_time = db.execute('SELECT MAX(execution_time_ms) as max FROM query_logs')
        mx = round(dict(max_time[0])["max"], 2) if max_time and max_time[0]["max"] else 0

        # 按表统计
        by_table = db.execute(
            'SELECT table_name, COUNT(*) as count, AVG(execution_time_ms) as avg_time FROM query_logs GROUP BY table_name ORDER BY avg_time DESC LIMIT 10'
        )
        table_stats = []
        if by_table:
            for row in by_table:
                r = dict(row)
                table_stats.append({
                    "table": r["table_name"],
                    "query_count": r["count"],
                    "avg_time_ms": round(r["avg_time"], 2) if r["avg_time"] else 0
                })

        return {
            "success": True,
            "stats": {
                "total_queries": total,
                "avg_query_time_ms": avg,
                "max_query_time_ms": mx,
                "cache": _cache_service.get_stats(),
                "by_table": table_stats
            }
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# ============================================================
# 细粒度性能指标服务（Observability Performance Tab）
# ============================================================

def _ensure_performance_metrics_table():
    """确保 performance_metrics 表存在"""
    db = get_db_client()
    db.execute('''
        CREATE TABLE IF NOT EXISTS performance_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            metric_type TEXT(32) NOT NULL,
            endpoint TEXT,
            value REAL,
            metadata TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS idx_perf_metrics_user_type_time
        ON performance_metrics(user_id, metric_type, created_at)
    ''')
    db.execute('''
        CREATE INDEX IF NOT EXISTS idx_perf_metrics_user_time
        ON performance_metrics(user_id, created_at)
    ''')


def _calc_percentile(values: List[float], percentile: float) -> float:
    """计算百分位值"""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * percentile / 100)))
    return sorted_vals[idx]


def _since_time(hours: int) -> str:
    """生成 N 小时前的时间戳（ISO 格式）"""
    return (datetime.now() - timedelta(hours=hours)).isoformat()


class PerformanceService:
    """性能指标收集与查询服务"""

    async def record_api_latency(
        self,
        user_id: int,
        endpoint: str,
        method: str,
        latency_ms: float,
        status_code: int,
    ) -> Dict[str, Any]:
        """记录API延迟"""
        try:
            _ensure_performance_metrics_table()
            db = get_db_client()
            metadata = json.dumps({
                "method": method,
                "status_code": status_code,
            }, ensure_ascii=False)
            db.execute(
                '''INSERT INTO performance_metrics
                   (user_id, metric_type, endpoint, value, metadata)
                   VALUES (?, ?, ?, ?, ?)''',
                (user_id, "api_latency", endpoint, float(latency_ms), metadata)
            )
            return {"success": True}
        except Exception as e:
            logger.debug(f"记录API延迟失败: {e}")
            return {"success": False, "error": str(e)}

    async def record_llm_call(
        self,
        user_id: int,
        model: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: float,
        cost: float,
    ) -> Dict[str, Any]:
        """记录LLM调用"""
        try:
            _ensure_performance_metrics_table()
            db = get_db_client()
            metadata = json.dumps({
                "model": model,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost": float(cost),
            }, ensure_ascii=False)
            db.execute(
                '''INSERT INTO performance_metrics
                   (user_id, metric_type, endpoint, value, metadata)
                   VALUES (?, ?, ?, ?, ?)''',
                (user_id, "llm_call", model, float(latency_ms), metadata)
            )
            return {"success": True}
        except Exception as e:
            logger.debug(f"记录LLM调用失败: {e}")
            return {"success": False, "error": str(e)}

    async def record_cache_access(
        self,
        user_id: int,
        cache_type: str,
        hit: bool,
    ) -> Dict[str, Any]:
        """记录缓存访问"""
        try:
            _ensure_performance_metrics_table()
            db = get_db_client()
            metadata = json.dumps({
                "cache_type": cache_type,
                "hit": bool(hit),
            }, ensure_ascii=False)
            # value=1 表示命中，0 表示未命中，便于聚合
            db.execute(
                '''INSERT INTO performance_metrics
                   (user_id, metric_type, endpoint, value, metadata)
                   VALUES (?, ?, ?, ?, ?)''',
                (user_id, "cache_access", cache_type, 1.0 if hit else 0.0, metadata)
            )
            return {"success": True}
        except Exception as e:
            logger.debug(f"记录缓存访问失败: {e}")
            return {"success": False, "error": str(e)}

    async def record_error(
        self,
        user_id: int,
        endpoint: str,
        error_message: str,
        status_code: int = 0,
        retried: bool = False,
        retry_success: bool = False,
    ) -> Dict[str, Any]:
        """记录错误事件（埋点）"""
        try:
            _ensure_performance_metrics_table()
            db = get_db_client()
            metadata = json.dumps({
                "error_message": error_message,
                "status_code": status_code,
                "retried": retried,
                "retry_success": retry_success,
            }, ensure_ascii=False)
            db.execute(
                '''INSERT INTO performance_metrics
                   (user_id, metric_type, endpoint, value, metadata)
                   VALUES (?, ?, ?, ?, ?)''',
                (user_id, "error", endpoint, float(status_code), metadata)
            )
            return {"success": True}
        except Exception as e:
            logger.debug(f"记录错误事件失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_api_latency_stats(
        self,
        user_id: int,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """获取API延迟统计 (p50/p95/p99)"""
        try:
            _ensure_performance_metrics_table()
            db = get_db_client()
            since = _since_time(hours)
            rows = db.execute(
                '''SELECT endpoint, value FROM performance_metrics
                   WHERE user_id = ? AND metric_type = ? AND created_at >= ?''',
                (user_id, "api_latency", since)
            )
            grouped: Dict[str, List[float]] = {}
            if rows:
                for r in rows:
                    ep = r["endpoint"] or "unknown"
                    grouped.setdefault(ep, []).append(float(r["value"] or 0))

            endpoints = []
            for ep, vals in grouped.items():
                endpoints.append({
                    "endpoint": ep,
                    "count": len(vals),
                    "p50": round(_calc_percentile(vals, 50), 2),
                    "p95": round(_calc_percentile(vals, 95), 2),
                    "p99": round(_calc_percentile(vals, 99), 2),
                    "avg": round(sum(vals) / len(vals), 2),
                    "max": round(max(vals), 2),
                })
            endpoints.sort(key=lambda x: x["p99"], reverse=True)

            return {
                "success": True,
                "hours": hours,
                "endpoints": endpoints,
                "count": sum(e["count"] for e in endpoints),
            }
        except Exception as e:
            logger.error(f"获取API延迟统计失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_llm_cost_stats(
        self,
        user_id: int,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """获取LLM成本统计"""
        try:
            _ensure_performance_metrics_table()
            db = get_db_client()
            since = _since_time(hours)
            rows = db.execute(
                '''SELECT endpoint, value, metadata FROM performance_metrics
                   WHERE user_id = ? AND metric_type = ? AND created_at >= ?''',
                (user_id, "llm_call", since)
            )
            grouped: Dict[str, List[Dict[str, Any]]] = {}
            if rows:
                for r in rows:
                    model = r["endpoint"] or "unknown"
                    grouped.setdefault(model, []).append({
                        "latency_ms": float(r["value"] or 0),
                        "metadata": r["metadata"],
                    })

            models = []
            total_calls = 0
            total_tokens = 0
            total_cost = 0.0
            for model, items in grouped.items():
                model_tokens = 0
                model_cost = 0.0
                latencies = []
                for item in items:
                    meta = {}
                    if item["metadata"]:
                        try:
                            meta = json.loads(item["metadata"])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    model_tokens += int(meta.get("tokens_in", 0) + meta.get("tokens_out", 0))
                    model_cost += float(meta.get("cost", 0))
                    latencies.append(item["latency_ms"])
                total_calls += len(items)
                total_tokens += model_tokens
                total_cost += model_cost
                avg_latency = round(sum(latencies) / len(latencies), 2) if latencies else 0
                models.append({
                    "model": model,
                    "calls": len(items),
                    "total_tokens": model_tokens,
                    "avg_latency_ms": avg_latency,
                    "total_cost": round(model_cost, 4),
                })
            models.sort(key=lambda x: x["total_cost"], reverse=True)

            return {
                "success": True,
                "hours": hours,
                "total_calls": total_calls,
                "total_tokens": total_tokens,
                "total_cost": round(total_cost, 4),
                "models": models,
            }
        except Exception as e:
            logger.error(f"获取LLM成本统计失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_cache_hit_rate(
        self,
        user_id: int,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """获取缓存命中率"""
        try:
            _ensure_performance_metrics_table()
            db = get_db_client()
            since = _since_time(hours)
            rows = db.execute(
                '''SELECT endpoint, value FROM performance_metrics
                   WHERE user_id = ? AND metric_type = ? AND created_at >= ?''',
                (user_id, "cache_access", since)
            )
            grouped: Dict[str, List[int]] = {}
            if rows:
                for r in rows:
                    ct = r["endpoint"] or "unknown"
                    grouped.setdefault(ct, []).append(1 if (r["value"] or 0) >= 1.0 else 0)

            caches = []
            total_hits = 0
            total_accesses = 0
            for cache_type, vals in grouped.items():
                hits = sum(vals)
                total = len(vals)
                total_hits += hits
                total_accesses += total
                caches.append({
                    "cache_type": cache_type,
                    "hits": hits,
                    "misses": total - hits,
                    "total": total,
                    "hit_rate": round(hits / total, 4) if total > 0 else 0,
                })
            caches.sort(key=lambda x: x["total"], reverse=True)

            overall_rate = round(total_hits / total_accesses, 4) if total_accesses > 0 else 0
            return {
                "success": True,
                "hours": hours,
                "overall_hit_rate": overall_rate,
                "total_hits": total_hits,
                "total_misses": total_accesses - total_hits,
                "caches": caches,
            }
        except Exception as e:
            logger.error(f"获取缓存命中率失败: {e}")
            return {"success": False, "error": str(e)}

    async def get_error_rate(
        self,
        user_id: int,
        hours: int = 24,
    ) -> Dict[str, Any]:
        """获取错误率统计"""
        try:
            _ensure_performance_metrics_table()
            db = get_db_client()
            since = _since_time(hours)

            # 错误数
            error_rows = db.execute(
                '''SELECT id, endpoint, value, metadata, created_at FROM performance_metrics
                   WHERE user_id = ? AND metric_type = ? AND created_at >= ?
                   ORDER BY created_at DESC''',
                (user_id, "error", since)
            )
            errors = []
            retried_count = 0
            retry_success_count = 0
            if error_rows:
                for r in error_rows:
                    meta = {}
                    if r["metadata"]:
                        try:
                            meta = json.loads(r["metadata"])
                        except (json.JSONDecodeError, TypeError):
                            pass
                    if meta.get("retried"):
                        retried_count += 1
                    if meta.get("retry_success"):
                        retry_success_count += 1
                    errors.append({
                        "id": r["id"],
                        "endpoint": r["endpoint"],
                        "status_code": int(r["value"] or 0),
                        "error_message": meta.get("error_message", ""),
                        "created_at": r["created_at"],
                    })

            # 总请求数：以 api_latency 记录数估算
            total_rows = db.execute(
                '''SELECT COUNT(*) as cnt FROM performance_metrics
                   WHERE user_id = ? AND metric_type = ? AND created_at >= ?''',
                (user_id, "api_latency", since)
            )
            total_requests = total_rows[0]["cnt"] if total_rows else 0
            error_count = len(errors)
            error_rate = round(error_count / max(1, total_requests + error_count), 4)
            retry_success_rate = round(retry_success_count / max(1, retried_count), 4) if retried_count > 0 else 0

            return {
                "success": True,
                "hours": hours,
                "total_errors": error_count,
                "total_requests": total_requests,
                "error_rate": error_rate,
                "retried_count": retried_count,
                "retry_success_count": retry_success_count,
                "retry_success_rate": retry_success_rate,
                "recent_errors": errors[:20],
            }
        except Exception as e:
            logger.error(f"获取错误率统计失败: {e}")
            return {"success": False, "error": str(e)}


# 全局实例
_performance_service = PerformanceService()


def get_performance_service() -> PerformanceService:
    """获取 PerformanceService 单例"""
    return _performance_service


# ============================================================
# 测试
# ============================================================

def test_performance_service():
    """测试性能优化服务"""
    print("\n" + "="*60)
    print("测试性能优化服务")
    print("="*60 + "\n")

    user_id = 999

    # 清理
    db = get_db_client()
    _ensure_perf_tables()
    db.execute('DELETE FROM query_logs WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM index_suggestions')

    print("1. 测试缓存服务...")
    cache = get_cache()
    cache.set("test", "key1", {"data": "hello"}, ttl=60)
    value = cache.get("test", "key1")
    print(f"   缓存值: {value}")
    assert value == {"data": "hello"}
    print(f"   ✓ 缓存读写成功\n")

    print("2. 测试缓存未命中...")
    value = cache.get("test", "nonexistent")
    print(f"   结果: {value}")
    assert value is None
    print(f"   ✓ 缓存未命中正确\n")

    print("3. 测试缓存统计...")
    stats = cache.get_stats()
    print(f"   统计: {stats}")
    assert stats["hits"] > 0
    assert stats["misses"] > 0
    print(f"   ✓ 统计获取成功\n")

    print("4. 测试缓存装饰器...")

    @cached("test_func", ttl=30)
    def expensive_function(x):
        time.sleep(0.01)
        return x * 2

    # 第一次调用（未缓存）
    start = time.time()
    result1 = expensive_function(5)
    time1 = time.time() - start

    # 第二次调用（已缓存）
    start = time.time()
    result2 = expensive_function(5)
    time2 = time.time() - start

    print(f"   第一次: result={result1}, time={time1*1000:.1f}ms")
    print(f"   第二次: result={result2}, time={time2*1000:.1f}ms")
    assert result1 == result2 == 10
    assert time2 < time1  # 缓存应更快
    print(f"   ✓ 缓存装饰器成功\n")

    print("5. 测试LLM响应缓存...")
    prompt_hash = compute_prompt_hash([{"role": "user", "content": "hello"}])
    cache_llm_response(user_id, prompt_hash, {"response": "Hi there!"}, ttl=60)
    cached_response = get_cached_llm_response(user_id, prompt_hash)
    print(f"   缓存的LLM响应: {cached_response}")
    assert cached_response == {"response": "Hi there!"}
    print(f"   ✓ LLM缓存成功\n")

    print("6. 测试查询日志...")
    log_query(user_id, "SELECT", "memory_fragments", 15.5, 10)
    log_query(user_id, "SELECT", "memory_fragments", 25.3, 5)
    log_query(user_id, "INSERT", "memory_variables", 5.2, 1)
    stats = get_performance_stats()
    print(f"   总查询数: {stats['stats']['total_queries']}")
    print(f"   平均时间: {stats['stats']['avg_query_time_ms']}ms")
    assert stats["stats"]["total_queries"] >= 3
    print(f"   ✓ 查询日志成功\n")

    print("7. 测试慢查询分析...")
    log_query(user_id, "SELECT", "memory_fragments", 150.0, 100)  # 模拟慢查询
    slow = get_slow_queries(threshold_ms=100)
    print(f"   慢查询数: {slow.get('count', 0)}")
    assert slow["count"] >= 1
    print(f"   ✓ 慢查询分析成功\n")

    print("8. 测试索引分析...")
    result = analyze_indexes()
    print(f"   分析表数: {result.get('table_count', 0)}")
    print(f"   建议数: {len(result.get('suggestions', []))}")
    assert result["success"] == True
    print(f"   ✓ 索引分析成功\n")

    print("9. 测试批量处理...")
    items = list(range(50))
    result = batch_process(items, lambda x: x * 2, batch_size=10)
    print(f"   处理数: {result['processed']}")
    print(f"   错误数: {result['errors']}")
    assert result["processed"] == 50
    assert result["errors"] == 0
    print(f"   ✓ 批量处理成功\n")

    print("10. 测试性能统计...")
    stats = get_performance_stats()
    print(f"   总查询: {stats['stats']['total_queries']}")
    print(f"   缓存命中率: {stats['stats']['cache']['hit_rate']}%")
    print(f"   表统计: {len(stats['stats']['by_table'])} tables")
    assert stats["success"] == True
    print(f"   ✓ 性能统计成功\n")

    # 清理
    db.execute('DELETE FROM query_logs WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM index_suggestions')

    print("="*60)
    print("✅ 性能优化服务测试完成！")
    print("="*60 + "\n")

    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    test_performance_service()
