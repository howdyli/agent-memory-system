"""
Milvus 向量数据库连接管理模块
提供 Milvus 连接管理、Collection 创建、向量插入/搜索接口，以及测试函数
"""

import logging
import time
import numpy as np
from typing import Optional, List, Dict, Any

from pymilvus import (
    connections,
    Collection,
    CollectionSchema,
    FieldSchema,
    DataType,
    utility,
    IndexType,
    MilvusException,
)

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_MILVUS_HOST = "localhost"
DEFAULT_MILVUS_PORT = 19530  # gRPC 端口
DEFAULT_MILVUS_ALIAS = "default"


class MilvusClient:
    """Milvus 向量数据库连接管理"""

    def __init__(
        self,
        host: str = DEFAULT_MILVUS_HOST,
        port: int = DEFAULT_MILVUS_PORT,
        alias: str = DEFAULT_MILVUS_ALIAS,
    ):
        self.host = host
        self.port = port
        self.alias = alias
        self._connected = False

    def connect(self) -> None:
        """连接到 Milvus 服务器"""
        try:
            connections.connect(
                alias=self.alias,
                host=self.host,
                port=self.port,
            )
            self._connected = True
            logger.info(f"Milvus 连接成功: {self.host}:{self.port}")
        except MilvusException as e:
            logger.error(f"Milvus 连接失败: {e}")
            raise

    def disconnect(self) -> None:
        """断开 Milvus 连接"""
        try:
            connections.disconnect(alias=self.alias)
            self._connected = False
            logger.info("Milvus 连接已断开")
        except MilvusException as e:
            logger.error(f"Milvus 断开连接失败: {e}")

    def is_connected(self) -> bool:
        """
        检查是否已连接
        
        Returns:
            bool: 连接状态
        """
        try:
            return connections.has_connection(self.alias)
        except Exception:
            return False

    def list_collections(self) -> List[str]:
        """
        列出所有 Collection
        
        Returns:
            List[str]: Collection 名称列表
        """
        self._ensure_connected()
        return utility.list_collections()

    def collection_exists(self, collection_name: str) -> bool:
        """
        检查 Collection 是否存在
        
        Args:
            collection_name: Collection 名称
            
        Returns:
            bool: 是否存在
        """
        self._ensure_connected()
        return utility.has_collection(collection_name)

    def create_memory_collection(
        self,
        collection_name: str = "memories",
        dim: int = 768,
        description: str = "Agent Memory 向量存储",
    ) -> Collection:
        """
        创建记忆向量 Collection
        
        Args:
            collection_name: Collection 名称
            dim: 向量维度（默认 768，适配 BGE-large 等模型）
            description: Collection 描述
            
        Returns:
            Collection: Milvus Collection 对象
        """
        self._ensure_connected()

        if self.collection_exists(collection_name):
            logger.info(f"Collection '{collection_name}' 已存在，直接返回")
            return Collection(name=collection_name, using=self.alias)

        # 定义字段
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="memory_id", dtype=DataType.INT64),  # 关联 PostgreSQL 的 memories.id
            FieldSchema(name="session_id", dtype=DataType.VARCHAR, max_length=255),
            FieldSchema(name="user_id", dtype=DataType.VARCHAR, max_length=255),
            FieldSchema(name="memory_type", dtype=DataType.VARCHAR, max_length=50),
            FieldSchema(name="content", dtype=DataType.VARCHAR, max_length=5000),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
            FieldSchema(name="timestamp", dtype=DataType.INT64),  # 时间戳
            FieldSchema(name="metadata", dtype=DataType.JSON),
        ]

        # 创建 Collection
        schema = CollectionSchema(fields=fields, description=description)
        collection = Collection(
            name=collection_name,
            schema=schema,
            using=self.alias,
        )

        # 创建向量索引（IVF_FLAT，适合中小规模数据）
        index_params = {
            "metric_type": "L2",  # 欧氏距离
            "index_type": "IVF_FLAT",
            "params": {"nlist": 1024},
        }
        collection.create_index(field_name="embedding", index_params=index_params)

        # 创建标量索引（用于过滤）
        collection.create_index(field_name="session_id", index_name="idx_session_id")
        collection.create_index(field_name="user_id", index_name="idx_user_id")
        collection.create_index(field_name="memory_type", index_name="idx_memory_type")

        logger.info(f"Collection '{collection_name}' 创建完成，向量维度: {dim}")
        return collection

    def get_collection(self, collection_name: str) -> Optional[Collection]:
        """
        获取 Collection 对象
        
        Args:
            collection_name: Collection 名称
            
        Returns:
            Optional[Collection]: Collection 对象，不存在则返回 None
        """
        self._ensure_connected()
        if self.collection_exists(collection_name):
            return Collection(name=collection_name, using=self.alias)
        return None

    def insert_vectors(
        self,
        collection_name: str,
        data: Dict[str, List[Any]],
    ) -> List[int]:
        """
        插入向量数据
        
        Args:
            collection_name: Collection 名称
            data: 插入数据，格式为字段名到值列表的映射
                  例如: {
                      "memory_id": [1, 2],
                      "session_id": ["s1", "s2"],
                      "embedding": [[0.1, 0.2, ...], [0.3, 0.4, ...]],
                      ...
                  }
                  
        Returns:
            List[int]: 插入的实体 ID 列表
        """
        self._ensure_connected()
        collection = self.get_collection(collection_name)
        if collection is None:
            raise ValueError(f"Collection '{collection_name}' 不存在")

        # 插入数据
        insert_result = collection.insert(data)
        collection.flush()  # 确保数据写入

        logger.info(f"插入 {len(insert_result.primary_keys)} 条向量数据")
        return list(insert_result.primary_keys)

    def search_vectors(
        self,
        collection_name: str,
        query_vectors: List[List[float]],
        top_k: int = 10,
        search_params: Optional[Dict] = None,
        expr: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
    ) -> List[List[Dict]]:
        """
        搜索向量
        
        Args:
            collection_name: Collection 名称
            query_vectors: 查询向量列表
            top_k: 返回最相似的 k 个结果
            search_params: 搜索参数，默认使用 IVF_FLAT 的参数
            expr: 过滤表达式（Milvus 表达式）
                  例如: "session_id == 'abc'" 或 "timestamp > 1234567890"
            output_fields: 返回的字段列表
            
        Returns:
            List[List[Dict]]: 搜索结果，每个查询向量对应一个结果列表
        """
        self._ensure_connected()
        collection = self.get_collection(collection_name)
        if collection is None:
            raise ValueError(f"Collection '{collection_name}' 不存在")

        # 加载 Collection 到内存（搜索前必须）
        collection.load()

        # 默认搜索参数
        if search_params is None:
            search_params = {"metric_type": "L2", "params": {"nprobe": 10}}

        if output_fields is None:
            output_fields = ["memory_id", "session_id", "content", "timestamp"]

        # 执行搜索
        results = collection.search(
            data=query_vectors,
            anns_field="embedding",
            param=search_params,
            limit=top_k,
            expr=expr,
            output_fields=output_fields,
        )

        # 格式化结果
        formatted_results = []
        for hits in results:
            hit_list = []
            for hit in hits:
                hit_dict = {
                    "id": hit.id,
                    "distance": hit.distance,
                    "entity": {field: hit.entity.get(field) for field in output_fields},
                }
                hit_list.append(hit_dict)
            formatted_results.append(hit_list)

        logger.info(f"向量搜索完成: {len(query_vectors)} 个查询向量，找到结果")
        return formatted_results

    def delete_entities(self, collection_name: str, expr: str) -> None:
        """
        删除实体
        
        Args:
            collection_name: Collection 名称
            expr: 删除表达式
                  例如: "memory_id in [1, 2, 3]" 或 "session_id == 'abc'"
        """
        self._ensure_connected()
        collection = self.get_collection(collection_name)
        if collection is None:
            raise ValueError(f"Collection '{collection_name}' 不存在")

        collection.delete(expr)
        logger.info(f"删除实体: {expr}")

    def drop_collection(self, collection_name: str) -> None:
        """
        删除 Collection
        
        Args:
            collection_name: Collection 名称
        """
        self._ensure_connected()
        utility.drop_collection(collection_name)
        logger.info(f"Collection '{collection_name}' 已删除")

    def get_collection_stats(self, collection_name: str) -> Dict:
        """
        获取 Collection 统计信息
        
        Args:
            collection_name: Collection 名称
            
        Returns:
            Dict: 统计信息
        """
        self._ensure_connected()
        collection = self.get_collection(collection_name)
        if collection is None:
            return {"error": f"Collection '{collection_name}' 不存在"}

        stats = {
            "name": collection.name,
            "num_entities": collection.num_entities,
            "schema": str(collection.schema),
            "indexes": [idx.params for idx in collection.indexes],
        }
        return stats

    def _ensure_connected(self) -> None:
        """确保已连接到 Milvus"""
        if not self.is_connected():
            self.connect()

    def health_check(self) -> dict:
        """
        健康检查
        
        Returns:
            dict: 健康状态信息
        """
        result = {
            "status": "unhealthy",
            "host": self.host,
            "port": self.port,
            "latency_ms": None,
            "error": None,
        }
        try:
            start = time.time()
            self._ensure_connected()
            # 尝试列出 collections 来验证连接
            self.list_collections()
            latency = (time.time() - start) * 1000
            result["status"] = "healthy"
            result["latency_ms"] = round(latency, 2)
        except Exception as e:
            result["error"] = str(e)
        return result


