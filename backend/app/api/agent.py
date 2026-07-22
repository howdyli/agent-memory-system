"""
Agent API 路由

提供 Agent 与记忆系统联动的 HTTP 接口：
- POST /chat         带记忆的 Agent 对话（核心入口）
- GET  /tools/schema 获取 Tool Schema（OpenAI Function Calling 格式）
- POST /tools        获取可用工具列表
- POST /extract      LLM 驱动的记忆抽取
"""
import logging
import json
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List, AsyncGenerator

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.agent_loop import memory_aware_chat, memory_aware_chat_stream, MEMORY_TOOLS, _handle_tool_call
from app.services.agent_memory_sdk import AgentMemoryClient
from app.services.llm_extraction_service import llm_extract_memories
from app.core.auth import Principal, get_current_principal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agent"])


# ============================================================
# 请求模型
# ============================================================

class AgentChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    system_prompt: Optional[str] = None
    session_id: Optional[str] = None


class ExtractRequest(BaseModel):
    conversation: List[Dict[str, str]] = Field(..., min_length=1)
    auto_store: Optional[bool] = True


class ExecuteToolRequest(BaseModel):
    parameters: Dict[str, Any] = {}


# ============================================================
# API 路由
# ============================================================

@router.post("/chat", summary="Agent 对话", description="带记忆的 Agent 对话（核心入口）：自动召回 → LLM 推理 → 记忆抽取")
async def agent_chat(
    request: AgentChatRequest,
    principal: Principal = Depends(get_current_principal),
):
    """
    带记忆的 Agent 对话（核心入口）

    流程：
    1. 自动召回相关记忆 → 注入上下文
    2. LLM 推理（支持 Tool Calling）
    3. 从对话中抽取新记忆

    响应包含：
    - response: 助手回复文本
    - tool_calls: 本轮工具调用记录
    - memories_extracted: 新抽取的记忆条数
    - memory_context_used: 是否使用了记忆上下文
    """
    try:
        result = memory_aware_chat(
            user_id=principal.user_id,
            user_message=request.message,
            system_prompt=request.system_prompt,
            session_id=request.session_id,
            workspace_id=principal.workspace_id,
        )

        if result.get("success"):
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("error", "Agent chat failed"),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Agent 对话失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


# ============================================================
# Streaming Helpers
# ============================================================

def _sse_event(event_type: str, data: dict) -> str:
    """构造 SSE 事件字符串"""
    return f"data: {json.dumps({'type': event_type, **data}, ensure_ascii=False)}\n\n"


async def _chat_stream_generator(
    user_id: int,
    message: str,
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
    workspace_id: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """SSE 事件生成器 - 真流式输出 Agent 对话结果"""
    try:
        for event in memory_aware_chat_stream(
            user_id=user_id,
            user_message=message,
            system_prompt=system_prompt,
            session_id=session_id,
            workspace_id=workspace_id,
        ):
            event_type = event.get("type")

            if event_type == "phase":
                yield _sse_event("phase", {"phase": event.get("phase"), "status": event.get("status", "start")})

            elif event_type == "memory_context":
                yield _sse_event("memory_context", {"memories": event.get("memories", [])})

            elif event_type == "tool_call":
                yield _sse_event("tool_call", {
                    "tool": event.get("tool", ""),
                    "arguments": event.get("arguments", {}),
                    "result": event.get("result", ""),
                })

            elif event_type == "token":
                yield _sse_event("token", {"content": event.get("content", "")})

            elif event_type == "memory":
                yield _sse_event("memory", {
                    "extracted": event.get("extracted", 0),
                    "context_used": event.get("context_used", False),
                    "details": event.get("details", []),
                })

            elif event_type == "done":
                yield _sse_event("done", {"session_id": event.get("session_id", session_id or "")})

            elif event_type == "error":
                yield _sse_event("error", {"message": event.get("message", "Unknown error")})

    except Exception as e:
        logger.error(f"流式 Agent 对话失败: {e}")
        yield _sse_event("error", {"message": str(e)})


# ============================================================
# Streaming API
# ============================================================

@router.post("/chat/stream", summary="流式 Agent 对话", description="SSE 流式输出 Agent 对话结果，支持渐进式 token 输出")
async def agent_chat_stream(
    request: AgentChatRequest,
    principal: Principal = Depends(get_current_principal),
):
    """
    SSE 流式 Agent 对话（渐进式输出）

    返回 SSE 事件流，包含以下事件类型：
    - token: 响应文本片段
    - tool_call: 工具调用记录
    - memory: 记忆抽取统计
    - done: 完成信号（含 session_id）
    - error: 错误信息
    """
    return StreamingResponse(
        _chat_stream_generator(
            user_id=principal.user_id,
            message=request.message,
            system_prompt=request.system_prompt,
            session_id=request.session_id,
            workspace_id=principal.workspace_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/tools/schema")
async def get_tools_schema(
    principal: Principal = Depends(get_current_principal),
):
    """
    获取记忆工具的 Tool Schema（OpenAI Function Calling 格式）

    可直接用于 OpenAI / 兼容 API 的 tools 参数。
    """
    return {
        "success": True,
        "tools": MEMORY_TOOLS,
    }


@router.post("/tools")
async def list_tools(
    principal: Principal = Depends(get_current_principal),
):
    """
    获取可用记忆工具列表（供外部 Agent 注册使用）
    """
    tools_summary = []
    for tool_def in MEMORY_TOOLS:
        func = tool_def.get("function", {})
        tools_summary.append({
            "name": func.get("name"),
            "description": func.get("description"),
            "parameters": func.get("parameters"),
        })

    return {
        "success": True,
        "tools": tools_summary,
        "count": len(tools_summary),
    }


@router.post("/tools/{tool_name}/execute")
async def execute_tool(
    tool_name: str,
    request: ExecuteToolRequest,
    principal: Principal = Depends(get_current_principal),
):
    """
    直接执行指定工具（不经过 Agent 对话）

    用于工具测试台，复用 agent_loop 中的工具函数。
    """
    valid_names = {t["function"]["name"] for t in MEMORY_TOOLS}
    if tool_name not in valid_names:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"未知工具: {tool_name}，可用工具: {sorted(valid_names)}",
        )

    try:
        sdk = AgentMemoryClient(principal.user_id, principal.workspace_id)
        result_str = _handle_tool_call(sdk, tool_name, request.parameters)
        try:
            result = json.loads(result_str)
        except (json.JSONDecodeError, TypeError):
            result = {"raw": result_str}
        return {"success": True, "tool": tool_name, "result": result}
    except Exception as e:
        logger.error(f"工具执行失败 [{tool_name}]: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/extract", summary="LLM 记忆抽取", description="从对话历史中智能抽取需要长期记忆的信息并自动存储")
async def extract_memories(
    request: ExtractRequest,
    principal: Principal = Depends(get_current_principal),
):
    """
    LLM 驱动的记忆抽取

    从对话历史中智能抽取需要长期记忆的信息，
    并自动存储到 KV 变量和语义片段中。
    """
    try:
        result = llm_extract_memories(
            user_id=principal.user_id,
            conversation=request.conversation,
            auto_store=request.auto_store if request.auto_store is not None else True,
        )

        if result.get("success"):
            return result
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("error", "Extraction failed"),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"记忆抽取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )
