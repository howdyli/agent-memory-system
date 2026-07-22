"""
Memory Lifecycle API 路由

提供记忆生命周期管理 REST API：
1. 半衰期信息与过期清理
2. 弹性驱逐（冷记忆、归档、恢复）
3. 自动合并与冲突检测
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from typing import Optional, Any, Dict, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.memory_lifecycle_service import (
    # 半衰期
    get_half_life_info,
    calculate_decay_score,
    estimate_remaining_life,
    cleanup_expired_memories,

    # 弹性驱逐
    mark_cold,
    auto_archive_cold_memories,
    soft_delete,
    restore_memory,
    hard_delete,
    list_cold_memories,
    list_deleted_memories,
    get_delete_log,
    get_lifecycle_stats,

    # 合并与冲突
    find_duplicates,
    detect_conflicts,
    merge_memories,
    resolve_conflict,
    list_pending_conflicts,
    list_merge_log,
)
from app.core.auth import Principal, get_current_principal
from app.core.rbac import Perm, require_permission

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-lifecycle"])


# ============================================================
# 请求 / 响应模型
# ============================================================

class MarkColdRequest(BaseModel):
    memory_type: str  # fragment, variable
    memory_id: str
    reason: Optional[str] = "user_requested"


class SoftDeleteRequest(BaseModel):
    memory_type: str
    memory_id: str
    reason: Optional[str] = None


class HardDeleteRequest(BaseModel):
    memory_type: str
    memory_id: str
    reason: Optional[str] = None


class MergeRequest(BaseModel):
    source_ids: List[int]
    target_content: str
    target_type: Optional[str] = "info"


class ResolveConflictRequest(BaseModel):
    conflict_id: Optional[int] = None
    resolution: str
    merged_value: Optional[str] = None


class ArchiveRequest(BaseModel):
    cold_days: Optional[int] = 30


class FindDuplicatesRequest(BaseModel):
    content: str
    threshold: Optional[float] = 0.85
    limit: Optional[int] = 10


class DetectConflictRequest(BaseModel):
    key: str
    new_value: str


# ============================================================
# 1. 半衰期与生命周期查询
# ============================================================

@router.get("/memory/lifecycle/half-life/{fragment_type}")
async def get_half_life_api(
    fragment_type: str,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取指定记忆类型的半衰期配置信息"""
    try:
        result = get_half_life_info(fragment_type)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/lifecycle/stats")