# 全局单例实例
_milvus_client: Optional[MilvusClient] = None


def get_milvus_client(
    host: str = DEFAULT_MILVUS_HOST,
    port: int = DEFAULT_MILVUS_PORT,
    alias: str = DEFAULT_MILVUS_ALIAS,
) -> MilvusClient:
    """
    获取全局 Milvus 客户端单例
    
    Returns:
        MilvusClient: Milvus 客户端实例
    """
    global _milvus_client
    if _milvus_client is None:
        _milvus_client = MilvusClient(host=host, port=port, alias=alias)
    return _milvus_client


def close_milvus_client() -> None:
    """关闭全局 Milvus 客户端"""
    global _milvus_client
    if _milvus_client:
        _milvus_client.disconnect()
        _milvus_client = None


def test_milvus_connection() -> dict:
    """
    测试 Milvus 连接和功能
    
    测试内容：
    1. 连接测试
    2. 创建测试 Collection
    3. 插入向量
    4. 搜索向量
    5. 删除测试数据
    
    Returns:
        dict: 测试结果
    """
    milvus_client = get_milvus_client()
    results = {
        "connection": False,
        "create_collection": False,
        "insert": False,
        "search": False,
        "delete": False,
        "errors": [],
    }

    test_collection = "test_memories"
    vector_dim = 128  # 使用小维度便于测试

    # 1. 连接测试
    try:
        health = milvus_client.health_check()
        results["connection"] = health["status"] == "healthy"
        print(f"✅ Milvus 连接: {health}")
    except Exception as e:
        results["errors"].append(f"连接失败: {e}")
        print(f"❌ Milvus 连接失败: {e}")
        return results

    # 2. 创建测试 Collection
    try:
        # 先删除已存在的测试 Collection
        if milvus_client.collection_exists(test_collection):
            milvus_client.drop_collection(test_collection)
            print(f"   已删除旧的测试 Collection: {test_collection}")

        collection = milvus_client.create_memory_collection(
            collection_name=test_collection,
            dim=vector_dim,
        )
        results["create_collection"] = True
        print(f"✅ Milvus 创建 Collection: {test_collection}, 维度: {vector_dim}")
    except Exception as e:
        results["errors"].append(f"创建 Collection 失败: {e}")
        print(f"❌ Milvus 创建 Collection 失败: {e}")

    # 3. 插入向量
    try:
        # 生成随机测试向量
        num_vectors = 10
        np.random.seed(42)
        embeddings = np.random.rand(num_vectors, vector_dim).tolist()

        data = {
            "memory_id": list(range(1, num_vectors + 1)),
            "session_id": ["test_session"] * num_vectors,
            "user_id": ["test_user"] * num_vectors,
            "memory_type": ["episodic"] * num_vectors,
            "content": [f"测试记忆内容 {i}" for i in range(num_vectors)],
            "embedding": embeddings,
            "timestamp": [int(time.time())] * num_vectors,
            "metadata": [{"source": "test"}] * num_vectors,
        }

        ids = milvus_client.insert_vectors(test_collection, data)
        results["insert"] = len(ids) == num_vectors
        print(f"✅ Milvus 插入向量: {len(ids)} 条")
    except Exception as e:
        results["errors"].append(f"插入向量失败: {e}")
        print(f"❌ Milvus 插入向量失败: {e}")

    # 4. 搜索向量
    try:
        # 使用第一条向量作为查询向量
        query_vector = embeddings[0:1]
        search_results = milvus_client.search_vectors(
            collection_name=test_collection,
            query_vectors=query_vector,
            top_k=5,
            expr="session_id == 'test_session'",
        )

        results["search"] = len(search_results) > 0 and len(search_results[0]) > 0
        print(f"✅ Milvus 搜索向量: 找到 {len(search_results[0])} 个结果")
        for hit in search_results[0][:3]:  # 显示前 3 个结果
            print(f"   ID: {hit['id']}, 距离: {hit['distance']:.4f}, "
                  f"内容: {hit['entity'].get('content', '')}")
    except Exception as e:
        results["errors"].append(f"搜索向量失败: {e}")
        print(f"❌ Milvus 搜索向量失败: {e}")

    # 5. 删除测试数据
    try:
        milvus_client.delete_entities(
            collection_name=test_collection,
            expr="session_id == 'test_session'",
        )
        results["delete"] = True
        print(f"✅ Milvus 删除测试数据")

        # 清理测试 Collection
        milvus_client.drop_collection(test_collection)
        print(f"✅ Milvus 删除测试 Collection: {test_collection}")
    except Exception as e:
        results["errors"].append(f"删除数据失败: {e}")
        print(f"❌ Milvus 删除数据失败: {e}")

    return results


if __name__ == "__main__":
    # 直接运行此文件进行测试
    print("=" * 60)
    print("Milvus 连接测试")
    print("=" * 60)
    results = test_milvus_connection()
    print("=" * 60)
    print(f"测试结果: {results}")
    print("=" * 60)

    # 关闭连接
    close_milvus_client()
