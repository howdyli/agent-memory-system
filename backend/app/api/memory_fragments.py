"""
记忆片段 API 路由

提供记忆片段的 CRUD、Prompt 模板管理、语义搜索 API
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, Any, Dict, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.memory_fragment_service import (
    # Task 11: 对话分析
    analyze_conversation_history,
    generate_summary,
    extract_fragments,
    # Task 12: Prompt 模板
    get_extraction_prompt,
    create_extraction_prompt,
    list_extraction_prompts,
    render_prompt,
    # Task 13: TTL 机制
    create_fragment,
    get_fragment,
    update_fragment,
    delete_fragment,
    list_fragments,
    cleanup_expired_fragments,
    # Task 14: 语义搜索
    search_fragments_by_semantic,
)
from app.core.auth import get_current_user, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-fragments"])


# ============================================================
# 请求模型
# ============================================================

class AnalyzeConversationRequest(BaseModel):
    messages: List[Dict[str, str]]


class GenerateSummaryRequest(BaseModel):
    messages: List[Dict[str, str]]
    max_length: Optional[int] = 200


class ExtractFragmentsRequest(BaseModel):
    messages: List[Dict[str, str]]


class CreatePromptRequest(BaseModel):
    prompt_name: str
    template: str


class RenderPromptRequest(BaseModel):
    prompt_name: str
    variables: Dict[str, Any]


class CreateFragmentRequest(BaseModel):
    fragment_type: str  # info, preference, plan
    content: str
    ttl: Optional[int] = None  # 秒
    importance_score: Optional[float] = 0.5
    metadata: Optional[Dict[str, Any]] = None


class UpdateFragmentRequest(BaseModel):
    content: Optional[str] = None
    ttl: Optional[int] = None
    importance_score: Optional[float] = None


class SemanticSearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = 5
    threshold: Optional[float] = 0.3


class BatchDeleteFragmentsRequest(BaseModel):
    fragment_ids: List[int]


# ============================================================
# Task 11: 对话历史分析与摘要生成
# ============================================================

@router.post("/analyze")
async def analyze_conversation(
    request: AnalyzeConversationRequest,
    current_user: User = Depends(get_current_user)
):
    """分析对话历史，识别用户信息、偏好、计划等"""
    try:
        result = analyze_conversation_history(request.messages)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Analysis failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 对话分析失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/summary")
async def generate_summary_api(
    request: GenerateSummaryRequest,
    current_user: User = Depends(get_current_user)
):
    """生成对话摘要"""
    try:
        result = generate_summary(request.messages, request.max_length or 200)
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
        logger.error(f"✗ 摘要生成失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/extract")
async def extract_fragments_api(
    request: ExtractFragmentsRequest,
    current_user: User = Depends(get_current_user)
):
    """从对话历史中抽取记忆片段"""
    try:
        result = extract_fragments(request.messages)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Fragment extraction failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 片段抽取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# ============================================================
# Task 12: Prompt 模板管理
# ============================================================

@router.get("/prompts")
async def list_prompts_api(
    current_user: User = Depends(get_current_user)
):
    """列出所有可用的抽取 Prompt 模板"""
    try:
        result = list_extraction_prompts(current_user.user_id)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to list prompts")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 列出 Prompt 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/prompts/{prompt_name}")
async def get_prompt_api(
    prompt_name: str,
    current_user: User = Depends(get_current_user)
):
    """获取指定的 Prompt 模板"""
    try:
        result = get_extraction_prompt(current_user.user_id, prompt_name)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result.get("error", "Prompt not found")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 获取 Prompt 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/prompts")
async def create_prompt_api(
    request: CreatePromptRequest,
    current_user: User = Depends(get_current_user)
):
    """创建或更新自定义 Prompt 模板"""
    try:
        result = create_extraction_prompt(
            user_id=current_user.user_id,
            prompt_name=request.prompt_name,
            template=request.template
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to create prompt")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 创建 Prompt 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/prompts/render")
async def render_prompt_api(
    request: RenderPromptRequest,
    current_user: User = Depends(get_current_user)
):
    """渲染 Prompt 模板"""
    try:
        result = render_prompt(
            user_id=current_user.user_id,
            prompt_name=request.prompt_name,
            variables=request.variables
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to render prompt")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 渲染 Prompt 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# ============================================================
# Task 13 & 15: 记忆片段 CRUD
# ============================================================

@router.post("/")
async def create_fragment_api(
    request: CreateFragmentRequest,
    current_user: User = Depends(get_current_user)
):
    """创建记忆片段（支持 TTL）"""
    try:
        result = create_fragment(
            user_id=current_user.user_id,
            fragment_type=request.fragment_type,
            content=request.content,
            ttl=request.ttl,
            importance_score=request.importance_score or 0.5,
            metadata=request.metadata
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to create fragment")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 创建片段失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/")
async def list_fragments_api(
    fragment_type: Optional[str] = None,
    limit: Optional[int] = 100,
    offset: Optional[int] = 0,
    current_user: User = Depends(get_current_user)
):
    """列出记忆片段"""
    try:
        result = list_fragments(
            user_id=current_user.user_id,
            fragment_type=fragment_type,
            limit=limit or 100,
            offset=offset or 0
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to list fragments")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 列出片段失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/{fragment_id}")
async def get_fragment_api(
    fragment_id: int,
    current_user: User = Depends(get_current_user)
):
    """获取单个记忆片段"""
    try:
        result = get_fragment(current_user.user_id, fragment_id)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result.get("error", "Fragment not found")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 获取片段失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.put("/{fragment_id}")
async def update_fragment_api(
    fragment_id: int,
    request: UpdateFragmentRequest,
    current_user: User = Depends(get_current_user)
):
    """更新记忆片段（内容或 TTL）"""
    try:
        result = update_fragment(
            user_id=current_user.user_id,
            fragment_id=fragment_id,
            content=request.content,
            ttl=request.ttl,
            importance_score=request.importance_score
        )
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to update fragment")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 更新片段失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.delete("/{fragment_id}")
async def delete_fragment_api(
    fragment_id: int,
    current_user: User = Depends(get_current_user)
):
    """删除记忆片段"""
    try:
        result = delete_fragment(current_user.user_id, fragment_id)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Failed to delete fragment")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 删除片段失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.delete("/batch")
async def batch_delete_fragments_api(
    request: BatchDeleteFragmentsRequest,
    current_user: User = Depends(get_current_user)
):
    """批量删除记忆片段"""
    try:
        deleted_count = 0
        failed_ids: List[int] = []
        for fid in request.fragment_ids:
            result = delete_fragment(current_user.user_id, fid)
            if result.get("success"):
                deleted_count += 1
            else:
                failed_ids.append(fid)
        return {
            "success": True,
            "deleted_count": deleted_count,
            "failed_ids": failed_ids,
            "total": len(request.fragment_ids),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 批量删除片段失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/cleanup")
async def cleanup_fragments_api(
    current_user: User = Depends(get_current_user)
):
    """清理过期的记忆片段"""
    try:
        result = cleanup_expired_fragments(current_user.user_id)
        if result["success"]:
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "Cleanup failed")
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 清理片段失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# ============================================================
# Task 14: 语义搜索
# ============================================================

@router.post("/search")
async def semantic_search_api(
    request: SemanticSearchRequest,
    current_user: User = Depends(get_current_user)
):
    """语义搜索记忆片段（基于向量相似性）"""
    try:
        result = search_fragments_by_semantic(
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
        logger.error(f"✗ 语义搜索失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
