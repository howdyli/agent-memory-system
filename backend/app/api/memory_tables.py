"""
记忆表 API 路由（动态表结构）
"""
import logging
import fastapi as _fastapi
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, Any, Dict, List

# 导入服务
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.memory_table_service import (
    create_memory_table,
    add_record,
    query_records,
    update_record,
    delete_record,
    list_tables,
    get_table_info,
    parse_natural_language_to_table,
    batch_add_records,
    batch_update_records,
    drop_table,
    query_records_with_filters
)
from app.services.natural_language_query_service import (
    natural_language_query,
    natural_language_to_sql_only,
    execute_confirmed_sql,
)
from app.core.auth import Principal, get_current_principal
from app.core.errors import AppException, NotFoundError, ValidationError
from app.core.rbac import Perm, require_permission

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-tables"])


# 请求模型
class CreateTableRequest(BaseModel):
    table_name: str
    fields: List[Dict[str, str]]  # [{"name": "field1", "type": "TEXT"}, ...]


class AddRecordRequest(BaseModel):
    record: Dict[str, Any]


class QueryRecordsRequest(BaseModel):
    filters: Optional[Dict[str, Any]] = None
    limit: Optional[int] = 100
    offset: Optional[int] = 0


class UpdateRecordRequest(BaseModel):
    updates: Dict[str, Any]


class DeleteRecordRequest(BaseModel):
    record_id: int


class ParseNaturalLanguageRequest(BaseModel):
    user_input: str


class BatchAddRecordsRequest(BaseModel):
    records: List[Dict[str, Any]]


class BatchUpdateRecordsRequest(BaseModel):
    updates_list: List[Dict[str, Any]]  # [{"record_id": 1, "updates": {...}}, ...]


class QueryWithFiltersRequest(BaseModel):
    filters: Optional[Dict[str, Any]] = None
    sort_by: Optional[str] = None
    sort_order: Optional[str] = "ASC"
    limit: Optional[int] = 100
    offset: Optional[int] = 0


class NaturalLanguageQueryRequest(BaseModel):
    question: str


class NaturalLanguageToSqlRequest(BaseModel):
    question: str


class ExecuteSqlRequest(BaseModel):
    sql: str


# API 路由
@router.post("/")
async def create_table(
    request: CreateTableRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    创建记忆表（动态表结构）
    
    Args:
        request: 创建表请求（table_name, fields）
        current_user: 当前登录用户
        
    Returns:
        创建结果
    """
    try:
        result = create_memory_table(
            user_id=principal.user_id,
            table_name=request.table_name,
            fields=request.fields
        )
        
        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to create table"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 创建表失败: {e}")
        raise AppException(str(e))


@router.post("/{table_name}/records")
async def add_record_api(
    table_name: str,
    request: AddRecordRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    向记忆表添加记录
    
    Args:
        table_name: 表名
        request: 添加记录请求（record）
        current_user: 当前登录用户
        
    Returns:
        添加结果（包含新记录的 ID）
    """
    try:
        result = add_record(
            user_id=principal.user_id,
            table_name=table_name,
            record=request.record
        )
        
        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to add record"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 添加记录失败: {e}")
        raise AppException(str(e))


@router.get("/{table_name}/records")
async def query_records_api(
    table_name: str,
    limit: Optional[int] = 100,
    offset: Optional[int] = 0,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """
    查询记忆表中的记录
    
    Args:
        table_name: 表名
        limit: 返回记录数限制
        offset: 偏移量（用于分页）
        current_user: 当前登录用户
        
    Returns:
        查询结果（包含记录列表）
    """
    try:
        result = query_records(
            user_id=principal.user_id,
            table_name=table_name,
            limit=limit,
            offset=offset
        )
        
        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to query records"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 查询记录失败: {e}")
        raise AppException(str(e))


@router.put("/{table_name}/records")
async def update_record_api(
    table_name: str,
    record_id: int,
    request: UpdateRecordRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    更新记忆表中的记录
    
    Args:
        table_name: 表名
        record_id: 记录ID
        request: 更新记录请求（updates）
        current_user: 当前登录用户
        
    Returns:
        更新结果
    """
    try:
        result = update_record(
            user_id=principal.user_id,
            table_name=table_name,
            record_id=record_id,
            updates=request.updates
        )
        
        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to update record"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 更新记录失败: {e}")
        raise AppException(str(e))


@router.delete("/{table_name}/records")
async def delete_record_api(
    table_name: str,
    record_id: int,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    删除记忆表中的记录
    
    Args:
        table_name: 表名
        record_id: 记录ID
        current_user: 当前登录用户
        
    Returns:
        删除结果
    """
    try:
        result = delete_record(
            user_id=principal.user_id,
            table_name=table_name,
            record_id=record_id
        )
        
        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to delete record"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 删除记录失败: {e}")
        raise AppException(str(e))


