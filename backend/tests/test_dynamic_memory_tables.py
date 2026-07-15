"""
动态记忆表测试用例

覆盖：
- 建表（多种字段类型、索引、可空约束）
- CRUD（单条、批量）
- 类型验证与错误处理
- 过滤查询与排序
- 自然语言解析
- 用户隔离
- 边界/异常场景
- 边缘案例（空字段、JSON 类型、大量数据）
"""
import pytest
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.memory_table_service import (
    create_memory_table,
    add_record,
    query_records,
    update_record,
    delete_record,
    batch_add_records,
    batch_update_records,
    list_tables,
    get_table_info,
    drop_table,
    query_records_with_filters,
    parse_natural_language_to_table,
)
from app.core.db_client import get_db_client

USER_ID = 999  # 测试用户 ID


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def cleanup():
    """每次测试后清理测试数据"""
    yield
    db = get_db_client()
    # 清理可能的测试表
    for tbl in ["projects", "contacts", "tasks", "test_types", "empty_fields",
                 "books", "user_isolation_a", "user_isolation_b",
                 "edge_case_table", "json_test", "large_table"]:
        db.execute(f'DROP TABLE IF EXISTS "memory_{USER_ID}_{tbl}"')
    for tbl in ["projects", "contacts", "tasks", "test_types", "empty_fields",
                 "books", "user_isolation_a", "user_isolation_b",
                 "edge_case_table", "json_test", "large_table"]:
        db.execute('DELETE FROM memory_tables WHERE user_id = ? AND table_name = ?',
                   (USER_ID, tbl))
    # 清理其他用户数据
    db.execute('DELETE FROM memory_tables WHERE user_id = 888')


# ============================================================
# 1. 建表测试
# ============================================================

class TestCreateTable:
    """建表功能测试"""

    def test_create_table_default(self):
        """创建包含 TEXT/INTEGER/BOOLEAN 三种类型字段的表"""
        fields = [
            {"name": "name", "type": "TEXT"},
            {"name": "age", "type": "INTEGER"},
            {"name": "active", "type": "BOOLEAN"},
        ]
        result = create_memory_table(USER_ID, "test_types", fields)
        assert result["success"] is True
        assert result["table_name"] == "test_types"
        assert len(result["fields"]) == 3

        # 验证表已创建
        info = get_table_info(USER_ID, "test_types")
        assert info is not None
        assert info["table_name"] == "test_types"
        assert len(info["fields"]) == 3

    def test_create_table_with_index(self):
        """创建带索引字段的表"""
        fields = [
            {"name": "name", "type": "TEXT", "index": True},
            {"name": "status", "type": "TEXT"},
        ]
        result = create_memory_table(USER_ID, "contacts", fields)
        assert result["success"] is True

    def test_create_table_unsupported_type_fallback(self):
        """不支持的字段类型自动降级为 TEXT"""
        fields = [
            {"name": "data", "type": "UNKNOWN_TYPE"},
        ]
        result = create_memory_table(USER_ID, "projects", fields)
        assert result["success"] is True
        # 验证降级为 TEXT
        info = get_table_info(USER_ID, "projects")
        assert info["fields"][0]["type"] == "TEXT"

    def test_create_table_empty_fields(self):
        """创建空的字段列表（只含 __id__ 等元数据字段）"""
        result = create_memory_table(USER_ID, "empty_fields", [])
        assert result["success"] is True

    def test_create_duplicate_table(self):
        """重复创建同名表应该成功（幂等）"""
        fields = [{"name": "name", "type": "TEXT"}]
        result1 = create_memory_table(USER_ID, "projects", fields)
        assert result1["success"] is True
        result2 = create_memory_table(USER_ID, "projects", fields)
        assert result2["success"] is True


# ============================================================
# 2. CRUD 测试
# ============================================================

