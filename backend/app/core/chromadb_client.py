"""
ChromaDB 客户端模块

提供向量数据库的连接和基础的向量存储/检索接口
用于记忆片段的语义化存储和相似性检索
"""
import logging
import uuid
import os
from typing import Optional, List, Dict, Any
from datetime import datetime

# 修复 protobuf 兼容性问题
os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'

logger = logging.getLogger(__name__)

# 全局变量
_chromadb_client = None
_chromadb_collection = None

class ChromaDBClient:
    """ChromaDB 客户端封装（进程内向量数据库，无需 Docker）"""
    
    def __init__(self, 
                 collection_name: str = "memory_fragments",
                 persist_directory: str = "./chromadb_data"):
        """
        初始化 ChromaDB 客户端
        
        Args:
            collection_name: 集合名称
            persist_directory: 持久化目录
        """
        self.collection_name = collection_name
        self.persist_directory = persist_directory
        self.client = None
        self.collection = None
        self._initialize_client()
    
    def _initialize_client(self):
        """初始化 ChromaDB 客户端和集合"""
        try:
            import chromadb
            from chromadb.config import Settings
            
            # 创建持久化客户端（本地存储）
            self.client = chromadb.PersistentClient(
                path=self.persist_directory
            )
            
            # 检查集合是否存在
            try:
                # 尝试获取集合
                self.collection = self.client.get_collection(
                    name=self.collection_name
                )
                logger.info(f"✓ 连接到现有集合: {self.collection_name}")
            except Exception:
                # 集合不存在，创建新集合
                self.collection = self.client.create_collection(
                    name=self.collection_name,
                    metadata={"hnsw:space": "cosine"}  # 使用余弦相似度
                )
                logger.info(f"✓ 创建新集合: {self.collection_name}")
            
            logger.info(f"✓ ChromaDB 初始化完成，持久化目录: {self.persist_directory}")
            
        except ImportError:
            logger.error("✗ ChromaDB 未安装，请运行: poetry add chromadb")
            raise
        except BaseException as e:
            logger.error(f"✗ ChromaDB 初始化失败: {e}")
            # 如果数据损坏，尝试清空数据目录重新初始化
            self.client = None
            self.collection = None
            raise
    
    def add_embedding(self, 
                     text: str, 
                     metadata: Optional[Dict[str, Any]] = None,
                     embedding: Optional[List[float]] = None) -> str:
        """
        添加文本及其向量嵌入
        
        Args:
            text: 文本内容
            metadata: 元数据（如 user_id, fragment_type, created_at 等）
            embedding: 预计算的向量嵌入（如果为 None，则使用默认嵌入函数）
        
        Returns:
            文档 ID
        """
        try:
            # 生成唯一 ID
            doc_id = str(uuid.uuid4())
            
            # 准备元数据
            if metadata is None:
                metadata = {}
            metadata["text"] = text[:500]  # 存储文本前 500 字符到元数据
            metadata["created_at"] = datetime.now().isoformat()
            
            # 添加到集合
            if embedding:
                self.collection.add(
                    embeddings=[embedding],
                    documents=[text],
                    metadatas=[metadata],
                    ids=[doc_id]
                )
            else:
                # 使用 ChromaDB 的默认嵌入函数（Sentence Transformers）
                self.collection.add(
                    documents=[text],
                    metadatas=[metadata],
                    ids=[doc_id]
                )
            
            logger.debug(f"✓ 添加向量嵌入: {doc_id}")
            return doc_id
            
        except Exception as e:
            logger.error(f"✗ 添加向量嵌入失败: {e}")
            raise
    
    def search_embeddings(self, 
                        query_text: str, 
                        n_results: int = 5,
                        where: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        搜索相似的向量嵌入
        
        Args:
            query_text: 查询文本
            n_results: 返回结果数量
            where: 可选的过滤条件（如 {"user_id": "123"}）
        
        Returns:
            相似结果列表，每个结果包含 id, document, metadata, distance
        """
        try:
            # 执行相似性搜索
            if where:
                results = self.collection.query(
                    query_texts=[query_text],
                    n_results=n_results,
                    where=where
                )
            else:
                results = self.collection.query(
                    query_texts=[query_text],
                    n_results=n_results
                )
            
            # 格式化结果
            formatted_results = []
            if results and results.get('ids'):
                for i in range(len(results['ids'][0])):
                    formatted_results.append({
                        'id': results['ids'][0][i],
                        'document': results['documents'][0][i],
                        'metadata': results['metadatas'][0][i],
                        'distance': results['distances'][0][i] if 'distances' in results else None,
                        'similarity': 1 - results['distances'][0][i] if 'distances' in results else None
                    })
            
            logger.debug(f"✓ 搜索到 {len(formatted_results)} 个相似结果")
            return formatted_results
            
        except Exception as e:
            logger.error(f"✗ 搜索向量嵌入失败: {e}")
            return []
    
    def get_by_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        """根据 ID 获取文档"""
        try:
            result = self.collection.get(
                ids=[doc_id],
                include=['documents', 'metadatas', 'embeddings']
            )
            
            # 检查结果是否有效
            if result and result.get('ids') is not None and len(result['ids']) > 0:
                embeddings = result.get('embeddings')
                embedding = None
                if embeddings is not None and len(embeddings) > 0:
                    embedding = embeddings[0]
                
                return {
                    'id': result['ids'][0],
                    'document': result['documents'][0] if result.get('documents') else None,
                    'metadata': result['metadatas'][0] if result.get('metadatas') else {},
                    'embedding': embedding
                }
            return None
            
        except Exception as e:
            logger.error(f"✗ 获取文档失败: {e}")
            return None
    
    def delete_by_id(self, doc_id: str) -> bool:
        """根据 ID 删除文档"""
        try:
            self.collection.delete(ids=[doc_id])
            logger.debug(f"✓ 删除文档: {doc_id}")
            return True
        except Exception as e:
            logger.error(f"✗ 删除文档失败: {e}")
            return False
    
    def update_by_id(self, 
                    doc_id: str, 
                    text: Optional[str] = None,
                    metadata: Optional[Dict[str, Any]] = None,
                    embedding: Optional[List[float]] = None) -> bool:
        """根据 ID 更新文档"""
        try:
            # ChromaDB 的 update 需要重新添加
            # 先获取现有数据
            existing = self.get_by_id(doc_id)
            if not existing:
                logger.error(f"✗ 文档不存在: {doc_id}")
                return False
            
            # 合并更新
            update_metadata = existing['metadata'].copy()
            if metadata:
                update_metadata.update(metadata)
            if text:
                update_metadata["text"] = text[:500]
            
            # 删除旧文档
            self.delete_by_id(doc_id)
            
            # 添加新文档
            if embedding:
                self.collection.add(
                    embeddings=[embedding],
                    documents=[text or existing['document']],
                    metadatas=[update_metadata],
                    ids=[doc_id]
                )
            else:
                self.collection.add(
                    documents=[text or existing['document']],
                    metadatas=[update_metadata],
                    ids=[doc_id]
                )
            
            logger.debug(f"✓ 更新文档: {doc_id}")
            return True
            
        except Exception as e:
            logger.error(f"✗ 更新文档失败: {e}")
            return False
    
    def count(self) -> int:
        """返回集合中的文档数量"""
        try:
            return self.collection.count()
        except Exception as e:
            logger.error(f"✗ 计数失败: {e}")
            return 0
    
    def clear_collection(self) -> bool:
        """清空集合（删除所有文档）"""
        try:
            # 获取所有 ID
            all_docs = self.collection.get(include=[])
            if all_docs and all_docs.get('ids'):
                self.collection.delete(ids=all_docs['ids'])
                logger.info(f"✓ 清空集合: {self.collection_name}")
            return True
        except Exception as e:
            logger.error(f"✗ 清空集合失败: {e}")
            return False
    
    def close(self):
        """关闭客户端（ChromaDB 会自动持久化）"""
        try:
            # ChromaDB PersistentClient 会自动持久化
            # 无需显式关闭
            logger.info("✓ ChromaDB 客户端已关闭（数据已持久化）")
        except Exception as e:
            logger.error(f"✗ 关闭客户端失败: {e}")


# 全局客户端实例
_chromadb_instance = None

def get_chromadb_client() -> Optional[ChromaDBClient]:
    """
    获取 ChromaDB 客户端实例（单例模式）
    
    返回 None 表示 ChromaDB 不可用（数据损坏、未安装等），
    调用方应对 None 做容错处理。
    """
    global _chromadb_instance
    if _chromadb_instance is None:
        try:
            _chromadb_instance = ChromaDBClient()
        except BaseException as e:
            logger.error(f"ChromaDB 不可用: {e}")
            # 如果是数据损坏，尝试清空数据目录后重试一次
            try:
                import shutil
                persist_dir = "./chromadb_data"
                if os.path.exists(persist_dir):
                    shutil.rmtree(persist_dir)
                    logger.info("已清空损坏的 ChromaDB 数据目录，尝试重建...")
                _chromadb_instance = ChromaDBClient()
                logger.info("✓ ChromaDB 重建成功")
            except BaseException as retry_err:
                logger.error(f"ChromaDB 重建也失败: {retry_err}")
                return None
    return _chromadb_instance


def test_chromadb_connection():
    """测试 ChromaDB 连接和基本操作"""
    print("\n" + "="*60)
    print("测试 ChromaDB 向量数据库连接和操作")
    print("="*60 + "\n")
    
    client = get_chromadb_client()
    
    # 测试添加向量嵌入
    print("1. 测试添加向量嵌入...")
    test_texts = [
        "鑫海喜欢极简设计风格",
        "源启·智能体工厂是鑫海负责的项目",
        "Agent星图是另一个重要项目",
        "鑫海在腾讯工作，负责PM和研发规划"
    ]
    
    doc_ids = []
    for text in test_texts:
        doc_id = client.add_embedding(
            text=text,
            metadata={"user_id": "xinhai", "source": "test"}
        )
        doc_ids.append(doc_id)
        print(f"   ✓ 添加文档: {text[:30]}... (ID: {doc_id[:8]})")
    
    # 测试搜索向量嵌入
    print(f"\n2. 测试相似性搜索...")
    query = "鑫海的项目"
    results = client.search_embeddings(query, n_results=3)
    print(f"   查询: '{query}'")
    for i, result in enumerate(results, 1):
        print(f"   {i}. {result['document'][:50]}... (相似度: {result['similarity']:.3f})")
    
    # 测试根据 ID 获取
    print(f"\n3. 测试根据 ID 获取文档...")
    first_id = doc_ids[0]
    doc = client.get_by_id(first_id)
    print(f"   ✓ 获取文档: {doc['document'][:50]}...")
    
    # 测试更新文档
    print(f"\n4. 测试更新文档...")
    client.update_by_id(
        first_id,
        metadata={"user_id": "xinhai", "source": "test", "updated": True}
    )
    updated_doc = client.get_by_id(first_id)
    print(f"   ✓ 更新后 metadata: {updated_doc['metadata']}")
    
    # 测试删除文档
    print(f"\n5. 测试删除文档...")
    client.delete_by_id(doc_ids[-1])  # 删除最后一个
    count = client.count()
    print(f"   ✓ 删除后文档数量: {count}")
    
    # 性能测试
    print(f"\n6. 性能测试（100 次搜索）...")
    import time
    start = time.time()
    for i in range(100):
        client.search_embeddings("测试查询", n_results=3)
    elapsed = time.time() - start
    print(f"   ✓ 100 次搜索耗时: {elapsed:.3f} 秒")
    print(f"   ✓ 平均延迟: {elapsed/100*1000:.2f} 毫秒")
    
    # 清理测试数据
    print(f"\n7. 清理测试数据...")
    client.clear_collection()
    print(f"   ✓ 集合已清空")
    
    print("\n" + "="*60)
    print("✅ ChromaDB 测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 运行测试
    test_chromadb_connection()
