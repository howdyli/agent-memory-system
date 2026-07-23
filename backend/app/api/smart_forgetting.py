"""
Smart Forgetting API 路由（R-07 智能遗忘机制）

提供智能遗忘管理的 REST API：
- 重要性重算：手动触发多因子重要性评分重算
- 重要性分解查询：查看单条记忆的评分因子分解
- 遗忘统计：获取重要性分布和遗忘统计
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.auth import Principal
from app.core.rbac import Perm, require_permission
from app.services.smart_forgetting_service import (
    recalculate_importance,
    get_importance_breakdown,
    get_forgetting_statistics,
    DEFAULT_WEIGHTS,
    DEFAULT_FORGET_THRESHOLD,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["smart-forgetting"])


# ============================================================
# 重要性重算 API
# ============================================================

@router.post("/memory/forgetting/recalculate", summary="重要性重算", description="基于多因子评分（召回频率/时间衰减/证据强度/矛盾次数）批量重算记忆重要性，可选自动遗忘低价值记忆")
async def recalculate_importance_api(
    auto_forget: bool = Query(True, description="是否自动将低重要性记忆标记为冷记忆"),
    forget_threshold: float = Query(
        DEFAULT_FORGET_THRESHOLD, ge=0.0, le=1.0, description="遗忘阈值（低于此值的记忆将被降级）"
    ),
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE)),
):
    """手动触发智能遗忘重要性重算。"""
    result = recalculate_importance(
        user_id=principal.user_id,
        workspace_id=principal.workspace_id,
        forget_threshold=forget_threshold,
        auto_forget=auto_forget,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ============================================================
# 重要性分解查询 API
# ============================================================

@router.get("/memory/forgetting/importance/{fragment_id}", summary="重要性分解查询", description="查看单条记忆的多因子重要性评分分解")
async def get_importance_breakdown_api(
    fragment_id: int,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取单条记忆的重要性评分因子分解。"""
    result = get_importance_breakdown(
        user_id=principal.user_id,
        fragment_id=fragment_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


# ============================================================
# 遗忘统计 API
# ============================================================

@router.get("/memory/forgetting/statistics", summary="遗忘统计", description="获取智能遗忘的统计信息（重要性分布、状态分布等）")
async def get_forgetting_statistics_api(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取智能遗忘统计信息。"""
    result = get_forgetting_statistics(
        user_id=principal.user_id,
        workspace_id=principal.workspace_id,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


# ============================================================
# 配置查询 API
# ============================================================

@router.get("/memory/forgetting/config", summary="遗忘配置", description="获取智能遗忘的权重配置和阈值")
async def get_forgetting_config_api(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取智能遗忘的配置参数。"""
    return {
        "success": True,
        "weights": DEFAULT_WEIGHTS,
        "forget_threshold": DEFAULT_FORGET_THRESHOLD,
        "formula": (
            "importance = w1*recall_frequency + w2*time_decay + "
            "w3*evidence + w4*contradiction"
        ),
        "factor_descriptions": {
            "recall_frequency": "基于 memory_trace_events 中 recalled 事件次数（对数归一化）",
            "time_decay": "基于半衰期的指数衰减（2^(-days/half_life)）",
            "evidence": "原始 importance_score（用户/系统设定的重要性）",
            "contradiction": "被标记为 superseded 的记忆获得 0 分惩罚",
        },
    }
