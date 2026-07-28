"""
Agents API 路由（P1 R-12 跨 Agent 共享记忆作用域）

Agent 注册 CRUD：创建 / 列表 / 详情 / 删除。
挂载于 /api/v1/agents（与 /api/v1/agent 对话路由互不冲突）。
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional

from app.services.agent_registry_service import (
    register_agent,
    list_agents,
    get_agent,
    delete_agent,
)
from app.core.auth import Principal
from app.core.rbac import Perm, require_permission

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])


class CreateAgentRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_agent_api(
    request: CreateAgentRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """注册 Agent（同一 workspace 内 name 唯一，重名返回 409）"""
    try:
        result = register_agent(
            user_id=principal.user_id,
            name=request.name,
            description=request.description,
            workspace_id=principal.workspace_id,
        )
        if result["success"]:
            return result
        if result.get("duplicate"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=result.get("error", "Agent name already exists")
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create agent")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 创建 Agent 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("")
async def list_agents_api(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """列出当前 workspace 的 Agents"""
    try:
        result = list_agents(principal.user_id, workspace_id=principal.workspace_id)
        if result["success"]:
            return result
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to list agents")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 列出 Agents 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/{agent_id}")
async def get_agent_api(
    agent_id: int,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """获取 Agent 详情"""
    try:
        result = get_agent(agent_id, principal.user_id, workspace_id=principal.workspace_id)
        if result["success"]:
            return result
        if result.get("not_found"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result.get("error", "Agent not found")
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to get agent")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 获取 Agent 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.delete("/{agent_id}")
async def delete_agent_api(
    agent_id: int,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """删除 Agent（保留记忆，其 private 记忆转为 shared）"""
    try:
        result = delete_agent(agent_id, principal.user_id, workspace_id=principal.workspace_id)
        if result["success"]:
            return result
        if result.get("not_found"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=result.get("error", "Agent not found")
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to delete agent")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 删除 Agent 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
