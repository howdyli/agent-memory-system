"""
SQL 安全校验与 NL2SQL 两步接口测试
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from app.services.sql_safety import validate_sql_safety, pre_check_sql, extract_literal_params
from app.services.natural_language_query_service import (
    natural_language_to_sql_only,
    execute_confirmed_sql,
)
from app.services.memory_table_service import create_memory_table, batch_add_records
from app.core.db_client import get_db_client


USER_ID = 999


@pytest.fixture(autouse=True)
def cleanup():
    """清理测试数据"""
    yield
    db = get_db_client()
    db.execute(f'DROP TABLE IF EXISTS "memory_{USER_ID}_projects"')
    db.execute('DELETE FROM memory_tables WHERE user_id = ? AND table_name = ?',
               (USER_ID, "projects"))


@pytest.fixture
def setup_table():
    fields = [
        {"name": "project_name", "type": "TEXT"},
        {"name": "负责人", "type": "TEXT"},
        {"name": "status", "type": "TEXT"},
    ]
    create_memory_table(USER_ID, "projects", fields)
    batch_add_records(USER_ID, "projects", [
        {"project_name": "源启·智能体工厂", "负责人": "鑫海", "status": "进行中"},
        {"project_name": "Agent星图", "负责人": "张三", "status": "进行中"},
        {"project_name": "徽商银行AI", "负责人": "李四", "status": "已完成"},
    ])


class TestSqlSafety:
    """SQL 安全校验单元测试"""

    def test_safe_select_passes(self):
        safe, reason = validate_sql_safety('SELECT * FROM "memory_999_projects"')
        assert safe is True
        assert "通过" in reason

    def test_drop_rejected(self):
        safe, reason = validate_sql_safety('DROP TABLE "memory_999_projects"')
        assert safe is False
        assert "DROP" in reason

    def test_delete_rejected(self):
        safe, reason = validate_sql_safety('DELETE FROM "memory_999_projects"')
        assert safe is False
        assert "DELETE" in reason

    def test_multi_statement_rejected(self):
        safe, reason = validate_sql_safety("SELECT * FROM t; DROP TABLE t")
        assert safe is False

    def test_comment_rejected(self):
        safe, reason = validate_sql_safety("SELECT * FROM t -- comment")
        assert safe is False

    def test_block_comment_rejected(self):
        safe, reason = validate_sql_safety("SELECT * FROM t /* comment */")
        assert safe is False

    def test_unmatched_quote_rejected(self):
        safe, reason = validate_sql_safety("SELECT * FROM t WHERE name = 'test")
        assert safe is False
        assert "单引号" in reason

    def test_readonly_allows_only_select(self):
        safe, _ = validate_sql_safety('INSERT INTO t VALUES (1)', readonly=True)
        assert safe is False

    def test_parameterize_literals(self):
        sql, params = extract_literal_params("SELECT * FROM t WHERE name = 'Alice' AND status = 'active'")
        assert "?" in sql
        assert params == ("Alice", "active")


class TestPreCheckSql:
    """EXPLAIN 预检查测试"""

    def test_valid_select_pre_check(self, setup_table):
        db = get_db_client()
        ok, reason = pre_check_sql(f'SELECT * FROM "memory_{USER_ID}_projects"', db)
        assert ok is True
        assert "通过" in reason

    def test_invalid_sql_pre_check_fails(self):
        db = get_db_client()
        ok, reason = pre_check_sql('SELECT * FROM "nonexistent_table_xyz"', db)
        assert ok is False


class TestNlToSqlTwoStep:
    """NL2SQL 两步接口集成测试"""

    def test_nl_to_sql_only_returns_safe_sql(self, setup_table):
        result = natural_language_to_sql_only(USER_ID, "进行中的项目有哪些？")
        assert result["success"] is True
        assert "SELECT" in result["sql"]
        assert result["is_safe"] is True
        assert result["table_name"] == "projects"

    def test_execute_confirmed_sql(self, setup_table):
        preview = natural_language_to_sql_only(USER_ID, "进行中的项目有哪些？")
        result = execute_confirmed_sql(USER_ID, preview["sql"])
        assert result["success"] is True
        assert len(result["records"]) == 2

    def test_execute_confirmed_sql_rejects_dangerous(self, setup_table):
        result = execute_confirmed_sql(USER_ID, 'DROP TABLE "memory_999_projects"')
        assert result["success"] is False
        assert "安全校验失败" in result["error"]

    def test_execute_confirmed_sql_rejects_other_user_table(self, setup_table):
        result = execute_confirmed_sql(USER_ID, 'SELECT * FROM "memory_888_projects"')
        assert result["success"] is False
        assert "其他用户" in result["error"]
