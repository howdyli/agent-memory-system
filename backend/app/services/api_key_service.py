"""API Key 服务（Phase 2）。

为机器对机器认证（SDK / 第三方集成）提供 API Key 的生成、撤销、校验。

Key 格式：`amk_<32 字节 hex>`，共 67 字符。
存储：仅存 SHA-256 哈希（key_hash），明文仅在创建时返回一次。
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
from datetime import datetime
from typing import Dict, List, Optional

from app.core.auth import Principal
from app.core.store import get_relational_store
from app.models.orm import ApiKey

logger = logging.getLogger(__name__)

API_KEY_PREFIX = "amk_"


def _generate_raw_key() -> str:
    return API_KEY_PREFIX + secrets.token_hex(32)


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def create_api_key(
    workspace_id: int,
    user_id: int,
    name: str,
    scopes: Optional[List[str]] = None,
    expires_at: Optional[datetime] = None,
) -> Dict:
    """创建 API Key。返回的 `key` 字段为明文，仅此次可见。

    Args:
        workspace_id: Key 所属 workspace
        user_id: Key 创建者
        name: 可读名称（如 "CI pipeline"）
        scopes: 授权权限列表，默认 ["memory:read"]
        expires_at: 过期时间（可选）

    Returns:
        {"id": ..., "key": "amk_...", "name": ..., "scopes": [...], ...}
    """
    store = get_relational_store()
    raw_key = _generate_raw_key()
    key_hash = _hash_key(raw_key)

    api_key = ApiKey(
        workspace_id=workspace_id,
        user_id=user_id,
        name=name,
        key_hash=key_hash,
        scopes=json.dumps(scopes or ["memory:read"]),
        expires_at=expires_at,
    )
    api_key = store.create(api_key)

    logger.info(
        f"✓ 创建 API Key: id={api_key.id} name={name!r} workspace={workspace_id}"
    )
    return {
        "id": api_key.id,
        "key": raw_key,  # 仅此次返回明文
        "name": api_key.name,
        "workspace_id": api_key.workspace_id,
        "scopes": scopes or ["memory:read"],
        "expires_at": api_key.expires_at.isoformat() if api_key.expires_at else None,
        "created_at": api_key.created_at.isoformat() if api_key.created_at else None,
    }


def list_api_keys(workspace_id: int) -> List[Dict]:
    """列出 workspace 下所有 API Key（不含明文）。"""
    store = get_relational_store()
    keys = store.query(ApiKey, filters={"workspace_id": workspace_id})
    return [_key_to_dict(k) for k in keys if k.revoked_at is None]


def revoke_api_key(key_id: int, workspace_id: int) -> bool:
    """撤销 API Key（软删除）。"""
    store = get_relational_store()
    key = store.get(ApiKey, key_id)
    if key is None or key.workspace_id != workspace_id:
        return False
    store.update(ApiKey, key_id, {"revoked_at": datetime.utcnow()})
    return True


async def validate_api_key(raw_key: str) -> Optional[Principal]:
    """校验 API Key 并返回 Principal。失败返回 None。

    校验逻辑：
    1. 前缀必须是 amk_
    2. SHA-256 哈希匹配
    3. 未被撤销（revoked_at 为 None）
    4. 未过期（expires_at 为 None 或 > now）
    5. 更新 last_used_at
    """
    if not raw_key.startswith(API_KEY_PREFIX):
        return None

    key_hash = _hash_key(raw_key)
    store = get_relational_store()
    keys = store.query(ApiKey, filters={"key_hash": key_hash})
    if not keys:
        return None
    key = keys[0]

    # 已撤销
    if key.revoked_at is not None:
        return None
    # 已过期
    if key.expires_at is not None and key.expires_at < datetime.utcnow():
        return None

    # 更新 last_used_at
    store.update(ApiKey, key.id, {"last_used_at": datetime.utcnow()})

    try:
        scopes = json.loads(key.scopes) if key.scopes else []
    except json.JSONDecodeError:
        scopes = []

    return Principal(
        user_id=key.user_id,
        workspace_id=key.workspace_id,
        scopes=scopes,
        auth_method="api_key",
        api_key_id=key.id,
    )


# ============================================================
# 内部辅助
# ============================================================
def _key_to_dict(k: ApiKey) -> Dict:
    try:
        scopes = json.loads(k.scopes) if k.scopes else []
    except json.JSONDecodeError:
        scopes = []
    return {
        "id": k.id,
        "name": k.name,
        "workspace_id": k.workspace_id,
        "user_id": k.user_id,
        "scopes": scopes,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "expires_at": k.expires_at.isoformat() if k.expires_at else None,
        "created_at": k.created_at.isoformat() if k.created_at else None,
    }


__all__ = [
    "create_api_key",
    "list_api_keys",
    "revoke_api_key",
    "validate_api_key",
]