@router.get("/")
async def list_tables_api(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """
    列出用户创建的所有记忆表
    
    Args:
        current_user: 当前登录用户
        
    Returns:
        表列表
    """
    try:
        result = list_tables(
            user_id=principal.user_id
        )
        
        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to list tables"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 列出表失败: {e}")
        raise AppException(str(e))


@router.post("/parse")
async def parse_natural_language(
    request: ParseNaturalLanguageRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """
    从自然语言输入中解析表结构和数据
    
    Args:
        request: 解析自然语言请求（user_input）
        current_user: 当前登录用户
        
    Returns:
        解析结果（包含表结构和数据）
    """
    try:
        parsed = parse_natural_language_to_table(request.user_input)
        
        if parsed:
            return {
                "success": True,
                "parsed": parsed,
                "message": "Natural language parsed successfully"
            }
        else:
            return {
                "success": False,
                "message": "Failed to parse natural language"
            }
            
    except Exception as e:
        logger.error(f"✗ 解析自然语言失败: {e}")
        raise AppException(str(e))


@router.post("/{table_name}/records/batch")
async def batch_add_records_api(
    table_name: str,
    request: BatchAddRecordsRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    批量添加记录到记忆表
    
    Args:
        table_name: 表名
        request: 批量添加记录请求（records 列表）
        current_user: 当前登录用户
        
    Returns:
        批量添加结果（包含成功和失败的记录）
    """
    try:
        result = batch_add_records(
            user_id=principal.user_id,
            table_name=table_name,
            records=request.records
        )
        
        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to batch add records"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 批量添加记录失败: {e}")
        raise AppException(str(e))


@router.put("/{table_name}/records/batch")
async def batch_update_records_api(
    table_name: str,
    request: BatchUpdateRecordsRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    批量更新记忆表中的记录
    
    Args:
        table_name: 表名
        request: 批量更新记录请求（updates_list）
        current_user: 当前登录用户
        
    Returns:
        批量更新结果（包含成功和失败的记录）
    """
    try:
        result = batch_update_records(
            user_id=principal.user_id,
            table_name=table_name,
            updates_list=request.updates_list
        )
        
        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to batch update records"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 批量更新记录失败: {e}")
        raise AppException(str(e))


@router.delete("/{table_name}")
async def drop_table_api(
    table_name: str,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    删除整个记忆表（包括表结构和所有数据）
    
    Args:
        table_name: 表名
        current_user: 当前登录用户
        
    Returns:
        删除结果
    """
    try:
        result = drop_table(
            user_id=principal.user_id,
            table_name=table_name
        )
        
        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to drop table"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 删除表失败: {e}")
        raise AppException(str(e))


@router.get("/{table_name}/info")
async def get_table_info_api(
    table_name: str,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """
    获取记忆表的详细信息（包括表结构、字段定义等）
    
    Args:
        table_name: 表名
        current_user: 当前登录用户
        
    Returns:
        表信息
    """
    try:
        table_info = get_table_info(
            user_id=principal.user_id,
            table_name=table_name
        )
        
        if table_info:
            return {
                "success": True,
                "table_info": table_info
            }
        else:
            raise NotFoundError(f"Table '{table_name}' not found")
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 获取表信息失败: {e}")
        raise AppException(str(e))


@router.post("/{table_name}/query")
async def query_with_filters_api(
    table_name: str,
    request: QueryWithFiltersRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """
    带过滤条件的查询记忆表记录
    
    支持按字段过滤、排序、分页
    
    Args:
        table_name: 表名
        request: 查询请求（filters, sort_by, sort_order, limit, offset）
        current_user: 当前登录用户
        
    Returns:
        查询结果（包含记录列表和总数）
    """
    try:
        result = query_records_with_filters(
            user_id=principal.user_id,
            table_name=table_name,
            filters=request.filters,
            sort_by=request.sort_by,
            sort_order=request.sort_order or "ASC",
            limit=request.limit or 100,
            offset=request.offset or 0
        )
        
        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to query records"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 过滤查询失败: {e}")
        raise AppException(str(e))


@router.post("/nl-query")
async def natural_language_query_api(
    request: NaturalLanguageQueryRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """
    自然语言查询（智能问数）
    
    将用户的自然语言问题转换为 SQL 查询并执行
    
    Args:
        request: 自然语言查询请求（question）
        current_user: 当前登录用户
        
    Returns:
        查询结果（包含自然语言回复和匹配的记录）
    """
    try:
        result = natural_language_query(
            user_id=principal.user_id,
            question=request.question
        )
        
        if result["success"]:
            return result
        else:
            # 查询失败不返回 500，而是返回 400 和错误信息
            raise ValidationError(result.get("error", "Failed to process natural language query"))
            
    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 自然语言查询失败: {e}")
        raise AppException(str(e))


@router.post("/{table_name}/nl-to-sql")
async def natural_language_to_sql_api(
    table_name: str,
    request: NaturalLanguageToSqlRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """
    自然语言转 SQL（仅生成，不执行）

    将用户问题转换为 SQL 并返回安全校验结果，供前端预览确认。

    Args:
        table_name: 目标表名（当前用于上下文，SQL 生成仍会自动匹配）
        request: 自然语言问题
        current_user: 当前登录用户

    Returns:
        {"success": True, "sql": str, "is_safe": bool, "safety_reason": str}
    """
    try:
        # 校验表存在性，同时让错误提示更清晰
        table_info = get_table_info(
            user_id=principal.user_id,
            table_name=table_name
        )
        if not table_info:
            raise NotFoundError(f"Table '{table_name}' not found")

        result = natural_language_to_sql_only(
            user_id=principal.user_id,
            question=request.question
        )

        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to generate SQL"))

    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 自然语言转 SQL 失败: {e}")
        raise AppException(str(e))


@router.post("/{table_name}/execute-sql")
async def execute_sql_api(
    table_name: str,
    request: ExecuteSqlRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    执行已确认的 SQL（仍需再次安全校验）

    前端预览确认后，调用此端点真正执行 SQL 并返回查询结果。

    Args:
        table_name: 目标表名
        request: 已确认的 SQL
        current_user: 当前登录用户

    Returns:
        查询结果
    """
    try:
        # 校验表存在性
        table_info = get_table_info(
            user_id=principal.user_id,
            table_name=table_name
        )
        if not table_info:
            raise NotFoundError(f"Table '{table_name}' not found")

        result = execute_confirmed_sql(
            user_id=principal.user_id,
            sql=request.sql
        )

        if result["success"]:
            return result
        else:
            raise ValidationError(result.get("error", "Failed to execute SQL"))

    except AppException:
        raise
    except Exception as e:
        logger.error(f"✗ 执行确认 SQL 失败: {e}")
        raise AppException(str(e))


# 测试函数
def test_memory_table_api():
    """测试记忆表 API（模拟）"""
    print("\n" + "="*60)
    print("测试记忆表服务（动态表结构）")
    print("="*60 + "\n")
    
    user_id = 999  # 测试用户ID
    
    # 测试1：创建记忆表
    print("1. 测试创建记忆表...")
    fields = [
        {"name": "project_name", "type": "TEXT"},
        {"name": "负责人", "type": "TEXT"},
        {"name": "status", "type": "TEXT", "index": True}
    ]
    
    result = create_memory_table(user_id, "projects", fields)
    print(f"   创建结果：{result['success']}")
    assert result["success"] == True
    print(f"   ✓ 创建记忆表成功")
    
    # 测试2：添加记录
    print(f"\n2. 测试添加记录...")
    record = {
        "project_name": "源启·智能体工厂",
        "负责人": "鑫海",
        "status": "进行中"
    }
    
    result = add_record(user_id, "projects", record)
    print(f"   添加结果：{result['success']}")
    assert result["success"] == True
    record_id = result["record_id"]
    print(f"   ✓ 添加记录成功，ID：{record_id}")
    
    # 测试3：查询记录
    print(f"\n3. 测试查询记录...")
    result = query_records(user_id, "projects")
    print(f"   查询结果：{result['success']}，记录数：{result['count']}")
    assert result["success"] == True
    assert result["count"] >= 1
    print(f"   ✓ 查询记录成功")
    
    # 测试4：更新记录
    print(f"\n4. 测试更新记录...")
    updates = {
        "status": "已完成"
    }
    
    result = update_record(user_id, "projects", record_id, updates)
    print(f"   更新结果：{result['success']}")
    assert result["success"] == True
    print(f"   ✓ 更新记录成功")
    
    # 测试5：删除记录
    print(f"\n5. 测试删除记录...")
    result = delete_record(user_id, "projects", record_id)
    print(f"   删除结果：{result['success']}")
    assert result["success"] == True
    print(f"   ✓ 删除记录成功")
    
    # 测试6：列出所有表
    print(f"\n6. 测试列出所有表...")
    result = list_tables(user_id)
    print(f"   列表结果：{result['success']}，表数：{result['count']}")
    assert result["success"] == True
    print(f"   ✓ 列出所有表成功")
    
    # 测试7：从自然语言解析表结构
    print(f"\n7. 测试从自然语言解析表结构...")
    user_input = "记录我的项目：源启·智能体工厂，负责人：鑫海，状态：进行中"
    parsed = parse_natural_language_to_table(user_input)
    
    print(f"   用户输入：{user_input}")
    print(f"   解析结果：{parsed}")
    assert parsed is not None
    assert "table_name" in parsed
    print(f"   ✓ 解析自然语言成功")
    
    # 清理测试数据
    print(f"\n8. 清理测试数据...")
    db = get_db_client()
    db.execute(f'DROP TABLE IF EXISTS "memory_{user_id}_projects"')
    db.execute('DELETE FROM memory_tables WHERE user_id = ?', (user_id,))
    print(f"   ✓ 清理完成")
    
    print("\n" + "="*60)
    print("✅ 记忆表 API 测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 运行测试
    test_memory_table_api()
