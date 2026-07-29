"""
多模态记忆 API 路由（P2 R-13）

- POST /upload：上传图片 → Vision 描述（或手写描述）→ 入库为 multimodal 记忆片段
- GET /attachments：附件列表
- GET /attachments/{id}/download：下载原图（带权限与归属校验，不挂 StaticFiles）
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from typing import Optional

from app.services.multimodal_service import (
    store_image_memory,
    get_attachment,
    list_attachments,
    delete_attachment,
)
from app.core.auth import Principal
from app.core.rbac import Perm, require_permission
from app.core.errors import handle_service_result

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-multimodal"])


@router.post("/upload", status_code=201, summary="上传图片记忆",
             description="上传图片并生成文字描述入库（Vision 自动描述或手写描述）")
@handle_service_result
async def upload_image_memory(
    file: UploadFile = File(...),
    description: Optional[str] = Form(None),
    workspace_id: Optional[int] = Form(None),
    agent_id: Optional[int] = Form(None),
    scope: str = Form("shared"),
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """上传图片记忆"""
    file_bytes = await file.read()
    return store_image_memory(
        user_id=principal.user_id,
        file_bytes=file_bytes,
        filename=file.filename or "unnamed",
        description=description,
        workspace_id=workspace_id,
        agent_id=agent_id,
        scope=scope,
    )


@router.get("/attachments", summary="附件列表")
@handle_service_result
async def get_attachments(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """列出当前用户的图片附件"""
    return list_attachments(principal.user_id, limit=limit, offset=offset)


@router.get("/attachments/{attachment_id}/download", summary="下载原图")
async def download_attachment(
    attachment_id: int,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """下载附件原图（校验归属）"""
    result = get_attachment(principal.user_id, attachment_id)
    if not result.get("success"):
        raise HTTPException(status_code=404, detail=result.get("error", "附件不存在"))
    att = result["attachment"]
    return FileResponse(
        result["file_path"],
        media_type=att.get("mime_type") or "application/octet-stream",
        filename=att.get("file_name") or "attachment",
    )


@router.delete("/attachments/{attachment_id}", summary="删除附件")
@handle_service_result
async def remove_attachment(
    attachment_id: int,
    principal: Principal = Depends(require_permission(Perm.MEMORY_DELETE))
):
    """删除附件（级联删除关联记忆片段与本地文件）"""
    return delete_attachment(principal.user_id, attachment_id)
