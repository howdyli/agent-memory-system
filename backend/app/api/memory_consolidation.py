"""
Memory Consolidation API 路由（P1 R-11 睡眠期记忆巩固）

提供巩固手动触发/预览、运行历史与统计查询 REST API。
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from typing import Optional

from app.services.memory_consolidation_service import (
    run_consolidation,
    get_consolidation_history,
    get_consolidation_statistics,
)
from app.core.auth import Principal
from app.core.rbac import Perm, require_permission

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-consolidation"])


class RunConsolidationRequest(BaseModel):
    dry_run: Optional[bool] = False


@router.post("/memory/consolidation/run")
async def run_consolidation_api(
    request: RunConsolidationRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """手动触发记忆巩固（dry_run=True 时仅返回聚簇预览，不调 LLM、不写库）"""
    try:
        result = run_consolidation(
            user_id=principal.user_id,
            workspace_id=principal.workspace_id,
            dry_run=request.dry_run or False,
            trigger="manual",
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Consolidation failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 记忆巩固触发失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/memory/consolidation/history")
async def consolidation_history_api(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """分页查询巩固运行历史"""
    try:
        result = get_consolidation_history(principal.user_id, limit=limit, offset=offset)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to get history")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 查询巩固历史失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/memory/consolidation/statistics")
async def consolidation_statistics_api(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """巩固统计（累计巩固条数、节省条数、按 trigger 分布）"""
    try:
        result = get_consolidation_statistics(principal.user_id)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to get statistics")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 查询巩固统计失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
