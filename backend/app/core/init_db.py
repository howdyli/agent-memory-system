"""
数据库初始化脚本

初始化所有数据库（Redis、SQLite、ChromaDB）的连接和配置
"""
import logging
import sys
import os

# 添加项目根目录到 Python 路径
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, BACKEND_DIR)

from app.core.redis_client import get_redis_client, test_redis_connection
from app.core.db_client import get_db_client, test_db_connection
from app.core.chromadb_client import get_chromadb_client, test_chromadb_connection

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def init_databases():
    """初始化所有数据库"""
    print("\n" + "="*60)
    print("Agent Memory System - 数据库初始化")
    print("="*60 + "\n")
    
    results = {
        "redis": False,
        "sqlite": False,
        "chromadb": False
    }
    
    # 1. 初始化 Redis（FakeRedis）
    print("\n[1/3] 初始化 Redis 连接...")
    try:
        redis_client = get_redis_client()
        logger.info("✓ Redis 连接成功（FakeRedis 模式）")
        results["redis"] = True
    except Exception as e:
        logger.error(f"✗ Redis 初始化失败: {e}")
        results["redis"] = False
    
    # 2. 初始化 SQLite
    print("\n[2/3] 初始化 SQLite 数据库...")
    try:
        db_client = get_db_client()
        logger.info("✓ SQLite 数据库初始化成功")
        results["sqlite"] = True
    except Exception as e:
        logger.error(f"✗ SQLite 初始化失败: {e}")
        results["sqlite"] = False
    
    # 3. 初始化 ChromaDB
    print("\n[3/3] 初始化 ChromaDB 向量数据库...")
    try:
        chromadb_client = get_chromadb_client()
        count = chromadb_client.count()
        logger.info(f"✓ ChromaDB 初始化成功（当前文档数: {count}）")
        results["chromadb"] = True
    except Exception as e:
        logger.error(f"✗ ChromaDB 初始化失败: {e}")
        results["chromadb"] = False
    
    # 总结
    print("\n" + "="*60)
    print("数据库初始化完成！")
    print("="*60)
    print(f"Redis:    {'✓ 成功' if results['redis'] else '✗ 失败'}")
    print(f"SQLite:   {'✓ 成功' if results['sqlite'] else '✗ 失败'}")
    print(f"ChromaDB: {'✓ 成功' if results['chromadb'] else '✗ 失败'}")
    print("="*60 + "\n")
    
    # 返回是否全部成功
    all_success = all(results.values())
    if all_success:
        print("✅ 所有数据库初始化成功！")
    else:
        print("⚠️  部分数据库初始化失败，请检查日志")
    
    return all_success


def run_all_tests():
    """运行所有数据库连接测试"""
    print("\n" + "="*60)
    print("运行数据库连接测试...")
    print("="*60 + "\n")
    
    test_results = {}
    
    # 测试 Redis
    print("\n" + "-"*60)
    print("测试 1/3: Redis 连接和 basic 操作")
    print("-"*60)
    try:
        test_redis_connection()
        test_results["redis"] = True
    except Exception as e:
        logger.error(f"✗ Redis 测试失败: {e}")
        test_results["redis"] = False
    
    # 测试 SQLite
    print("\n" + "-"*60)
    print("测试 2/3: SQLite 连接和 CRUD 操作")
    print("-"*60)
    try:
        test_db_connection()
        test_results["sqlite"] = True
    except Exception as e:
        logger.error(f"✗ SQLite 测试失败: {e}")
        test_results["sqlite"] = False
    
    # 测试 ChromaDB
    print("\n" + "-"*60)
    print("测试 3/3: ChromaDB 连接和向量操作")
    print("-"*60)
    try:
        test_chromadb_connection()
        test_results["chromadb"] = True
    except Exception as e:
        logger.error(f"✗ ChromaDB 测试失败: {e}")
        test_results["chromadb"] = False
    
    # 总结
    print("\n" + "="*60)
    print("测试完成！")
    print("="*60)
    for db_name, result in test_results.items():
        status = "✓ 通过" if result else "✗ 失败"
        print(f"{db_name:15s} {status}")
    print("="*60 + "\n")
    
    return all(test_results.values())


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Agent Memory System - 数据库初始化工具')
    parser.add_argument('--test', action='store_true', help='运行测试')
    parser.add_argument('--init-only', action='store_true', help='仅初始化，不运行测试')
    
    args = parser.parse_args()
    
    if args.test:
        # 仅运行测试
        success = run_all_tests()
    elif args.init_only:
        # 仅初始化
        success = init_databases()
    else:
        # 默认：初始化 + 测试
        print("步骤 1: 初始化数据库...")
        init_success = init_databases()
        
        if init_success:
            print("\n步骤 2: 运行测试...")
            test_success = run_all_tests()
            success = test_success
        else:
            print("\n⚠️  初始化失败，跳过测试")
            success = False
    
    # 退出码
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
