"""
自然语言转查询服务（智能问数）

将用户的自然语言问题转换为 SQL 查询，并安全执行
"""
import logging
import json
import re
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.services.memory_table_service import get_table_info, list_tables, _physical_table
from app.services.sql_safety import (
    validate_sql_safety as _validate_sql_safety,
    pre_check_sql,
    is_select_only,
)


# ============================================================
# 安全校验：防止 SQL 注入
# ============================================================

# 向后兼容：保留旧的 Dict 返回形式
# 内部已迁移到 sql_safety 模块的 tuple[bool, str] 实现


def validate_sql_safety(sql: str) -> Dict[str, Any]:
    """
    校验 SQL 语句的安全性（兼容旧接口）

    Args:
        sql: 要校验的 SQL 语句

    Returns:
        校验结果字典 {"safe": bool, "error": str|None}
    """
    is_safe, reason = _validate_sql_safety(sql, readonly=True)
    if is_safe:
        return {"safe": True}
    return {"safe": False, "error": reason}


def sanitize_table_name(table_name: str) -> str:
    """
    清理表名，防止注入
    
    Args:
        table_name: 原始表名
        
    Returns:
        清理后的安全表名
    """
    # 只允许字母、数字、下划线、中文字符
    sanitized = re.sub(r'[^\w\u4e00-\u9fff]', '', table_name)
    return sanitized


# ============================================================
# 自然语言转 SQL
# ============================================================

