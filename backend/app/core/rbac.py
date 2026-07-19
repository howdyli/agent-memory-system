"""RBAC 权限框架（Phase 2）。

定义权限常量、角色-权限映射，以及 FastAPI 依赖注入 `require_permission()`。

设计要点：
- JWT 用户（auth_method="jwt"）默认拥有全部权限（has_scope 返回 True），
  由角色-权限映射在 workspace 维度进一步约束（如 viewer 不能写）。
- API Key（auth_method="api_key"）严格按 key.scopes 校验。
- 路径参数 workspace_id 校验：请求的 ws 必须与 Principal.workspace_id 一致
  （防止跨 workspace 越权）。
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import Depends, HTTPException, Path, status

from app.core.auth import Principal, get_current_principal

logger = logging.getLogger(__name__)


# ============================================================
# 权限常量
# ============================================================
class Perm:
    """权限常量命名空间。"""
    MEMORY_READ = "memory:read"
    MEMORY_WRITE = "memory:write"
    MEMORY_DELETE = "memory:delete"
    MEMORY_ADMIN = "memory:admin"

    WORKSPACE_READ = "workspace:read"
    WORKSPACE_WRITE = "workspace:write"
    WORKSPACE_ADMIN = "workspace:admin"

    API_KEY_MANAGE = "api_key:manage"


# ============================================================
# 角色 → 权限映射
# ============================================================
ROLE_PERMISSIONS = {
    "owner": [
        Perm.MEMORY_READ, Perm.MEMORY_WRITE, Perm.MEMORY_DELETE, Perm.MEMORY_ADMIN,
        Perm.WORKSPACE_READ, Perm.WORKSPACE_WRITE, Perm.WORKSPACE_ADMIN,
        Perm.API_KEY_MANAGE,
    ],
    "admin": [
        Perm.MEMORY_READ, Perm.MEMORY_WRITE, Perm.MEMORY_DELETE, Perm.MEMORY_ADMIN,
        Perm.WORKSPACE_READ, Perm.WORKSPACE_WRITE,
        Perm.API_KEY_MANAGE,
    ],
    "member": [
        Perm.MEMORY_READ, Perm.MEMORY_WRITE, Perm.MEMORY_DELETE,
        Perm.WORKSPACE_READ,
    ],
    "viewer": [
        Perm.MEMORY_READ,
        Perm.WORKSPACE_READ,
    ],
}


def role_has_permission(role: str, perm: str) -> bool:
    """检查角色是否拥有指定权限。"""
    return perm in ROLE_PERMISSIONS.get(role, [])


# ============================================================
# 依赖注入：require_permission
# ============================================================
def require_permission(perm: str):
    """FastAPI 依赖注入：要求当前主体拥有指定权限。

    用法：
        @router.post("/memory")
        async def create_memory(
            principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE)),
        ): ...
    """

    async def _checker(
        principal: Principal = Depends(get_current_principal),
    ) -> Principal:
        # JWT 用户：按 workspace 角色约束（viewer 不能写等）
        if principal.auth_method == "jwt":
            # 如果用户在该 workspace 有角色，按角色检查
            # 否则（例如未配置 workspace 的旧用户）放行，保持兼容
            if principal.workspace_id is not None:
                roles = _load_roles_for_principal(principal)
                if roles and not any(role_has_permission(r, perm) for r in roles):
                    logger.warning(
                        f"权限拒绝: user={principal.user_id} perm={perm} roles={roles}"
                    )
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Missing permission: {perm}",
                    )
            return principal

        # API Key：严格按 scopes 检查
        if not principal.has_scope(perm):
            logger.warning(
                f"API Key 权限拒绝: key_id={principal.api_key_id} perm={perm}"
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"API key missing permission: {perm}",
            )
        return principal

    return _checker


def require_workspace_access(
    workspace_id: int = Path(...),
    principal: Principal = Depends(get_current_principal),
) -> Principal:
    """校验请求的 workspace_id 与 Principal 可访问的 workspace 一致。

    JWT 用户：仅能访问自己所属的 workspace。
    API Key：仅能访问 key 所属的 workspace。
    """
    if principal.auth_method == "jwt":
        accessible = _load_accessible_workspaces(principal.user_id)
        if workspace_id not in accessible:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot access this workspace",
            )
    else:  # api_key
        if principal.workspace_id != workspace_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key belongs to a different workspace",
            )
    return principal


# ============================================================
# 内部辅助
# ============================================================
def _load_roles_for_principal(principal: Principal) -> List[str]:
    """从 DB 读取 Principal 在 workspace_id 下的角色列表。"""
    if principal.workspace_id is None:
        return []
    try:
        from app.core.db_client import get_db_client
        client = get_db_client()
        rows = client.execute(
            "SELECT role FROM workspace_members "
            "WHERE workspace_id = ? AND user_id = ?",
            (principal.workspace_id, principal.user_id),
        )
        return [r["role"] for r in rows] if rows else []
    except Exception as e:
        logger.warning(f"读取角色失败: {e}")
        return []


def _load_accessible_workspaces(user_id: int) -> List[int]:
    """读取用户所属的所有 workspace_id。"""
    try:
        from app.core.db_client import get_db_client
        client = get_db_client()
        rows = client.execute(
            "SELECT workspace_id FROM workspace_members WHERE user_id = ?",
            (user_id,),
        )
        return [r["workspace_id"] for r in rows] if rows else []
    except Exception as e:
        logger.warning(f"读取可访问 workspace 失败: {e}")
        return []


__all__ = ["Perm", "ROLE_PERMISSIONS", "require_permission", "require_workspace_access"]
