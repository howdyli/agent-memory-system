"""
多模态记忆服务（P2 R-13）

图片上传 → Vision LLM 生成文字描述 → 描述入库为记忆片段（fragment_type='multimodal'），
attachment 元数据关联原图，复用现有文本嵌入/召回全链路。

降级策略：
- 用户显式提供 description → 直接使用（caption_source='manual'）
- 未提供且 Vision 不可用（无 API Key / 调用失败）→ 返回失败并清理已存文件，要求用户补描述
"""
import logging
import os
import json
import uuid
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.config import get_settings


_VISION_CAPTION_PROMPT = (
    "请客观描述这张图片的内容，包括图中的主要对象、场景和可见的文字信息。"
    "用 1-3 句中文陈述，不要推测图片之外的信息，不要任何额外说明。"
)

_MIME_MAP = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp",
}


# ============================================================
# memory_attachments 附件表
# ============================================================

def _ensure_attachment_table() -> None:
    """创建附件表（与 consolidation_runs 同款服务内建表模式）"""
    db = get_db_client()
    db.execute('''CREATE TABLE IF NOT EXISTS memory_attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        fragment_id INTEGER,
        file_name TEXT NOT NULL,
        stored_path TEXT NOT NULL,
        mime_type TEXT,
        file_size INTEGER,
        caption_source TEXT DEFAULT 'manual',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    )''')
    for sql in [
        'CREATE INDEX IF NOT EXISTS idx_attachments_user ON memory_attachments(user_id)',
        'CREATE INDEX IF NOT EXISTS idx_attachments_fragment ON memory_attachments(fragment_id)',
    ]:
        try:
            db.execute(sql)
        except Exception:
            pass


def _get_upload_dir() -> str:
    """获取上传目录绝对路径（不存在则创建）"""
    settings = get_settings()
    upload_dir = settings.MULTIMODAL_UPLOAD_DIR
    if not os.path.isabs(upload_dir):
        # 相对 backend 根（app/services/ 的上两级）
        backend_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        upload_dir = os.path.join(backend_root, upload_dir)
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def _validate_file(filename: str, file_size: int) -> Optional[str]:
    """校验扩展名与大小，返回错误信息（None 表示通过）"""
    settings = get_settings()
    ext = os.path.splitext(filename or "")[1].lstrip(".").lower()
    allowed = [e.strip().lower() for e in settings.MULTIMODAL_ALLOWED_EXTENSIONS.split(",") if e.strip()]
    if ext not in allowed:
        return f"不支持的文件类型 '{ext}'，允许: {', '.join(allowed)}"
    max_bytes = settings.MULTIMODAL_MAX_FILE_SIZE_MB * 1024 * 1024
    if file_size > max_bytes:
        return f"文件过大（{file_size} 字节），上限 {settings.MULTIMODAL_MAX_FILE_SIZE_MB}MB"
    if file_size <= 0:
        return "文件内容为空"
    return None


# ============================================================
# 核心：存储图片记忆
# ============================================================