class TestCRUD:
    """增删改查操作测试"""

    @pytest.fixture(autouse=True)
    def setup_table(self):
        fields = [
            {"name": "name", "type": "TEXT"},
            {"name": "age", "type": "INTEGER"},
            {"name": "active", "type": "BOOLEAN"},
        ]
        create_memory_table(USER_ID, "test_types", fields)
        yield

    def test_add_record(self):
        """添加单条记录"""
        result = add_record(USER_ID, "test_types", {
            "name": "张三",
            "age": 25,
            "active": True,
        })
        assert result["success"] is True
        assert result["record_id"] > 0

    def test_add_record_field_subset(self):
        """添加部分字段的记录"""
        result = add_record(USER_ID, "test_types", {"name": "李四"})
        assert result["success"] is True

    def test_add_record_unknown_field(self):
        """添加包含不存在的字段"""
        result = add_record(USER_ID, "test_types", {
            "name": "王五",
            "unknown_field": "value",
        })
        assert result["success"] is True  # 未知字段被忽略，不应报错
        assert result["record_id"] > 0

    def test_add_record_to_nonexistent_table(self):
        """向不存在的表添加记录"""
        result = add_record(USER_ID, "nonexistent", {"name": "test"})
        assert result["success"] is False

    def test_query_records(self):
        """查询所有记录"""
        add_record(USER_ID, "test_types", {"name": "A"})
        add_record(USER_ID, "test_types", {"name": "B"})
        result = query_records(USER_ID, "test_types")
        assert result["success"] is True
        assert result["count"] == 2
        assert len(result["records"]) == 2

    def test_query_records_empty_table(self):
        """查询空表"""
        result = query_records(USER_ID, "test_types")
        assert result["success"] is True
        assert result["count"] == 0

    def test_query_records_with_filters(self):
        """带过滤条件的查询"""
        add_record(USER_ID, "test_types", {"name": "A", "active": True})
        add_record(USER_ID, "test_types", {"name": "B", "active": False})
        result = query_records(USER_ID, "test_types", filters={"active": True})
        assert result["success"] is True
        assert result["count"] == 1
        assert result["records"][0]["name"] == "A"

    def test_update_record(self):
        """更新单条记录"""
        r = add_record(USER_ID, "test_types", {"name": "张三", "age": 25})
        record_id = r["record_id"]
        result = update_record(USER_ID, "test_types", record_id, {"age": 26})
        assert result["success"] is True

        # 验证更新结果
        records = query_records(USER_ID, "test_types")["records"]
        updated = [rec for rec in records if rec["id"] == record_id]
        assert len(updated) > 0
        assert updated[0]["age"] == 26

    def test_update_record_partial(self):
        """更新部分字段"""
        r = add_record(USER_ID, "test_types", {"name": "张三", "age": 25, "active": True})
        result = update_record(USER_ID, "test_types", r["record_id"], {"active": False})
        assert result["success"] is True

    def test_update_nonexistent_record(self):
        """更新不存在的记录"""
        result = update_record(USER_ID, "test_types", 99999, {"name": "test"})
        assert result["success"] is True  # SQLite 不会报错，影响行数为 0

    def test_delete_record(self):
        """删除单条记录"""
        r = add_record(USER_ID, "test_types", {"name": "待删除"})
        record_id = r["record_id"]
        result = delete_record(USER_ID, "test_types", record_id)
        assert result["success"] is True
        # 验证删除后记录数
        records = query_records(USER_ID, "test_types")["records"]
        assert all(rec["id"] != record_id for rec in records)

    def test_delete_nonexistent_record(self):
        """删除不存在的记录"""
        result = delete_record(USER_ID, "test_types", 99999)
        assert result["success"] is True


# ============================================================
# 3. 批量操作测试
# ============================================================

class TestBatchOperations:
    """批量操作功能测试"""

    @pytest.fixture(autouse=True)
    def setup_table(self):
        fields = [
            {"name": "name", "type": "TEXT"},
            {"name": "status", "type": "TEXT"},
        ]
        create_memory_table(USER_ID, "projects", fields)
        yield

    def test_batch_add_records(self):
        """批量添加多条记录"""
        records = [
            {"name": "项目A", "status": "进行中"},
            {"name": "项目B", "status": "已完成"},
            {"name": "项目C", "status": "计划中"},
        ]
        result = batch_add_records(USER_ID, "projects", records)
        assert result["success"] is True
        assert result["inserted_count"] == 3

        # 验证记录数
        records_result = query_records(USER_ID, "projects")["records"]
        assert len(records_result) == 3

    def test_batch_add_empty(self):
        """批量添加空列表"""
        result = batch_add_records(USER_ID, "projects", [])
        assert result["success"] is False

    def test_batch_update_records(self):
        """批量更新多条记录"""
        # 添加 3 条记录
        records = [
            {"name": "项目A", "status": "进行中"},
            {"name": "项目B", "status": "进行中"},
            {"name": "项目C", "status": "计划中"},
        ]
        batch_add_records(USER_ID, "projects", records)

        # 查询获取 IDs
        existing = query_records(USER_ID, "projects")["records"]
        updates_list = [
            {"record_id": rec["id"], "updates": {"status": "已完成"}}
            for rec in existing[:2]
        ]
        result = batch_update_records(USER_ID, "projects", updates_list)
        assert result["success"] is True
        assert result["updated_count"] == 2

    def test_batch_update_empty(self):
        """批量更新空列表"""
        result = batch_update_records(USER_ID, "projects", [])
        assert result["success"] is False


