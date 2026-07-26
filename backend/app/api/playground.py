"""
记忆 Playground API 路由（W4-F4.3）

提供交互式记忆调试工具的三个模拟端点（全部只读，不产生落库副作用）：
    - POST /simulate-injection  注入模拟器：实体抽取 → 矛盾预判 → 时间推断 → 片段预览
    - POST /simulate-recall     召回调试器：L1 变量 / L2 语义 / L3 实体扩展 + Token 预算
    - POST /simulate-decay      生命周期模拟器：半衰期衰减曲线 + 剩余寿命

冲突演变链（区域 4）复用现有 /api/v1/memory/evolution/chain 端点。
"""
import logging
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Optional, Any, Dict, List

from app.core.auth import Principal, get_current_principal
from app.core.metrics import playground_requests_total

logger = logging.getLogger(__name__)

router = APIRouter(tags=["playground"])


# ============================================================
# 请求模型
# ============================================================

class SimulateInjectionRequest(BaseModel):
    content: str
    fragment_type: Optional[str] = "info"
    importance: Optional[float] = 0.5


class SimulateRecallRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    budget_tokens: Optional[int] = 2000


class SimulateDecayRequest(BaseModel):
    fragment_type: Optional[str] = "info"
    importance: Optional[float] = 0.5
    days: Optional[int] = 180
    half_life_days: Optional[float] = None


# ============================================================
# 区域 1: 注入模拟器
# ============================================================

