"""
记忆变量抽取与注入 API 路由
"""
import logging
import fastapi as _fastapi
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional, Any, Dict, List, Literal

# 导入服务
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.memory_extraction_service import (
    process_user_input,
    inject_memory_into_prompt,
    generate_personalized_response,
    batch_extract_from_conversation,
    get_user_context_for_llm
)
from app.services.llm_extraction_service import (
    llm_extract_memories,
    list_extraction_templates,
    upsert_extraction_template,
    reset_extraction_template,
)
from app.core.auth import Principal, get_current_principal
from app.core.rbac import Perm, require_permission

logger = logging.getLogger(__name__)

router = APIRouter(tags=["memory-extraction"])


# 请求模型
class ProcessInputRequest(BaseModel):
    user_input: str
    session_id: Optional[str] = None


class InjectPromptRequest(BaseModel):
    prompt_template: str
    session_id: Optional[str] = None
    custom_variables: Optional[Dict[str, Any]] = None


class GenerateResponseRequest(BaseModel):
    response_template: str
    session_id: Optional[str] = None
    context: Optional[Dict[str, Any]] = None


class BatchExtractRequest(BaseModel):
    conversation_history: List[Dict[str, str]]
    session_id: Optional[str] = None


class FeedbackRequest(BaseModel):
    extraction_id: str
    rating: Literal["correct", "partial", "incorrect"]
    correction: Optional[str] = None
    source_text: Optional[str] = None
    extracted_data: Optional[Dict[str, Any]] = None


class PreviewRequest(BaseModel):
    text: str = Field(..., min_length=1, description="待抽取的文本（不能为空）")
    session_id: Optional[str] = None


class UpdateTemplateRequest(BaseModel):
    content: str
    name: Optional[str] = None  # 路径参数优先；若路径无 name 则用此字段
    set_active: Optional[bool] = True


