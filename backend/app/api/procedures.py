"""
程序记忆 API 路由（P2 R-14）

- POST /：手动录入操作步骤（组装标准 content 入库为 procedure 片段）
- GET /：列出 procedure 片段
- PUT /{fragment_id} / DELETE /{fragment_id}：更新/删除（校验类型为 procedure）
- POST /extract：手动触发当前用户轨迹提炼
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional, List

from app.services.memory_fragment_service import (
    update_fragment,
    delete_fragment,
    list_fragments,
    create_fragment,
)
from app.services.procedure_extraction_service import (
    build_procedure_content,
    extract_procedures_for_user,
)
from app.core.auth import Principal
from app.core.rbac import Perm, require_permission
from app.core.errors import handle_service_result

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-procedures"])


# ============================================================
# 请求模型
# ============================================================

class ProcedureCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    steps: List[str] = Field(..., min_length=1, max_length=50)
    applicable_scenario: Optional[str] = Field(None, max_length=500)
    workspace_id: Optional[int] = None
    agent_id: Optional[int] = None
    scope: str = Field("shared", pattern="^(shared|private)$")


class ProcedureUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=200)
    steps: Optional[List[str]] = Field(None, min_length=1, max_length=50)
    applicable_scenario: Optional[str] = Field(None, max_length=500)
    importance_score: Optional[float] = Field(None, ge=0.0, le=1.0)


# ============================================================
# 工具：校验片段归属且类型为 procedure
# ============================================================

def _get_procedure_or_404(user_id: int, fragment_id: int) -> dict:
    # 直接按 id+user 查询（不受 workspace 过滤影响，后续操作携带实际 workspace_id）
    from app.core.db_client import get_db_client
    rows = get_db_client().execute(
        'SELECT * FROM memory_fragments WHERE id = ? AND user_id = ?',
        (fragment_id, user_id)
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"片段 {fragment_id} 不存在")
    fragment = dict(rows[0])
    if fragment.get("fragment_type") != "procedure":
        raise HTTPException(status_code=400, detail=f"片段 {fragment_id} 不是 procedure 类型")
    return fragment


# ============================================================
# CRUD API
# ============================================================

@router.post("/", status_code=201, summary="手动录入程序记忆")
@handle_service_result
async def create_procedure(
    request: ProcedureCreateRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """手动录入操作步骤为 procedure 记忆"""
    content = build_procedure_content(
        title=request.title,
        steps=request.steps,
        applicable_scenario=request.applicable_scenario,
    )
    return create_fragment(
        user_id=principal.user_id,
        fragment_type="procedure",
        content=content,
        importance_score=0.7,
        metadata={"source": "manual"},
        workspace_id=request.workspace_id,
        agent_id=request.agent_id,
        scope=request.scope,
    )


@router.get("/", summary="程序记忆列表")
@handle_service_result
async def list_procedures(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    workspace_id: Optional[int] = Query(None),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """列出当前用户的 procedure 片段"""
    return list_fragments(
        user_id=principal.user_id,
        fragment_type="procedure",
        limit=limit,
        offset=offset,
        workspace_id=workspace_id,
    )


@router.put("/{fragment_id}", summary="更新程序记忆")
@handle_service_result
async def update_procedure(
    fragment_id: int,
    request: ProcedureUpdateRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """更新 procedure 片段（标题/步骤/场景变更时重组 content）"""
    fragment = _get_procedure_or_404(principal.user_id, fragment_id)

    content = None
    if request.title is not None or request.steps is not None or request.applicable_scenario is not None:
        if not (request.title and request.steps):
            raise HTTPException(
                status_code=422,
                detail="更新流程内容时 title 与 steps 必须同时提供"
            )
        content = build_procedure_content(
            title=request.title,
            steps=request.steps,
            applicable_scenario=request.applicable_scenario,
        )

    if content is None and request.importance_score is None:
        raise HTTPException(status_code=422, detail="没有可更新的字段")

    return update_fragment(
        user_id=principal.user_id,
        fragment_id=fragment_id,
        content=content,
        importance_score=request.importance_score,
        workspace_id=fragment.get("workspace_id"),
    )


@router.delete("/{fragment_id}", summary="删除程序记忆")
@handle_service_result
async def delete_procedure(
    fragment_id: int,
    principal: Principal = Depends(require_permission(Perm.MEMORY_DELETE))
):
    """删除 procedure 片段"""
    fragment = _get_procedure_or_404(principal.user_id, fragment_id)
    return delete_fragment(
        user_id=principal.user_id,
        fragment_id=fragment_id,
        workspace_id=fragment.get("workspace_id"),
    )


# ============================================================
# 轨迹提炼
# ============================================================

@router.post("/extract", summary="触发轨迹提炼")
@handle_service_result
async def trigger_extraction(
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """手动触发当前用户的工具轨迹 → procedure 记忆提炼"""
    return extract_procedures_for_user(principal.user_id)
