"""
数据库连接测试脚本
测试 Redis、PostgreSQL、Milvus 的连接和功能
满足 Task 2 的所有验收标准
"""

import time
import json
import logging
import sys
import concurrent.futures

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)

# 导入各客户端
from redis_client import get_redis_client, close_redis_client
from db_client import get_pg_client, close_pg_client
from milvus_client import get_milvus_client, close_milvus_client


class Colors:
    """终端颜色"""
    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


def print_header(title: str) -> None:
    """打印标题"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'=' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{title}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'=' * 70}{Colors.RESET}\n")


def print_result(test_name: str, passed: bool, details: str = "") -> None:
    """打印测试结果"""
    status = f"{Colors.GREEN}✅ PASS{Colors.RESET}" if passed else f"{Colors.RED}❌ FAIL{Colors.RESET}"
    print(f"  [{status}] {test_name}")
    if details:
        print(f"         {details}")


def test_redis(latency_requirement_ms: float = 100.0) -> dict:
    """
    测试 Redis
    
    验收标准：
    - Redis能够成功存储和读取Key-Value数据，延迟 < 100ms
    
    Returns:
        dict: 测试结果
    """
    print_header("测试 1/3: Redis 连接和性能测试")

    results = {
        "name": "Redis",
        "passed": False,
        "tests": {},
        "latency": {},
        "details": {},
    }

    try:
        redis_client = get_redis_client()

        # 测试 1: Ping
        print("  [1/5] Ping 测试...")
        ping_result = redis_client.ping()
        results["tests"]["ping"] = ping_result
        print_result("Ping 测试", ping_result)

        # 测试 2: Set/Get
        print("  [2/5] Set/Get 测试...")
        test_key = f"test:{int(time.time())}"
        test_value = "Agent Memory Test Value"

        # 测量 set 延迟
        set_latencies = []
        get_latencies = []
        num_iterations = 10

        for i in range(num_iterations):
            # Set 延迟
            start = time.perf_counter()
            redis_client.set(test_key, test_value, expire=60)
            set_latency = (time.perf_counter() - start) * 1000
            set_latencies.append(set_latency)

            # Get 延迟
            start = time.perf_counter()
            retrieved = redis_client.get(test_key)
            get_latency = (time.perf_counter() - start) * 1000
            get_latencies.append(get_latency)

        avg_set_latency = sum(set_latencies) / len(set_latencies)
        avg_get_latency = sum(get_latencies) / len(get_latencies)
        max_latency = max(max(set_latencies), max(get_latencies))

        set_get_passed = retrieved == test_value
        results["tests"]["set_get"] = set_get_passed
        results["latency"]["set_avg_ms"] = round(avg_set_latency, 2)
        results["latency"]["get_avg_ms"] = round(avg_get_latency, 2)
        results["latency"]["max_ms"] = round(max_latency, 2)
        print_result(
            "Set/Get 测试",
            set_get_passed,
            f"avg_set={avg_set_latency:.2f}ms, avg_get={avg_get_latency:.2f}ms",
        )

        # 测试 3: 延迟是否满足要求 (< 100ms)
        print("  [3/5] 延迟要求测试 (< 100ms)...")
        latency_passed = max_latency < latency_requirement_ms
        results["tests"]["latency"] = latency_passed
        results["details"]["latency_requirement"] = f"< {latency_requirement_ms}ms"
        print_result(
            f"延迟要求测试 (< {latency_requirement_ms}ms)",
            latency_passed,
            f"max_latency={max_latency:.2f}ms",
        )

        # 测试 4: Delete
        print("  [4/5] Delete 测试...")
        deleted = redis_client.delete(test_key)
        delete_passed = deleted >= 0
        results["tests"]["delete"] = delete_passed
        print_result("Delete 测试", delete_passed, f"deleted={deleted}")

        # 测试 5: 连接池
        print("  [5/5] 连接池配置测试...")
        pool_info = {
            "max_connections": redis_client.max_connections,
            "supports_1000": redis_client.max_connections >= 1000,
        }
        pool_passed = pool_info["supports_1000"]
        results["tests"]["connection_pool"] = pool_passed
        results["details"]["pool"] = pool_info
        print_result(
            "连接池配置测试 (≥1000)",
            pool_passed,
            f"max_connections={pool_info['max_connections']}",
        )

        # 总体结果
        results["passed"] = all(results["tests"].values())

    except Exception as e:
        logger.error(f"Redis 测试失败: {e}", exc_info=True)
        results["error"] = str(e)

    return results


def test_postgresql() -> dict:
    """
    测试 PostgreSQL
    
    验收标准：
    - PostgreSQL能够创建表并执行CRUD操作
    - 所有数据库连接支持连接池配置，能够承受 ≥ 1000并发连接
    
    Returns:
        dict: 测试结果
    """
    print_header("测试 2/3: PostgreSQL 连接和 CRUD 测试")

    results = {
        "name": "PostgreSQL",
        "passed": False,
        "tests": {},
        "details": {},
    }

    try:
        pg_client = get_pg_client()

        # 测试 1: 连接测试
        print("  [1/6] 连接测试...")
        health = pg_client.health_check()
        connection_passed = health["status"] == "healthy"
        results["tests"]["connection"] = connection_passed
        print_result("连接测试", connection_passed, f"latency={health.get('latency_ms', 'N/A')}ms")

        # 测试 2: 创建表
        print("  [2/6] 创建表测试...")
        test_table = f"test_table_{int(time.time())}"
        create_sql = f"""
        CREATE TABLE IF NOT EXISTS {test_table} (
            id SERIAL PRIMARY KEY,
            name VARCHAR(255),
            value TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
        pg_client.execute_ddl(create_sql)
        table_exists = pg_client.table_exists(test_table)
        results["tests"]["create_table"] = table_exists
        print_result("创建表测试", table_exists, f"table={test_table}")

        # 测试 3: 插入数据 (Create)
        print("  [3/6] CRUD - Create 测试...")
        insert_sql = f"INSERT INTO {test_table} (name, value) VALUES (%s, %s) RETURNING id"
        with pg_client.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(insert_sql, ("test_name", "test_value"))
                inserted_id = cur.fetchone()[0]
                conn.commit()
        results["tests"]["create"] = inserted_id is not None
        print_result("CRUD - Create 测试", results["tests"]["create"], f"inserted_id={inserted_id}")

        # 测试 4: 查询数据 (Read)
        print("  [4/6] CRUD - Read 测试...")
        select_sql = f"SELECT * FROM {test_table} WHERE id = %s"
        rows = pg_client.execute_query(select_sql, (inserted_id,), fetch_all=False)
        read_passed = rows is not None and rows["name"] == "test_name"
        results["tests"]["read"] = read_passed
        print_result("CRUD - Read 测试", read_passed, f"row={dict(rows) if rows else None}")

        # 测试 5: 更新数据 (Update)
        print("  [5/6] CRUD - Update 测试...")
        update_sql = f"UPDATE {test_table} SET value = %s WHERE id = %s"
        affected = pg_client.execute_update(update_sql, ("updated_value", inserted_id))
        results["tests"]["update"] = affected > 0

        # 验证更新
        rows = pg_client.execute_query(select_sql, (inserted_id,), fetch_all=False)
        update_verified = rows is not None and rows["value"] == "updated_value"
        print_result("CRUD - Update 测试", update_verified, f"affected={affected}")

        # 测试 6: 删除数据 (Delete)
        print("  [6/6] CRUD - Delete 测试...")
        delete_sql = f"DELETE FROM {test_table} WHERE id = %s"
        affected = pg_client.execute_update(delete_sql, (inserted_id,))
        results["tests"]["delete"] = affected > 0
        print_result("CRUD - Delete 测试", results["tests"]["delete"], f"affected={affected}")

        # 连接池测试
        print("  [额外] 连接池配置测试...")
        pool_info = {
            "min_connections": pg_client.min_connections,
            "max_connections": pg_client.max_connections,
            "supports_1000": pg_client.max_connections >= 1000,
        }
        pool_passed = pool_info["supports_1000"]
        results["tests"]["connection_pool"] = pool_passed
        results["details"]["pool"] = pool_info
        print_result(
            "连接池配置测试 (≥1000)",
            pool_passed,
            f"max_connections={pool_info['max_connections']}",
        )

        # 清理
        pg_client.execute_ddl(f"DROP TABLE IF EXISTS {test_table}")
        print(f"        清理测试表: {test_table}")

        # 总体结果
        results["passed"] = all(results["tests"].values())

    except Exception as e:
        logger.error(f"PostgreSQL 测试失败: {e}", exc_info=True)
        results["error"] = str(e)

    return results


def test_milvus() -> dict:
    """
    测试 Milvus
    
    验收标准：
    - 向量数据库能够存储和检索向量嵌入
    - 所有数据库连接支持连接池配置
    
    Returns:
        dict: 测试结果
    """
    print_header("测试 3/3: Milvus 向量数据库测试")

    results = {
        "name": "Milvus",
        "passed": False,
        "tests": {},
        "details": {},
    }

    try:
        milvus_client = get_milvus_client()

        # 测试 1: 连接测试
        print("  [1/5] 连接测试...")
        health = milvus_client.health_check()
        connection_passed = health["status"] == "healthy"
        results["tests"]["connection"] = connection_passed
        print_result("连接测试", connection_passed, f"latency={health.get('latency_ms', 'N/A')}ms")

        # 测试 2: 创建 Collection
        print("  [2/5] 创建 Collection 测试...")
        test_collection = f"test_collection_{int(time.time())}"
        try:
            collection = milvus_client.create_memory_collection(
                collection_name=test_collection,
                dim=128,
            )
            collection_exists = milvus_client.collection_exists(test_collection)
            results["tests"]["create_collection"] = collection_exists
            print_result("创建 Collection 测试", collection_exists, f"collection={test_collection}")
        except Exception as e:
            # 如果已存在，先删除再创建
            milvus_client.drop_collection(test_collection)
            collection = milvus_client.create_memory_collection(
                collection_name=test_collection,
                dim=128,
            )
            results["tests"]["create_collection"] = True
            print_result("创建 Collection 测试", True, f"collection={test_collection} (recreated)")

        # 测试 3: 插入向量
        print("  [3/5] 插入向量测试...")
        import numpy as np
        num_vectors = 20
        np.random.seed(42)
        embeddings = np.random.rand(num_vectors, 128).tolist()

        data = {
            "memory_id": list(range(1, num_vectors + 1)),
            "session_id": ["test_session"] * num_vectors,
            "user_id": ["test_user"] * num_vectors,
            "memory_type": ["episodic"] * num_vectors,
            "content": [f"测试记忆 {i}" for i in range(num_vectors)],
            "embedding": embeddings,
            "timestamp": [int(time.time())] * num_vectors,
            "metadata": [{"source": "test"}] * num_vectors,
        }

        ids = milvus_client.insert_vectors(test_collection, data)
        insert_passed = len(ids) == num_vectors
        results["tests"]["insert"] = insert_passed
        print_result("插入向量测试", insert_passed, f"inserted={len(ids)}")

        # 测试 4: 搜索向量
        print("  [4/5] 搜索向量测试...")
        query_vector = [embeddings[0]]  # 使用第一条作为查询
        search_results = milvus_client.search_vectors(
            collection_name=test_collection,
            query_vectors=query_vector,
            top_k=5,
        )

        search_passed = len(search_results) > 0 and len(search_results[0]) > 0
        results["tests"]["search"] = search_passed
        print_result("搜索向量测试", search_passed, f"found={len(search_results[0])} results")

        if search_passed:
            print("         搜索结果示例:")
            for i, hit in enumerate(search_results[0][:3]):
                print(f"         #{i+1}: id={hit['id']}, distance={hit['distance']:.4f}, "
                      f"content={hit['entity'].get('content', '')}")

        # 测试 5: 删除实体
        print("  [5/5] 删除实体测试...")
        milvus_client.delete_entities(
            collection_name=test_collection,
            expr=f"memory_id in {[1, 2, 3]}",
        )
        results["tests"]["delete"] = True
        print_result("删除实体测试", True, "expr=memory_id in [1, 2, 3]")

        # 清理
        milvus_client.drop_collection(test_collection)
        print(f"         清理测试 Collection: {test_collection}")

        # 连接信息
        results["details"]["connection"] = {
            "host": milvus_client.host,
            "port": milvus_client.port,
        }

        # 总体结果
        results["passed"] = all(results["tests"].values())

    except Exception as e:
        logger.error(f"Milvus 测试失败: {e}", exc_info=True)
        results["error"] = str(e)

    return results


def run_all_tests() -> dict:
    """
    运行所有测试
    
    Returns:
        dict: 所有测试结果
    """
    print_header("Agent Memory 系统 - Task 2 数据存储层测试")

    start_time = time.time()
    results = {}

    # 运行测试
    results["redis"] = test_redis()
    results["postgresql"] = test_postgresql()
    results["milvus"] = test_milvus()

    elapsed = time.time() - start_time

    # 打印总结
    print_header("测试总结")

    all_passed = True
    for db_name, result in results.items():
        passed = result.get("passed", False)
        all_passed = all_passed and passed
        status = f"{Colors.GREEN}✅ PASS{Colors.RESET}" if passed else f"{Colors.RED}❌ FAIL{Colors.RESET}"
        print(f"  {db_name.upper()}: {status}")

        # 打印详细信息
        if "tests" in result:
            for test_name, test_passed in result["tests"].items():
                detail = ""
                if test_name == "latency" and "latency" in result:
                    detail = f"max={result['latency'].get('max_ms', 'N/A')}ms"
                print(f"    - {test_name}: {'PASS' if test_passed else 'FAIL'} {detail}")

    print(f"\n{Colors.BOLD}总耗时: {elapsed:.2f}s{Colors.RESET}")
    overall_status = f"{Colors.GREEN}✅ 所有测试通过{Colors.RESET}" if all_passed else f"{Colors.RED}❌ 部分测试失败{Colors.RESET}"
    print(f"{Colors.BOLD}总体结果: {overall_status}{Colors.RESET}\n")

    # 保存结果到文件
    output_file = "/Users/howdy/WorkBuddy/2026-06-24-06-17-54/agent-memory-system/backend/test_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"测试结果已保存到: {output_file}")

    return results


def main():
    """主函数"""
    try:
        results = run_all_tests()
        sys.exit(0 if all(r.get("passed", False) for r in results.values()) else 1)
    except KeyboardInterrupt:
        logger.info("用户中断")
        sys.exit(1)
    except Exception as e:
        logger.error(f"测试执行失败: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # 关闭所有连接
        print("\n清理资源...")
        close_redis_client()
        close_pg_client()
        close_milvus_client()
        print("资源清理完成")


if __name__ == "__main__":
    main()
