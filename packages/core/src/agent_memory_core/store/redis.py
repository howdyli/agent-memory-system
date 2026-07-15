"""
Redis cache store adapter.

Implements CacheStore ABC with two variants:
- RedisCacheStore: real Redis connection
- FakeRedisCacheStore: in-memory FakeRedis for development

Adapted from backend/app/core/redis_client.py — singleton removed.
"""

import json
import logging
from typing import Any, Dict, Optional

from .base import CacheStore

logger = logging.getLogger(__name__)


class RedisCacheStore(CacheStore):
    """Real Redis implementation of CacheStore."""

    def __init__(self, redis_url: Optional[str] = None):
        try:
            import redis as real_redis
        except ImportError:
            raise ImportError("redis package not installed. Install with: pip install redis")

        if redis_url and redis_url.startswith("redis://"):
            from urllib.parse import urlparse
            parsed = urlparse(redis_url)
            config = {
                "host": parsed.hostname or "localhost",
                "port": parsed.port or 6379,
                "db": 0,
                "password": parsed.password,
                "decode_responses": True,
                "socket_timeout": 5,
                "socket_connect_timeout": 3,
            }
            if parsed.path and len(parsed.path) > 1:
                try:
                    config["db"] = int(parsed.path.lstrip("/"))
                except ValueError:
                    pass
            self._conn = real_redis.Redis(**config)
        else:
            self._conn = real_redis.Redis(
                host="localhost", port=6379, db=0,
                decode_responses=True, socket_timeout=5, socket_connect_timeout=3,
            )
        try:
            self._conn.ping()
            logger.info("Connected to real Redis")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}, falling back to FakeRedis")
            self._init_fakeredis()

    def _init_fakeredis(self):
        try:
            import fakeredis
            self._conn = fakeredis.FakeStrictRedis(version=(7, 2))
            self._conn.ping()
            logger.info("Using FakeRedis fallback")
        except ImportError:
            raise RuntimeError("Neither redis nor fakeredis available")

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value, ensure_ascii=False)
        else:
            value_str = str(value)
        if ttl:
            return bool(self._conn.setex(key, ttl, value_str))
        return bool(self._conn.set(key, value_str))

    def get(self, key: str) -> Optional[Any]:
        value = self._conn.get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    def delete(self, key: str) -> bool:
        return bool(self._conn.delete(key))

    def exists(self, key: str) -> bool:
        return bool(self._conn.exists(key))

    def expire(self, key: str, ttl: int) -> bool:
        return bool(self._conn.expire(key, ttl))

    def set_hash(self, name: str, mapping: Dict) -> bool:
        serialized = {}
        for k, v in mapping.items():
            if isinstance(v, (dict, list)):
                serialized[k] = json.dumps(v, ensure_ascii=False)
            else:
                serialized[k] = str(v)
        self._conn.hset(name, mapping=serialized)
        return True

    def get_hash(self, name: str) -> Optional[Dict]:
        result = self._conn.hgetall(name)
        if not result:
            return None
        deserialized = {}
        for k, v in result.items():
            try:
                deserialized[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                deserialized[k] = v
        return deserialized

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass


class FakeRedisCacheStore(CacheStore):
    """In-memory FakeRedis implementation of CacheStore for development."""

    def __init__(self):
        try:
            import fakeredis
            self._conn = fakeredis.FakeStrictRedis(version=(7, 2), decode_responses=True)
        except ImportError:
            raise ImportError("fakeredis not installed. Install with: pip install fakeredis")

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        if isinstance(value, (dict, list)):
            value_str = json.dumps(value, ensure_ascii=False)
        else:
            value_str = str(value)
        if ttl:
            return bool(self._conn.setex(key, ttl, value_str))
        return bool(self._conn.set(key, value_str))

    def get(self, key: str) -> Optional[Any]:
        value = self._conn.get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    def delete(self, key: str) -> bool:
        return bool(self._conn.delete(key))

    def exists(self, key: str) -> bool:
        return bool(self._conn.exists(key))

    def expire(self, key: str, ttl: int) -> bool:
        return bool(self._conn.expire(key, ttl))

    def set_hash(self, name: str, mapping: Dict) -> bool:
        serialized = {}
        for k, v in mapping.items():
            if isinstance(v, (dict, list)):
                serialized[k] = json.dumps(v, ensure_ascii=False)
            else:
                serialized[k] = str(v)
        self._conn.hset(name, mapping=serialized)
        return True

    def get_hash(self, name: str) -> Optional[Dict]:
        result = self._conn.hgetall(name)
        if not result:
            return None
        deserialized = {}
        for k, v in result.items():
            try:
                deserialized[k] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                deserialized[k] = v
        return deserialized

    def close(self) -> None:
        pass  # FakeRedis has no persistent connection
