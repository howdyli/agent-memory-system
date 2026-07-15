"""
记忆表服务（动态表结构）

实现 Schema-less 动态表结构定义和存储
"""
import logging
import json
import re
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

# 导入数据库客户端
from app.core.db_client import get_db_client


def create_memory_table(user_id: int,
                        table_name: str,
                        fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    创建记忆表（动态表结构）
    
    Args:
        user_id: 用户ID
        table_name: 表名
        fields: 字段定义列表（格式：[{"name": "field1", "type": "TEXT", "index": True, "nullable": False}]）
               支持的类型：TEXT, INTEGER, REAL, BOOLEAN, DATE, DATETIME, JSON
        
    Returns:
        创建结果字典
    """
    try:
        db = get_db_client()
        
        # 0. 验证字段定义
        validated_fields = []
        for field in fields:
            field_name = field.get("name")
            field_type = field.get("type", "TEXT").upper()
            
            # 验证类型是否支持
            supported_types = ["TEXT", "INTEGER", "REAL", "BOOLEAN", "DATE", "DATETIME", "JSON"]
            if field_type not in supported_types:
                logger.warning(f"⚠️  不支持的字段类型 '{field_type}'，将使用 TEXT")
                field_type = "TEXT"
            
            validated_fields.append({
                "name": field_name,
                "type": field_type,
                "index": field.get("index", False),
                "nullable": field.get("nullable", True),
                "default": field.get("default", None)
            })
        
        # 1. 将字段定义存储到 memory_tables 表
        table_schema = {
            "fields": validated_fields,
            "created_at": datetime.now().isoformat(),
            "version": 1
        }
        
        db.create_memory_table(
            user_id=user_id,
            table_name=table_name,
            schema=table_schema
        )
        
        # 2. 动态创建实际的数据表（如果不存在）
        # 使用 SQLite 动态 SQL
        field_defs = []
        for field in validated_fields:
            field_name = field.get("name")
            field_type = field.get("type")
            nullable = "NOT NULL" if not field.get("nullable", True) else ""
            default = f"DEFAULT {field.get('default')}" if field.get("default") is not None else ""
            
            field_defs.append(f'"{field_name}" {field_type} {nullable} {default}'.strip())
        
        # 添加元数据字段
        field_defs.append('"__id__" INTEGER PRIMARY KEY AUTOINCREMENT')
        field_defs.append('"__created_at__" TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
        field_defs.append('"__updated_at__" TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
        
        create_table_sql = f'''
            CREATE TABLE IF NOT EXISTS "memory_{user_id}_{table_name}" (
                {', '.join(field_defs)}
            )
        '''
        
        db.execute(create_table_sql)
        
        # 3. 创建索引（如果有指定索引字段）
        index_fields = [f.get("name") for f in validated_fields if f.get("index")]
        for index_field in index_fields:
            index_name = f"idx_{user_id}_{table_name}_{index_field}"
            create_index_sql = f'''
                CREATE INDEX IF NOT EXISTS "{index_name}" 
                ON "memory_{user_id}_{table_name}" ("{index_field}")
            '''
            db.execute(create_index_sql)
        
        logger.info(f"✓ 创建记忆表：{table_name}，字段数：{len(validated_fields)}")
        
        return {
            "success": True,
            "table_name": table_name,
            "fields": validated_fields,
            "message": f"Table '{table_name}' created successfully"
        }
        
    except Exception as e:
        logger.error(f"✗ 创建记忆表失败：{e}")
        return {
            "success": False,
            "error": str(e)
        }


def add_record(user_id: int,
               table_name: str,
               record: Dict[str, Any],
               validate_types: bool = True) -> Dict[str, Any]:
    """
    向记忆表添加记录
    
    Args:
        user_id: 用户ID
        table_name: 表名
        record: 记录数据（字典，key 为字段名）
        validate_types: 是否验证数据类型（默认 True）
        
    Returns:
        添加结果字典（包含新记录的 ID）
    """
    try:
        db = get_db_client()
        
        # 1. 获取表结构
        table_info = get_table_info(user_id, table_name)
        if not table_info:
            return {
                "success": False,
                "error": f"Table '{table_name}' not found"
            }
        
        fields = table_info.get("fields", [])
        field_names = [f.get("name") for f in fields]
        
        # 2. 验证记录数据
        for key in record.keys():
            if key not in field_names:
                logger.warning(f"⚠️  字段 '{key}' 不在表结构中，将忽略")
        
        # 3. 类型校验（如果启用）
        validated_record = {}
        if validate_types:
            for key, value in record.items():
                if key not in field_names:
                    continue
                
                field_def = next((f for f in fields if f.get("name") == key), None)
                if field_def:
                    field_type = field_def.get("type", "TEXT").upper()
                    
                    try:
                        if field_type == "INTEGER":
                            validated_record[key] = int(value)
                        elif field_type == "REAL":
                            validated_record[key] = float(value)
                        elif field_type == "BOOLEAN":
                            validated_record[key] = bool(value)
                        elif field_type == "DATE":
                            validated_record[key] = str(value)
                        elif field_type == "DATETIME":
                            validated_record[key] = str(value)
                        elif field_type == "JSON":
                            if isinstance(value, str):
                                json.loads(value)
                                validated_record[key] = value
                            else:
                                validated_record[key] = json.dumps(value)
                        else:
                            validated_record[key] = str(value)
                    except (ValueError, json.JSONDecodeError) as e:
                        return {
                            "success": False,
                            "error": f"Type validation failed for field '{key}': {str(e)}"
                        }
                else:
                    validated_record[key] = value
        else:
            validated_record = record
        
        valid_keys = [k for k in validated_record.keys() if k in field_names]
        if not valid_keys:
            return {
                "success": False,
                "error": "No valid fields to insert"
            }
        
        # 4. 构建 INSERT SQL
        columns = ', '.join([f'"{k}"' for k in valid_keys])
        placeholders = ', '.join(['?'] * len(valid_keys))
        
        insert_sql = f'''
            INSERT INTO "memory_{user_id}_{table_name}" ({columns})
            VALUES ({placeholders})
        '''
        
        # 参数
        params = tuple(validated_record.get(k) for k in valid_keys)
        
        # 执行
        record_id = db.execute(insert_sql, params)
        
        logger.info(f"✓ 添加记录到表 {table_name}，记录 ID：{record_id}")
        
        return {
            "success": True,
            "record_id": record_id,
            "message": f"Record added to '{table_name}' successfully"
        }
        
    except Exception as e:
        logger.error(f"✗ 添加记录失败：{e}")
        return {
            "success": False,
            "error": str(e)
        }


def query_records(user_id: int,
                  table_name: str,
                  filters: Optional[Dict[str, Any]] = None,
                  limit: int = 100,
                  offset: int = 0) -> Dict[str, Any]:
    """
    查询记忆表中的记录
    
    Args:
        user_id: 用户ID
        table_name: 表名
        filters: 过滤条件（字典，key 为字段名，value 为过滤值）
        limit: 返回记录数限制
        offset: 偏移量（用于分页）
        
    Returns:
        查询结果字典（包含记录列表）
    """
    try:
        db = get_db_client()
        
        # 1. 构建 WHERE 子句
        where_clause = ""
        params = ()
        
        if filters:
            conditions = []
            for key, value in filters.items():
                conditions.append(f'"{key}" = ?')
                params += (value,)
            
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)
        
        # 2. 构建 SELECT SQL
        select_sql = f'''
            SELECT * FROM "memory_{user_id}_{table_name}"
            {where_clause}
            ORDER BY "__id__" DESC
            LIMIT ? OFFSET ?
        '''
        
        params += (limit, offset)
        
        # 执行查询
        rows = db.execute(select_sql, params)
        
        # 3. 转换为字典列表，将 __id__ 映射为 id
        result = []
        if rows:
            for row in rows:
                record = dict(row)
                record["id"] = record.pop("__id__")
                record.pop("__created_at__", None)
                record.pop("__updated_at__", None)
                result.append(record)
        
        logger.info(f"✓ 查询表 {table_name}，返回 {len(result)} 条记录")
        
        return {
            "success": True,
            "records": result,
            "count": len(result),
            "table_name": table_name
        }
        
    except Exception as e:
        logger.error(f"✗ 查询记录失败：{e}")
        return {
            "success": False,
            "error": str(e)
        }


def update_record(user_id: int,
                  table_name: str,
                  record_id: int,
                  updates: Dict[str, Any],
                  validate_types: bool = True) -> Dict[str, Any]:
    """
    更新记忆表中的记录
    
    Args:
        user_id: 用户ID
        table_name: 表名
        record_id: 记录ID（__id__ 字段）
        updates: 要更新的字段和值（字典）
        validate_types: 是否验证数据类型（默认 True）
        
    Returns:
        更新结果字典
    """
    try:
        db = get_db_client()
        
        # 1. 获取表结构
        table_info = get_table_info(user_id, table_name)
        if not table_info:
            return {
                "success": False,
                "error": f"Table '{table_name}' not found"
            }
        
        fields = table_info.get("fields", [])
        field_names = [f.get("name") for f in fields]
        
        # 2. 验证更新数据
        valid_keys = [k for k in updates.keys() if k in field_names]
        if not valid_keys:
            return {
                "success": False,
                "error": "No valid fields to update"
            }
        
        # 3. 类型校验（如果启用）
        validated_updates = {}
        if validate_types:
            for key in valid_keys:
                value = updates.get(key)
                field_def = next((f for f in fields if f.get("name") == key), None)
                
                if field_def:
                    field_type = field_def.get("type", "TEXT").upper()
                    
                    try:
                        if field_type == "INTEGER":
                            validated_updates[key] = int(value)
                        elif field_type == "REAL":
                            validated_updates[key] = float(value)
                        elif field_type == "BOOLEAN":
                            validated_updates[key] = bool(value)
                        elif field_type == "DATE":
                            validated_updates[key] = str(value)
                        elif field_type == "DATETIME":
                            validated_updates[key] = str(value)
                        elif field_type == "JSON":
                            if isinstance(value, str):
                                json.loads(value)
                                validated_updates[key] = value
                            else:
                                validated_updates[key] = json.dumps(value)
                        else:
                            validated_updates[key] = str(value)
                    except (ValueError, json.JSONDecodeError) as e:
                        return {
                            "success": False,
                            "error": f"Type validation failed for field '{key}': {str(e)}"
                        }
                else:
                    validated_updates[key] = value
        else:
            validated_updates = {k: updates.get(k) for k in valid_keys}
        
        # 4. 构建 UPDATE SQL
        set_clause = ', '.join([f'"{k}" = ?' for k in validated_updates.keys()])
        update_sql = f'''
            UPDATE "memory_{user_id}_{table_name}"
            SET {set_clause}, "__updated_at__" = CURRENT_TIMESTAMP
            WHERE "__id__" = ?
        '''
        
        # 参数
        params = tuple(validated_updates.get(k) for k in validated_updates.keys()) + (record_id,)
        
        # 执行
        db.execute(update_sql, params)
        
        logger.info(f"✓ 更新表 {table_name} 的记录 ID：{record_id}")
        
        return {
            "success": True,
            "record_id": record_id,
            "message": f"Record {record_id} in '{table_name}' updated successfully"
        }
        
    except Exception as e:
        logger.error(f"✗ 更新记录失败：{e}")
        return {
            "success": False,
            "error": str(e)
        }


def delete_record(user_id: int,
                  table_name: str,
                  record_id: int) -> Dict[str, Any]:
    """
    删除记忆表中的记录
    
    Args:
        user_id: 用户ID
        table_name: 表名
        record_id: 记录ID（__id__ 字段）
        
    Returns:
        删除结果字典
    """
    try:
        db = get_db_client()
        
        # 构建 DELETE SQL
        delete_sql = f'''
            DELETE FROM "memory_{user_id}_{table_name}"
            WHERE "__id__" = ?
        '''
        
        # 执行
        db.execute(delete_sql, (record_id,))
        
        logger.info(f"✓ 删除表 {table_name} 的记录 ID：{record_id}")
        
        return {
            "success": True,
            "record_id": record_id,
            "message": f"Record {record_id} in '{table_name}' deleted successfully"
        }
        
    except Exception as e:
        logger.error(f"✗ 删除记录失败：{e}")
        return {
            "success": False,
            "error": str(e)
        }


def batch_add_records(user_id: int,
                     table_name: str,
                     records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    批量添加记录到记忆表
    
    Args:
        user_id: 用户ID
        table_name: 表名
        records: 记录数据列表（每个元素是一个字典，key 为字段名）
        
    Returns:
        添加结果字典（包含成功添加的记录和失败的记录）
    """
    try:
        if not records:
            return {
                "success": False,
                "error": "No records to insert"
            }
        
        db = get_db_client()
        
        # 1. 获取表结构
        table_info = get_table_info(user_id, table_name)
        if not table_info:
            return {
                "success": False,
                "error": f"Table '{table_name}' not found"
            }
        
        fields = table_info.get("fields", [])
        field_names = [f.get("name") for f in fields]
        
        # 2. 验证并构建所有记录的 SQL
        valid_records = []
        failed_records = []
        
        for idx, record in enumerate(records):
            # 验证记录数据
            valid_keys = [k for k in record.keys() if k in field_names]
            
            if not valid_keys:
                failed_records.append({
                    "index": idx,
                    "error": "No valid fields to insert",
                    "record": record
                })
                continue
            
            # 类型校验
            validated_record = {}
            for key in valid_keys:
                value = record.get(key)
                field_def = next((f for f in fields if f.get("name") == key), None)
                
                if field_def:
                    field_type = field_def.get("type", "TEXT").upper()
                    
                    # 类型转换和校验
                    try:
                        if field_type == "INTEGER":
                            validated_record[key] = int(value)
                        elif field_type == "REAL":
                            validated_record[key] = float(value)
                        elif field_type == "BOOLEAN":
                            validated_record[key] = bool(value)
                        elif field_type == "DATE":
                            # 尝试解析日期
                            validated_record[key] = str(value)
                        elif field_type == "JSON":
                            # 如果是字符串，尝试解析为 JSON
                            if isinstance(value, str):
                                json.loads(value)  # 验证是否为有效 JSON
                            validated_record[key] = json.dumps(value) if not isinstance(value, str) else value
                        else:
                            validated_record[key] = str(value)
                    except (ValueError, json.JSONDecodeError) as e:
                        failed_records.append({
                            "index": idx,
                            "error": f"Type validation failed for field '{key}': {str(e)}",
                            "record": record
                        })
                        break
                else:
                    validated_record[key] = value
            
            if "error" not in (failed_records[-1] if failed_records else {}):
                valid_records.append({
                    "keys": [k for k in validated_record.keys() if k in field_names],
                    "values": [validated_record.get(k) for k in validated_record.keys() if k in field_names]
                })
        
        if not valid_records:
            return {
                "success": False,
                "error": "No valid records to insert",
                "failed_records": failed_records
            }
        
        # 3. 构建批量 INSERT SQL
        # 使用第一种记录的字段作为标准（假设所有记录的字段类似）
        all_columns = []
        all_placeholders = []
        all_params = []
        
        for record_data in valid_records:
            columns = ', '.join([f'"{k}"' for k in record_data["keys"]])
            placeholders = ', '.join(['?'] * len(record_data["keys"]))
            
            if columns not in all_columns:
                all_columns.append(columns)
                all_placeholders.append(placeholders)
            
            all_params.extend(record_data["values"])
        
        # 如果所有记录字段相同，使用单条 SQL 批量插入
        if len(set(all_columns)) == 1:
            columns = all_columns[0]
            placeholder_template = f'({all_placeholders[0]})'
            placeholders = ', '.join([placeholder_template] * len(valid_records))
            
            insert_sql = f'''
                INSERT INTO "memory_{user_id}_{table_name}" ({columns})
                VALUES {placeholders}
            '''
            
            # 执行批量插入
            record_ids = db.execute(insert_sql, tuple(all_params))
        else:
            # 如果字段不同，逐条插入
            record_ids = []
            for record_data in valid_records:
                columns = ', '.join([f'"{k}"' for k in record_data["keys"]])
                placeholders = ', '.join(['?'] * len(record_data["keys"]))
                
                insert_sql = f'''
                    INSERT INTO "memory_{user_id}_{table_name}" ({columns})
                    VALUES ({placeholders})
                '''
                
                record_id = db.execute(insert_sql, tuple(record_data["values"]))
                record_ids.append(record_id)
        
        logger.info(f"✓ 批量添加 {len(valid_records)} 条记录到表 {table_name}")
        
        return {
            "success": True,
            "inserted_count": len(valid_records),
            "record_ids": record_ids if isinstance(record_ids, list) else [record_ids],
            "failed_count": len(failed_records),
            "failed_records": failed_records,
            "message": f"Batch inserted {len(valid_records)} records into '{table_name}' successfully"
        }
        
    except Exception as e:
        logger.error(f"✗ 批量添加记录失败：{e}")
        return {
            "success": False,
            "error": str(e)
        }


def batch_update_records(user_id: int,
                        table_name: str,
                        updates_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    批量更新记忆表中的记录
    
    Args:
        user_id: 用户ID
        table_name: 表名
        updates_list: 更新列表（每个元素是一个字典，包含 'record_id' 和 'updates'）
                    格式：[{"record_id": 1, "updates": {"field1": "value1"}}, ...]
        
    Returns:
        更新结果字典（包含成功更新的记录和失败的记录）
    """
    try:
        if not updates_list:
            return {
                "success": False,
                "error": "No records to update"
            }
        
        db = get_db_client()
        
        # 1. 获取表结构
        table_info = get_table_info(user_id, table_name)
        if not table_info:
            return {
                "success": False,
                "error": f"Table '{table_name}' not found"
            }
        
        fields = table_info.get("fields", [])
        field_names = [f.get("name") for f in fields]
        
        # 2. 验证并构建所有更新
        successful_updates = []
        failed_updates = []
        
        for item in updates_list:
            record_id = item.get("record_id")
            updates = item.get("updates", {})
            
            if not record_id:
                failed_updates.append({
                    "item": item,
                    "error": "Missing 'record_id'"
                })
                continue
            
            if not updates:
                failed_updates.append({
                    "record_id": record_id,
                    "error": "No valid fields to update"
                })
                continue
            
            # 验证更新数据
            valid_keys = [k for k in updates.keys() if k in field_names]
            
            if not valid_keys:
                failed_updates.append({
                    "record_id": record_id,
                    "error": "No valid fields to update",
                    "updates": updates
                })
                continue
            
            # 类型校验
            validated_updates = {}
            type_error = False
            
            for key in valid_keys:
                value = updates.get(key)
                field_def = next((f for f in fields if f.get("name") == key), None)
                
                if field_def:
                    field_type = field_def.get("type", "TEXT").upper()
                    
                    # 类型转换和校验
                    try:
                        if field_type == "INTEGER":
                            validated_updates[key] = int(value)
                        elif field_type == "REAL":
                            validated_updates[key] = float(value)
                        elif field_type == "BOOLEAN":
                            validated_updates[key] = bool(value)
                        elif field_type == "DATE":
                            validated_updates[key] = str(value)
                        elif field_type == "JSON":
                            if isinstance(value, str):
                                json.loads(value)
                            validated_updates[key] = json.dumps(value) if not isinstance(value, str) else value
                        else:
                            validated_updates[key] = str(value)
                    except (ValueError, json.JSONDecodeError) as e:
                        failed_updates.append({
                            "record_id": record_id,
                            "error": f"Type validation failed for field '{key}': {str(e)}",
                            "updates": updates
                        })
                        type_error = True
                        break
            
            if type_error:
                continue
            
            # 构建 UPDATE SQL
            set_clause = ', '.join([f'"{k}" = ?' for k in validated_updates.keys()])
            update_sql = f'''
                UPDATE "memory_{user_id}_{table_name}"
                SET {set_clause}, "__updated_at__" = CURRENT_TIMESTAMP
                WHERE "__id__" = ?
            '''
            
            params = tuple(validated_updates.values()) + (record_id,)
            
            # 执行更新
            db.execute(update_sql, params)
            
            successful_updates.append({
                "record_id": record_id,
                "updated_fields": list(validated_updates.keys())
            })
        
        logger.info(f"✓ 批量更新 {len(successful_updates)} 条记录在表 {table_name}")
        
        return {
            "success": True,
            "updated_count": len(successful_updates),
            "successful_updates": successful_updates,
            "failed_count": len(failed_updates),
            "failed_updates": failed_updates,
            "message": f"Batch updated {len(successful_updates)} records in '{table_name}' successfully"
        }
        
    except Exception as e:
        logger.error(f"✗ 批量更新记录失败：{e}")
        return {
            "success": False,
            "error": str(e)
        }


def list_tables(user_id: int) -> Dict[str, Any]:
    """
    列出用户创建的所有记忆表
    
    Args:
        user_id: 用户ID
        
    Returns:
        表列表字典
    """
    try:
        db = get_db_client()
        
        # 从 memory_tables 表中查询
        rows = db.execute(
            'SELECT table_name, table_schema FROM memory_tables WHERE user_id = ?',
            (user_id,)
        )
        
        tables = []
        if rows:
            for row in rows:
                table_name = row["table_name"]
                table_schema = json.loads(row["table_schema"])
                
                tables.append({
                    "table_name": table_name,
                    "fields": table_schema.get("fields", []),
                    "created_at": table_schema.get("created_at"),
                    "version": table_schema.get("version", 1)
                })
        
        logger.info(f"✓ 列出用户 {user_id} 的 {len(tables)} 个记忆表")
        
        return {
            "success": True,
            "tables": tables,
            "count": len(tables)
        }
        
    except Exception as e:
        logger.error(f"✗ 列出表失败：{e}")
        return {
            "success": False,
            "error": str(e)
        }


def get_table_info(user_id: int,
                    table_name: str) -> Optional[Dict[str, Any]]:
    """
    获取表的详细信息（包括字段定义）
    
    Args:
        user_id: 用户ID
        table_name: 表名
        
    Returns:
        表信息字典，如果表不存在则返回 None
    """
    try:
        db = get_db_client()
        
        # 从 memory_tables 表中查询
        result = db.execute(
            'SELECT table_schema FROM memory_tables WHERE user_id = ? AND table_name = ?',
            (user_id, table_name)
        )
        
        if not result:
            return None
        
        table_schema = json.loads(result[0]["table_schema"])
        
        return {
            "table_name": table_name,
            "fields": table_schema.get("fields", []),
            "created_at": table_schema.get("created_at"),
            "version": table_schema.get("version", 1)
        }
        
    except Exception as e:
        logger.error(f"✗ 获取表信息失败：{e}")
        return None


def drop_table(user_id: int, table_name: str) -> Dict[str, Any]:
    """
    删除记忆表（包括表结构和所有数据）
    
    Args:
        user_id: 用户ID
        table_name: 表名
        
    Returns:
        删除结果字典
    """
    try:
        db = get_db_client()
        
        # 1. 检查表是否存在
        table_info = get_table_info(user_id, table_name)
        if not table_info:
            return {
                "success": False,
                "error": f"Table '{table_name}' not found"
            }
        
        # 2. 删除实际的动态表
        actual_table_name = f"memory_{user_id}_{table_name}"
        db.execute(f'DROP TABLE IF EXISTS "{actual_table_name}"')
        
        # 3. 从 memory_tables 中删除记录
        db.execute(
            'DELETE FROM memory_tables WHERE user_id = ? AND table_name = ?',
            (user_id, table_name)
        )
        
        logger.info(f"✓ 删除记忆表: {table_name}（用户 {user_id}）")
        
        return {
            "success": True,
            "table_name": table_name,
            "message": f"Table '{table_name}' dropped successfully"
        }
        
    except Exception as e:
        logger.error(f"✗ 删除表失败：{e}")
        return {
            "success": False,
            "error": str(e)
        }


def query_records_with_filters(user_id: int,
                               table_name: str,
                               filters: Optional[Dict[str, Any]] = None,
                               sort_by: Optional[str] = None,
                               sort_order: str = "ASC",
                               limit: int = 100,
                               offset: int = 0) -> Dict[str, Any]:
    """
    带过滤条件的查询记忆表记录
    
    Args:
        user_id: 用户ID
        table_name: 表名
        filters: 过滤条件字典（key 为字段名，value 为要匹配的值）
        sort_by: 排序字段
        sort_order: 排序顺序（ASC 或 DESC）
        limit: 返回记录数限制
        offset: 偏移量（分页）
        
    Returns:
        查询结果字典
    """
    try:
        db = get_db_client()
        
        # 1. 获取表结构
        table_info = get_table_info(user_id, table_name)
        if not table_info:
            return {
                "success": False,
                "error": f"Table '{table_name}' not found"
            }
        
        fields = table_info.get("fields", [])
        field_names = [f.get("name") for f in fields]
        actual_table_name = f"memory_{user_id}_{table_name}"
        
        # 2. 构建 SQL
        sql = f'SELECT * FROM "{actual_table_name}"'
        params = []
        
        # 3. 添加过滤条件
        if filters:
            conditions = []
            for key, value in filters.items():
                if key in field_names:
                    conditions.append(f'"{key}" = ?')
                    params.append(value)
            
            if conditions:
                sql += ' WHERE ' + ' AND '.join(conditions)
        
        # 4. 添加排序
        if sort_by and sort_by in field_names:
            order = 'DESC' if sort_order.upper() == 'DESC' else 'ASC'
            sql += f' ORDER BY "{sort_by}" {order}'
        
        # 5. 添加分页
        sql += f' LIMIT {limit} OFFSET {offset}'
        
        # 6. 执行查询
        rows = db.execute(sql, tuple(params))
        
        records = []
        if rows:
            for row in rows:
                record = dict(row)
                record["id"] = record.pop("__id__")
                record.pop("__created_at__", None)
                record.pop("__updated_at__", None)
                records.append(record)
        
        # 7. 查询总数
        count_sql = f'SELECT COUNT(*) as total FROM "{actual_table_name}"'
        if filters:
            conditions = []
            for key, value in filters.items():
                if key in field_names:
                    conditions.append(f'"{key}" = ?')
            if conditions:
                count_sql += ' WHERE ' + ' AND '.join(conditions)
        
        count_rows = db.execute(count_sql, tuple(params))
        total = count_rows[0]["total"] if count_rows else 0
        
        logger.info(f"✓ 过滤查询表 {table_name}，返回 {len(records)} 条记录（共 {total} 条）")
        
        return {
            "success": True,
            "records": records,
            "count": len(records),
            "total": total,
            "table_name": table_name,
            "filters": filters,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        logger.error(f"✗ 过滤查询失败：{e}")
        return {
            "success": False,
            "error": str(e)
        }


def parse_natural_language_to_table(user_input: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """
    从自然语言中输入中抽取表结构和数据
    
    支持的模式：
    - "记录我的项目：源启·智能体工厂，负责人：鑫海，状态：进行中"
    - "创建表：项目（项目名称，负责人，状态）"
    
    Args:
        user_input: 用户输入文本
        user_id: 用户ID（可选，用于自动创建表）
        
    Returns:
        表结构和数据字典，如果无法解析则返回 None
    """
    try:
        # 模式1：记录 表名：值1（字段1），字段2：值2...
        # 示例："记录我的项目：源启·智能体工厂，负责人：鑫海，状态：进行中"
        # 这里第一个值对应一个默认字段名（如 "名称" 或 "项目"）
        match = re.search(r'记录(.*?)：(.*?)(?:$|。)', user_input)
        if match:
            table_name = match.group(1).strip()
            fields_part = match.group(2).strip()
            
            # 解析字段和值
            field_value_pairs = [pair.strip() for pair in fields_part.split('，')]
            
            fields = []
            record = {}
            
            # 第一个值（不包含 '：' 的）对应一个默认字段
            # 尝试推断第一个字段的名称
            default_field_name = "名称"  # 默认字段名
            
            # 如果表名包含常见关键词，使用更合适的字段名
            if "项目" in table_name:
                default_field_name = "项目名称"
            elif "任务" in table_name or "待办" in table_name:
                default_field_name = "任务名称"
            elif "联系人" in table_name or "客户" in table_name:
                default_field_name = "姓名"
            
            first_pair = field_value_pairs[0] if field_value_pairs else ""
            
            if '：' not in first_pair:
                # 第一个值是默认字段的值
                fields.append({"name": default_field_name, "type": "TEXT"})
                record[default_field_name] = first_pair
                
                # 处理剩余的字段：值对
                for pair in field_value_pairs[1:]:
                    if '：' in pair:
                        field, value = pair.split('：', 1)
                        field = field.strip()
                        value = value.strip()
                        
                        fields.append({"name": field, "type": "TEXT"})
                        record[field] = value
            else:
                # 所有部分都是 字段：值 格式
                for pair in field_value_pairs:
                    if '：' in pair:
                        field, value = pair.split('：', 1)
                        field = field.strip()
                        value = value.strip()
                        
                        fields.append({"name": field, "type": "TEXT"})
                        record[field] = value
            
            return {
                "table_name": table_name,
                "fields": fields,
                "record": record
            }
        
        # 模式2：创建表：表名（字段1，字段2...）
        # 示例："创建表：项目（项目名称，负责人，状态）"
        match = re.search(r'创建表：(.*?)（(.*?)）', user_input)
        if match:
            table_name = match.group(1).strip()
            fields_part = match.group(2).strip()
            
            # 解析字段
            field_names = [f.strip() for f in fields_part.split('，')]
            
            fields = [{"name": f, "type": "TEXT"} for f in field_names]
            
            return {
                "table_name": table_name,
                "fields": fields,
                "record": None  # 无初始数据
            }
        
        # 无法解析
        return None
        
    except Exception as e:
        logger.error(f"✗ 解析自然语言失败：{e}")
        return None


# 测试函数
def test_memory_table_service():
    """测试记忆表服务"""
    print("\n" + "="*60)
    print("测试记忆表服务（动态表结构）")
    print("="*60 + "\n")
    
    user_id = 999  # 测试用户ID
    
    # 清理之前可能存在的测试表
    db = get_db_client()
    db.execute(f'DROP TABLE IF EXISTS "memory_{user_id}_projects"')
    db.execute('DELETE FROM memory_tables WHERE user_id = ? AND table_name = ?', (user_id, "projects"))
    
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
    
    # 测试8：批量添加记录
    print(f"\n8. 测试批量添加记录...")
    records = [
        {
            "project_name": "项目A",
            "负责人": "张三",
            "status": "进行中"
        },
        {
            "project_name": "项目B",
            "负责人": "李四",
            "status": "已完成"
        },
        {
            "project_name": "项目C",
            "负责人": "鑫海",
            "status": "计划中"
        }
    ]
    
    result = batch_add_records(user_id, "projects", records)
    print(f"   批量添加结果：{result['success']}，插入数量：{result.get('inserted_count', 0)}")
    assert result["success"] == True
    assert result["inserted_count"] == 3
    print(f"   ✓ 批量添加记录成功，插入 {result['inserted_count']} 条记录")
    
    # 测试9：批量更新记录
    print(f"\n9. 测试批量更新记录...")
    # 先查询所有记录
    query_result = query_records(user_id, "projects")
    records_to_update = query_result["records"]
    
    updates_list = []
    for idx, record in enumerate(records_to_update[:2]):  # 只更新前2条
        record_id = record.get("__id__", idx + 1)  # 如果找不到 __id__，使用索引
        updates_list.append({
            "record_id": record_id,
            "updates": {"status": "已更新"}
        })
    
    result = batch_update_records(user_id, "projects", updates_list)
    print(f"   批量更新结果：{result['success']}，更新数量：{result.get('updated_count', 0)}")
    assert result["success"] == True
    assert result["updated_count"] == 2
    print(f"   ✓ 批量更新记录成功，更新 {result['updated_count']} 条记录")
    
    # 测试10：类型验证（添加错误类型）
    print(f"\n10. 测试类型验证...")
    # 先创建一个包含 INTEGER 字段的表
    print(f"   创建包含 INTEGER 字段的测试表...")
    test_fields = [
        {"name": "name", "type": "TEXT"},
        {"name": "age", "type": "INTEGER"},
        {"name": "active", "type": "BOOLEAN"}
    ]
    
    create_result = create_memory_table(user_id, "test_types", test_fields)
    assert create_result["success"] == True
    
    # 测试正确的类型
    valid_record = {
        "name": "测试用户",
        "age": 25,  # 正确类型
        "active": True  # 正确类型
    }
    
    result = add_record(user_id, "test_types", valid_record)
    print(f"   正确类型添加结果：{result['success']}")
    assert result["success"] == True
    print(f"   ✓ 正确类型验证通过")
    
    # 测试错误的类型
    invalid_record = {
        "name": "测试用户2",
        "age": "不是数字",  # 错误类型
        "active": True
    }
    
    result = add_record(user_id, "test_types", invalid_record)
    print(f"   错误类型添加结果：{result['success']}")
    assert result["success"] == False
    print(f"   ✓ 错误类型验证正确拒绝")
    
    # 清理测试表
    db.execute(f'DROP TABLE IF EXISTS "memory_{user_id}_test_types"')
    db.execute('DELETE FROM memory_tables WHERE user_id = ? AND table_name = ?', (user_id, "test_types"))
    
    # 清理测试数据
    print(f"\n11. 清理测试数据...")
    # 删除测试表（动态表）
    db.execute(f'DROP TABLE IF EXISTS "memory_{user_id}_projects"')
    db.execute('DELETE FROM memory_tables WHERE user_id = ?', (user_id,))
    print(f"   ✓ 清理完成")
    
    print("\n" + "="*60)
    print("✅ 记忆表服务测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 运行测试
    test_memory_table_service()
