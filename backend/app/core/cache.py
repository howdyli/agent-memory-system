"""
TTL 缓存装饰器

为统计类接口提供简单的 TTL 缓存，减少重复全表扫描。
优先使用 Redis，不可用时回退到进程内字典。

使用方式：
    from app.core.cache import ttl_cache

    @ttl_cache(ttl=60)
    def get_dashboard_stats(user_id: int):
        ...

    @ttl_cache(ttl=30, key_prefix="lifecycle_stats")
    def get_lifecycle_stats(user_id: int):
        ...

注意：
- 缓存键基于 key_prefix + 函数参数
- 仅缓存成功结果（success=True）
- Redis 不可用时静默回退到内存缓存
"""
import json
import time
import hashlib
import logging
from typing import Optional, Callable, Any, Dict
from functools import wraps

logger = logging.getLogger(__name__)

# 进程内缓存（Redis 不可用时的回退）
_memory_cache: Dict[str, tuple] = {}  # key -> (value, expire_at)


def _make_cache_key(prefix: str, args: tuple, kwargs: dict) -> str:
    """根据函数名和参数生成缓存键"""
    # 只使用位置参数和关键字参数的值来生成键
    key_parts = [prefix]
    for arg in args:
        key_parts.append(str(arg))
    for k, v in sorted(kwargs.items()):
        key_parts.append(f"{k}={v}")
    raw_key = ":".join(key_parts)
    return hashlib.md5(raw_key.encode()).hexdigest()[:32]


def _read_cache(key: str) -> Optional[Any]:
    """读取缓存，优先 Redis，回退内存"""
    # 尝试 Redis
    try:
        from app.core.redis_client import get_redis_client
        redis = get_redis_client()
        if redis:
            stored = redis.get(f"stats_cache:{key}")
            if stored:
                return json.loads(stored)
    except Exception:
        pass

    # 回退到内存
    entry = _memory_cache.get(key)
    if entry:
        value, expire_at = entry
        if time.time() < expire_at:
            return value
        else:
            _memory_cache.pop(key, None)
    return None


def _write_cache(key: str, value: Any, ttl: int) -> None:
    """写入缓存，优先 Redis，回退内存"""
    # 尝试 Redis
    try:
        from app.core.redis_client import get_redis_client
        redis = get_redis_client()
        if redis:
            redis.setex(f"stats_cache:{key}", ttl, json.dumps(value, ensure_ascii=False, default=str))
            return
    except Exception:
        pass

    # 回退到内存
    _memory_cache[key] = (value, time.time() + ttl)


def ttl_cache(ttl: int = 60, key_prefix: str = "") -> Callable:
    """
    TTL 缓存装饰器。

    Args:
        ttl: 缓存存活时间（秒），默认 60 秒
        key_prefix: 缓存键前缀（默认使用函数名）

    Returns:
        装饰器函数
    """
    def decorator(func: Callable) -> Callable:
        prefix = key_prefix or func.__name__

        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = _make_cache_key(prefix, args, kwargs)

            # 尝试读取缓存
            cached = _read_cache(cache_key)
            if cached is not None:
                return cached

            # 执行函数
            result = func(*args, **kwargs)

            # 仅缓存成功结果
            if isinstance(result, dict) and result.get("success") is not False:
                _write_cache(cache_key, result, ttl)

            return result

        # 添加缓存清除方法
        def cache_clear(*args, **kwargs):
            """清除指定参数的缓存"""
            cache_key = _make_cache_key(prefix, args, kwargs)
            _memory_cache.pop(cache_key, None)
            try:
                from app.core.redis_client import get_redis_client
                redis = get_redis_client()
                if redis:
                    redis.delete(f"stats_cache:{cache_key}")
            except Exception:
                pass

        wrapper.cache_clear = cache_clear
        return wrapper

    return decorator
