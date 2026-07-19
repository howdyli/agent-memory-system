"""Webhook API 路由（Phase 4）。

提供 Webhook CRUD、投递记录查询、测试端点。
挂载在 /api/v1/webhooks 下。

Stability: BETA — 接口可能在小版本间调整，生产使用前请关注 CHANGELOG。
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.core.auth import Principal, get_current_principal
from app.core.errors import ForbiddenError, NotFoundError
from app.services import webhook_service

router = APIRouter()


# ============================================================
# 请求/响应模型
# ============================================================
class WebhookCreate(BaseModel):
    url: str
    event_types: List[str]
    workspace_id: Optional[int] = None
    description: Optional[str] = None


class WebhookUpdate(BaseModel):
    url: Optional[str] = None
    event_types: Optional[List[str]] = None
    active: Optional[bool] = None
    description: Optional[str] = None


class WebhookOut(BaseModel):
    id: int
    user_id: int
    url: str
    secret: str
    event_types: List[str]
    workspace_id: Optional[int] = None
    description: Optional[str] = None
    active: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DeliveryOut(BaseModel):
    id: int
    webhook_id: int
    event_id: str
    event_type: str
    status_code: Optional[int] = None
    success: bool
    attempt: int
    next_retry_at: Optional[str] = None
    created_at: Optional[str] = None


# ============================================================
# CRUD 端点
# ============================================================

@router.post("", response_model=WebhookOut)
async def create_webhook(
    body: WebhookCreate,
    principal: Principal = Depends(get_current_principal),
):
    """创建 Webhook 订阅"""
    result = webhook_service.create_webhook(
        user_id=principal.user_id,
        url=body.url,
        event_types=body.event_types,
        workspace_id=body.workspace_id,
        description=body.description,
    )
    return result


@router.get("", response_model=List[WebhookOut])
async def list_webhooks(
    workspace_id: Optional[int] = Query(None),
    principal: Principal = Depends(get_current_principal),
):
    """列出当前用户的 Webhooks"""
    return webhook_service.list_webhooks(
        user_id=principal.user_id,
        workspace_id=workspace_id,
    )


@router.get("/{webhook_id}", response_model=WebhookOut)
async def get_webhook(
    webhook_id: int,
    principal: Principal = Depends(get_current_principal),
):
    """获取 Webhook 详情"""
    webhook = webhook_service.get_webhook(webhook_id)
    if not webhook:
        raise NotFoundError("Webhook not found")
    if webhook["user_id"] != principal.user_id:
        raise ForbiddenError("Forbidden")
    return webhook


@router.put("/{webhook_id}", response_model=WebhookOut)
async def update_webhook(
    webhook_id: int,
    body: WebhookUpdate,
    principal: Principal = Depends(get_current_principal),
):
    """更新 Webhook"""
    existing = webhook_service.get_webhook(webhook_id)
    if not existing:
        raise NotFoundError("Webhook not found")
    if existing["user_id"] != principal.user_id:
        raise ForbiddenError("Forbidden")

    result = webhook_service.update_webhook(
        webhook_id=webhook_id,
        url=body.url,
        event_types=body.event_types,
        active=body.active,
        description=body.description,
    )
    return result


@router.delete("/{webhook_id}")
async def delete_webhook(
    webhook_id: int,
    principal: Principal = Depends(get_current_principal),
):
    """删除 Webhook"""
    existing = webhook_service.get_webhook(webhook_id)
    if not existing:
        raise NotFoundError("Webhook not found")
    if existing["user_id"] != principal.user_id:
        raise ForbiddenError("Forbidden")

    webhook_service.delete_webhook(webhook_id)
    return {"success": True}


# ============================================================
# 测试 & 投递记录
# ============================================================

@router.post("/{webhook_id}/test")
async def test_webhook(
    webhook_id: int,
    principal: Principal = Depends(get_current_principal),
):
    """发送测试事件到 Webhook"""
    existing = webhook_service.get_webhook(webhook_id)
    if not existing:
        raise NotFoundError("Webhook not found")
    if existing["user_id"] != principal.user_id:
        raise ForbiddenError("Forbidden")

    result = await webhook_service.test_webhook(webhook_id)
    return result


@router.get("/{webhook_id}/deliveries", response_model=List[DeliveryOut])
async def get_delivery_logs(
    webhook_id: int,
    limit: int = Query(50, ge=1, le=200),
    principal: Principal = Depends(get_current_principal),
):
    """查询 Webhook 投递记录"""
    existing = webhook_service.get_webhook(webhook_id)
    if not existing:
        raise NotFoundError("Webhook not found")
    if existing["user_id"] != principal.user_id:
        raise ForbiddenError("Forbidden")

    return webhook_service.get_delivery_logs(webhook_id, limit=limit)