# API 路由
@router.post("/process")
async def process_input(
    request: ProcessInputRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    处理用户输入，抽取并存储记忆变量
    
    Args:
        request: 处理输入请求（user_input, session_id）
        current_user: 当前登录用户
        
    Returns:
        抽取结果
    """
    try:
        result = process_user_input(
            user_id=principal.user_id,
            user_input=request.user_input,
            session_id=request.session_id
        )
        
        return result
            
    except Exception as e:
        logger.error(f"✗ 处理用户输入失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/inject")
async def inject_prompt(
    request: InjectPromptRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    将记忆变量注入到 Prompt 模板中
    
    Args:
        request: 注入 Prompt 请求（prompt_template, session_id, custom_variables）
        current_user: 当前登录用户
        
    Returns:
        注入后的 Prompt 字符串
    """
    try:
        injected = inject_memory_into_prompt(
            user_id=principal.user_id,
            prompt_template=request.prompt_template,
            session_id=request.session_id,
            custom_variables=request.custom_variables
        )
        
        return {
            "prompt_template": request.prompt_template,
            "injected_prompt": injected,
            "success": True
        }
            
    except Exception as e:
        logger.error(f"✗ 注入 Prompt 失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/generate")
async def generate_response(
    request: GenerateResponseRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    生成个性化回复（基于记忆变量）
    
    Args:
        request: 生成回复请求（response_template, session_id, context）
        current_user: 当前登录用户
        
    Returns:
        个性化回复字符串
    """
    try:
        personalized = generate_personalized_response(
            user_id=principal.user_id,
            response_template=request.response_template,
            session_id=request.session_id,
            context=request.context
        )
        
        return {
            "response_template": request.response_template,
            "personalized_response": personalized,
            "success": True
        }
            
    except Exception as e:
        logger.error(f"✗ 生成个性化回复失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.post("/batch-extract")
async def batch_extract(
    request: BatchExtractRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    从对话历史中批量抽取记忆变量
    
    Args:
        request: 批量抽取请求（conversation_history, session_id）
        current_user: 当前登录用户
        
    Returns:
        批量抽取结果
    """
    try:
        result = batch_extract_from_conversation(
            user_id=principal.user_id,
            conversation_history=request.conversation_history,
            session_id=request.session_id
        )
        
        return result
            
    except Exception as e:
        logger.error(f"✗ 批量抽取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/context")
async def get_context(
    session_id: Optional[str] = None,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """
    获取用户上下文（用于注入到 LLM Prompt）
    
    Args:
        session_id: 会话ID（可选）
        current_user: 当前登录用户
        
    Returns:
        格式化的上下文字符串
    """
    try:
        context_str = get_user_context_for_llm(
            user_id=principal.user_id,
            session_id=session_id
        )
        
        return {
            "context": context_str,
            "success": True
        }
            
    except Exception as e:
        logger.error(f"✗ 获取用户上下文失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/summary")
async def get_memory_summary(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """
    获取用户记忆摘要
    
    汇总用户的所有记忆变量、事实、偏好和计划，生成结构化摘要。
    
    Args:
        current_user: 当前登录用户
        
    Returns:
        记忆摘要字符串
    """
    try:
        # 获取用户上下文
        context_str = get_user_context_for_llm(
            user_id=principal.user_id,
            session_id=None
        )
        
        # 获取记忆变量
        from app.services.memory_variable_service import list_memory_variables
        variables = list_memory_variables(user_id=principal.user_id)
        
        # 构建摘要
        summary_parts = []
        
        if variables:
            summary_parts.append("=== 记忆变量 ===")
            for key, value in variables.items():
                summary_parts.append(f"  • {key}: {value}")
        
        if context_str and context_str.strip():
            summary_parts.append("\n=== 记忆上下文 ===")
            summary_parts.append(context_str)
        
        if not summary_parts:
            summary = "暂无记忆数据"
        else:
            summary = "\n".join(summary_parts)
        
        return {
            "summary": summary,
            "variable_count": len(variables) if variables else 0,
            "success": True
        }
            
    except Exception as e:
        logger.error(f"✗ 获取记忆摘要失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


# ============================================================
# 抽取反馈 / 模板 / 预览 API
# ============================================================


@router.post("/feedback")
async def submit_feedback(
    request: FeedbackRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    接收用户对抽取结果的反馈

    body: { extraction_id, rating: "correct"|"partial"|"incorrect", correction?, source_text?, extracted_data? }
    存储到数据库用于后续分析。
    """
    try:
        import json
        from app.core.db_client import get_db_client
        db = get_db_client()

        extracted_json = json.dumps(request.extracted_data, ensure_ascii=False) if request.extracted_data else None
        db.execute(
            """INSERT INTO extraction_feedback (user_id, extraction_id, rating, correction, source_text, extracted_data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                principal.user_id,
                request.extraction_id,
                request.rating,
                request.correction,
                request.source_text,
                extracted_json,
            ),
        )

        logger.info(f"✓ 保存抽取反馈: extraction_id={request.extraction_id} rating={request.rating}")
        return {
            "success": True,
            "message": "反馈已保存",
            "extraction_id": request.extraction_id,
            "rating": request.rating,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 保存抽取反馈失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get("/feedback")
async def list_feedback(
    limit: int = 50,
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """获取当前用户的抽取反馈历史（用于前端避免重复反馈）。"""
    try:
        from app.core.db_client import get_db_client
        db = get_db_client()
        rows = db.execute(
            """SELECT extraction_id, rating, correction, created_at
               FROM extraction_feedback WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (principal.user_id, min(limit, 200)),
        )
        feedbacks = []
        if rows:
            for r in rows:
                feedbacks.append({
                    "extraction_id": r["extraction_id"],
                    "rating": r["rating"],
                    "correction": r["correction"],
                    "created_at": str(r["created_at"]),
                })
        return {"success": True, "feedbacks": feedbacks, "count": len(feedbacks)}
    except Exception as e:
        logger.error(f"✗ 获取抽取反馈失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.get("/templates")
async def get_templates(
    principal: Principal = Depends(require_permission(Perm.MEMORY_READ))
):
    """
    获取抽取 Prompt 模板列表

    返回用户自定义模板 + 默认模板。
    """
    try:
        templates = list_extraction_templates(principal.user_id)
        return {"success": True, "templates": templates, "count": len(templates)}
    except Exception as e:
        logger.error(f"✗ 获取抽取模板失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.put("/templates/{name}")
async def update_template(
    name: str,
    request: UpdateTemplateRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    更新（或创建）抽取 Prompt 模板

    路径参数 name 为模板名称；body.content 为模板内容。
    默认设为当前生效模板（set_active=True）。
    """
    try:
        result = upsert_extraction_template(
            user_id=principal.user_id,
            name=name,
            content=request.content,
            set_active=request.set_active if request.set_active is not None else True,
        )
        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "保存失败"),
            )
        return {"success": True, "name": name, "message": "模板已保存并设为生效"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 更新抽取模板失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/templates/reset")
async def reset_template(
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """恢复默认抽取 Prompt 模板。"""
    try:
        result = reset_extraction_template(principal.user_id)
        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=result.get("error", "重置失败"),
            )
        return {"success": True, "message": "已恢复默认模板"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 重置抽取模板失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


@router.post("/preview")
async def preview_extraction(
    request: PreviewRequest,
    principal: Principal = Depends(require_permission(Perm.MEMORY_WRITE))
):
    """
    预览抽取结果（不保存）

    接收文本，返回抽取结果但不持久化到记忆层。
    用于在用户确认前预览抽取质量。
    """
    try:
        conversation = [
            {"role": "user", "content": request.text},
            {"role": "assistant", "content": "好的，我已经记住了这些信息。"},
        ]

        # auto_store=False → 仅抽取不保存
        result = llm_extract_memories(
            user_id=principal.user_id,
            conversation=conversation,
            auto_store=False,
            session_id=request.session_id,
        )

        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result.get("error", "抽取失败"),
            )

        return {
            "success": True,
            "preview": True,
            "variables": result.get("variables", []),
            "facts": result.get("facts", []),
            "preferences": result.get("preferences", []),
            "plans": result.get("plans", []),
            "count": (
                len(result.get("variables", []))
                + len(result.get("facts", []))
                + len(result.get("preferences", []))
                + len(result.get("plans", []))
            ),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"✗ 预览抽取失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e),
        )


# 测试函数
def test_memory_extraction_api():
    """测试记忆变量抽取与注入 API（模拟）"""
    print("\n" + "="*60)
    print("测试记忆变量抽取与注入服务")
    print("="*60 + "\n")
    
    user_id = 999  # 测试用户ID
    session_id = "test_session_123"
    
    # 测试1：处理用户输入
    print("1. 测试处理用户输入...")
    user_input = "我叫鑫海，我的角色是 PM。我的项目是源启·智能体工厂。"
    result = process_user_input(user_id, user_input, session_id)
    
    print(f"   用户输入：{user_input}")
    print(f"   抽取结果：{result['extracted']}")
    assert result["success"] == True
    assert "user_name" in result["extracted"]
    print(f"   ✓ 处理用户输入成功")
    
    # 测试2：注入记忆变量到 Prompt
    print(f"\n2. 测试注入记忆变量到 Prompt...")
    prompt_template = "你好，{user_name}！你的角色是 {user_role}。"
    injected = inject_memory_into_prompt(user_id, prompt_template, session_id)
    
    print(f"   原始 Prompt：{prompt_template}")
    print(f"   注入后：{injected}")
    assert "{user_name}" not in injected
    assert "鑫海" in injected
    print(f"   ✓ 注入记忆变量成功")
    
    # 测试3：生成个性化回复
    print(f"\n3. 测试生成个性化回复...")
    response_template = "你好，{user_name}！你的角色是 {user_role}。你的项目包括：{user_projects}。"
    personalized = generate_personalized_response(user_id, response_template, session_id)
    
    print(f"   原始回复模板：{response_template}")
    print(f"   个性化回复：{personalized}")
    assert "{user_name}" not in personalized
    print(f"   ✓ 生成个性化回复成功")
    
    # 测试4：从对话历史中批量抽取
    print(f"\n4. 测试从对话历史中批量抽取...")
    conversation_history = [
        {"role": "user", "content": "我叫鑫海"},
        {"role": "assistant", "content": "你好，鑫海！"},
        {"role": "user", "content": "我的名字是XinHai，我的角色是 PM"}
    ]
    batch_result = batch_extract_from_conversation(user_id, conversation_history, session_id)
    
    print(f"   对话历史：{len(conversation_history)} 条消息")
    print(f"   批量抽取结果：{batch_result['all_extracted']}")
    assert batch_result["success"] == True
    print(f"   ✓ 批量抽取成功")
    
    # 测试5：获取用户上下文（用于 LLM Prompt）
    print(f"\n5. 测试获取用户上下文...")
    context_str = get_user_context_for_llm(user_id, session_id)
    
    print(f"   用户上下文：\n{context_str}")
    assert "鑫海" in context_str
    print(f"   ✓ 获取用户上下文成功")
    
    # 清理测试数据
    print(f"\n6. 清理测试数据...")
    from app.services.memory_variable_service import clear_memory_variables
    clear_memory_variables(user_id, session_id)
    print(f"   ✓ 清理完成")
    
    print("\n" + "="*60)
    print("✅ 记忆变量抽取与注入 API 测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 运行测试
    test_memory_extraction_api()