# ============================================================
# 4. 类型验证测试
# ============================================================

class TestTypeValidation:
    """类型验证功能测试"""

    @pytest.fixture(autouse=True)
    def setup_table(self):
        fields = [
            {"name": "name", "type": "TEXT"},
            {"name": "age", "type": "INTEGER"},
            {"name": "score", "type": "REAL"},
            {"name": "active", "type": "BOOLEAN"},
            {"name": "birthday", "type": "DATE"},
            {"name": "metadata", "type": "JSON"},
        ]
        create_memory_table(USER_ID, "test_types", fields)
        yield

    def test_valid_types(self):
        """所有有效类型的字段都能正确存储"""
        record = {
            "name": "测试",
            "age": 30,
            "score": 95.5,
            "active": True,
            "birthday": "2024-01-01",
            "metadata": json.dumps({"key": "value"}),
        }
        result = add_record(USER_ID, "test_types", record)
        assert result["success"] is True

    def test_invalid_integer(self):
        """非法的 INTEGER 类型应该被拒绝"""
        record = {"name": "测试", "age": "不是数字"}
        result = add_record(USER_ID, "test_types", record)
        assert result["success"] is False

    def test_invalid_boolean(self):
        """BOOLEAN 接受各种真值"""
        record = {"name": "测试", "active": True}
        result = add_record(USER_ID, "test_types", record)
        assert result["success"] is True

    def test_json_validation(self):
        """无效的 JSON 字符串应该被拒绝"""
        record = {"name": "测试", "metadata": "{invalid json}"}
        result = add_record(USER_ID, "test_types", record)
        assert result["success"] is False  # JSON 解析失败应拒绝

    def test_type_cast_update(self):
        """更新时的类型转换验证"""
        r = add_record(USER_ID, "test_types", {"name": "测试", "age": 30})
        result = update_record(USER_ID, "test_types", r["record_id"], {"age": "invalid"})
        assert result["success"] is False


# ============================================================
# 5. 过滤查询与排序测试
# ============================================================

class TestQueryWithFilters:
    """高级查询功能测试"""

    @pytest.fixture(autouse=True)
    def setup_table(self):
        fields = [
            {"name": "name", "type": "TEXT"},
            {"name": "priority", "type": "INTEGER"},
            {"name": "status", "type": "TEXT"},
        ]
        create_memory_table(USER_ID, "tasks", fields)
        records = [
            {"name": "任务A", "priority": 1, "status": "进行中"},
            {"name": "任务B", "priority": 2, "status": "已完成"},
            {"name": "任务C", "priority": 3, "status": "进行中"},
            {"name": "任务D", "priority": 1, "status": "已完成"},
        ]
        batch_add_records(USER_ID, "tasks", records)
        yield

    def test_filter_single_field(self):
        """单字段过滤"""
        result = query_records_with_filters(USER_ID, "tasks",
                                            filters={"status": "进行中"})
        assert result["success"] is True
        assert result["count"] == 2

    def test_filter_multi_field(self):
        """多字段联合过滤"""
        result = query_records_with_filters(USER_ID, "tasks",
                                            filters={"status": "进行中", "priority": 1})
        assert result["success"] is True
        assert result["count"] == 1
        assert result["records"][0]["name"] == "任务A"

    def test_sort_ascending(self):
        """升序排序"""
        result = query_records_with_filters(USER_ID, "tasks",
                                            sort_by="priority", sort_order="ASC")
        assert result["success"] is True
        assert result["count"] >= 2
        assert result["records"][0]["priority"] <= result["records"][1]["priority"]

    def test_sort_descending(self):
        """降序排序"""
        result = query_records_with_filters(USER_ID, "tasks",
                                            sort_by="priority", sort_order="DESC")
        assert result["success"] is True
        if result["count"] >= 2:
            assert result["records"][0]["priority"] >= result["records"][1]["priority"]

    def test_pagination(self):
        """分页查询"""
        total = query_records_with_filters(USER_ID, "tasks", limit=100)["count"]
        page1 = query_records_with_filters(USER_ID, "tasks", limit=2, offset=0)
        page2 = query_records_with_filters(USER_ID, "tasks", limit=2, offset=2)
        assert page1["count"] == 2
        assert page2["count"] == min(2, total - 2)
        # 两页 ID 不应重复
        ids1 = {r["id"] for r in page1["records"]}
        ids2 = {r["id"] for r in page2["records"]}
        assert ids1.isdisjoint(ids2)

    def test_filter_nonexistent_field(self):
        """过滤不存在的字段"""
        result = query_records_with_filters(USER_ID, "tasks",
                                            filters={"nonexistent_field": "value"})
        assert result["success"] is True
        assert result["count"] == 4  # 条件无效，返回全部

    def test_filter_on_nonexistent_table(self):
        """在不存在的表上过滤查询"""
        result = query_records_with_filters(USER_ID, "ghost_table",
                                            filters={"name": "test"})
        assert result["success"] is False
        assert "not found" in result.get("error", "").lower() or "not found" in str(result)


