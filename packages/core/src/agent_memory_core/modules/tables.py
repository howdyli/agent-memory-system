"""TableManager — Core-layer dynamic table management.

Pure business logic, no HTTP/auth dependency.
Uses RelationalStore for persistent storage and raw SQL for dynamic table operations.
Migrated from backend/services/memory_table_service.py.
"""

import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..events import EventEmitter, MemoryEvent, MemoryEventType
from ..store.base import RelationalStore

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────
SUPPORTED_TYPES = ["TEXT", "INTEGER", "REAL", "BOOLEAN", "DATE", "DATETIME", "JSON"]

# Physical table naming convention: dt_{workspace_id}_{table_name}
# (dt = dynamic table, shorter than "memory_" prefix)


class TableManager:
    """Manage dynamic table CRUD within a workspace.

    Replaces backend/services/memory_table_service.py.
    Uses RelationalStore for schema metadata and execute_sql() for
    physical table DDL/DML operations.
    """

    def __init__(
        self,
        relational_store: RelationalStore,
        event_emitter: Optional[EventEmitter] = None,
    ):
        self._relational = relational_store
        self._events = event_emitter

    # ── Physical Table Naming ──────────────────────────────────

    @staticmethod
    def _physical_table_name(workspace_id: int, table_name: str) -> str:
        """Generate physical SQLite table name for a dynamic table."""
        return f"dt_{workspace_id}_{table_name}"

    # ── Type Validation ────────────────────────────────────────

    @staticmethod
    def validate_type(field_type: str, value: Any) -> Any:
        """Validate and coerce a value according to field type.

        Raises ValueError if coercion fails.
        """
        ft = field_type.upper()
        if ft == "INTEGER":
            return int(value)
        elif ft == "REAL":
            return float(value)
        elif ft == "BOOLEAN":
            return bool(value)
        elif ft == "DATE":
            return str(value)
        elif ft == "DATETIME":
            return str(value)
        elif ft == "JSON":
            if isinstance(value, str):
                json.loads(value)  # validate only
                return value
            else:
                return json.dumps(value)
        else:  # TEXT or unknown
            return str(value)

    @staticmethod
    def _validate_fields(fields: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Validate and normalize field definitions."""
        validated = []
        for field in fields:
            field_name = field.get("name")
            field_type = field.get("type", "TEXT").upper()

            if field_type not in SUPPORTED_TYPES:
                logger.warning(f"Unsupported field type '{field_type}', using TEXT")
                field_type = "TEXT"

            validated.append({
                "name": field_name,
                "type": field_type,
                "index": field.get("index", False),
                "nullable": field.get("nullable", True),
                "default": field.get("default", None),
            })
        return validated

    # ── Core CRUD ──────────────────────────────────────────────

    def create(
        self,
        workspace_id: int,
        table_name: str,
        fields: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Create a dynamic table definition and physical table.

        Returns dict with table_name and validated fields.
        Raises ValueError on invalid field definitions.
        """
        validated_fields = self._validate_fields(fields)

        table_schema = {
            "fields": validated_fields,
            "created_at": datetime.now().isoformat(),
            "version": 1,
        }

        # 1. Register in metadata table
        self._relational.create_table(workspace_id, table_name, table_schema)

        # 2. Create physical table
        phys_name = self._physical_table_name(workspace_id, table_name)

        field_defs = []
        for field in validated_fields:
            fn = field["name"]
            ft = field["type"]
            nullable = "" if field.get("nullable", True) else "NOT NULL"
            default = f"DEFAULT {field['default']}" if field.get("default") is not None else ""
            field_defs.append(f'"{fn}" {ft} {nullable} {default}'.strip())

        # Add metadata columns
        field_defs.append('"__id__" INTEGER PRIMARY KEY AUTOINCREMENT')
        field_defs.append('"__created_at__" TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
        field_defs.append('"__updated_at__" TIMESTAMP DEFAULT CURRENT_TIMESTAMP')

        create_sql = (
            f'CREATE TABLE IF NOT EXISTS "{phys_name}" (\n'
            f'  {", ".join(field_defs)}\n'
            f')'
        )
        self._relational.execute_sql(create_sql)

        # 3. Create indexes
        for field in validated_fields:
            if field.get("index"):
                idx_name = f"idx_dt_{workspace_id}_{table_name}_{field['name']}"
                idx_sql = (
                    f'CREATE INDEX IF NOT EXISTS "{idx_name}" '
                    f'ON "{phys_name}" ("{field["name"]}")'
                )
                self._relational.execute_sql(idx_sql)

        if self._events:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.TABLE_CREATED,
                workspace_id=workspace_id,
                memory_type="table",
                memory_id=table_name,
                data={"table_name": table_name, "fields_count": len(validated_fields)},
            ))

        return {"table_name": table_name, "fields": validated_fields}

    def get_table_info(self, workspace_id: int, table_name: str) -> Optional[Dict[str, Any]]:
        """Get table schema definition. Returns None if table not found."""
        result = self._relational.get_table(workspace_id, table_name)
        if result is None:
            return None

        schema = result.get("table_schema") or result.get("schema")
        if isinstance(schema, str):
            schema = json.loads(schema)

        return {
            "table_name": table_name,
            "fields": schema.get("fields", []),
            "created_at": schema.get("created_at"),
            "version": schema.get("version", 1),
        }

    def list_tables(self, workspace_id: int) -> List[Dict[str, Any]]:
        """List all dynamic table definitions for a workspace."""
        raw_list = self._relational.list_tables(workspace_id)
        tables = []
        for row in raw_list:
            schema = row.get("table_schema") or row.get("schema")
            if isinstance(schema, str):
                schema = json.loads(schema)
            tables.append({
                "table_name": row.get("table_name"),
                "fields": schema.get("fields", []),
                "created_at": schema.get("created_at"),
                "version": schema.get("version", 1),
            })
        return tables

    def drop(self, workspace_id: int, table_name: str) -> bool:
        """Delete a dynamic table definition and its physical table.

        Returns True on success, raises if table not found.
        """
        table_info = self.get_table_info(workspace_id, table_name)
        if table_info is None:
            raise ValueError(f"Table '{table_name}' not found")

        phys_name = self._physical_table_name(workspace_id, table_name)
        self._relational.execute_sql(f'DROP TABLE IF EXISTS "{phys_name}"')
        self._relational.delete_table(workspace_id, table_name)

        if self._events:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.TABLE_DELETED,
                workspace_id=workspace_id,
                memory_type="table",
                memory_id=table_name,
                data={"table_name": table_name},
            ))

        return True

    # ── Record Operations ──────────────────────────────────────

    def add_record(
        self,
        workspace_id: int,
        table_name: str,
        record: Dict[str, Any],
        validate_types: bool = True,
    ) -> int:
        """Insert a record into a dynamic table.

        Returns record ID (__id__).
        Raises ValueError on type validation failure or missing table.
        """
        table_info = self.get_table_info(workspace_id, table_name)
        if table_info is None:
            raise ValueError(f"Table '{table_name}' not found")

        fields = table_info.get("fields", [])
        field_names = [f.get("name") for f in fields]

        # Type validation
        validated = {}
        for key, value in record.items():
            if key not in field_names:
                logger.warning(f"Field '{key}' not in schema, ignored")
                continue
            if validate_types:
                field_def = next((f for f in fields if f.get("name") == key), None)
                if field_def:
                    validated[key] = self.validate_type(field_def.get("type", "TEXT"), value)
                else:
                    validated[key] = value
            else:
                validated[key] = value

        valid_keys = [k for k in validated.keys() if k in field_names]
        if not valid_keys:
            raise ValueError("No valid fields to insert")

        return self._relational.add_record(workspace_id, table_name, validated)

    def query_records(
        self,
        workspace_id: int,
        table_name: str,
        filters: Optional[Dict[str, Any]] = None,
        order_by: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query records from a dynamic table with filters.

        Returns list of record dicts (with 'id' field mapped from '__id__').
        """
        rows = self._relational.query_records(
            workspace_id=workspace_id,
            table_name=table_name,
            filters=filters,
            order_by=order_by or "__id__",
            limit=limit,
            offset=offset,
        )

        # Map __id__ → id, strip metadata columns
        result = []
        for row in rows:
            r = dict(row)
            r["id"] = r.pop("__id__", r.get("id"))
            r.pop("__created_at__", None)
            r.pop("__updated_at__", None)
            result.append(r)

        return result

    def update_record(
        self,
        workspace_id: int,
        table_name: str,
        record_id: int,
        updates: Dict[str, Any],
        validate_types: bool = True,
    ) -> bool:
        """Update a record in a dynamic table.

        Returns True on success. Raises on validation failure.
        """
        table_info = self.get_table_info(workspace_id, table_name)
        if table_info is None:
            raise ValueError(f"Table '{table_name}' not found")

        fields = table_info.get("fields", [])
        field_names = [f.get("name") for f in fields]

        validated = {}
        for key in updates.keys():
            if key not in field_names:
                continue
            value = updates[key]
            if validate_types:
                field_def = next((f for f in fields if f.get("name") == key), None)
                if field_def:
                    validated[key] = self.validate_type(field_def.get("type", "TEXT"), value)
                else:
                    validated[key] = value
            else:
                validated[key] = value

        if not validated:
            raise ValueError("No valid fields to update")

        success = self._relational.update_record(workspace_id, table_name, record_id, validated)

        if success and self._events:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.RECORD_UPDATED,
                workspace_id=workspace_id,
                memory_type="table_record",
                memory_id=str(record_id),
                data={"table_name": table_name, "record_id": record_id},
            ))

        return success

    def delete_record(self, workspace_id: int, table_name: str, record_id: int) -> bool:
        """Delete a record from a dynamic table.

        Returns True on success.
        """
        success = self._relational.delete_record(workspace_id, table_name, record_id)

        if success and self._events:
            self._events.emit(MemoryEvent(
                event_type=MemoryEventType.RECORD_DELETED,
                workspace_id=workspace_id,
                memory_type="table_record",
                memory_id=str(record_id),
                data={"table_name": table_name, "record_id": record_id},
            ))

        return success

    # ── Batch Operations ───────────────────────────────────────

    def batch_add_records(
        self,
        workspace_id: int,
        table_name: str,
        records: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Batch insert records. Returns dict with inserted_count and failed records."""
        if not records:
            raise ValueError("No records to insert")

        table_info = self.get_table_info(workspace_id, table_name)
        if table_info is None:
            raise ValueError(f"Table '{table_name}' not found")

        fields = table_info.get("fields", [])
        field_names = [f.get("name") for f in fields]

        valid_records = []
        failed_records = []

        for idx, record in enumerate(records):
            valid_keys = [k for k in record.keys() if k in field_names]
            if not valid_keys:
                failed_records.append({"index": idx, "error": "No valid fields", "record": record})
                continue

            validated = {}
            type_error = False
            for key in valid_keys:
                value = record.get(key)
                field_def = next((f for f in fields if f.get("name") == key), None)
                if field_def:
                    try:
                        validated[key] = self.validate_type(field_def.get("type", "TEXT"), value)
                    except (ValueError, json.JSONDecodeError) as e:
                        failed_records.append({
                            "index": idx,
                            "error": f"Type validation failed for '{key}': {e}",
                            "record": record,
                        })
                        type_error = True
                        break
                else:
                    validated[key] = value

            if not type_error and validated:
                record_id = self._relational.add_record(workspace_id, table_name, validated)
                valid_records.append({"record_id": record_id, "keys": list(validated.keys())})

        return {
            "inserted_count": len(valid_records),
            "valid_records": valid_records,
            "failed_count": len(failed_records),
            "failed_records": failed_records,
        }

    def batch_update_records(
        self,
        workspace_id: int,
        table_name: str,
        updates_list: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Batch update records. Each item: {record_id: int, updates: dict}."""
        if not updates_list:
            raise ValueError("No records to update")

        table_info = self.get_table_info(workspace_id, table_name)
        if table_info is None:
            raise ValueError(f"Table '{table_name}' not found")

        fields = table_info.get("fields", [])
        field_names = [f.get("name") for f in fields]

        successful = []
        failed = []

        for item in updates_list:
            record_id = item.get("record_id")
            updates = item.get("updates", {})

            if not record_id:
                failed.append({"item": item, "error": "Missing record_id"})
                continue

            valid_keys = [k for k in updates.keys() if k in field_names]
            if not valid_keys:
                failed.append({"record_id": record_id, "error": "No valid fields"})
                continue

            validated = {}
            type_error = False
            for key in valid_keys:
                value = updates.get(key)
                field_def = next((f for f in fields if f.get("name") == key), None)
                if field_def:
                    try:
                        validated[key] = self.validate_type(field_def.get("type", "TEXT"), value)
                    except (ValueError, json.JSONDecodeError) as e:
                        failed.append({"record_id": record_id, "error": f"Type validation: {e}"})
                        type_error = True
                        break
                else:
                    validated[key] = value

            if not type_error and validated:
                self._relational.update_record(workspace_id, table_name, record_id, validated)
                successful.append({"record_id": record_id, "updated_fields": list(validated.keys())})

        return {
            "updated_count": len(successful),
            "successful_updates": successful,
            "failed_count": len(failed),
            "failed_updates": failed,
        }

    # ── Advanced Query ─────────────────────────────────────────

    def query_records_with_filters(
        self,
        workspace_id: int,
        table_name: str,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: Optional[str] = None,
        sort_order: str = "ASC",
        limit: int = 100,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Query records with filters, sorting, and total count.

        Returns {records, count, total, ...}.
        """
        # Use the store's query_records with extended filtering
        rows = self._relational.query_records(
            workspace_id=workspace_id,
            table_name=table_name,
            filters=filters,
            order_by=sort_by or "__id__",
            limit=limit,
            offset=offset,
        )

        records = []
        for row in rows:
            r = dict(row)
            r["id"] = r.pop("__id__", r.get("id"))
            r.pop("__created_at__", None)
            r.pop("__updated_at__", None)
            records.append(r)

        # Get total count with same filters
        all_rows = self._relational.query_records(
            workspace_id=workspace_id,
            table_name=table_name,
            filters=filters,
            limit=10000,
            offset=0,
        )
        total = len(all_rows)

        return {
            "records": records,
            "count": len(records),
            "total": total,
            "table_name": table_name,
            "filters": filters,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
        }

    # ── Natural Language Parsing (static) ──────────────────────

    @staticmethod
    def parse_natural_language_to_table(user_input: str) -> Optional[Dict[str, Any]]:
        """Parse natural language input into table structure and data.

        Supports:
        - "记录我的项目：源启·智能体工厂，负责人：鑫海，状态：进行中"
        - "创建表：项目（项目名称，负责人，状态）"
        """
        # Pattern 1: "记录 表名：值1，字段：值2..."
        match = re.search(r'记录(.*?)：(.*?)(?:$|。)', user_input)
        if match:
            table_name = match.group(1).strip()
            fields_part = match.group(2).strip()

            pairs = [p.strip() for p in fields_part.split('，')]
            fields = []
            record = {}

            # Infer default field name
            default_name = "名称"
            if "项目" in table_name:
                default_name = "项目名称"
            elif "任务" in table_name or "待办" in table_name:
                default_name = "任务名称"
            elif "联系人" in table_name or "客户" in table_name:
                default_name = "姓名"

            first = pairs[0] if pairs else ""
            if '：' not in first:
                fields.append({"name": default_name, "type": "TEXT"})
                record[default_name] = first
                for pair in pairs[1:]:
                    if '：' in pair:
                        k, v = pair.split('：', 1)
                        fields.append({"name": k.strip(), "type": "TEXT"})
                        record[k.strip()] = v.strip()
            else:
                for pair in pairs:
                    if '：' in pair:
                        k, v = pair.split('：', 1)
                        fields.append({"name": k.strip(), "type": "TEXT"})
                        record[k.strip()] = v.strip()

            return {"table_name": table_name, "fields": fields, "record": record}

        # Pattern 2: "创建表：表名（字段1，字段2...）"
        match = re.search(r'创建表：(.*?)（(.*?)）', user_input)
        if match:
            table_name = match.group(1).strip()
            fields_part = match.group(2).strip()
            field_names = [f.strip() for f in fields_part.split('，')]
            fields = [{"name": f, "type": "TEXT"} for f in field_names]
            return {"table_name": table_name, "fields": fields, "record": None}

        return None
