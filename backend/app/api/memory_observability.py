"""
Memory Observability API 路由

提供记忆观测性 REST API：
1. 仪表盘指标（总量/速率/命中率/延迟/Token）
2. 记忆追踪（生命周期/触发记录）
3. 质量评估（LLM 准确率/召回相关性）
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, Any, Dict, List

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.memory_observability_service import (
    get_dashboard_stats,
    get_metrics_history,
    snapshot_metrics,
    get_memory_trace,
    get_trace_events,
    get_extraction_triggers,
    evaluate_memory_accuracy,
    evaluate_recall_relevance,
    batch_evaluate_quality,
    get_quality_report,
)
from app.services.performance_service import get_performance_service
from app.core.auth import get_current_user, User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-observability"])


# ============================================================
# Pydantic 模型
# ============================================================

class AccuracyEvalRequest(BaseModel):
    memory_id: str
    memory_type: str = "fragment"
    conversation_text: Optional[str] = None


class RelevanceEvalRequest(BaseModel):
    query: str
    fragments: List[Dict[str, Any]]


class BatchEvalRequest(BaseModel):
    limit: int = 10


# ============================================================
# API 端点
# ============================================================

@router.get("/memory/observability/dashboard")
async def api_dashboard(
    current_user: User = Depends(get_current_user),
):
    """
    获取观测仪表盘聚合指标。

    包括记忆总量、新增速率、召回命中率、存储占用、
    检索延迟 P50/P99、LLM Token 消耗等关键指标。
    """
    try:
        result = get_dashboard_stats(user_id=current_user.id)
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "获取指标失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"仪表盘 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/observability/metrics-history")
async def api_metrics_history(
    days: int = Query(30, description="查询最近 N 天"),
    current_user: User = Depends(get_current_user),
):
    """
    获取指标历史时间序列。

    - **days**: 查询最近 N 天（默认 30）
    """
    try:
        result = get_metrics_history(user_id=current_user.id, days=days)
        return result
    except Exception as e:
        logger.error(f"指标历史 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/observability/snapshot")
async def api_snapshot(
    current_user: User = Depends(get_current_user),
):
    """
    手动执行指标快照。

    将当前指标写入 memory_metrics_snapshots 表，
    用于生成时间序列趋势图。
    """
    try:
        result = snapshot_metrics(user_id=current_user.id)
        return result
    except Exception as e:
        logger.error(f"快照 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/observability/trace/{memory_id}")
async def api_memory_trace(
    memory_id: str,
    memory_type: Optional[str] = Query(None, description="记忆类型 (fragment/variable/table)"),
    current_user: User = Depends(get_current_user),
):
    """
    获取某条记忆的完整生命周期追踪。

    - **memory_id**: 记忆 ID
    - **memory_type**: 记忆类型过滤
    """
    try:
        result = get_memory_trace(memory_id=memory_id, memory_type=memory_type)
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("error", "记忆不存在"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"记忆追踪 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/observability/events")
async def api_trace_events(
    event_type: Optional[str] = Query(None, description="事件类型过滤"),
    event_source: Optional[str] = Query(None, description="事件来源过滤"),
    days: int = Query(7, description="查询最近 N 天"),
    limit: int = Query(100, description="最大返回条数"),
    current_user: User = Depends(get_current_user),
):
    """
    获取追踪事件列表。

    - **event_type**: created|recalled|updated|deleted|decayed|merged|cold_marked|restored
    - **event_source**: conversation|extraction|recall|lifecycle|manual|system
    - **days**: 查询最近 N 天（默认 7）
    - **limit**: 最大返回条数（默认 100）
    """
    try:
        result = get_trace_events(
            user_id=current_user.id,
            event_type=event_type,
            event_source=event_source,
            days=days,
            limit=limit,
        )
        return result
    except Exception as e:
        logger.error(f"事件列表 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/observability/extraction-triggers")
async def api_extraction_triggers(
    limit: int = Query(50, description="最大返回条数"),
    days: int = Query(30, description="查询最近 N 天"),
    current_user: User = Depends(get_current_user),
):
    """
    获取记忆抽取触发记录。

    记录了哪些对话/会话触发了记忆抽取，以及抽取的 Token 消耗等。
    """
    try:
        result = get_extraction_triggers(
            user_id=current_user.id,
            limit=limit,
            days=days,
        )
        return result
    except Exception as e:
        logger.error(f"抽取触发 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/observability/quality/evaluate")
async def api_evaluate_accuracy(
    request: AccuracyEvalRequest,
    current_user: User = Depends(get_current_user),
):
    """
    LLM 自动评估记忆片段准确率。

    - **memory_id**: 记忆 ID
    - **memory_type**: 记忆类型（fragment/variable）
    - **conversation_text**: 原文对话（可选，不提供则用启发式评估）
    """
    try:
        result = evaluate_memory_accuracy(
            user_id=current_user.id,
            memory_id=request.memory_id,
            conversation_text=request.conversation_text,
            memory_type=request.memory_type,
        )
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("error", "评估失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"准确率评估 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/observability/quality/relevance")
async def api_evaluate_relevance(
    request: RelevanceEvalRequest,
    current_user: User = Depends(get_current_user),
):
    """
    评估召回片段与查询的相关性。

    - **query**: 用户查询
    - **fragments**: 召回的记忆片段列表
    """
    try:
        result = evaluate_recall_relevance(
            user_id=current_user.id,
            query=request.query,
            fragments=request.fragments,
        )
        return result
    except Exception as e:
        logger.error(f"召回相关性 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/observability/quality/batch-evaluate")
async def api_batch_evaluate(
    request: BatchEvalRequest,
    current_user: User = Depends(get_current_user),
):
    """
    批量评估最近创建的记忆片段质量。

    - **limit**: 评估条数（默认 10）
    """
    try:
        result = batch_evaluate_quality(
            user_id=current_user.id,
            limit=request.limit,
        )
        return result
    except Exception as e:
        logger.error(f"批量评估 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/observability/quality-report")
async def api_quality_report(
    days: int = Query(30, description="查询最近 N 天"),
    current_user: User = Depends(get_current_user),
):
    """
    获取质量评估聚合报告。

    按评估类型（accuracy/relevance/satisfaction）聚合，
    包括平均分、最低分、最高分和评估次数。
    """
    try:
        result = get_quality_report(
            user_id=current_user.id,
            days=days,
        )
        return result
    except Exception as e:
        logger.error(f"质量报告 API 错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 性能指标端点
# ============================================================

@router.get("/memory/observability/performance/latency")
async def api_performance_latency(
    hours: int = Query(24, description="查询最近 N 小时"),
    current_user: User = Depends(get_current_user),
):
    """
    获取API延迟统计（p50/p95/p99）。
    """
    try:
        svc = get_performance_service()
        result = await svc.get_api_latency_stats(
            user_id=current_user.id,
            hours=hours,
        )
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "获取失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API延迟统计错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/observability/performance/llm-costs")
async def api_performance_llm_costs(
    hours: int = Query(24, description="查询最近 N 小时"),
    current_user: User = Depends(get_current_user),
):
    """
    获取LLM调用成本统计。
    """
    try:
        svc = get_performance_service()
        result = await svc.get_llm_cost_stats(
            user_id=current_user.id,
            hours=hours,
        )
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "获取失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"LLM成本统计错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/observability/performance/cache")
async def api_performance_cache(
    hours: int = Query(24, description="查询最近 N 小时"),
    current_user: User = Depends(get_current_user),
):
    """
    获取缓存命中率统计。
    """
    try:
        svc = get_performance_service()
        result = await svc.get_cache_hit_rate(
            user_id=current_user.id,
            hours=hours,
        )
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "获取失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"缓存命中率统计错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/observability/performance/errors")
async def api_performance_errors(
    hours: int = Query(24, description="查询最近 N 小时"),
    current_user: User = Depends(get_current_user),
):
    """
    获取错误率统计与最近错误列表。
    """
    try:
        svc = get_performance_service()
        result = await svc.get_error_rate(
            user_id=current_user.id,
            hours=hours,
        )
        if not result.get("success"):
            raise HTTPException(status_code=500, detail=result.get("error", "获取失败"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"错误率统计错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))
