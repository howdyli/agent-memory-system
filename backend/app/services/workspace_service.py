"""Workspace 服务（Phase 2）。

提供 workspace 的创建 / 列表 / 成员管理 / 切换等核心能力。
所有写操作通过 SQLAlchemyStore 事务化执行，失败自动 rollback。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from app.core.store import get_relational_store
from app.models.orm import Organization, Workspace, WorkspaceMember

logger = logging.getLogger(__name__)


def create_workspace(
    name: str,
    slug: str,
    owner_user_id: int,
    kind: str = "team",
    org_id: Optional[int] = None,
) -> Dict:
    """创建 workspace。若未指定 org_id，自动创建/复用个人 org。

    Returns:
        {"workspace": {...}, "member": {...}}
    """
    store = get_relational_store()

    if org_id is None:
        # 为 owner 创建一个 personal org
        org = Organization(name=f"{name} Org", plan="free")
        org = store.create(org)
        org_id = org.id

    ws = Workspace(org_id=org_id, name=name, slug=slug, kind=kind)
    ws = store.create(ws)

    member = WorkspaceMember(workspace_id=ws.id, user_id=owner_user_id, role="owner")
    member = store.create(member)

    logger.info(f"✓ 创建 workspace: id={ws.id} slug={slug} owner={owner_user_id}")
    return {
        "workspace": _ws_to_dict(ws),
        "member": _member_to_dict(member),
    }


def list_workspaces(user_id: int) -> List[Dict]:
    """返回用户所属的所有 workspace。"""
    store = get_relational_store()
    members = store.query(WorkspaceMember, filters={"user_id": user_id})
    result = []
    for m in members:
        ws = store.get(Workspace, m.workspace_id)
        if ws:
            result.append({**_ws_to_dict(ws), "role": m.role, "joined_at": _dt(m.joined_at)})
    return result


def get_workspace(workspace_id: int) -> Optional[Dict]:
    store = get_relational_store()
    ws = store.get(Workspace, workspace_id)
    return _ws_to_dict(ws) if ws else None


def add_member(workspace_id: int, user_id: int, role: str = "member") -> Dict:
    store = get_relational_store()
    # 幂等：若已存在则更新 role
    existing = store.query(
        WorkspaceMember, filters={"workspace_id": workspace_id, "user_id": user_id}
    )
    if existing:
        updated = store.update(WorkspaceMember, existing[0].id, {"role": role})
        return _member_to_dict(updated)
    member = WorkspaceMember(workspace_id=workspace_id, user_id=user_id, role=role)
    member = store.create(member)
    return _member_to_dict(member)


def remove_member(workspace_id: int, user_id: int) -> bool:
    store = get_relational_store()
    existing = store.query(
        WorkspaceMember, filters={"workspace_id": workspace_id, "user_id": user_id}
    )
    if not existing:
        return False
    return store.delete(WorkspaceMember, existing[0].id)


def switch_workspace(user_id: int, workspace_id: int) -> bool:
    """切换用户的 default_workspace_id。"""
    store = get_relational_store()
    from app.models.orm import User
    # 校验用户是该 workspace 的成员
    member = store.query(
        WorkspaceMember, filters={"workspace_id": workspace_id, "user_id": user_id}
    )
    if not member:
        return False
    updated = store.update(User, user_id, {"default_workspace_id": workspace_id})
    return updated is not None


# ============================================================
# 内部辅助
# ============================================================
def _ws_to_dict(ws: Workspace) -> Dict:
    return {
        "id": ws.id,
        "org_id": ws.org_id,
        "name": ws.name,
        "slug": ws.slug,
        "kind": ws.kind,
        "created_at": _dt(ws.created_at),
    }


def _member_to_dict(m: WorkspaceMember) -> Dict:
    return {
        "id": m.id,
        "workspace_id": m.workspace_id,
        "user_id": m.user_id,
        "role": m.role,
        "joined_at": _dt(m.joined_at),
    }


def _dt(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


__all__ = [
    "create_workspace",
    "list_workspaces",
    "get_workspace",
    "add_member",
    "remove_member",
    "switch_workspace",
]
