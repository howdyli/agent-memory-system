"""
Graph Memory API 路由

提供知识图谱记忆管理 REST API：
- 实体管理（创建、搜索、合并）
- 关系管理（创建、查询、更新、删除）
- 图遍历（邻居查询、图谱文本）
- 时序追踪（关系变更历史）
- 自然语言实体抽取
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel, Field
from typing import Optional, Any, Dict, List, Literal

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.graph_memory_service import (
    # 实体管理
    ensure_entity,
    search_entities,
    get_entity,
    merge_entities,
    delete_entity,
    update_entity,
    # 关系管理
    add_relationship,
    deactivate_relationship,
    update_relationship,
    get_relationship_history,
    get_relationship_at_time,
    list_relationships,
    # 图遍历
    get_neighbors,
    get_entity_graph_text,
    # 自然语言查询
    query_graph,
    extract_entities_from_text,
    # 统计
    get_graph_statistics,
    # 去重检测
    detect_duplicate_entities,
)
from app.core.auth import Principal, get_current_principal
from app.core.rbac import Perm, require_permission
from app.core.errors import handle_service_result

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-graph"])


# ============================================================
# 请求模型
# ============================================================

class EntityCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    entity_type: Literal["person", "organization", "location", "event", "concept"]
    aliases: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None


class EntityUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    entity_type: Optional[Literal["person", "organization", "location", "event", "concept"]] = None
    metadata: Optional[Dict[str, Any]] = None


class EntityMergeRequest(BaseModel):
    target_id: int = Field(..., ge=1)
    source_ids: List[int] = Field(..., min_length=1)


class RelationshipCreateRequest(BaseModel):
    source_name: str = Field(..., min_length=1, max_length=200)
    target_name: str = Field(..., min_length=1, max_length=200)
    relation_type: str = Field(..., min_length=1, max_length=100)
    source_type: Literal["person", "organization", "location", "event", "concept"] = "person"
    target_type: Literal["person", "organization", "location", "event", "concept"] = "organization"
    relation_subtype: Optional[str] = Field(None, max_length=100)
    properties: Optional[Dict[str, Any]] = None
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    valid_from: Optional[str] = None
    extraction_source: Optional[str] = "manual"


class RelationshipUpdateRequest(BaseModel):
    properties: Optional[Dict[str, Any]] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    relation_subtype: Optional[str] = Field(None, max_length=100)


class RelationshipDeactivateRequest(BaseModel):
    reason: Optional[str] = Field("ended", max_length=200)


class ExtractRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)


# ============================================================
# 1. 实体管理 API
# ============================================================

@router.post("/memory/graph/entities", status_code=status.HTTP_201_CREATED, summary="创建实体", description="创建或获取知识图谱实体（人物/组织/地点/事件/概念）")
@handle_service_result
async def create_entity(
    request: EntityCreateRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """创建或获取实体"""
    return ensure_entity(
        user_id=principal.user_id,
        name=request.name,
        entity_type=request.entity_type,
        aliases=request.aliases,
        metadata=request.metadata,
        workspace_id=principal.workspace_id,
    )


@router.get("/memory/graph/entities", summary="搜索实体", description="按关键词搜索实体，支持类型过滤")
async def search_entities_api(
    query: str = Query("", description="搜索关键词，为空则返回所有"),
    entity_type: Optional[str] = Query(None, description="过滤实体类型"),
    limit: int = Query(20, ge=1, le=100),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """搜索实体"""
    try:
        result = search_entities(principal.user_id, query, entity_type, limit, workspace_id=principal.workspace_id)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/graph/entities/{entity_id}")
async def get_entity_api(
    entity_id: int,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取实体详情（含关系计数）"""
    try:
        result = get_entity(principal.user_id, entity_id, workspace_id=principal.workspace_id)
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/memory/graph/entities/{entity_id}")
async def update_entity_api(
    entity_id: int,
    request: EntityUpdateRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """更新实体信息"""
    try:
        result = update_entity(
            user_id=principal.user_id,
            entity_id=entity_id,
            name=request.name,
            entity_type=request.entity_type,
            metadata=request.metadata,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/graph/entities/{entity_id}")
async def delete_entity_api(
    entity_id: int,
    principal: Principal = Depends(require_permission(Perm.MEMORY_DELETE))
):
    """删除实体及其所有关系"""
    try:
        result = delete_entity(
            user_id=principal.user_id,
            entity_id=entity_id,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=404, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/memory/graph/entities/merge")
@handle_service_result
async def merge_entities_api(
    request: EntityMergeRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """合并重复实体"""
    return merge_entities(
        user_id=principal.user_id,
        target_id=request.target_id,
        source_ids=request.source_ids,
        workspace_id=principal.workspace_id,
    )


# ============================================================
# 2. 关系管理 API
# ============================================================

@router.post("/memory/graph/relationships", status_code=status.HTTP_201_CREATED, summary="创建关系", description="创建两个实体之间的关系（支持时序属性和置信度）")
@handle_service_result
async def create_relationship(
    request: RelationshipCreateRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """创建两个实体之间的关系"""
    return add_relationship(
        user_id=principal.user_id,
        source_name=request.source_name,
        target_name=request.target_name,
        relation_type=request.relation_type,
        source_type=request.source_type,
        target_type=request.target_type,
        relation_subtype=request.relation_subtype,
        properties=request.properties,
        confidence=request.confidence,
        valid_from=request.valid_from,
        extraction_source=request.extraction_source,
        workspace_id=principal.workspace_id,
    )


@router.get("/memory/graph/relationships")
async def list_relationships_api(
    source_name: Optional[str] = Query(None, description="源实体名称"),
    target_name: Optional[str] = Query(None, description="目标实体名称"),
    relation_type: Optional[str] = Query(None, description="关系类型"),
    is_active: Optional[bool] = Query(None, description="是否活跃"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """查询关系列表"""
    try:
        result = list_relationships(
            user_id=principal.user_id,
            source_name=source_name,
            target_name=target_name,
            relation_type=relation_type,
            is_active=is_active,
            limit=limit,
            offset=offset,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/memory/graph/relationships/{relationship_id}")
async def update_relationship_api(
    relationship_id: int,
    request: RelationshipUpdateRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """更新关系属性"""
    try:
        updates = {}
        if request.properties is not None:
            updates["properties"] = request.properties
        if request.confidence is not None:
            updates["confidence"] = request.confidence
        if request.relation_subtype is not None:
            updates["relation_subtype"] = request.relation_subtype

        result = update_relationship(
            user_id=principal.user_id,
            relationship_id=relationship_id,
            updates=updates,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/memory/graph/relationships/{relationship_id}")
async def deactivate_relationship_api(
    relationship_id: int,
    request: Optional[RelationshipDeactivateRequest] = None,
    principal: Principal = Depends(require_permission(Perm.MEMORY_DELETE))
):
    """结束一个关系（软删除，标记 is_active=0）"""
    try:
        reason = request.reason if request else "ended"
        result = deactivate_relationship(
            user_id=principal.user_id,
            relationship_id=relationship_id,
            reason=reason,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 3. 图遍历 API
# ============================================================

@router.get("/memory/graph/neighbors", summary="图遍历", description="查询指定实体的邻居节点，支持 1-3 跳深度遍历")
async def get_neighbors_api(
    entity_id: Optional[int] = Query(None, description="实体 ID（优先使用）"),
    entity_name: Optional[str] = Query(None, description="实体名称（entity_id 为空时使用）"),
    entity_type: str = Query("person", description="实体类型"),
    relation_type: Optional[str] = Query(None, description="过滤关系类型"),
    depth: int = Query(1, ge=1, le=3, description="遍历深度"),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """查询指定实体的邻居节点（图遍历）"""
    try:
        if not entity_id and not entity_name:
            raise HTTPException(status_code=400, detail="必须提供 entity_id 或 entity_name")
        result = get_neighbors(
            user_id=principal.user_id,
            entity_name=entity_name,
            entity_type=entity_type,
            relation_type=relation_type,
            depth=depth,
            entity_id=entity_id,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 4. 时序追踪 API
# ============================================================

@router.get("/memory/graph/history")
async def get_relationship_history_api(
    entity1: str = Query(..., description="第一个实体名称"),
    entity2: str = Query(..., description="第二个实体名称"),
    entity1_type: str = Query("person"),
    entity2_type: str = Query("organization"),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取两个实体之间的关系变化历史"""
    try:
        result = get_relationship_history(
            user_id=principal.user_id,
            entity1_name=entity1,
            entity2_name=entity2,
            entity1_type=entity1_type,
            entity2_type=entity2_type,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/memory/graph/temporal", summary="时序点查询", description="查询在指定时间点成立的关系（双时序模型：支持事件时间和系统时间）")
async def get_relationship_at_time_api(
    at: str = Query(..., description="查询时间点（ISO 8601，如 2026-06-01T00:00:00）"),
    entity: Optional[str] = Query(None, description="源实体名称（为空则查所有实体）"),
    entity_type: str = Query("person", description="源实体类型"),
    target_entity: Optional[str] = Query(None, description="目标实体名称（为空则不限制目标）"),
    target_entity_type: str = Query("organization", description="目标实体类型"),
    relation_type: Optional[str] = Query(None, description="关系类型过滤（如 colleague）"),
    time_mode: Literal["event", "system"] = Query(
        "event", description="时间模式：event=事件时间(valid_from/valid_to), system=系统时间(observed_at/expired_at)"
    ),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """
    时序点查询：返回在 ``at`` 时间点成立的关系。

    **event 模式**：基于事件时间判断关系在该时刻是否为真
    （例："2026-06-01 时 Alice 在哪家公司？"）

    **system 模式**：基于系统时间判断系统在该时刻是否已观测到该关系
    （例："昨天系统知道 Alice 在哪家公司？"）
    """
    try:
        result = get_relationship_at_time(
            user_id=principal.user_id,
            at_time=at,
            entity_name=entity,
            entity_type=entity_type,
            target_entity_name=target_entity,
            target_entity_type=target_entity_type,
            relation_type=relation_type,
            time_mode=time_mode,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 5. 实体抽取 API
# ============================================================

@router.post("/memory/graph/extract")
async def extract_entities_api(
    request: ExtractRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """从文本中批量抽取实体和关系并存入数据库"""
    try:
        result = extract_entities_from_text(
            user_id=principal.user_id,
            text=request.text,
            workspace_id=principal.workspace_id,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 6. 自然语言图查询 API
# ============================================================

@router.get("/memory/graph/query")
async def query_graph_api(
    q: str = Query(..., description="自然语言查询（如'张三的同事'）"),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """自然语言图查询"""
    try:
        result = query_graph(
            user_id=principal.user_id,
            query=q,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 7. 去重检测 API
# ============================================================

@router.get("/memory/graph/duplicates")
async def detect_duplicates_api(
    threshold: int = Query(3, ge=1, le=10, description="Levenshtein 距离阈值"),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """检测相似实体（基于名称编辑距离）"""
    try:
        result = detect_duplicate_entities(
            user_id=principal.user_id,
            threshold=threshold,
            workspace_id=principal.workspace_id,
        )
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# 8. 图谱统计 API
# ============================================================

@router.get("/memory/graph/statistics")
async def get_statistics_api(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ)),
):
    """获取知识图谱统计信息"""
    try:
        result = get_graph_statistics(user_id=principal.user_id, workspace_id=principal.workspace_id)
        if not result.get("success"):
            raise HTTPException(status_code=400, detail=result.get("error"))
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
