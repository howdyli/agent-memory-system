"""
Hybrid Search API 路由

提供多信号混合检索 REST API：
1. 混合检索（语义 + BM25 + 实体 + 时间衰减）
2. 纯 BM25 全文搜索
3. LLM 重排序
4. 融合权重配置管理
5. FTS5 索引重建
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from typing import Optional, Any, Dict, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.hybrid_search_service import (
    hybrid_search,
    search_bm25,
    rerank_with_llm,
    get_config,
    update_config,
    set_weights,
    rebuild_fts_index,
    get_weights,
    analyze_search,
)
from app.core.auth import Principal, get_current_principal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["hybrid-search"])


# ============================================================
# Pydantic 模型
# ============================================================

class HybridSearchRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    alpha: Optional[float] = None
    beta: Optional[float] = None
    gamma: Optional[float] = None
    delta: Optional[float] = None
    offset: int = 0
    limit: Optional[int] = None


class AnalyzeRequest(BaseModel):
    query: str
    top_k: int = 10


class BM25SearchRequest(BaseModel):
    query: str
    top_k: int = 20


class RerankRequest(BaseModel):
    query: str
    top_k: int = 5
    fragments: List[Dict[str, Any]]


class ConfigUpdateRequest(BaseModel):
    alpha: Optional[float] = None
    beta: Optional[float] = None
    gamma: Optional[float] = None
    delta: Optional[float] = None
    top_k_initial: Optional[int] = None
    top_k_final: Optional[int] = None
    bm25_top_k: Optional[int] = None
    semantic_top_k: Optional[int] = None
    recency_half_life: Optional[int] = None
    recency_min_score: Optional[float] = None
    rerank_enabled: Optional[bool] = None


# ============================================================
# API 端点
# ============================================================

@router.post("/memory/hybrid-search")
async def api_hybrid_search(
    request: HybridSearchRequest,
    principal: Principal = Depends(get_current_principal),
):
    """
    多信号混合检索。

    融合语义搜索（ChromaDB）、全文搜索（BM25 FTS5）、
    实体加权和时间衰减四种信号，支持 LLM 重排序。

    - **query**: 查询文本
    - **top_k**: 最终返回数（默认 10）
    - **alpha/beta/gamma/delta**: 可选权重覆盖
    """
    try:
        result = hybrid_search(
            user_id=principal.user_id,
            query=request.query,
            workspace_id=principal.workspace_id,
            alpha=request.alpha,
            beta=request.beta,
            gamma=request.gamma,
            delta=request.delta,
            top_k=request.top_k,
            offset=request.offset,
            limit=request.limit,
        )
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "检索失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"混合检索 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/hybrid-search/analyze")
async def api_analyze_search(
    request: AnalyzeRequest,
    principal: Principal = Depends(get_current_principal),
):
    """
    子搜索贡献度分析。

    用于可解释性调试与融合权重调优，返回：
    - 每个候选的信号分解（语义/BM25/实体/时间）
    - 语义与 BM25 召回集合的重叠情况
    - 各权重 ±0.1 时 top-k 排名敏感度

    - **query**: 查询文本
    - **top_k**: 分析的候选数（默认 10）
    """
    try:
        result = analyze_search(
            user_id=principal.user_id,
            query=request.query,
            workspace_id=principal.workspace_id,
            top_k=request.top_k,
        )
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "分析失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"搜索分析 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/hybrid-search/bm25")
async def api_bm25_search(
    request: BM25SearchRequest,
    principal: Principal = Depends(get_current_principal),
):
    """
    纯 BM25 全文搜索。

    使用 SQLite FTS5 引擎进行关键词检索，
    适合精确匹配和关键词搜索场景。

    - **query**: 查询文本
    - **top_k**: 返回数（默认 20）
    """
    try:
        result = search_bm25(
            query=request.query,
            user_id=principal.user_id,
            top_k=request.top_k,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "BM25 搜索失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"BM25 搜索 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/hybrid-search/rerank")
async def api_rerank(
    request: RerankRequest,
    principal: Principal = Depends(get_current_principal),
):
    """
    LLM 重排序。

    使用大模型对候选记忆按相关性从高到低重新排序。
    当 LLM 不可用时自动回退到融合分数排序。

    - **query**: 用户查询
    - **fragments**: 候选记忆列表
    - **top_k**: 保留前 K 条（默认 5）
    """
    try:
        reranked = rerank_with_llm(
            query=request.query,
            fragments=request.fragments,
            user_id=principal.user_id,
            top_k=request.top_k,
        )
        return {"success": True, "fragments": reranked, "count": len(reranked)}
    except Exception as e:
        logger.error(f"重排序 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/hybrid-search/config")
async def api_get_config(
    principal: Principal = Depends(get_current_principal),
):
    """
    获取混合检索的当前配置，包括融合权重和检索参数。
    """
    try:
        config = get_config()
        return {"success": True, "config": config}
    except Exception as e:
        logger.error(f"获取配置 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/memory/hybrid-search/config")
async def api_update_config(
    request: ConfigUpdateRequest,
    principal: Principal = Depends(get_current_principal),
):
    """
    更新混合检索配置。

    支持更新融合权重（alpha/beta/gamma/delta）或
    检索参数（top_k 等）。未提供的字段保持原值。

    - **alpha/beta/gamma/delta**: 融合权重（0~1），
      四个权重不需要和为 1（系统不强制归一化）
    """
    try:
        updates = request.dict(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="未提供任何更新数据")
        result = update_config(updates)
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "更新失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新配置 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/hybrid-search/rebuild-index")
async def api_rebuild_index(
    principal: Principal = Depends(get_current_principal),
):
    """
    重建 FTS5 全文搜索索引。

    从 memory_fragments 表重新填充碎片全文索引。
    在索引损坏或首次部署后需要执行此操作。
    """
    try:
        result = rebuild_fts_index()
        return result
    except Exception as e:
        logger.error(f"重建索引 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))