def natural_language_to_sql(user_id: int, question: str,
                            workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    将自然语言问题转换为 SQL 查询
    
    使用规则匹配的方式将自然语言转换为安全的 SQL 查询
    （生产环境可接入 LLM 增强转换能力）
    
    Args:
        user_id: 用户 ID
        question: 用户的自然语言问题
        
    Returns:
        转换结果（包含 SQL 和解析信息）
    """
    try:
        question = question.strip()
        
        # 1. 获取用户所有表信息
        tables_result = list_tables(user_id, workspace_id)
        if not tables_result["success"] or tables_result["count"] == 0:
            return {
                "success": False,
                "error": "没有可用的记忆表"
            }
        
        tables = tables_result.get("tables", [])
        
        # 2. 识别问题中涉及的表
        matched_table = None
        matched_fields = []
        
        for table_info in tables:
            table_name = table_info.get("table_name", "")
            fields = table_info.get("fields", [])
            field_names = [f.get("name", "") for f in fields]
            
            # 检查表名是否在问题中
            if table_name in question:
                matched_table = table_info
                matched_fields = fields
                break
            
            # 检查字段名是否在问题中
            for fname in field_names:
                if fname in question:
                    matched_table = table_info
                    matched_fields = fields
                    break
            
            if matched_table:
                break
        
        # 如果没有匹配到表，使用第一个表
        if not matched_table and tables:
            matched_table = tables[0]
            matched_fields = matched_table.get("fields", [])
        
        if not matched_table:
            return {
                "success": False,
                "error": "无法识别相关的记忆表"
            }
        
        table_name = matched_table["table_name"]
        field_names = [f.get("name", "") for f in matched_fields]
        actual_table_name = _physical_table(user_id, sanitize_table_name(table_name), workspace_id)
        
        # 3. 解析查询条件
        conditions = []
        select_fields = ["*"]
        order_by = None
        limit = 100
        
        # 3a. 检测聚合函数请求
        count_match = re.search(r'多少|数量|count|总数', question, re.IGNORECASE)
        if count_match:
            select_fields = ["COUNT(*) as total_count"]
        
        # 3b. 检测排序
        if re.search(r'按.*排序|降序|从高到低|从大到小', question):
            for fname in field_names:
                if fname in question:
                    order_by = f'"{fname}" DESC'
                    break
        elif re.search(r'按.*升序|从低到高|从小到大', question):
            for fname in field_names:
                if fname in question:
                    order_by = f'"{fname}" ASC'
                    break
        
        # 3c. 检测限制数量
        limit_match = re.search(r'前(\d+)条|top\s*(\d+)|前(\d+)个', question, re.IGNORECASE)
        if limit_match:
            limit = int(limit_match.group(1) or limit_match.group(2) or limit_match.group(3))
        
        # 3d. 检测等值条件
        # 模式: "状态是进行中" / "状态为进行中" / "status = 进行中"
        for fname in field_names:
            # 中文模式: 字段名 + 是/为/等于 + 值
            patterns = [
                rf'{re.escape(fname)}[是为](.+?)(?:[，,。的]|$)',
                rf'{re.escape(fname)}等于(.+?)(?:[，,。的]|$)',
                rf'{re.escape(fname)}\s*[=：:]\s*(.+?)(?:[，,。的]|$)',
            ]
            for pattern in patterns:
                match = re.search(pattern, question)
                if match:
                    value = match.group(1).strip()
                    conditions.append(f'"{fname}" = \'{value}\'')
                    break
        
        # 3e. 检测 LIKE 条件
        # 模式: "包含XXX" / "像XXX"
        for fname in field_names:
            like_patterns = [
                rf'包含(.+?)(?:的|，|,|。|$)',
                rf'像(.+?)(?:的|，|,|。|$)',
                rf'{re.escape(fname)}包含(.+?)(?:[，,。的]|$)',
            ]
            for pattern in like_patterns:
                match = re.search(pattern, question)
                if match:
                    value = match.group(1).strip()
                    conditions.append(f'"{fname}" LIKE \'%{value}%\'')
                    break
        
        # 3f. 检测状态类查询
        # 模式: "进行中的" / "已完成的" / "计划中的"
        status_match = re.search(r'(进行中|已完成|计划中|待开始|已取消)', question)
        if status_match:
            status_value = status_match.group(1)
            # 找一个可能是状态字段的
            status_fields = [f for f in field_names if 'status' in f.lower() or '状态' in f]
            if status_fields:
                conditions.append(f'"{status_fields[0]}" = \'{status_value}\'')
        
        # 4. 构建 SQL
        select_clause = ', '.join(select_fields)
        sql = f'SELECT {select_clause} FROM "{actual_table_name}"'
        
        if conditions:
            sql += ' WHERE ' + ' AND '.join(conditions)
        
        if order_by:
            sql += f' ORDER BY {order_by}'
        
        sql += f' LIMIT {limit}'
        
        # 5. 安全校验
        safety = validate_sql_safety(sql)
        if not safety["safe"]:
            return {
                "success": False,
                "error": f"SQL 安全校验失败: {safety['error']}",
                "generated_sql": sql
            }
        
        logger.info(f"✓ 自然语言转 SQL 成功: '{question}' -> {sql}")
        
        return {
            "success": True,
            "sql": sql,
            "table_name": table_name,
            "conditions": conditions,
            "select_fields": select_fields,
            "order_by": order_by,
            "limit": limit,
            "parsed_info": {
                "matched_table": table_name,
                "matched_fields": [f.get("name") for f in matched_fields],
                "conditions_count": len(conditions)
            }
        }
        
    except Exception as e:
        logger.error(f"✗ 自然语言转 SQL 失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def execute_safe_query(user_id: int, sql: str) -> Dict[str, Any]:
    """
    安全执行 SQL 查询

    Args:
        user_id: 用户 ID（用于二次验证）
        sql: 经过校验的 SQL 语句

    Returns:
        查询结果
    """
    return execute_confirmed_sql(user_id, sql)


def natural_language_to_sql_only(user_id: int, question: str,
                                 workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    仅将自然语言转换为 SQL，不执行。

    返回生成 SQL 及安全校验结果，用于前端预览确认。

    Args:
        user_id: 用户 ID
        question: 自然语言问题

    Returns:
        {"success": True, "sql": str, "is_safe": bool, "safety_reason": str, ...}
        或 {"success": False, "error": str}
    """
    try:
        nl_result = natural_language_to_sql(user_id, question, workspace_id)
        if not nl_result["success"]:
            return nl_result

        sql = nl_result["sql"]

        # 安全校验（只读）
        is_safe, safety_reason = _validate_sql_safety(sql, readonly=True)

        # EXPLAIN 预检查
        explain_ok, explain_reason = True, "未执行预检查"
        if is_safe:
            db = get_db_client()
            explain_ok, explain_reason = pre_check_sql(sql, db)

        return {
            "success": True,
            "sql": sql,
            "is_safe": is_safe and explain_ok,
            "safety_reason": safety_reason if not is_safe else explain_reason,
            "table_name": nl_result.get("table_name"),
            "parsed_info": nl_result.get("parsed_info", {}),
        }

    except Exception as e:
        logger.error(f"✗ 自然语言转 SQL 预览失败: {e}")
        return {"success": False, "error": str(e)}


def execute_confirmed_sql(user_id: int, sql: str) -> Dict[str, Any]:
    """
    执行用户已确认的 SQL（仍需再次安全校验 + EXPLAIN 预检查 + 用户隔离）。

    Args:
        user_id: 用户 ID
        sql: 用户确认后的 SQL 语句

    Returns:
        查询结果
    """
    try:
        # 1. 安全校验（只读，强制 SELECT）
        is_safe, safety_reason = _validate_sql_safety(sql, readonly=True)
        if not is_safe:
            return {
                "success": False,
                "error": f"SQL 安全校验失败: {safety_reason}"
            }

        # 2. 验证表名包含正确的 user_id 前缀（用户隔离）
        table_pattern = r'"memory_(\d+)_'
        user_ids_in_sql = re.findall(table_pattern, sql)
        for uid in user_ids_in_sql:
            if int(uid) != user_id:
                return {
                    "success": False,
                    "error": "安全违规: SQL 引用了其他用户的数据表"
                }

        # 3. EXPLAIN 预检查
        db = get_db_client()
        explain_ok, explain_reason = pre_check_sql(sql, db)
        if not explain_ok:
            return {
                "success": False,
                "error": f"SQL 执行计划预检查失败: {explain_reason}"
            }

        # 4. 执行查询
        rows = db.execute(sql)

        # 5. 格式化结果
        records = []
        if rows:
            for row in rows:
                records.append(dict(row))

        return {
            "success": True,
            "records": records,
            "count": len(records),
            "sql": sql
        }

    except Exception as e:
        logger.error(f"✗ 安全查询执行失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def natural_language_query(user_id: int, question: str,
                           workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    自然语言查询完整流程
    
    将自然语言转换为 SQL，安全校验后执行，并格式化返回结果
    
    Args:
        user_id: 用户 ID
        question: 用户的自然语言问题
        
    Returns:
        查询结果（包含格式化的记录和解析信息）
    """
    try:
        # 1. 自然语言转 SQL
        nl_result = natural_language_to_sql(user_id, question, workspace_id)
        
        if not nl_result["success"]:
            return nl_result
        
        sql = nl_result["sql"]

        # 2. 安全执行
        exec_result = execute_confirmed_sql(user_id, sql)
        
        if not exec_result["success"]:
            return exec_result
        
        # 3. 格式化结果
        records = exec_result["records"]
        
        # 检测是否是聚合查询
        is_aggregate = 'COUNT' in sql.upper()
        
        if is_aggregate and records:
            # 聚合查询返回简要结果
            formatted = {
                "question": question,
                "answer": f"共 {records[0].get('total_count', 0)} 条记录",
                "sql": sql,
                "parsed_info": nl_result.get("parsed_info", {})
            }
        else:
            # 普通查询返回详细记录
            formatted = {
                "question": question,
                "answer": f"找到 {len(records)} 条相关记录",
                "records": records,
                "count": len(records),
                "sql": sql,
                "parsed_info": nl_result.get("parsed_info", {})
            }
        
        logger.info(f"✓ 自然语言查询完成: '{question}' -> {len(records)} 条结果")
        
        return {
            "success": True,
            **formatted
        }
        
    except Exception as e:
        logger.error(f"✗ 自然语言查询失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def format_query_result(records: List[Dict], question: str) -> str:
    """
    格式化查询结果为自然语言回复
    
    Args:
        records: 查询结果记录列表
        question: 原始问题
        
    Returns:
        格式化的自然语言回复
    """
    if not records:
        return "没有找到相关的记录。"
    
    if len(records) == 1 and 'total_count' in records[0]:
        return f"共有 {records[0]['total_count']} 条记录。"
    
    lines = [f"找到了 {len(records)} 条相关记录：\n"]
    
    for i, record in enumerate(records[:10], 1):  # 最多展示10条
        # 跳过系统字段
        display_fields = {k: v for k, v in record.items() 
                         if not k.startswith('__') and k != 'total_count'}
        
        if display_fields:
            parts = [f"{k}: {v}" for k, v in display_fields.items()]
            lines.append(f"  {i}. {' | '.join(parts)}")
    
    if len(records) > 10:
        lines.append(f"\n  ... 还有 {len(records) - 10} 条记录")
    
    return '\n'.join(lines)


# ============================================================
# 测试函数
# ============================================================

def test_natural_language_query():
    """测试自然语言查询服务"""
    print("\n" + "="*60)
    print("测试自然语言转查询（智能问数）")
    print("="*60 + "\n")
    
    from app.services.memory_table_service import (
        create_memory_table, add_record, batch_add_records, get_db_client
    )
    
    user_id = 999
    
    # 清理
    db = get_db_client()
    db.execute(f'DROP TABLE IF EXISTS "memory_{user_id}_projects"')
    db.execute('DELETE FROM memory_tables WHERE user_id = ? AND table_name = ?', (user_id, "projects"))
    
    # 准备测试数据
    print("0. 准备测试数据...")
    fields = [
        {"name": "project_name", "type": "TEXT"},
        {"name": "负责人", "type": "TEXT"},
        {"name": "status", "type": "TEXT", "index": True}
    ]
    
    create_memory_table(user_id, "projects", fields)
    
    records = [
        {"project_name": "源启·智能体工厂", "负责人": "鑫海", "status": "进行中"},
        {"project_name": "Agent星图", "负责人": "张三", "status": "进行中"},
        {"project_name": "徽商银行AI", "负责人": "李四", "status": "已完成"},
        {"project_name": "健康Dashboard", "负责人": "鑫海", "status": "计划中"},
        {"project_name": "AI写作助手", "负责人": "王五", "status": "已完成"},
    ]
    
    batch_add_records(user_id, "projects", records)
    print(f"   ✓ 插入 {len(records)} 条测试数据\n")
    
    # 测试1: 查询所有记录
    print("1. 测试查询所有记录...")
    question = "我的项目有哪些？"
    result = natural_language_query(user_id, question)
    print(f"   问题: {question}")
    print(f"   SQL: {result.get('sql', 'N/A')}")
    print(f"   结果: {result.get('answer', result.get('error', 'N/A'))}")
    assert result["success"] == True
    print(f"   ✓ 查询成功\n")
    
    # 测试2: 条件查询
    print("2. 测试条件查询...")
    question = "进行中的项目有哪些？"
    result = natural_language_query(user_id, question)
    print(f"   问题: {question}")
    print(f"   SQL: {result.get('sql', 'N/A')}")
    print(f"   结果: {result.get('answer', result.get('error', 'N/A'))}")
    if result["success"] and result.get("records"):
        for r in result["records"]:
            print(f"      - {r.get('project_name', 'N/A')} | {r.get('负责人', 'N/A')} | {r.get('status', 'N/A')}")
    assert result["success"] == True
    print(f"   ✓ 条件查询成功\n")
    
    # 测试3: 聚合查询
    print("3. 测试聚合查询...")
    question = "我有多少个项目？"
    result = natural_language_query(user_id, question)
    print(f"   问题: {question}")
    print(f"   SQL: {result.get('sql', 'N/A')}")
    print(f"   结果: {result.get('answer', result.get('error', 'N/A'))}")
    assert result["success"] == True
    print(f"   ✓ 聚合查询成功\n")
    
    # 测试4: SQL 安全校验
    print("4. 测试 SQL 安全校验...")
    # 测试禁止的关键字
    dangerous_sql = "DROP TABLE memory_999_projects"
    safety = validate_sql_safety(dangerous_sql)
    print(f"   危险 SQL: {dangerous_sql}")
    print(f"   校验结果: safe={safety['safe']}, error={safety.get('error', 'N/A')}")
    assert safety["safe"] == False
    print(f"   ✓ 正确拒绝了危险 SQL\n")
    
    # 测试注入
    injection_sql = "SELECT * FROM memory_999_projects; DROP TABLE users; --"
    safety = validate_sql_safety(injection_sql)
    print(f"   注入 SQL: {injection_sql}")
    print(f"   校验结果: safe={safety['safe']}, error={safety.get('error', 'N/A')}")
    assert safety["safe"] == False
    print(f"   ✓ 正确拒绝了 SQL 注入\n")
    
    # 测试5: 限制数量
    print("5. 测试限制数量...")
    question = "前2条项目记录"
    result = natural_language_query(user_id, question)
    print(f"   问题: {question}")
    print(f"   SQL: {result.get('sql', 'N/A')}")
    print(f"   结果: {result.get('answer', result.get('error', 'N/A'))}")
    assert result["success"] == True
    if result.get("records"):
        assert len(result["records"]) <= 2
        print(f"   ✓ 限制数量查询成功\n")
    
    # 测试6: 格式化结果
    print("6. 测试结果格式化...")
    query_result = execute_safe_query(user_id, f'SELECT * FROM "memory_{user_id}_projects" LIMIT 3')
    if query_result["success"]:
        formatted = format_query_result(query_result["records"], "测试")
        print(f"   格式化结果:\n{formatted}")
    print(f"   ✓ 结果格式化成功\n")
    
    # 清理
    print("7. 清理测试数据...")
    db.execute(f'DROP TABLE IF EXISTS "memory_{user_id}_projects"')
    db.execute('DELETE FROM memory_tables WHERE user_id = ? AND table_name = ?', (user_id, "projects"))
    print(f"   ✓ 清理完成")
    
    print("\n" + "="*60)
    print("✅ 自然语言转查询测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    test_natural_language_query()