@router.post("/simulate-injection")
async def simulate_injection_api(
    request: SimulateInjectionRequest,
    principal: Principal = Depends(get_current_principal)
):
    """注入模拟器：展示一条内容进入记忆系统后的完整处理链路（只读，不落库）。"""
    playground_requests_total.labels(simulator="injection").inc()
    content = (request.content or "").strip()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="content 不能为空"
        )
    try:
        result = _simulate_injection(
            user_id=principal.user_id,
            content=content,
            fragment_type=request.fragment_type or "info",
            importance=request.importance if request.importance is not None else 0.5,
        )
        return result
    except Exception as e:
        logger.error(f"✗ 注入模拟失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


def _simulate_injection(
    user_id: int, content: str, fragment_type: str, importance: float
) -> Dict[str, Any]:
    """只读模拟注入链路：实体抽取 → 矛盾预判 → 时间推断 → 片段预览。"""
    from app.services.advanced_recall import _extract_updatable_entity, _text_similarity
    from app.services.temporal_inference_service import infer_validity_window
    from app.services.memory_lifecycle_service import get_half_life_info

    # 1. 实体抽取
    entity = _extract_updatable_entity(content)
    entity_info = None
    if entity:
        entity_info = {"entity_type": entity[0], "entity_value": entity[1]}

    # 2. 矛盾预判（只读：查找同类型不同值的活跃记忆，预测解决策略，不写库）
    contradictions = _preview_contradictions(user_id, content, entity, importance)

    # 3. 时间推断
    valid_from, valid_until = infer_validity_window(content)
    temporal_info = {
        "valid_from": valid_from.isoformat() if valid_from else None,
        "valid_until": valid_until.isoformat() if valid_until else None,
        "has_expiry": valid_until is not None,
    }

    # 4. 生命周期信息
    half_life = get_half_life_info(fragment_type)

    # 5. 最终片段预览
    preview = {
        "content": content,
        "fragment_type": fragment_type,
        "importance_score": importance,
        "valid_from": temporal_info["valid_from"],
        "valid_until": temporal_info["valid_until"],
        "would_supersede": [
            c["old_fragment_id"] for c in contradictions
            if c["predicted_action"] == "superseded_old"
        ],
    }

    return {
        "success": True,
        "entity": entity_info,
        "contradictions": contradictions,
        "temporal": temporal_info,
        "lifecycle": half_life,
        "fragment_preview": preview,
    }


def _preview_contradictions(
    user_id: int,
    new_content: str,
    entity: Optional[tuple],
    new_importance: float,
) -> List[Dict[str, Any]]:
    """只读矛盾预判：复用 ConflictResolver 的纯策略函数，不修改任何数据。"""
    if not entity:
        return []

    from app.core.db_client import get_db_client
    from app.services.advanced_recall import _extract_updatable_entity, _text_similarity
    from app.services.conflict_resolution_service import get_conflict_resolver

    update_type, new_value = entity
    db = get_db_client()
    rows = db.execute(
        """SELECT id, user_id, content, importance_score,
                  created_at, last_recalled_at
           FROM memory_fragments
           WHERE user_id = ? AND lifecycle_status = 'active'
           ORDER BY created_at DESC""",
        (user_id,),
    )

    resolver = get_conflict_resolver()
    new_fragment = {"id": None, "importance_score": new_importance}
    results: List[Dict[str, Any]] = []

    for row in (rows or []):
        old_entity = _extract_updatable_entity(row["content"])
        if not old_entity:
            continue
        old_type, old_value = old_entity
        if old_type != update_type or old_value.lower() == new_value.lower():
            continue

        old_fragment = dict(row)
        # 纯函数：仅选择策略与计算可信度，不产生写操作
        strategy = resolver._select_strategy(old_fragment, new_fragment)
        old_score = resolver._confidence_score(old_fragment)
        new_score = resolver._confidence_score(new_fragment)
        if strategy == "manual_review":
            predicted_action = "pending_review"
        elif strategy == "confidence_based":
            predicted_action = "kept_old" if old_score >= new_score else "superseded_old"
        else:
            predicted_action = "superseded_old"

        results.append({
            "old_fragment_id": row["id"],
            "old_content": row["content"],
            "old_value": old_value,
            "new_value": new_value,
            "entity_type": update_type,
            "similarity_score": round(_text_similarity(row["content"], new_content), 4),
            "predicted_strategy": strategy,
            "predicted_action": predicted_action,
            "confidence": {"old": round(old_score, 4), "new": round(new_score, 4)},
        })
    return results


# ============================================================
# 区域 2: 召回调试器
# ============================================================

@router.post("/simulate-recall")
async def simulate_recall_api(
    request: SimulateRecallRequest,
    principal: Principal = Depends(get_current_principal)
):
    """召回调试器：展示三层召回各自的候选与 Token 预算占用（不更新生命周期）。"""
    playground_requests_total.labels(simulator="recall").inc()
    query = (request.query or "").strip()
    if not query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="query 不能为空"
        )
    try:
        return _simulate_recall(
            user_id=principal.user_id,
            workspace_id=principal.workspace_id,
            query=query,
            top_k=request.top_k,
            budget_tokens=request.budget_tokens or 2000,
        )
    except Exception as e:
        logger.error(f"✗ 召回模拟失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


def _simulate_recall(
    user_id: int,
    workspace_id: Optional[int],
    query: str,
    top_k: Optional[int],
    budget_tokens: int,
) -> Dict[str, Any]:
    """三层召回调试：L1 变量 / L2 语义 / L3 实体扩展 + 预算与去重信息。"""
    from app.services.recall_engine import RecallEngine
    from app.services.memory_variable_service import list_memory_variables
    from app.services.context_compressor import estimate_tokens

    # L1: KV Profile 变量
    try:
        variables = list_memory_variables(user_id, workspace_id=workspace_id) or {}
    except Exception:
        variables = {}

    engine = RecallEngine()

    # L2: 语义召回（关闭生命周期更新与埋点，保证只读）
    l2 = engine.recall(
        user_id=user_id,
        query=query,
        budget_tokens=budget_tokens,
        top_k=top_k,
        update_lifecycle=False,
        record_traces=False,
    )
    l2_ids = {m.get("id") for m in l2.memories if m.get("id")}

    # L3: 实体图谱扩展召回（跨层去重）
    l3 = engine.recall_with_entities(
        user_id=user_id,
        query=query,
        budget_tokens=max(budget_tokens // 4, 200),
        exclude_ids=l2_ids,
    )

    total_used = l2.token_used + estimate_tokens(l3.context_text)
    utilization = min(total_used / budget_tokens, 1.0) if budget_tokens > 0 else 0.0

    def _fmt(memories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return [
            {
                "id": m.get("id"),
                "content": m.get("content", ""),
                "score": m.get("similarity") or m.get("score") or m.get("hybrid_score"),
                "fragment_type": m.get("fragment_type") or m.get("type"),
                "importance_score": m.get("importance_score"),
                "lifecycle_status": m.get("lifecycle_status", "active"),
                "source_entity": m.get("source_entity"),
            }
            for m in memories
        ]

    return {
        "success": True,
        "query": query,
        "level1": {"variables": variables, "count": len(variables)},
        "level2": {
            "memories": _fmt(l2.memories),
            "total_candidates": l2.total_candidates,
            "token_used": l2.token_used,
        },
        "level3": {
            "memories": _fmt(l3.memories),
            "total_candidates": l3.total_candidates,
            "excluded_ids": sorted(i for i in l2_ids if i is not None),
        },
        "budget": {
            "budget_tokens": budget_tokens,
            "token_used": total_used,
            "utilization": round(utilization, 4),
        },
    }


# ============================================================
# 区域 3: 生命周期模拟器
# ============================================================

@router.post("/simulate-decay")
async def simulate_decay_api(
    request: SimulateDecayRequest,
    principal: Principal = Depends(get_current_principal)
):
    """生命周期模拟器：按半衰期生成衰减曲线（纯计算）。"""
    playground_requests_total.labels(simulator="decay").inc()
    try:
        return _simulate_decay(
            fragment_type=request.fragment_type or "info",
            importance=request.importance if request.importance is not None else 0.5,
            days=max(request.days or 180, 1),
            half_life_override=request.half_life_days,
        )
    except Exception as e:
        logger.error(f"✗ 衰减模拟失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


def _simulate_decay(
    fragment_type: str,
    importance: float,
    days: int,
    half_life_override: Optional[float],
) -> Dict[str, Any]:
    """生成衰减曲线数据点（约 60 个采样点）。"""
    from app.services.memory_lifecycle_service import (
        get_half_life_info,
        calculate_decay_score,
    )

    info = get_half_life_info(fragment_type)
    half_life = half_life_override if half_life_override else info.get("half_life_days")
    is_permanent = half_life is None

    # 服务内部使用 naive datetime.now() 计算差值，这里保持一致
    now = datetime.now()
    points: List[Dict[str, Any]] = []
    step = max(days / 60.0, 1.0)
    d = 0.0
    while d <= days:
        if is_permanent:
            decay = 1.0
        else:
            decay = calculate_decay_score(now - timedelta(days=d), half_life)
        points.append({
            "day": round(d, 1),
            "decay_score": round(decay, 4),
            "effective_score": round(decay * importance, 4),
        })
        d += step

    # 5% 阈值 → 有效寿命 ≈ half_life × log2(20) ≈ half_life × 4.32
    effective_life_days = None if is_permanent else round(half_life * 4.32, 1)

    return {
        "success": True,
        "fragment_type": fragment_type,
        "importance": importance,
        "half_life_days": half_life,
        "is_permanent": is_permanent,
        "effective_life_days": effective_life_days,
        "curve": points,
    }
