"""
Redis 客户端模块 - 支持真实 Redis 和 fakeredis

连接优先级：
1. REDIS_URL 环境变量（如 redis://:password@localhost:6379/0）
2. 优先尝试连接真实 Redis（默认 localhost:6379）
3. 连接失败则降级到 fakeredis（内存模拟）
"""
import logging
import os
from typing import Optional, Any
import json
from urllib.parse import urlparse

try:
    import fakeredis
    FAKEREDIS_AVAILABLE = True
except ImportError:
    FAKEREDIS_AVAILABLE = False

try:
    import redis as real_redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis 客户端封装（支持真实 Redis 和 fakeredis）"""
    
    _instance = None
    _connection = None
    _is_fake = False
    
    def __new__(cls):
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if self._connection is None:
            self._initialize_connection()
    
    def _parse_redis_url(self, url: str) -> dict:
        """解析 REDIS_URL 为连接参数字典"""
        parsed = urlparse(url)
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
        return config

    def _connect_real_redis(self, config: dict) -> bool:
        """尝试连接真实 Redis"""
        if not REDIS_AVAILABLE:
            return False
        try:
            conn = real_redis.Redis(**config)
            conn.ping()
            self._connection = conn
            self._is_fake = False
            safe_info = {k: v for k, v in config.items() if k != "password"}
            logger.info(f"✓ 连接到真实 Redis: {safe_info}")
            return True
        except Exception as e:
            logger.warning(f"✗ 真实 Redis 连接失败: {e}")
            return False

    def _connect_fakeredis(self):
        """降级到 fakeredis（内存模拟）"""
        if not FAKEREDIS_AVAILABLE:
            logger.error("✗ fakeredis 未安装，无法使用 Redis")
            raise RuntimeError("No Redis backend available (real_redis and fakeredis are both unavailable)")
        self._connection = fakeredis.FakeStrictRedis(version=(7, 2))
        self._is_fake = True
        self._connection.ping()
        logger.info("✓ 使用 FakeRedis（内存模拟模式）")

    def _initialize_connection(self):
        """初始化 Redis 连接"""
        redis_url = os.environ.get("REDIS_URL", "").strip()

        if redis_url and redis_url.startswith("redis://"):
            config = self._parse_redis_url(redis_url)
            if not self._connect_real_redis(config):
                logger.warning("REDIS_URL 配置了真实 Redis 但连接失败，降级到 fakeredis")
                self._connect_fakeredis()
        elif redis_url and redis_url.startswith("fakeredis://"):
            self._connect_fakeredis()
        else:
            # 默认：先尝试连接本地 Redis，失败则降级
            default_config = {
                "host": "localhost",
                "port": 6379,
                "db": 0,
                "decode_responses": True,
                "socket_timeout": 5,
                "socket_connect_timeout": 3,
            }
            if self._connect_real_redis(default_config):
                return
            self._connect_fakeredis()
    
    def get_connection(self):
        """获取 Redis 连接"""
        return self._connection
    
    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        设置键值
        
        Args:
            key: 键名
            value: 值（会自动 JSON 序列化）
            ttl: 过期时间（秒）
        
        Returns:
            bool: 是否成功
        """
        try:
            # 序列化值
            if isinstance(value, (dict, list)):
                value_str = json.dumps(value, ensure_ascii=False)
            else:
                value_str = str(value)
            
            # 设置键值
            if ttl:
                result = self._connection.setex(key, ttl, value_str)
            else:
                result = self._connection.set(key, value_str)
            
            return bool(result)
        except Exception as e:
            logger.error(f"✗ Redis SET 失败 ({key}): {e}")
            return False
    
    def get(self, key: str) -> Optional[Any]:
        """
        获取键值
        
        Args:
            key: 键名
        
        Returns:
            解码后的值，不存在则返回 None
        """
        try:
            value = self._connection.get(key)
            if value is None:
                return None
            
            # 尝试 JSON 反序列化
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return value
        except Exception as e:
            logger.error(f"✗ Redis GET 失败 ({key}): {e}")
            return None
    
    def delete(self, key: str) -> bool:
        """删除键"""
        try:
            result = self._connection.delete(key)
            return bool(result)
        except Exception as e:
            logger.error(f"✗ Redis DELETE 失败 ({key}): {e}")
            return False
    
    def expire(self, key: str, ttl: int) -> bool:
        """
        设置键的过期时间

        Args:
            key: 键名
            ttl: 过期时间（秒）

        Returns:
            bool: 是否成功
        """
        try:
            result = self._connection.expire(key, ttl)
            return bool(result)
        except Exception as e:
            logger.error(f"✗ Redis EXPIRE 失败 ({key}): {e}")
            return False

    def persist(self, key: str) -> bool:
        """
        移除键的过期时间（持久化）

        Args:
            key: 键名

        Returns:
            bool: 是否成功
        """
        try:
            result = self._connection.persist(key)
            return bool(result)
        except Exception as e:
            logger.error(f"✗ Redis PERSIST 失败 ({key}): {e}")
            return False

    def ttl(self, key: str) -> int:
        """
        获取键的剩余过期时间（秒）

        Returns:
            剩余秒数，-1 表示无过期，-2 表示键不存在
        """
        try:
            return self._connection.ttl(key)
        except Exception as e:
            logger.error(f"✗ Redis TTL 失败 ({key}): {e}")
            return -2

    def exists(self, key: str) -> bool:
        """检查键是否存在"""
        try:
            return bool(self._connection.exists(key))
        except Exception as e:
            logger.error(f"✗ Redis EXISTS 失败 ({key}): {e}")
            return False
    
    def set_hash(self, name: str, mapping: dict) -> bool:
        """设置哈希表"""
        try:
            # 序列化字典值
            serialized = {}
            for k, v in mapping.items():
                if isinstance(v, (dict, list)):
                    serialized[k] = json.dumps(v, ensure_ascii=False)
                else:
                    serialized[k] = str(v)
            
            result = self._connection.hset(name, mapping=serialized)
            return True
        except Exception as e:
            logger.error(f"✗ Redis HSET 失败 ({name}): {e}")
            return False
    
    def get_hash(self, name: str) -> Optional[dict]:
        """获取哈希表"""
        try:
            result = self._connection.hgetall(name)
            if not result:
                return None
            
            # 尝试反序列化值
            deserialized = {}
            for k, v in result.items():
                try:
                    deserialized[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    deserialized[k] = v
            
            return deserialized
        except Exception as e:
            logger.error(f"✗ Redis HGETALL 失败 ({name}): {e}")
            return None
    
    def close(self):
        """关闭连接"""
        if self._connection and not self._is_fake:
            self._connection.close()
            logger.info("✓ Redis 连接已关闭")


# 全局客户端实例
redis_client = RedisClient()


def get_redis_client() -> RedisClient:
    """获取 Redis 客户端实例"""
    return redis_client


def test_redis_connection():
    """测试 Redis 连接和基本操作"""
    print("\n" + "="*60)
    print("测试 Redis 连接和 basic 操作")
    print("="*60 + "\n")
    
    client = get_redis_client()
    
    # 测试 SET/GET
    print("1. 测试 SET/GET 操作...")
    client.set("test:string", "Hello, Agent Memory!")
    value = client.get("test:string")
    print(f"   ✓ 存储字符串: {value}")
    
    # 测试 JSON 序列化
    print("\n2. 测试 JSON 序列化...")
    test_data = {"user": "鑫海", "role": "PM", "projects": ["源启·智能体工厂", "Agent星图"]}
    client.set("test:json", test_data)
    retrieved = client.get("test:json")
    print(f"   ✓ 存储对象: {retrieved}")
    
    # 测试 TTL
    print("\n3. 测试 TTL（过期时间）...")
    client.set("test:ttl", "过期数据", ttl=10)
    ttl = client._connection.ttl("test:ttl")
    print(f"   ✓ 设置 TTL: {ttl} 秒")
    
    # 测试哈希表
    print("\n4. 测试哈希表操作...")
    client.set_hash("test:hash", {"name": "鑫海", "preference": "极简设计"})
    hash_data = client.get_hash("test:hash")
    print(f"   ✓ 存储哈希表: {hash_data}")
    
    # 测试 EXISTS
    print("\n5. 测试 EXISTS 操作...")
    exists = client.exists("test:string")
    print(f"   ✓ 键存在: {exists}")
    
    # 测试 DELETE
    print("\n6. 测试 DELETE 操作...")
    client.delete("test:string")
    exists = client.exists("test:string")
    print(f"   ✓ 删除后键存在: {exists}")
    
    # 性能测试
    print("\n7. 性能测试（1000次读写）...")
    import time
    start = time.time()
    for i in range(1000):
        client.set(f"perf:test:{i}", f"value_{i}")
        client.get(f"perf:test:{i}")
    elapsed = time.time() - start
    print(f"   ✓ 1000次读写耗时: {elapsed:.3f}秒")
    print(f"   ✓ 平均延迟: {elapsed/1000*1000:.2f}毫秒")
    
    print("\n" + "="*60)
    print("✅ Redis 测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    test_redis_connection()