# ============================================================
# 6. 自然语言解析测试
# ============================================================

class TestNaturalLanguageParsing:
    """自然语言→表结构解析测试"""

    def test_parse_with_default_name(self):
        """记录项目：自动推断默认字段名"""
        input_text = "记录我的项目：源启·智能体工厂，负责人：鑫海，状态：进行中"
        result = parse_natural_language_to_table(input_text)
        assert result is not None
        assert result["table_name"] == "我的项目"
        # "项目"关键词 → 默认字段名 "项目名称"
        assert any(f["name"] == "项目名称" for f in result["fields"])
        assert "负责人" in result["record"]
        assert "状态" in result["record"]

    def test_parse_contact(self):
        """记录联系人：默认字段名 '姓名'"""
        input_text = "记录联系人：张三，电话：13800138000，公司：腾讯"
        result = parse_natural_language_to_table(input_text)
        assert result is not None
        assert "联系人" in result["table_name"]
        assert any(f["name"] == "姓名" for f in result["fields"])

    def test_parse_create_table(self):
        """创建表语法"""
        input_text = "创建表：项目（项目名称，负责人，状态）"
        result = parse_natural_language_to_table(input_text)
        assert result is not None
        assert result["table_name"] == "项目"
        assert len(result["fields"]) == 3
        assert result["fields"][0]["name"] == "项目名称"
        assert result["record"] is None  # 无初始数据

    def test_parse_invalid_input(self):
        """无法解析的输入返回 None"""
        result = parse_natural_language_to_table("你好，今天天气不错")
        assert result is None

    def test_parse_empty_input(self):
        """空输入"""
        result = parse_natural_language_to_table("")
        assert result is None


# ============================================================
# 7. 用户隔离测试
# ============================================================

class TestUserIsolation:
    """用户间数据隔离测试"""

    def test_users_see_own_tables(self):
        """不同用户只能看到自己的表"""
        user_a, user_b = 999, 888

        create_memory_table(user_a, "user_isolation_a",
                           [{"name": "data", "type": "TEXT"}])
        create_memory_table(user_b, "user_isolation_b",
                           [{"name": "data", "type": "TEXT"}])

        tables_a = list_tables(user_a)["tables"]
        tables_b = list_tables(user_b)["tables"]

        names_a = {t["table_name"] for t in tables_a}
        names_b = {t["table_name"] for t in tables_b}

        assert "user_isolation_a" in names_a
        assert "user_isolation_b" not in names_a
        assert "user_isolation_b" in names_b
        assert "user_isolation_a" not in names_b

    def test_users_cannot_access_each_other_data(self):
        """用户 A 无法查询用户 B 的表数据"""
        user_b = 888
        create_memory_table(user_b, "user_isolation_b",
                           [{"name": "data", "type": "TEXT"}])
        add_record(user_b, "user_isolation_b", {"data": "secret"})

        # 用户 A 尝试查询用户 B 的表
        result = query_records(999, "user_isolation_b")
        # 应该失败（表名隔离，实际表名为 memory_888_user_isolation_b）
        assert result["success"] is False or result["count"] == 0