def store_image_memory(user_id: int,
                       file_bytes: bytes,
                       filename: str,
                       description: Optional[str] = None,
                       workspace_id: Optional[int] = None,
                       agent_id: Optional[int] = None,
                       scope: str = "shared") -> Dict[str, Any]:
    """
    存储图片记忆：存文件 → 获取描述（manual/vision）→ create_fragment → 写附件行

    Returns:
        {"success": True, "fragment_id", "attachment_id", "caption", "caption_source", ...}
    """
    try:
        settings = get_settings()
        if not settings.MULTIMODAL_ENABLED:
            return {"success": False, "error": "多模态记忆功能未启用（MULTIMODAL_ENABLED=False）"}

        # 1. 校验并落盘
        error = _validate_file(filename, len(file_bytes))
        if error:
            return {"success": False, "error": error}

        ext = os.path.splitext(filename)[1].lstrip(".").lower()
        stored_name = f"{uuid.uuid4().hex}.{ext}"
        stored_path = os.path.join(_get_upload_dir(), stored_name)
        with open(stored_path, "wb") as f:
            f.write(file_bytes)

        # 2. 获取描述：手写优先，否则 Vision captioning，两者皆无 → 失败并清理文件
        if description and description.strip():
            caption = description.strip()
            caption_source = "manual"
        else:
            from app.services.llm_backend_service import llm_vision_chat
            caption = llm_vision_chat(_VISION_CAPTION_PROMPT, stored_path)
            caption_source = "vision"
            if not caption:
                try:
                    os.remove(stored_path)
                except OSError:
                    pass
                return {
                    "success": False,
                    "error": "Vision 模型不可用且未提供图片描述，请在 description 字段中手动填写图片说明",
                }

        # 3. 描述文本入库为记忆片段（走既有嵌入/召回链路）
        from app.services.memory_fragment_service import create_fragment
        create_result = create_fragment(
            user_id=user_id,
            fragment_type="multimodal",
            content=caption,
            metadata={
                "attachment": stored_name,
                "original_filename": filename,
                "caption_source": caption_source,
            },
            workspace_id=workspace_id,
            agent_id=agent_id,
            scope=scope,
        )
        if not create_result.get("success"):
            try:
                os.remove(stored_path)
            except OSError:
                pass
            return {"success": False, "error": f"创建记忆片段失败: {create_result.get('error')}"}
        fragment_id = create_result["fragment_id"]

        # 4. 写附件行
        _ensure_attachment_table()
        db = get_db_client()
        attachment_id = db.execute(
            '''INSERT INTO memory_attachments
               (user_id, fragment_id, file_name, stored_path, mime_type, file_size, caption_source)
               VALUES (?, ?, ?, ?, ?, ?, ?)''',
            (user_id, fragment_id, filename, stored_name,
             _MIME_MAP.get(ext, "application/octet-stream"), len(file_bytes), caption_source)
        )

        logger.info(f"✓ 存储图片记忆: fragment={fragment_id}, attachment={attachment_id}, source={caption_source}")
        return {
            "success": True,
            "fragment_id": fragment_id,
            "attachment_id": attachment_id,
            "caption": caption,
            "caption_source": caption_source,
            "file_name": filename,
            "file_size": len(file_bytes),
        }

    except Exception as e:
        logger.error(f"✗ 存储图片记忆失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 附件查询/删除
# ============================================================

def get_attachment(user_id: int, attachment_id: int) -> Dict[str, Any]:
    """获取附件元信息与本地文件路径（校验归属）"""
    try:
        _ensure_attachment_table()
        db = get_db_client()
        rows = db.execute(
            'SELECT * FROM memory_attachments WHERE id = ? AND user_id = ?',
            (attachment_id, user_id)
        )
        if not rows:
            return {"success": False, "error": f"附件 {attachment_id} 不存在"}
        att = dict(rows[0])
        file_path = os.path.join(_get_upload_dir(), att["stored_path"])
        if not os.path.exists(file_path):
            return {"success": False, "error": "附件文件已丢失"}
        return {"success": True, "attachment": att, "file_path": file_path}
    except Exception as e:
        logger.error(f"✗ 获取附件失败: {e}")
        return {"success": False, "error": str(e)}


def list_attachments(user_id: int, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
    """列出用户附件（按创建时间倒序）"""
    try:
        _ensure_attachment_table()
        db = get_db_client()
        rows = db.execute(
            '''SELECT id, fragment_id, file_name, mime_type, file_size, caption_source, created_at
               FROM memory_attachments WHERE user_id = ?
               ORDER BY id DESC LIMIT ? OFFSET ?''',
            (user_id, limit, offset)
        )
        attachments = [dict(r) for r in rows] if rows else []
        return {"success": True, "attachments": attachments, "count": len(attachments)}
    except Exception as e:
        logger.error(f"✗ 列出附件失败: {e}")
        return {"success": False, "error": str(e)}


def delete_attachment(user_id: int, attachment_id: int, delete_fragment_too: bool = True) -> Dict[str, Any]:
    """删除附件（本地文件 + 附件行，默认级联删除关联片段）"""
    try:
        result = get_attachment(user_id, attachment_id)
        if not result.get("success") and "已丢失" not in result.get("error", ""):
            return result

        db = get_db_client()
        rows = db.execute(
            'SELECT * FROM memory_attachments WHERE id = ? AND user_id = ?',
            (attachment_id, user_id)
        )
        if not rows:
            return {"success": False, "error": f"附件 {attachment_id} 不存在"}
        att = dict(rows[0])

        # 删本地文件（丢失时忽略）
        file_path = os.path.join(_get_upload_dir(), att["stored_path"])
        try:
            os.remove(file_path)
        except OSError:
            pass

        # 级联删除关联片段（直接按 id+user 删除，不受 workspace 过滤影响）
        if delete_fragment_too and att.get("fragment_id"):
            db.execute('DELETE FROM memory_fragments WHERE id = ? AND user_id = ?',
                       (att["fragment_id"], user_id))

        db.execute('DELETE FROM memory_attachments WHERE id = ? AND user_id = ?',
                   (attachment_id, user_id))
        logger.info(f"✓ 删除附件: {attachment_id}")
        return {"success": True, "attachment_id": attachment_id}
    except Exception as e:
        logger.error(f"✗ 删除附件失败: {e}")
        return {"success": False, "error": str(e)}
