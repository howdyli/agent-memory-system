"""
记忆变量抽取与注入服务

实现从对话中抽取记忆变量，并注入到对话上下文
"""
import logging
import re
import json
from typing import Optional, Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)

# 导入记忆变量服务
from app.services.memory_variable_service import (
    set_memory_variable,
    get_memory_variable,
    extract_variables_from_text,
    render_template
)


def process_user_input(user_id: int,
                         user_input: str,
                         session_id: Optional[str] = None,
                         workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    处理用户输入，使用 LLM 智能抽取并存储记忆变量
    
    Args:
        user_id: 用户ID
        user_input: 用户输入文本
        session_id: 会话ID（可选）
        
    Returns:
        抽取到的变量字典
    """
    try:
        # 导入 LLM 抽取服务
        from app.services.llm_extraction_service import llm_extract_memories
        
        # 构建对话格式（将单条文本转为对话格式）
        conversation = [
            {"role": "user", "content": user_input},
            {"role": "assistant", "content": "好的，我已经记住了这些信息。"}
        ]
        
        # 使用 LLM 抽取
        llm_result = llm_extract_memories(
            user_id=user_id,
            conversation=conversation,
            auto_store=True,
            session_id=session_id,
        )
        
        if not llm_result.get("success"):
            # LLM 抽取失败，回退到正则抽取
            logger.warning("LLM 抽取失败，回退到正则抽取")
            extracted = extract_variables_from_text(user_input)
            if not extracted:
                return {
                    "success": True,
                    "extracted": {},
                    "message": "No variables extracted"
                }
            for key, value in extracted.items():
                set_memory_variable(
                    user_id=user_id,
                    key=key,
                    value=value,
                    session_id=session_id
                )
            return {
                "success": True,
                "extracted": extracted,
                "count": len(extracted),
                "message": f"Extracted and stored {len(extracted)} variables (regex fallback)"
            }
        
        # 汇总 LLM 抽取结果
        variables = llm_result.get("variables", [])
        facts = llm_result.get("facts", [])
        preferences = llm_result.get("preferences", [])
        plans = llm_result.get("plans", [])
        
        # 构建返回结果
        extracted_dict = {}
        for v in variables:
            extracted_dict[v.get("key", "")] = v.get("value", "")
        
        # 添加事实、偏好、计划到结果
        if facts:
            extracted_dict["_facts"] = facts
        if preferences:
            extracted_dict["_preferences"] = preferences
        if plans:
            extracted_dict["_plans"] = plans
        
        total_count = len(variables) + len(facts) + len(preferences) + len(plans)
        stored_count = llm_result.get("stored_count", 0)
        
        logger.info(f"✓ LLM 抽取完成: {total_count} 条记忆, 存储 {stored_count} 条")
        
        return {
            "success": True,
            "extracted": extracted_dict,
            "count": total_count,
            "stored_count": stored_count,
            "variables": variables,
            "facts": facts,
            "preferences": preferences,
            "plans": plans,
            "message": f"LLM extracted and stored {stored_count} memories"
        }
        
    except Exception as e:
        logger.error(f"✗ 处理用户输入失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def inject_memory_into_prompt(user_id: int,
                             prompt_template: str,
                             session_id: Optional[str] = None,
                             custom_variables: Optional[Dict[str, Any]] = None,
                             workspace_id: Optional[int] = None) -> str:
    """
    将记忆变量注入到 Prompt 模板中
    
    Args:
        user_id: 用户ID
        prompt_template: Prompt 模板（如 "你好，{user_name}！"）
        session_id: 会话ID（可选）
        custom_variables: 自定义变量（可选，会覆盖记忆变量）
        
    Returns:
        注入后的 Prompt 字符串
    """
    try:
        # 1. 获取所有记忆变量
        from app.services.memory_variable_service import list_memory_variables
        memory_vars = list_memory_variables(user_id, session_id)
        
        # 2. 如果有自定义变量，合并（自定义变量优先）
        if custom_variables:
            memory_vars.update(custom_variables)
        
        # 3. 注入到模板
        rendered = render_template(prompt_template, memory_vars)
        
        logger.debug(f"✓ 注入记忆变量到 Prompt：{rendered[:100]}...")
        
        return rendered
        
    except Exception as e:
        logger.error(f"✗ 注入记忆变量失败: {e}")
        return prompt_template  # 失败时返回原始模板


def generate_personalized_response(user_id: int,
                                      response_template: str,
                                      session_id: Optional[str] = None,
                                      context: Optional[Dict[str, Any]] = None,
                                      workspace_id: Optional[int] = None) -> str:
    """
    生成个性化回复（基于记忆变量）
    
    Args:
        user_id: 用户ID
        response_template: 回复模板（如 "你好，{user_name}！你的角色是 {user_role}。"）
        session_id: 会话ID（可选）
        context: 额外上下文（可选）
        
    Returns:
        个性化回复字符串
    """
    try:
        # 1. 获取所有记忆变量
        from app.services.memory_variable_service import list_memory_variables
        memory_vars = list_memory_variables(user_id, session_id)
        
        # 2. 合并上下文
        if context:
            memory_vars.update(context)
        
        # 3. 渲染模板
        personalized = render_template(response_template, memory_vars)
        
        logger.debug(f"✓ 生成个性化回复：{personalized[:100]}...")
        
        return personalized
        
    except Exception as e:
        logger.error(f"✗ 生成个性化回复失败: {e}")
        return response_template  # 失败时返回原始模板


def batch_extract_from_conversation(user_id: int,
                                      conversation_history: List[Dict[str, str]],
                                      session_id: Optional[str] = None,
                                      workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    从整个对话历史中批量抽取记忆变量
    
    Args:
        user_id: 用户ID
        conversation_history: 对话历史（格式：[{{"role": "user", "content": "..."}, ...]）
        session_id: 会话ID（可选）
        
    Returns:
        抽取统计信息
    """
    try:
        extracted_count = 0
        all_extracted = {}
        
        # 遍历对话历史
        for message in conversation_history:
            if message.get("role") == "user":
                content = message.get("content", "")
                
                # 抽取变量
                extracted = extract_variables_from_text(content)
                
                if extracted:
                    # 存储到 Redis
                    for key, value in extracted.items():
                        set_memory_variable(
                            user_id=user_id,
                            key=key,
                            value=value,
                            session_id=session_id
                        )
                        extracted_count += 1
                        all_extracted[key] = value
        
        logger.info(f"✓ 从对话历史中批量抽取了 {extracted_count} 个变量")
        
        return {
            "success": True,
            "extracted_count": extracted_count,
            "all_extracted": all_extracted,
            "message": f"Batch extracted {extracted_count} variables from conversation history"
        }
        
    except Exception as e:
        logger.error(f"✗ 批量抽取失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def get_user_context_for_llm(user_id: int,
                             session_id: Optional[str] = None,
                             workspace_id: Optional[int] = None) -> str:
    """
    获取用户上下文（用于注入到 LLM Prompt）
    
    Args:
        user_id: 用户ID
        session_id: 会话ID（可选）
        
    Returns:
        格式化的上下文字符串（用于注入到 LLM Prompt）
    """
    try:
        # 获取所有记忆变量
        from app.services.memory_variable_service import list_memory_variables
        memory_vars = list_memory_variables(user_id, session_id)
        
        if not memory_vars:
            return ""
        
        # 格式化为上下文字符串
        context_parts = []
        for key, value in memory_vars.items():
            if isinstance(value, (dict, list)):
                value_str = json.dumps(value, ensure_ascii=False)
            else:
                value_str = str(value)
            context_parts.append(f"{key}: {value_str}")
        
        context_str = "用户记忆信息：\n" + "\n".join(context_parts)
        
        logger.debug(f"✓ 获取用户上下文：{context_str[:100]}...")
        
        return context_str
        
    except Exception as e:
        logger.error(f"✗ 获取用户上下文失败: {e}")
        return ""


# 测试函数
def test_memory_extraction_service():
    """测试记忆变量抽取与注入服务"""
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
    assert "XinHai" in context_str or "鑫海" in context_str
    print(f"   ✓ 获取用户上下文成功")
    
    # 清理测试数据
    print(f"\n6. 清理测试数据...")
    from app.services.memory_variable_service import clear_memory_variables
    clear_memory_variables(user_id, session_id)
    print(f"   ✓ 清理完成")
    
    print("\n" + "="*60)
    print("✅ 记忆变量抽取与注入服务测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    # 运行测试
    test_memory_extraction_service()
