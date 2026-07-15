"""
自动记忆召回 API 路由

提供自动召回、配置管理、效果统计 API
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, Any, Dict, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.auto_recall_service import (
    auto_recall,
    generate_auto_summary,
    search_relevant_memories,
    inject_memory_context,
    get_recall_config,
    update_recall_config,
    get_recall_stats,
)
from app.core.auth import get_current_user, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auto-recall"])


class AutoRecallRequest(BaseModel):
    query: str


class GenerateSummaryRequest(BaseModel):
    messages: List[Dict[str, str]]


class SearchMemoriesRequest(BaseModel):
    query: str
    top_k: Optional[int] = 5
    threshold: Optional[float] = 0.3


class InjectContextRequest(BaseModel):
    query: str
    max_length: Optional[int] = 2000
    format: Optional[str] = "structured"


class UpdateConfigRequest(BaseModel):
    config: Dict[str, Any]


@router.post("/")
async def auto_recall_api(
    request: AutoRecallRequest,
    current_user: User = Depends(get_current_user)
):
    """自动记忆召回（一键召回相关记忆并注入上下文）"""
    try:
        result = auto_recall(current_user.user_id, request.query)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Auto recall failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 自动召回失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/summary")
async def auto_summary_api(
    request: GenerateSummaryRequest,
    current_user: User = Depends(get_current_user)
):
    """生成对话历史自动摘要（带缓存和增量更新）"""
    try:
        result = generate_auto_summary(current_user.user_id, request.messages)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Summary generation failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 自动摘要失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/search")
async def search_memories_api(
    request: SearchMemoriesRequest,
    current_user: User = Depends(get_current_user)
):
    """相关性检索（Top-K 记忆检索）"""
    try:
        result = search_relevant_memories(
            user_id=current_user.user_id,
            query=request.query,
            top_k=request.top_k or 5,
            threshold=request.threshold or 0.3
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Search failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 检索失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/inject")
async def inject_context_api(
    request: InjectContextRequest,
    current_user: User = Depends(get_current_user)
):
    """上下文注入（将记忆注入到对话上下文）"""
    try:
        result = inject_memory_context(
            user_id=current_user.user_id,
            query=request.query,
            max_length=request.max_length or 2000,
            format=request.format or "structured"
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Context injection failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 上下文注入失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/config")
async def get_config_api(
    current_user: User = Depends(get_current_user)
):
    """获取自动召回配置"""
    try:
        config = get_recall_config(current_user.user_id)
        return {
            "success": True,
            "config": config
        }
    except Exception as e:
        logger.error(f"✗ 获取配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.put("/config")
async def update_config_api(
    request: UpdateConfigRequest,
    current_user: User = Depends(get_current_user)
):
    """更新自动召回配置"""
    try:
        result = update_recall_config(current_user.user_id, request.config)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Config update failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 更新配置失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/stats")
async def get_stats_api(
    current_user: User = Depends(get_current_user)
):
    """获取召回效果统计"""
    try:
        result = get_recall_stats(current_user.user_id)
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