async def get_stats_api(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取记忆生命周期统计信息"""
    try:
        result = get_lifecycle_stats(principal.user_id)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/lifecycle/cold")
async def list_cold_api(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """列出用户的冷记忆"""
    try:
        result = list_cold_memories(principal.user_id, limit=limit, offset=offset)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/lifecycle/deleted")
async def list_deleted_api(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """列出用户已软删除的记忆（可恢复窗口）"""
    try:
        result = list_deleted_memories(principal.user_id, limit=limit, offset=offset)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 2. 弹性驱逐操作
# ============================================================

@router.post("/memory/lifecycle/cold/mark")
async def mark_cold_api(
    request: MarkColdRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """标记记忆为冷记忆"""
    try:
        result = mark_cold(
            user_id=principal.user_id,
            memory_type=request.memory_type,
            memory_id=request.memory_id,
            reason=request.reason,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/lifecycle/{memory_type}/{memory_id}/archive")
async def archive_memory_api(
    memory_type: str,
    memory_id: str,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """归档指定记忆"""
    try:
        result = mark_cold(
            user_id=principal.user_id,
            memory_type=memory_type,
            memory_id=memory_id,
            reason="archived_by_user",
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/lifecycle/{memory_type}/{memory_id}/soft-delete")
async def soft_delete_api(
    memory_type: str,
    memory_id: str,
    request: Optional[SoftDeleteRequest] = None,
    principal: Principal = Depends(require_permission(Perm.MEMORY_DELETE))
):
    """软删除记忆（可恢复）"""
    try:
        reason = request.reason if request else None
        result = soft_delete(
            user_id=principal.user_id,
            memory_type=memory_type,
            memory_id=memory_id,
            reason=reason,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/lifecycle/{memory_type}/{memory_id}/restore")
async def restore_memory_api(
    memory_type: str,
    memory_id: str,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """恢复软删除的记忆"""
    try:
        result = restore_memory(
            user_id=principal.user_id,
            memory_type=memory_type,
            memory_id=memory_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/lifecycle/{memory_type}/{memory_id}/hard-delete")
async def hard_delete_api(
    memory_type: str,
    memory_id: str,
    request: Optional[HardDeleteRequest] = None,
    principal: Principal = Depends(require_permission(Perm.MEMORY_DELETE))
):
    """永久删除记忆（不可恢复）"""
    try:
        reason = request.reason if request else None
        result = hard_delete(
            user_id=principal.user_id,
            memory_type=memory_type,
            memory_id=memory_id,
            reason=reason,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 3. 批量操作
# ============================================================

@router.post("/memory/lifecycle/auto-archive")
async def auto_archive_api(
    request: Optional[ArchiveRequest] = None,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """触发自动归档冷记忆"""
    try:
        cold_days = request.cold_days if request else 30
        result = auto_archive_cold_memories(
            user_id=principal.user_id,
            cold_days=cold_days,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/lifecycle/run-cleanup")
async def run_cleanup_api(
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """触发过期记忆清理"""
    try:
        result = cleanup_expired_memories()
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 4. 审计日志
# ============================================================

@router.get("/memory/lifecycle/delete-log")
async def get_delete_log_api(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取删除操作审计日志"""
    try:
        result = get_delete_log(principal.user_id, limit=limit, offset=offset)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/lifecycle/merge-log")
async def get_merge_log_api(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取合并操作审计日志"""
    try:
        result = list_merge_log(principal.user_id, limit=limit, offset=offset)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 5. 合并与冲突检测
# ============================================================

@router.post("/memory/lifecycle/duplicates/find")
async def find_duplicates_api(
    request: FindDuplicatesRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """检测与指定内容重复的记忆"""
    try:
        result = find_duplicates(
            user_id=principal.user_id,
            content=request.content,
            threshold=request.threshold,
            limit=request.limit,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/lifecycle/duplicates/merge")
async def merge_memories_api(
    request: MergeRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """合并多条重复记忆"""
    try:
        result = merge_memories(
            user_id=principal.user_id,
            source_ids=request.source_ids,
            target_content=request.target_content,
            target_type=request.target_type,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/lifecycle/conflicts/detect")
async def detect_conflicts_api(
    request: DetectConflictRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """检测记忆值冲突"""
    try:
        result = detect_conflicts(
            user_id=principal.user_id,
            key=request.key,
            new_value=request.new_value,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/lifecycle/conflicts")
async def list_conflicts_api(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """列出待处理的冲突"""
    try:
        result = list_pending_conflicts(principal.user_id, workspace_id=principal.workspace_id)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/lifecycle/conflicts/resolve")
async def resolve_conflict_api(
    request: ResolveConflictRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """解决一条已检测到的冲突（兼容旧版 body 传 conflict_id）"""
    if request.conflict_id is None:
        raise HTTPException(status_code=422, detail="缺少 conflict_id")
    try:
        result = resolve_conflict(
            user_id=principal.user_id,
            conflict_id=request.conflict_id,
            resolution=request.resolution,
            merged_value=request.merged_value,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/lifecycle/conflicts/{conflict_id}/resolve")
async def resolve_conflict_by_id_api(
    conflict_id: int,
    request: ResolveConflictRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """解决一条已检测到的冲突（RESTful 路径传 conflict_id）"""
    try:
        result = resolve_conflict(
            user_id=principal.user_id,
            conflict_id=conflict_id,
            resolution=request.resolution,
            merged_value=request.merged_value,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
