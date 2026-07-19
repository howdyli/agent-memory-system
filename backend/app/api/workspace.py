"""Workspace API 路由（Phase 2）。

提供 workspace 的 CRUD、成员管理、切换默认 workspace。

所有端点挂载在 /api/v1/workspaces 下。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from app.core.auth import Principal, get_current_principal
from app.core.errors import ForbiddenError, NotFoundError
from app.core.rbac import Perm, require_permission, require_workspace_access
from app.services import workspace_service

router = APIRouter()


# ============================================================
# 请求/响应模型
# ============================================================
class WorkspaceCreate(BaseModel):
    name: str
    slug: str
    kind: str = "team"  # personal | team


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = None


class MemberAdd(BaseModel):
    user_id: int
    role: str = "member"  # owner | admin | member | viewer


class WorkspaceSwitch(BaseModel):
    workspace_id: int


class WorkspaceOut(BaseModel):
    id: int
    org_id: int
    name: str
    slug: str
    kind: str
    role: Optional[str] = None
    joined_at: Optional[str] = None
    created_at: Optional[str] = None


class MemberOut(BaseModel):
    id: int
    workspace_id: int
    user_id: int
    role: str
    joined_at: Optional[str] = None


# ============================================================
# 路由
# ============================================================
@router.get("", response_model=List[WorkspaceOut])
async def list_workspaces(
    principal: Principal = Depends(require_permission(Perm.WORKSPACE_READ)),
):
    """列出当前用户所属的所有 workspace。"""
    return workspace_service.list_workspaces(principal.user_id)


@router.post("", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreate,
    principal: Principal = Depends(require_permission(Perm.WORKSPACE_ADMIN)),
):
    """创建 team workspace（需 workspace:admin 权限）。"""
    result = workspace_service.create_workspace(
        name=body.name,
        slug=body.slug,
        owner_user_id=principal.user_id,
        kind=body.kind,
    )
    return result["workspace"]


@router.get("/{workspace_id}", response_model=WorkspaceOut)
async def get_workspace(
    principal: Principal = Depends(require_workspace_access),
    workspace_id: int = 0,  # 由 require_workspace_access 校验
):
    """获取 workspace 详情。"""
    ws = workspace_service.get_workspace(workspace_id)
    if ws is None:
        raise NotFoundError("Workspace not found")
    return ws


@router.put("/{workspace_id}", response_model=WorkspaceOut)
async def update_workspace(
    body: WorkspaceUpdate,
    principal: Principal = Depends(require_permission(Perm.WORKSPACE_WRITE)),
    workspace_id: int = 0,
):
    """更新 workspace 名称（需 workspace:write）。"""
    # 简化：当前仅支持 name 更新；完整实现可走 Store session
    from app.core.store import get_relational_store
    from app.models.orm import Workspace
    store = get_relational_store()
    ws = store.get(Workspace, workspace_id)
    if ws is None:
        raise NotFoundError("Workspace not found")
    values = {}
    if body.name is not None:
        values["name"] = body.name
    if values:
        store.update(Workspace, workspace_id, values)
        ws = store.get(Workspace, workspace_id)
    return {
        "id": ws.id, "org_id": ws.org_id, "name": ws.name,
        "slug": ws.slug, "kind": ws.kind,
        "created_at": ws.created_at.isoformat() if ws.created_at else None,
    }


@router.post("/{workspace_id}/members", response_model=MemberOut)
async def add_member(
    body: MemberAdd,
    principal: Principal = Depends(require_permission(Perm.WORKSPACE_WRITE)),
    workspace_id: int = 0,
):
    """添加成员（需 workspace:write）。"""
    return workspace_service.add_member(workspace_id, body.user_id, body.role)


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(
    principal: Principal = Depends(require_permission(Perm.WORKSPACE_WRITE)),
    workspace_id: int = 0,
    user_id: int = 0,
):
    """移除成员。"""
    ok = workspace_service.remove_member(workspace_id, user_id)
    if not ok:
        raise NotFoundError("Member not found")


@router.post("/switch")
async def switch_workspace(
    body: WorkspaceSwitch,
    principal: Principal = Depends(get_current_principal),
):
    """切换当前用户的 default_workspace_id。"""
    ok = workspace_service.switch_workspace(principal.user_id, body.workspace_id)
    if not ok:
        raise ForbiddenError("You are not a member of this workspace")
    return {"status": "ok", "workspace_id": body.workspace_id}
