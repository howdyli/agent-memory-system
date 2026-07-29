"""
长期记忆管理 API 路由

提供记忆版本控制、自我改进、长期记忆管理 API
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, Any, Dict, List, Literal

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.long_term_memory_service import (
    # Task 22: 版本控制
    record_version,
    get_version_history,
    rollback_to_version,
    get_audit_log,
    # Task 23: 自我改进
    submit_feedback,
    auto_adjust_importance,
    get_self_improvement_stats,
    # Task 24: 长期记忆管理
    get_all_memories,
    batch_delete_memories,
    adjust_memory_weight,
)
from app.core.auth import Principal, get_current_principal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["long-term-memory"])


class RecordVersionRequest(BaseModel):
    memory_type: Literal["fragment", "variable", "table"]
    memory_id: str = Field(..., min_length=1)
    action: str = Field(..., min_length=1, max_length=50)
    old_value: Optional[Any] = None
    new_value: Optional[Any] = None


class RollbackRequest(BaseModel):
    memory_type: Literal["fragment", "variable", "table"]
    memory_id: str = Field(..., min_length=1)
    target_version: int = Field(..., ge=1)


class FeedbackRequest(BaseModel):
    memory_type: Literal["fragment", "variable", "table"]
    memory_id: str = Field(..., min_length=1)
    feedback_type: Literal["positive", "negative"]
    feedback_value: Optional[float] = Field(1.0, ge=0.0, le=1.0)


class BatchDeleteRequest(BaseModel):
    memory_ids: List[Dict[str, str]] = Field(..., min_length=1)  # [{"type": "fragment", "id": "1"}, ...]


class AdjustWeightRequest(BaseModel):
    memory_type: Literal["fragment", "variable", "table"]
    memory_id: str = Field(..., min_length=1)
    new_weight: float = Field(..., ge=0.0, le=2.0)


# Task 22: 版本控制 API

@router.get("/memories")
async def get_all_memories_api(
    memory_type: Optional[str] = None,
    sort_by: Optional[str] = "importance",
    sort_order: Optional[str] = "DESC",
    limit: Optional[int] = 50,
    offset: Optional[int] = 0,
    principal: Principal = Depends(get_current_principal)
):
    """获取所有长期记忆（支持分页、过滤、排序）"""
    try:
        result = get_all_memories(
            user_id=principal.user_id,
            memory_type=memory_type,
            sort_by=sort_by or "importance",
            sort_order=sort_order or "DESC",
            limit=limit or 50,
            offset=offset or 0
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to get memories")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 获取记忆失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/versions", status_code=status.HTTP_201_CREATED)
async def record_version_api(
    request: RecordVersionRequest,
    principal: Principal = Depends(get_current_principal)
):
    """记录记忆版本变更"""
    try:
        result = record_version(
            user_id=principal.user_id,
            memory_type=request.memory_type,
            memory_id=request.memory_id,
            action=request.action,
            old_value=request.old_value,
            new_value=request.new_value
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to record version")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 记录版本失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/versions/{memory_type}/{memory_id}")
async def get_version_history_api(
    memory_type: str,
    memory_id: str,
    principal: Principal = Depends(get_current_principal)
):
    """获取记忆的版本历史"""
    try:
        result = get_version_history(
            user_id=principal.user_id,
            memory_type=memory_type,
            memory_id=memory_id
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to get version history")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 获取版本历史失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/rollback")
async def rollback_api(
    request: RollbackRequest,
    principal: Principal = Depends(get_current_principal)
):
    """回滚到指定版本"""
    try:
        result = rollback_to_version(
            user_id=principal.user_id,
            memory_type=request.memory_type,
            memory_id=request.memory_id,
            target_version=request.target_version
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Rollback failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 回滚失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/audit-log")
async def get_audit_log_api(
    memory_type: Optional[str] = None,
    limit: Optional[int] = 50,
    offset: Optional[int] = 0,
    principal: Principal = Depends(get_current_principal)
):
    """获取记忆变更审计日志"""
    try:
        result = get_audit_log(
            user_id=principal.user_id,
            memory_type=memory_type,
            limit=limit or 50,
            offset=offset or 0
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to get audit log")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 获取审计日志失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# Task 23: 自我改进 API

@router.post("/feedback")
async def submit_feedback_api(
    request: FeedbackRequest,
    principal: Principal = Depends(get_current_principal)
):
    """提交记忆反馈"""
    try:
        result = submit_feedback(
            user_id=principal.user_id,
            memory_type=request.memory_type,
            memory_id=request.memory_id,
            feedback_type=request.feedback_type,
            feedback_value=request.feedback_value or 1.0
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Feedback submission failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 提交反馈失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/auto-adjust")
async def auto_adjust_api(
    principal: Principal = Depends(get_current_principal)
):
    """自动调整记忆重要性评分"""
    try:
        result = auto_adjust_importance(principal.user_id)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Auto adjust failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 自动调整失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/improvement-stats")
async def get_improvement_stats_api(
    principal: Principal = Depends(get_current_principal)
):
    """获取自我改进效果统计"""
    try:
        result = get_self_improvement_stats(principal.user_id)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Stats retrieval failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 获取统计失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# Task 24: 长期记忆管理 API

@router.post("/batch-delete")
async def batch_delete_api(
    request: BatchDeleteRequest,
    principal: Principal = Depends(get_current_principal)
):
    """批量删除记忆"""
    try:
        result = batch_delete_memories(
            user_id=principal.user_id,
            memory_ids=request.memory_ids
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Batch delete failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 批量删除失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/adjust-weight")
async def adjust_weight_api(
    request: AdjustWeightRequest,
    principal: Principal = Depends(get_current_principal)
):
    """调整记忆权重"""
    try:
        result = adjust_memory_weight(
            user_id=principal.user_id,
            memory_type=request.memory_type,
            memory_id=request.memory_id,
            new_weight=request.new_weight
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Weight adjustment failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 调整权重失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