# ============================================================
# 8. 边界/异常场景测试
# ============================================================

class TestEdgeCases:
    """边界条件和异常场景测试"""

    def test_large_text_field(self):
        """存储大量文本内容"""
        fields = [{"name": "content", "type": "TEXT"}]
        create_memory_table(USER_ID, "edge_case_table", fields)
        large_text = "A" * 10000
        result = add_record(USER_ID, "edge_case_table", {"content": large_text})
        assert result["success"] is True

    def test_special_characters(self):
        """字段名和值中的特殊字符"""
        fields = [{"name": "special_chars!@#", "type": "TEXT"}]
        create_memory_table(USER_ID, "edge_case_table", fields)
        result = add_record(USER_ID, "edge_case_table",
                           {"special_chars!@#": "value with 'quotes' and spaces"})
        assert result["success"] is True

    def test_query_nonexistent_table(self):
        """查询不存在的表"""
        result = query_records(USER_ID, "phantom_table")
        assert result["success"] is False

    def test_list_tables_empty(self):
        """空列表"""
        result = list_tables(USER_ID)
        assert result["success"] is True
        assert result["count"] == 0
        assert result["tables"] == []

    def test_drop_nonexistent_table(self):
        """删除不存在的表"""
        result = drop_table(USER_ID, "phantom_table")
        assert result["success"] is False

    def test_drop_table_then_recreate(self):
        """删除表后重新创建"""
        fields = [{"name": "name", "type": "TEXT"}]
        create_memory_table(USER_ID, "projects", fields)
        add_record(USER_ID, "projects", {"name": "旧数据"})
        drop_table(USER_ID, "projects")

        create_memory_table(USER_ID, "projects", [{"name": "ver", "type": "INTEGER"}])
        result = query_records(USER_ID, "projects")
        assert result["count"] == 0  # 旧数据已清除


# ============================================================
# 9. JSON 字段测试
# ============================================================

class TestJsonField:
    """JSON 字段类型测试"""

    @pytest.fixture(autouse=True)
    def setup(self):
        fields = [{"name": "data", "type": "JSON"}]
        create_memory_table(USER_ID, "json_test", fields)
        yield

    def test_store_json_string(self):
        """存储 JSON 字符串"""
        result = add_record(USER_ID, "json_test",
                           {"data": json.dumps({"key": "value", "num": 42})})
        assert result["success"] is True

    def test_store_json_dict(self):
        """存储字典类型"""
        result = add_record(USER_ID, "json_test",
                           {"data": {"nested": {"a": 1, "b": [1, 2, 3]}}})
        assert result["success"] is True

    def test_store_json_list(self):
        """存储列表类型"""
        result = add_record(USER_ID, "json_test",
                           {"data": [1, 2, 3, {"mixed": "value"}]})
        assert result["success"] is True


# ============================================================
# 10. 大量数据测试
# ============================================================

class TestBulkData:
    """大量数据场景测试"""

    def test_bulk_insert_100_records(self):
        """批量插入 100 条记录"""
        fields = [{"name": "value", "type": "INTEGER"}]
        create_memory_table(USER_ID, "large_table", fields)

        records = [{"value": i} for i in range(100)]
        result = batch_add_records(USER_ID, "large_table", records)
        assert result["success"] is True
        assert result["inserted_count"] == 100

        # 验证总数
        all_records = query_records(USER_ID, "large_table", limit=200)
        assert all_records["count"] == 100

    def test_query_within_large_dataset(self):
        """在大数据集中过滤查询"""
        fields = [{"name": "category", "type": "TEXT"}, {"name": "score", "type": "INTEGER"}]
        create_memory_table(USER_ID, "large_table", fields)

        records = [
            {"category": "A" if i % 3 == 0 else "B" if i % 3 == 1 else "C",
             "score": i}
            for i in range(90)
        ]
        batch_add_records(USER_ID, "large_table", records)

        # 过滤 category = A
        result = query_records_with_filters(USER_ID, "large_table",
                                           filters={"category": "A"})
        assert result["success"] is True
        assert result["count"] == 30
