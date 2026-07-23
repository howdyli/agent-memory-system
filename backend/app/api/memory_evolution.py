"""
Memory Evolution API 路由（R-05 矛盾检测与调和引擎）

提供记忆演变管理的 REST API：
- 演变链追溯：查询某事实的完整变化历史
- 演变历史查询：查询某记忆片段参与的演变事件
- 演变统计：汇总用户的演变数据
- 手动矛盾检测：对指定内容触发矛盾检测
"""
import logging
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import Principal
from app.core.rbac import Perm, require_permission
from app.services.contradiction_service import (
    detect_contradiction,
    get_evolution_chain,
    get_evolution_history,
    get_evolution_statistics,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-evolution"])


# ============================================================
# 演变链追溯 API
# ============================================================

@router.get("/memory/evolution/chain", summary="演变链追溯", description="查询某事实的完整变化历史（v1 → v2 → v3 ...）")
async def get_evolution_chain_api(
    entity_type: Literal["location", "organization", "title", "status", "semantic"] = Query(
        ..., description="实体类型"
    ),
    entity_key: Optional[str] = Query(None, description="实体 key（为空则返回该类型的所有演变）"),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """追溯某事实的完整演变链。"""
    result = get_evolution_chain(
        user_id=principal.user_id,
        entity_type=entity_type,
        entity_key=entity_key,
        workspace_id=principal.workspace_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ============================================================
# 演变历史查询 API
# ============================================================

@router.get("/memory/evolution/fragment/{fragment_id}", summary="片段演变历史", description="查询某记忆片段参与的所有演变事件")
async def get_evolution_history_api(
    fragment_id: int,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """查询某个记忆片段相关的演变历史。"""
    result = get_evolution_history(
        user_id=principal.user_id,
        fragment_id=fragment_id,
        workspace_id=principal.workspace_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ============================================================
# 演变统计 API
# ============================================================

@router.get("/memory/evolution/statistics", summary="演变统计", description="获取用户的记忆演变统计信息")
async def get_evolution_statistics_api(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取用户的演变统计信息。"""
    result = get_evolution_statistics(
        user_id=principal.user_id,
        workspace_id=principal.workspace_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ============================================================
# 手动矛盾检测 API
# ============================================================

@router.post("/memory/evolution/detect", summary="手动矛盾检测", description="对指定内容触发矛盾检测（不创建记忆）")
async def detect_contradiction_api(
    content: str = Query(..., description="待检测的内容"),
    enable_semantic: bool = Query(True, description="是否启用语义检测"),
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE)),
):
    """手动触发矛盾检测，返回检测结果（不创建记忆片段）。"""
    result = detect_contradiction(
        user_id=principal.user_id,
        new_content=content,
        new_fragment_id=None,
        workspace_id=principal.workspace_id,
        enable_semantic=enable_semantic,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result
