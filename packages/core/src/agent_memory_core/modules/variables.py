"""VariableManager — Core-layer memory variable management.

Pure business logic, no HTTP/auth dependency.
Uses RelationalStore for persistent storage, CacheStore for optional hot caching.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..events import EventEmitter, MemoryEvent, MemoryEventType
from ..store.base import RelationalStore, CacheStore

logger = logging.getLogger(__name__)


class VariableManager:
    """Manage memory variables within a workspace.

    Replaces backend/services/memory_variable_service.py.
    Uses RelationalStore (SQLite) for persistent KV storage
    and optional CacheStore for hot data caching.
    """

    def __init__(
        self,
        relational_store: RelationalStore,
        cache_store: Optional[CacheStore] = None,
        event_emitter: Optional[EventEmitter] = None,
    ):
        self._relational = relational_store
        self._cache = cache_store
        self._events = event_emitter or EventEmitter()

    # ── CRUD ───────────────────────────────────────────────────

    def set(
        self,
        workspace_id: int,
        key: str,
        value: Any,
        session_id: Optional[str] = None,
        ttl: Optional[int] = None,
    ) -> bool:
        """Set a memory variable.

        Uses session_id as key prefix for scoped variables.
        Writes to RelationalStore (persistent) and CacheStore (hot cache).
        """
        scoped_key = self._scoped_key(key, session_id)

        # Persistent write
        success = self._relational.set_variable(workspace_id, scoped_key, value, ttl=ttl)

        if success:
            # Sync to cache for hot-read acceleration
            if self._cache is not None:
                cache_key = self._cache_key(workspace_id, scoped_key)
                self._cache.set(cache_key, value, ttl=ttl)

            self._events.emit(
                MemoryEvent(
                    event_type=MemoryEventType.VARIABLE_SET,
                    workspace_id=workspace_id,
                    memory_type="variable",
                    memory_id=scoped_key,
                    data={
                        "key": key,
                        "session_id": session_id,
                        "ttl": ttl,
                        "value_preview": str(value)[:100],
                    },
                )
            )

            logger.debug(
                f"Set variable: workspace={workspace_id} key={scoped_key}"
            )

        return success

    def get(
        self,
        workspace_id: int,
        key: str,
        session_id: Optional[str] = None,
        default: Any = None,
    ) -> Any:
        """Get a variable value.

        Falls back from session scope to global if session_id is provided
        but the session-scoped key doesn't exist.
        Checks CacheStore first (fast), then RelationalStore (reliable).
        """
        scoped_key = self._scoped_key(key, session_id)

        # Fast path: check cache
        if self._cache is not None:
            cache_key = self._cache_key(workspace_id, scoped_key)
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        # Reliable path: check relational store
        value = self._relational.get_variable(workspace_id, scoped_key)
        found_key = scoped_key if value is not None else None

        # Session-to-global fallback
        if value is None and session_id is not None:
            global_key = self._scoped_key(key, None)

            # Try cache for global key
            if self._cache is not None:
                cache_key = self._cache_key(workspace_id, global_key)
                cached = self._cache.get(cache_key)
                if cached is not None:
                    return cached

            value = self._relational.get_variable(workspace_id, global_key)
            if value is not None:
                found_key = global_key

        if value is None:
            return default

        # Backfill cache for subsequent reads
        if self._cache is not None and found_key is not None:
            cache_key = self._cache_key(workspace_id, found_key)
            self._cache.set(cache_key, value)

        return value

    def delete(
        self,
        workspace_id: int,
        key: str,
        session_id: Optional[str] = None,
    ) -> bool:
        """Delete a variable from both persistent store and cache."""
        scoped_key = self._scoped_key(key, session_id)

        success = self._relational.delete_variable(workspace_id, scoped_key)

        if success:
            # Invalidate cache
            if self._cache is not None:
                cache_key = self._cache_key(workspace_id, scoped_key)
                self._cache.delete(cache_key)

            self._events.emit(
                MemoryEvent(
                    event_type=MemoryEventType.VARIABLE_DELETED,
                    workspace_id=workspace_id,
                    memory_type="variable",
                    memory_id=scoped_key,
                    data={"key": key, "session_id": session_id},
                )
            )

            logger.debug(
                f"Deleted variable: workspace={workspace_id} key={scoped_key}"
            )

        return success

    def list(
        self, workspace_id: int, session_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """List all variables as a key-value dict.

        When session_id is provided, lists only that session's variables.
        When session_id is None, lists only global variables (excludes session-scoped).
        Keys are returned without the session prefix.
        """
        prefix = self._prefix_for_session(session_id)
        rows = self._relational.list_variables(workspace_id, prefix)

        result: Dict[str, Any] = {}
        for row in rows:
            raw_key = row["key"]

            # When listing globals (session_id is None), skip session-scoped keys
            if session_id is None and raw_key.startswith("session:"):
                continue

            clean_key = self._strip_prefix(raw_key, session_id)
            value = self._parse_value(row.get("value"))
            result[clean_key] = value

        return result

    def list_detailed(
        self, workspace_id: int, session_id: Optional[str] = None
    ) -> List[Dict]:
        """List variables with TTL and metadata.

        Returns a list of dicts: [{key, value, ttl, expires_at, created_at, updated_at}, ...]
        """
        prefix = self._prefix_for_session(session_id)
        rows = self._relational.list_variables(workspace_id, prefix)

        result: List[Dict] = []
        for row in rows:
            raw_key = row["key"]

            # When listing globals (session_id is None), skip session-scoped keys
            if session_id is None and raw_key.startswith("session:"):
                continue

            clean_key = self._strip_prefix(raw_key, session_id)
            value = self._parse_value(row.get("value"))

            # SQLiteStore doesn't store TTL in memory_variables table;
            # ttl/expires_at are None unless tracked externally.
            ttl_val: Optional[int] = None
            expires_at: Optional[str] = None

            result.append(
                {
                    "key": clean_key,
                    "value": value,
                    "ttl": ttl_val,
                    "expires_at": expires_at,
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )

        return result

    def update_ttl(
        self,
        workspace_id: int,
        key: str,
        ttl: Optional[int],
        session_id: Optional[str] = None,
    ) -> bool:
        """Update variable TTL / renew expiry.

        Since SQLiteStore.set_variable doesn't support standalone TTL updates,
        this re-sets the variable with the same value and new TTL.
        """
        scoped_key = self._scoped_key(key, session_id)

        # Fetch current value
        value = self._relational.get_variable(workspace_id, scoped_key)
        if value is None:
            return False

        # Re-set with new TTL
        self._relational.set_variable(workspace_id, scoped_key, value, ttl=ttl)

        # Update cache TTL
        if self._cache is not None:
            cache_key = self._cache_key(workspace_id, scoped_key)
            if ttl and ttl > 0:
                # expire() sets TTL on an existing key; re-set first to ensure existence
                self._cache.set(cache_key, value, ttl=ttl)
            else:
                # No TTL — persist without expiry
                self._cache.set(cache_key, value)

        logger.debug(
            f"Updated TTL: workspace={workspace_id} key={scoped_key} ttl={ttl}"
        )
        return True

    def clear(self, workspace_id: int, session_id: Optional[str] = None) -> int:
        """Clear all variables in scope. Returns count deleted.

        When session_id is provided, clears only that session's variables.
        When session_id is None, clears only global variables.
        """
        prefix = self._prefix_for_session(session_id)
        rows = self._relational.list_variables(workspace_id, prefix)

        count = 0
        for row in rows:
            raw_key = row["key"]

            # When clearing globals (session_id is None), skip session-scoped keys
            if session_id is None and raw_key.startswith("session:"):
                continue

            if self._relational.delete_variable(workspace_id, raw_key):
                count += 1
                # Invalidate cache
                if self._cache is not None:
                    cache_key = self._cache_key(workspace_id, raw_key)
                    self._cache.delete(cache_key)

        self._events.emit(
            MemoryEvent(
                event_type=MemoryEventType.VARIABLE_DELETED,
                workspace_id=workspace_id,
                memory_type="variable",
                memory_id="*",
                data={
                    "action": "clear",
                    "session_id": session_id,
                    "count": count,
                },
            )
        )

        logger.info(
            f"Cleared {count} variables: workspace={workspace_id} session={session_id}"
        )
        return count

    # ── Text Processing (pure logic, no store) ─────────────────

    @staticmethod
    def extract_from_text(text: str) -> Dict[str, str]:
        """Extract variables from natural language text.

        Supported patterns:
        - "我叫{name}"         → user_name = name
        - "我的名字是{name}"   → user_name = name
        - "{key}是{value}"     → key = value
        - "设置{key}为{value}" → key = value
        """
        variables: Dict[str, str] = {}

        # Pattern 1: 我叫{name}
        match = re.search(r"我叫(.+?)[\s。，]", text)
        if match:
            variables["user_name"] = match.group(1).strip()

        # Pattern 2: 我的名字是{name}
        match = re.search(r"我的名字是(.+?)[\s。，]", text)
        if match:
            variables["user_name"] = match.group(1).strip()

        # Pattern 3: {key}是{value}
        matches = re.findall(r"(\w+)是(.+?)[\s。，]", text)
        for key, value in matches:
            variables[key.strip()] = value.strip()

        # Pattern 4: 设置{key}为{value}
        matches = re.findall(r"设置(\w+)为(.+?)[\s。，]", text)
        for key, value in matches:
            variables[key.strip()] = value.strip()

        return variables

    @staticmethod
    def render_template(template: str, variables: Dict[str, Any]) -> str:
        """Render a template by replacing {variable_name} placeholders.

        Example:
            render_template("你好，{user_name}！", {"user_name": "Alice"})
            → "你好，Alice！"
        """
        result = template
        for key, value in variables.items():
            placeholder = "{" + key + "}"
            if placeholder in result:
                result = result.replace(placeholder, str(value))
        return result

    # ── Internal Helpers ───────────────────────────────────────

    def _scoped_key(self, key: str, session_id: Optional[str] = None) -> str:
        """Build scoped key: session:{session_id}:{key} or just {key}."""
        if session_id:
            return f"session:{session_id}:{key}"
        return key

    def _prefix_for_session(self, session_id: Optional[str] = None) -> Optional[str]:
        """Build prefix for list_variables filtering."""
        if session_id:
            return f"session:{session_id}:"
        return None

    def _cache_key(self, workspace_id: int, scoped_key: str) -> str:
        """Build cache key with workspace namespace."""
        return f"var:{workspace_id}:{scoped_key}"

    def _strip_prefix(
        self, scoped_key: str, session_id: Optional[str] = None
    ) -> str:
        """Strip session prefix from scoped key to return clean key."""
        if session_id:
            prefix = f"session:{session_id}:"
            if scoped_key.startswith(prefix):
                return scoped_key[len(prefix) :]
        return scoped_key

    @staticmethod
    def _parse_value(raw: Any) -> Any:
        """Parse a stored value string, attempting JSON deserialization.

        Falls back to the raw string if JSON parsing fails.
        """
        if raw is None:
            return None
        if not isinstance(raw, str):
            return raw
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw
