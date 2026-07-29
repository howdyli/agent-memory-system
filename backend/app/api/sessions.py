"""
Sessions API 路由

提供会话管理相关的 REST 接口：列表、详情、删除、重命名、消息获取、搜索、批量删除、摘要管理
"""
import logging
import re
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List

from app.core.auth import Principal, get_current_principal
from app.core.db_client import get_db_client
from app.services.context_compressor import ConversationManager

logger = logging.getLogger(__name__)

router = APIRouter()

_conversation_mgr: Optional[ConversationManager] = None


def _get_conversation_mgr() -> ConversationManager:
    global _conversation_mgr
    if _conversation_mgr is None:
        _conversation_mgr = ConversationManager()
    return _conversation_mgr


class RenameRequest(BaseModel):
    title: str


class BatchDeleteRequest(BaseModel):
    session_ids: List[str] = Field(..., min_length=1)


# ----------------------------------------------------------
# GET /sessions — 列出用户会话
# ----------------------------------------------------------
@router.get("/sessions", summary="列出会话", description="列出当前用户的会话，支持传统分页（limit/offset）和页码分页（page/page_size）")
async def list_sessions(
    principal: Principal = Depends(get_current_principal),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    page: int = Query(0, ge=0),
    page_size: int = Query(0, ge=0, le=200),
):
    """
    列出用户会话。

    支持两种分页方式：
    - 传统：limit / offset
    - 页码：page / page_size（优先级高于 limit/offset，page 从 1 开始）
    """
    mgr = _get_conversation_mgr()
    if page > 0 and page_size > 0:
        offset = (page - 1) * page_size
        limit = page_size
    sessions = mgr.list_sessions(principal.user_id, limit=limit, offset=offset)
    return {"success": True, "sessions": sessions, "count": len(sessions)}


# ----------------------------------------------------------
# GET /sessions/search — 搜索会话
# ----------------------------------------------------------
@router.get("/sessions/search", summary="搜索会话", description="搜索会话标题与消息内容，返回匹配会话列表及高亮片段")
async def search_sessions(
    principal: Principal = Depends(get_current_principal),
    q: str = Query(..., min_length=1, max_length=200, description="搜索关键词"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    highlight_length: int = Query(80, ge=20, le=300, description="高亮片段长度"),
):
    """
    搜索会话标题与消息内容。

    返回匹配的会话列表，并附带匹配内容的高亮片段。
    """
    db = get_db_client()
    keyword = f"%{q}%"
    offset = (page - 1) * page_size

    try:
        # 1. 先找出标题或消息内容匹配关键词的 session_id 集合（去重）
        matched_rows = db.execute(
            """
            SELECT DISTINCT s.session_id
            FROM chat_sessions s
            LEFT JOIN conversation_history h ON h.session_id = s.session_id
            WHERE s.user_id = ?
              AND (s.title LIKE ? OR h.content LIKE ?)
            ORDER BY s.updated_at DESC
            LIMIT ? OFFSET ?
            """,
            (principal.user_id, keyword, keyword, page_size, offset),
        )
        session_ids = [row["session_id"] for row in (matched_rows or [])]

        if not session_ids:
            return {"success": True, "sessions": [], "count": 0, "query": q}

        # 2. 查询这些会话的元数据
        placeholders = ",".join(["?"] * len(session_ids))
        sessions_rows = db.execute(
            f"""
            SELECT * FROM chat_sessions
            WHERE session_id IN ({placeholders})
            ORDER BY updated_at DESC
            """,
            tuple(session_ids),
        )
        sessions = [dict(r) for r in (sessions_rows or [])]

        # 3. 批量查询所有会话的匹配消息（避免 N+1 查询）
        placeholders = ",".join(["?"] * len(session_ids))
        all_content_rows = db.execute(
            f"""
            SELECT session_id, content FROM conversation_history
            WHERE session_id IN ({placeholders}) AND content LIKE ?
            ORDER BY id DESC
            """,
            (*session_ids, keyword),
        )
        # 按会话分组，每个会话最多取 3 条
        from collections import defaultdict
        content_by_session = defaultdict(list)
        for row in (all_content_rows or []):
            sid = row["session_id"]
            if len(content_by_session[sid]) < 3:
                content_by_session[sid].append(row["content"] or "")

        # 为每个会话提取高亮片段
        for session in sessions:
            sid = session["session_id"]
            highlights = []
            for text in content_by_session.get(sid, []):
                idx = text.lower().find(q.lower())
                if idx < 0:
                    idx = 0
                start = max(0, idx - highlight_length // 2)
                end = min(len(text), idx + highlight_length // 2)
                snippet = text[start:end]
                if start > 0:
                    snippet = "..." + snippet
                if end < len(text):
                    snippet = snippet + "..."
                highlights.append(snippet)
            session["highlights"] = highlights
            session["title_matched"] = bool(session.get("title") and q.lower() in session["title"].lower())

        return {"success": True, "sessions": sessions, "count": len(sessions), "query": q}
    except Exception as e:
        logger.warning(f"搜索会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------------------------------------
# DELETE /sessions/batch — 批量删除会话
# ----------------------------------------------------------
@router.delete("/sessions/batch")
async def batch_delete_sessions(
    req: BatchDeleteRequest,
    principal: Principal = Depends(get_current_principal),
):
    """批量删除指定会话（仅删除属于当前用户的会话）"""
    db = get_db_client()
    mgr = _get_conversation_mgr()

    try:
        placeholders = ",".join(["?"] * len(req.session_ids))
        # 过滤出属于当前用户的 session_id
        rows = db.execute(
            f"""
            SELECT session_id FROM chat_sessions
            WHERE user_id = ? AND session_id IN ({placeholders})
            """,
            (principal.user_id, *req.session_ids),
        )
        owned_ids = [r["session_id"] for r in (rows or [])]

        deleted = 0
        failed = []
        for sid in owned_ids:
            result = mgr.delete_session(sid)
            if result.get("success"):
                deleted += 1
            else:
                failed.append({"session_id": sid, "error": result.get("error", "unknown")})

        skipped = len(req.session_ids) - len(owned_ids)
        return {
            "success": True,
            "deleted": deleted,
            "skipped": skipped,
            "failed": failed,
        }
    except Exception as e:
        logger.warning(f"批量删除会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ----------------------------------------------------------
# GET /sessions/{session_id} — 会话详情
# ----------------------------------------------------------
@router.get("/sessions/{session_id}")
async def get_session(session_id: str):
    mgr = _get_conversation_mgr()
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"success": True, "session": session}


# ----------------------------------------------------------
# DELETE /sessions/{session_id} — 删除会话
# ----------------------------------------------------------
@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    mgr = _get_conversation_mgr()
    result = mgr.delete_session(session_id)
    return result


# ----------------------------------------------------------
# PUT /sessions/{session_id}/title — 重命名
# ----------------------------------------------------------
@router.put("/sessions/{session_id}/title")
async def rename_session(session_id: str, req: RenameRequest):
    mgr = _get_conversation_mgr()
    result = mgr.rename_session(session_id, req.title)
    return result


# ----------------------------------------------------------
# GET /sessions/{session_id}/messages — 获取会话消息
# ----------------------------------------------------------
@router.get("/sessions/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    limit: int = Query(20, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    mgr = _get_conversation_mgr()
    messages, total = mgr.get_session_messages(session_id, limit=limit, offset=offset)
    return {"success": True, "messages": messages, "count": len(messages), "total": total}


# ============================================================
# 摘要相关 API
# ============================================================

class UpdateSummaryRequest(BaseModel):
    summary: str


def _calculate_summary_quality(summary: str) -> dict:
    """
    计算摘要质量评分（0-5 分）。

    评分维度：
    - 长度适中（50-300 字为佳）
    - 信息密度（关键信息覆盖率）
    - 结构清晰度（是否有逻辑分段）
    """
    if not summary or not summary.strip():
        return {"score": 0, "details": {"length": 0, "density": 0, "structure": 0}}

    text = summary.strip()
    char_count = len(text)

    # 长度评分 (0-5)：50-300 字最优
    if char_count < 10:
        length_score = 0.5
    elif char_count < 30:
        length_score = 2.0
    elif char_count < 50:
        length_score = 3.0
    elif char_count <= 300:
        length_score = 5.0
    elif char_count <= 500:
        length_score = 4.0
    else:
        length_score = 3.0

    # 信息密度：包含关键信息标记（人名、数字、时间等）
    info_signals = 0
    # 中文人名模式（2-4字）
    if re.search(r'[\u4e00-\u9fff]{2,4}(?:叫|是|在|说)', text):
        info_signals += 1
    # 数字/时间
    if re.search(r'\d+', text):
        info_signals += 1
    # 组织/地点
    if re.search(r'(公司|学校|团队|项目|系统|平台|部门)', text):
        info_signals += 1
    # 偏好/计划
    if re.search(r'(喜欢|偏好|计划|打算|想要|需要)', text):
        info_signals += 1
    # 多个话题（用分号或句号分隔）
    segments = re.split(r'[；;。！？\n]', text)
    segments = [s.strip() for s in segments if s.strip()]
    if len(segments) >= 3:
        info_signals += 1

    density_score = min(5.0, info_signals * 1.2 + 1.0)

    # 结构评分：有多段/多句为佳
    sentence_count = len(re.split(r'[。！？；\n]', text))
    sentence_count = max(1, sentence_count)
    if sentence_count >= 5:
        structure_score = 5.0
    elif sentence_count >= 3:
        structure_score = 4.0
    elif sentence_count >= 2:
        structure_score = 3.0
    else:
        structure_score = 2.0

    # 综合评分（加权平均）
    final_score = round(0.35 * length_score + 0.40 * density_score + 0.25 * structure_score, 1)
    final_score = min(5.0, max(0.0, final_score))

    return {
        "score": final_score,
        "details": {
            "length": round(length_score, 1),
            "density": round(density_score, 1),
            "structure": round(structure_score, 1),
        },
        "char_count": char_count,
    }


# ----------------------------------------------------------
# GET /sessions/{session_id}/summary — 获取当前摘要
# ----------------------------------------------------------
@router.get("/sessions/{session_id}/summary")
async def get_session_summary(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
):
    """
    获取会话的当前对话摘要。

    返回：
    - summary: 摘要文本
    - quality: 质量评分（0-5）
    - updated_at: 最后更新时间
    - history_count: 历史版本数
    """
    db = get_db_client()

    # 获取最新摘要
    rows = db.execute(
        '''SELECT * FROM conversation_summaries
           WHERE session_id = ? AND user_id = ?
           ORDER BY id DESC LIMIT 1''',
        (session_id, principal.user_id)
    )

    if not rows:
        return {
            "success": True,
            "summary": None,
            "quality": {"score": 0, "details": {}},
            "updated_at": None,
            "history_count": 0,
        }

    row = dict(rows[0])
    summary_text = row["summary"]

    # 获取历史版本数
    count_rows = db.execute(
        'SELECT COUNT(*) as cnt FROM conversation_summaries WHERE session_id = ? AND user_id = ?',
        (session_id, principal.user_id)
    )
    history_count = count_rows[0]["cnt"] if count_rows else 0

    quality = _calculate_summary_quality(summary_text)

    return {
        "success": True,
        "summary": summary_text,
        "quality": quality,
        "updated_at": row.get("created_at"),
        "from_round": row.get("from_round"),
        "to_round": row.get("to_round"),
        "history_count": history_count,
    }


# ----------------------------------------------------------
# PUT /sessions/{session_id}/summary — 更新摘要（仅 SQLite）
# ----------------------------------------------------------
@router.put("/sessions/{session_id}/summary")
async def update_session_summary(
    session_id: str,
    req: UpdateSummaryRequest,
    principal: Principal = Depends(get_current_principal),
):
    """
    手动更新会话摘要。

    重要：仅在 SQLite 中创建新版本记录，
    不会更新 ChromaDB 向量索引，避免触发全量重建。
    """
    db = get_db_client()

    # 验证会话存在且属于当前用户
    session_rows = db.execute(
        'SELECT session_id FROM chat_sessions WHERE session_id = ? AND user_id = ?',
        (session_id, principal.user_id)
    )
    if not session_rows:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 获取当前摘要的轮次范围（用于保持一致性）
    latest = db.execute(
        '''SELECT from_round, to_round FROM conversation_summaries
           WHERE session_id = ? AND user_id = ?
           ORDER BY id DESC LIMIT 1''',
        (session_id, principal.user_id)
    )
    from_round = 1
    to_round = 0
    if latest:
        from_round = latest[0]["from_round"] or 1
        to_round = latest[0]["to_round"] or 0

    # 插入新版本（不更新 ChromaDB）
    db.execute(
        '''INSERT INTO conversation_summaries
           (session_id, user_id, from_round, to_round, summary)
           VALUES (?, ?, ?, ?, ?)''',
        (session_id, principal.user_id, from_round, to_round, req.summary)
    )

    quality = _calculate_summary_quality(req.summary)

    logger.info(
        f"✓ 手动更新摘要: session={session_id}, "
        f"长度={len(req.summary)} 字符, "
        f"评分={quality['score']}"
    )

    return {
        "success": True,
        "message": "摘要已更新（仅存储，未更新向量索引）",
        "quality": quality,
    }


# ----------------------------------------------------------
# GET /sessions/{session_id}/summary/history — 获取摘要历史版本
# ----------------------------------------------------------
@router.get("/sessions/{session_id}/summary/history")
async def get_summary_history(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
    limit: int = Query(20, ge=1, le=100),
):
    """
    获取会话摘要的历史版本列表。

    按时间倒序返回，每条包含摘要内容、创建时间和质量评分。
    """
    db = get_db_client()
    rows = db.execute(
        '''SELECT * FROM conversation_summaries
           WHERE session_id = ? AND user_id = ?
           ORDER BY id DESC
           LIMIT ?''',
        (session_id, principal.user_id, limit)
    )

    history = []
    for row in (rows or []):
        r = dict(row)
        summary_text = r["summary"]
        quality = _calculate_summary_quality(summary_text)
        history.append({
            "id": r["id"],
            "summary": summary_text,
            "from_round": r.get("from_round"),
            "to_round": r.get("to_round"),
            "created_at": r.get("created_at"),
            "quality": quality,
        })

    return {
        "success": True,
        "history": history,
        "count": len(history),
    }


# ----------------------------------------------------------
# POST /sessions/{session_id}/summary/regenerate — 重新生成摘要
# ----------------------------------------------------------
@router.post("/sessions/{session_id}/summary/regenerate")
async def regenerate_summary(
    session_id: str,
    principal: Principal = Depends(get_current_principal),
):
    """
    重新生成会话摘要（调用 LLM）。

    将覆盖当前摘要，生成前会弹出确认提示。
    新生成的摘要仅存储在 SQLite 中。
    """
    db = get_db_client()

    # 验证会话存在
    session_rows = db.execute(
        'SELECT session_id FROM chat_sessions WHERE session_id = ? AND user_id = ?',
        (session_id, principal.user_id)
    )
    if not session_rows:
        raise HTTPException(status_code=404, detail="会话不存在")

    mgr = _get_conversation_mgr()

    # 获取全部对话历史用于生成摘要
    messages = mgr.get_conversation_history(session_id)
    if not messages:
        raise HTTPException(status_code=400, detail="无对话历史，无法生成摘要")

    # 构建对话文本
    conversation_text = ""
    for msg in messages:
        role_label = "用户" if msg["role"] == "user" else "助手"
        conversation_text += f"{role_label}: {msg['content']}\n"

    # 使用 LLM 生成摘要
    try:
        from app.services.llm_backend_service import llm_chat

        summary_prompt = (
            "请将以下对话历史压缩为一段简洁的中文摘要（不超过 200 字）。\n"
            "保留以下关键信息：\n"
            "1. 用户的基本信息（姓名、角色、组织等）\n"
            "2. 用户的偏好和习惯\n"
            "3. 用户的计划和待办事项\n"
            "4. 已完成的对话主题\n"
            "5. 重要的约定和决定\n\n"
            "对话历史：\n"
            f"{conversation_text}\n\n"
            "摘要："
        )

        result = llm_chat(
            user_id=principal.user_id,
            messages=[{"role": "user", "content": summary_prompt}],
            temperature=0.3,
            max_tokens=500,
        )

        if result.get("success") and result.get("content"):
            new_summary = result["content"].strip()
            if len(new_summary) > 500:
                new_summary = new_summary[:497] + "..."
        else:
            raise Exception("LLM 返回为空")

    except Exception as e:
        logger.warning(f"LLM 摘要生成失败: {e}")
        # 回退到简单摘要
        lines = conversation_text.strip().split("\n")
        user_msgs = [l[3:].strip() for l in lines if l.startswith("用户:") and len(l) > 3]
        recent = "；".join(user_msgs[-3:]) if user_msgs else "(无用户消息)"
        total_turns = len([l for l in lines if l.startswith("用户:")])
        new_summary = f"对话共 {total_turns} 轮。最近讨论：{recent}"

    # 获取轮次范围
    total_rounds = mgr.get_total_rounds(session_id)

    # 插入新版本
    db.execute(
        '''INSERT INTO conversation_summaries
           (session_id, user_id, from_round, to_round, summary)
           VALUES (?, ?, 1, ?, ?)''',
        (session_id, principal.user_id, total_rounds, new_summary)
    )

    quality = _calculate_summary_quality(new_summary)

    logger.info(
        f"✓ 重新生成摘要: session={session_id}, "
        f"长度={len(new_summary)} 字符, "
        f"评分={quality['score']}"
    )

    return {
        "success": True,
        "summary": new_summary,
        "quality": quality,
        "message": "摘要已重新生成",
    }
