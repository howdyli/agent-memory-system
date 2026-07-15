"""
Agent 循环编排服务

实现记忆感知的 Agent 对话循环：
  用户输入 → 记忆召回 → 注入上下文 → LLM 推理（含 Tool Calling）→ 记忆抽取 → 响应
"""
import logging
import json
import uuid
from typing import Optional, Dict, Any, List, Generator

logger = logging.getLogger(__name__)

from app.services.agent_memory_sdk import AgentMemoryClient
from app.services.llm_backend_service import llm_chat, llm_chat_stream
from app.services.llm_extraction_service import llm_extract_memories
from app.services.context_compressor import ContextCompressor
from app.services import graph_memory_service as gm

# ============================================================
# Tool Schema 定义（OpenAI Function Calling 格式）
# ============================================================

MEMORY_TOOLS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "memory_recall",
            "description": "召回与查询相关的历史记忆信息，返回格式化的记忆上下文",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要回忆/搜索的内容描述",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回记忆条数，默认 5",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_remember",
            "description": "记住一条新的信息，供未来对话使用",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "记忆的名称/键",
                    },
                    "value": {
                        "type": "string",
                        "description": "记忆的内容/值",
                    },
                },
                "required": ["key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_forget",
            "description": "删除一条已存储的记忆变量",
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "要删除的记忆名称/键",
                    },
                },
                "required": ["key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "语义搜索记忆片段，返回匹配的记忆列表",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询",
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "返回条数，默认 5",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get_context",
            "description": "获取当前用户的完整记忆上下文",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_create_table",
            "description": "创建一个结构化记忆表，用于存储多条同类信息（如联系人、任务清单、项目信息、会议记录等），创建后可用 memory_add_record 添加数据",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "表名，如 contacts、tasks、projects、meetings",
                    },
                    "fields": {
                        "type": "array",
                        "description": "字段定义列表，每个字段包含 name（字段名）和 type（类型：TEXT/INTEGER/REAL/BOOLEAN/DATE/DATETIME/JSON）",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "字段名"},
                                "type": {"type": "string", "description": "字段类型：TEXT/INTEGER/REAL/BOOLEAN/DATE/DATETIME/JSON"},
                                "index": {"type": "boolean", "description": "是否创建索引（可选）"},
                                "nullable": {"type": "boolean", "description": "是否允许为空（可选）"},
                            },
                            "required": ["name", "type"],
                        },
                    },
                },
                "required": ["table_name", "fields"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_add_record",
            "description": "向已存在的记忆表中添加一条结构化记录",
            "parameters": {
                "type": "object",
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "目标表名",
                    },
                    "record": {
                        "type": "object",
                        "description": "记录数据，key 为字段名，value 为字段值",
                        "additionalProperties": {},
                    },
                },
                "required": ["table_name", "record"],
            },
        },
    },
    # ============================================================
    # 知识图谱工具
    # ============================================================
    {
        "type": "function",
        "function": {
            "name": "graph_add_entity",
            "description": "在知识图谱中创建或更新一个实体（人物、组织、地点、事件）。当用户提到重要的人、公司、地点时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "实体名称，如 '张三'、'腾讯'、'北京'",
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["person", "organization", "location", "event"],
                        "description": "实体类型：person(人物), organization(组织/公司), location(地点), event(事件)",
                    },
                    "aliases": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "实体的别名列表（可选），如 ['三哥', '老张']",
                    },
                    "metadata": {
                        "type": "object",
                        "description": "附加元数据（可选），如 {'role': '产品经理', 'company': '腾讯'}",
                    },
                },
                "required": ["name", "entity_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_add_relationship",
            "description": "在知识图谱中创建两个实体之间的关系。当用户描述人与人、人与公司等关系时使用。支持的关系类型：colleague(同事), friend(朋友), superior(上级), subordinate(下属), project_member(项目成员), family(家人), classmate(同学), mentor(导师)。",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_name": {
                        "type": "string",
                        "description": "源实体名称，如 '张三'",
                    },
                    "target_name": {
                        "type": "string",
                        "description": "目标实体名称，如 '李四' 或 '腾讯'",
                    },
                    "relation_type": {
                        "type": "string",
                        "description": "关系类型：colleague, friend, superior, subordinate, project_member, family, classmate, mentor",
                    },
                    "source_type": {
                        "type": "string",
                        "enum": ["person", "organization", "location", "event"],
                        "description": "源实体类型，默认 person",
                    },
                    "target_type": {
                        "type": "string",
                        "enum": ["person", "organization", "location", "event"],
                        "description": "目标实体类型，默认 organization",
                    },
                    "confidence": {
                        "type": "number",
                        "description": "关系置信度 0-1，默认 0.8",
                    },
                },
                "required": ["source_name", "target_name", "relation_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_search_entities",
            "description": "按名称模糊搜索知识图谱中的实体。当用户问'有没有叫XX的人'或'XX公司'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如 '张三' 或 '腾讯'",
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["person", "organization", "location", "event"],
                        "description": "按类型过滤（可选）",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "返回数量，默认 10",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_query_neighbors",
            "description": "查询知识图谱中某个实体的关联邻居（关系网络）。当用户问'张三认识谁'、'张三的同事有哪些'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {
                        "type": "string",
                        "description": "要查询的实体名称，如 '张三'",
                    },
                    "entity_type": {
                        "type": "string",
                        "enum": ["person", "organization", "location", "event"],
                        "description": "实体类型，默认 person",
                    },
                    "relation_type": {
                        "type": "string",
                        "description": "按关系类型过滤（可选），如 colleague, friend",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "遍历深度，1=直接关系，2=二度关系，默认 1",
                    },
                },
                "required": ["entity_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_analyze",
            "description": "对知识图谱进行自然语言查询和关系分析。支持查询关系历史、关系网络、实体搜索等。当用户问'张三和李四什么关系'、'张三的经历'、'张三的关系网'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然语言查询，如 '张三的同事'、'张三和李四的关系'、'关于腾讯的关系网'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "graph_extract_from_text",
            "description": "从文本中自动抽取实体（人物、组织、地点）和关系，并存入知识图谱。当用户提供一段描述性文本并希望自动构建知识图谱时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要分析的文本内容",
                    },
                },
                "required": ["text"],
            },
        },
    },
]


# ============================================================
# Tool Handler
# ============================================================

def _handle_tool_call(sdk: AgentMemoryClient, tool_name: str, arguments: Dict[str, Any]) -> str:
    """
    执行单个工具调用，返回结果字符串。

    Args:
        sdk: AgentMemoryClient 实例
        tool_name: 工具名称
        arguments: 工具参数

    Returns:
        工具执行结果（JSON 字符串）
    """
    try:
        if tool_name == "memory_recall":
            result = sdk.recall(
                query=arguments.get("query", ""),
                top_k=arguments.get("top_k", 5),
            )
            return json.dumps({"success": True, "context": result}, ensure_ascii=False)

        elif tool_name == "memory_remember":
            ok = sdk.remember(
                key=arguments.get("key", ""),
                value=arguments.get("value", ""),
            )
            return json.dumps({"success": ok, "key": arguments.get("key", "")}, ensure_ascii=False)

        elif tool_name == "memory_forget":
            ok = sdk.forget(key=arguments.get("key", ""))
            return json.dumps({"success": ok, "key": arguments.get("key", "")}, ensure_ascii=False)

        elif tool_name == "memory_search":
            results = sdk.search(
                query=arguments.get("query", ""),
                top_k=arguments.get("top_k", 5),
            )
            return json.dumps({"success": True, "memories": results, "count": len(results)}, ensure_ascii=False)

        elif tool_name == "memory_get_context":
            ctx = sdk.get_context()
            return json.dumps({"success": True, "context": ctx}, ensure_ascii=False)

        elif tool_name == "memory_create_table":
            result = sdk.create_table(
                table_name=arguments.get("table_name", ""),
                fields=arguments.get("fields", []),
            )
            return json.dumps(result, ensure_ascii=False)

        elif tool_name == "memory_add_record":
            result = sdk.remember_structured(
                table_name=arguments.get("table_name", ""),
                record=arguments.get("record", {}),
            )
            return json.dumps(result, ensure_ascii=False)

        # ----------------------------------------------------------
        # 知识图谱工具
        # ----------------------------------------------------------
        elif tool_name == "graph_add_entity":
            result = gm.ensure_entity(
                user_id=sdk.user_id,
                name=arguments.get("name", ""),
                entity_type=arguments.get("entity_type", "person"),
                aliases=arguments.get("aliases"),
                metadata=arguments.get("metadata"),
            )
            return json.dumps(result, ensure_ascii=False, default=str)

        elif tool_name == "graph_add_relationship":
            result = gm.add_relationship(
                user_id=sdk.user_id,
                source_name=arguments.get("source_name", ""),
                target_name=arguments.get("target_name", ""),
                relation_type=arguments.get("relation_type", "colleague"),
                source_type=arguments.get("source_type", "person"),
                target_type=arguments.get("target_type", "organization"),
                confidence=arguments.get("confidence", 0.8),
                extraction_source="agent_tool",
            )
            return json.dumps(result, ensure_ascii=False, default=str)

        elif tool_name == "graph_search_entities":
            result = gm.search_entities(
                user_id=sdk.user_id,
                query=arguments.get("query", ""),
                entity_type=arguments.get("entity_type"),
                limit=arguments.get("limit", 10),
            )
            return json.dumps(result, ensure_ascii=False, default=str)

        elif tool_name == "graph_query_neighbors":
            result = gm.get_neighbors(
                user_id=sdk.user_id,
                entity_name=arguments.get("entity_name", ""),
                entity_type=arguments.get("entity_type", "person"),
                relation_type=arguments.get("relation_type"),
                depth=arguments.get("depth", 1),
            )
            return json.dumps(result, ensure_ascii=False, default=str)

        elif tool_name == "graph_analyze":
            result = gm.query_graph(
                user_id=sdk.user_id,
                query=arguments.get("query", ""),
            )
            return json.dumps(result, ensure_ascii=False, default=str)

        elif tool_name == "graph_extract_from_text":
            result = gm.extract_entities_from_text(
                user_id=sdk.user_id,
                text=arguments.get("text", ""),
            )
            return json.dumps(result, ensure_ascii=False, default=str)

        else:
            return json.dumps({"success": False, "error": f"未知工具: {tool_name}"}, ensure_ascii=False)

    except Exception as e:
        logger.error(f"工具执行失败 [{tool_name}]: {e}")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


# ============================================================
# Agent Loop 核心编排
# ============================================================

DEFAULT_SYSTEM_PROMPT = """你是一个拥有长期记忆能力和知识图谱的智能助手。

你可以通过工具调用来管理记忆和知识图谱：

【记忆管理工具】
- memory_recall: 回忆与某个话题相关的历史记忆
- memory_remember: 记住一条新信息
- memory_forget: 删除一条记忆
- memory_search: 语义搜索记忆片段
- memory_get_context: 获取完整的记忆上下文
- memory_create_table: 创建结构化记忆表（如联系人表、任务清单、项目表等）
- memory_add_record: 向已存在的记忆表添加一条记录

【知识图谱工具】
- graph_add_entity: 在知识图谱中创建实体（人物/组织/地点/事件）
- graph_add_relationship: 创建两个实体之间的关系（同事/朋友/上下级等）
- graph_search_entities: 按名称搜索图谱中的实体
- graph_query_neighbors: 查询某实体的关联邻居（关系网络）
- graph_analyze: 自然语言图查询（关系历史、关系网分析等）
- graph_extract_from_text: 从文本中自动抽取实体和关系存入图谱

请充分利用记忆工具和知识图谱来提供个性化的回答。如果用户提到了之前聊过的内容，请使用 memory_recall 工具回忆相关信息。如果用户提到了人物、公司、组织之间的关系，请使用知识图谱工具来构建和分析关系网络。如果用户希望用表格形式整理数据，请使用 memory_create_table 创建表，再用 memory_add_record 添加数据。
"""


# 全局 Context Compressor 实例
_compressor = None


def _get_compressor() -> ContextCompressor:
    """获取 Context Compressor 单例"""
    global _compressor
    if _compressor is None:
        _compressor = ContextCompressor()
    return _compressor


def memory_aware_chat(
    user_id: int,
    user_message: str,
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
    max_tool_rounds: int = 5,
) -> Dict[str, Any]:
    """
    记忆感知的 Agent 对话循环。

    流程：
    1. 自动召回相关记忆 → 注入 system prompt
    2. 调用 LLM（带 tool 定义）
    3. 如果 LLM 返回 tool_calls → 执行工具 → 结果追加到 messages → 回到步骤 2
    4. 如果 LLM 返回文本 → 从对话中抽取新记忆 → 返回响应

    Args:
        user_id: 用户 ID
        user_message: 用户消息
        system_prompt: 自定义 system prompt（可选）
        session_id: 会话 ID（可选）
        max_tool_rounds: 最大工具调用轮数（防止无限循环）

    Returns:
        对话结果：
        {
            "success": True,
            "response": "助手回复文本",
            "tool_calls": [...],
            "memories_extracted": N,
            "memory_context_used": True/False
        }
    """
    sdk = AgentMemoryClient(user_id)
    all_tool_calls: List[Dict[str, Any]] = []
    memory_context_used = False

    # ----------------------------------------------------------
    # 步骤 1：Context Compression + 分层记忆注入
    # ----------------------------------------------------------
    base_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT

    # 注入当前日期时间，使 LLM 具备时间感知能力
    from datetime import datetime
    now = datetime.now()
    weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    weekday_name = weekdays[now.weekday()]
    date_header = f"当前时间：{now.year}年{now.month}月{now.day}日 {weekday_name} {now.hour}:{now.minute:02d}\n\n"
    base_prompt = date_header + base_prompt

    # 生成会话 ID（如果未提供）
    _is_new_session = not session_id
    if not session_id:
        session_id = f"session_{user_id}_{uuid.uuid4().hex[:8]}"

    # 会话元数据管理
    compressor = _get_compressor()
    try:
        if _is_new_session:
            compressor.conversation_mgr.create_session(session_id, user_id, user_message[:20])
        else:
            compressor.conversation_mgr.touch_session(session_id)
    except Exception:
        pass  # 不影响主流程

    # 使用 ContextCompressor 构建压缩上下文
    compressed_context = compressor.build_context(
        user_id=user_id,
        session_id=session_id,
        user_query=user_message,
    )

    if compressed_context:
        memory_context_used = True
        augmented_prompt = (
            f"{base_prompt}\n\n"
            f"---\n"
            f"以下是与该用户相关的历史记忆和对话记录，请在回答时参考：\n\n"
            f"{compressed_context}\n"
            f"---"
        )
    else:
        augmented_prompt = base_prompt

    # ----------------------------------------------------------
    # 构建初始 messages
    # ----------------------------------------------------------
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": augmented_prompt},
        {"role": "user", "content": user_message},
    ]

    # ----------------------------------------------------------
    # 步骤 2-3：LLM 推理 + Tool Calling 循环
    # ----------------------------------------------------------
    final_response = ""

    for round_idx in range(max_tool_rounds + 1):
        # 调用 LLM
        llm_result = llm_chat(
            user_id=user_id,
            messages=messages,
            tools=MEMORY_TOOLS,
        )

        if not llm_result.get("success"):
            logger.error(f"LLM 调用失败: {llm_result.get('error')}")
            final_response = f"抱歉，AI 服务暂时不可用：{llm_result.get('error', '未知错误')}"
            break

        # 检查是否有 tool_calls
        tool_calls = llm_result.get("tool_calls")

        if not tool_calls:
            # 没有 tool_calls → LLM 返回了最终文本
            final_response = llm_result.get("content", "")
            # 检测截断
            if llm_result.get("finish_reason") == "length":
                final_response += "\n\n[提示：响应内容过长，可能被截断]"
            break

        # 有 tool_calls → 执行工具
        # 先将 assistant 的 tool_calls 消息追加
        assistant_msg = {
            "role": "assistant",
            "content": llm_result.get("content", "") or "",
            "tool_calls": tool_calls,
        }
        messages.append(assistant_msg)

        for tc in tool_calls:
            func_name = tc.get("function", {}).get("name", "")
            func_args_str = tc.get("function", {}).get("arguments", "{}")
            tool_call_id = tc.get("id", "")

            try:
                func_args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
            except json.JSONDecodeError:
                func_args = {}

            # 执行工具
            tool_result = _handle_tool_call(sdk, func_name, func_args)

            all_tool_calls.append({
                "tool": func_name,
                "arguments": func_args,
                "result": tool_result,
            })

            # 将工具结果追加到 messages
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": tool_result,
            })

    else:
        # 超过最大轮数
        if not final_response:
            final_response = llm_result.get("content", "抱歉，工具调用轮数已达上限。")

    # ----------------------------------------------------------
    # 步骤 4：从对话中抽取新记忆（异步/后台）
    # ----------------------------------------------------------
    memories_extracted = 0
    try:
        conversation_for_extraction = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": final_response},
        ]
        extract_result = llm_extract_memories(
            user_id=user_id,
            conversation=conversation_for_extraction,
            auto_store=True,
        )
        memories_extracted = extract_result.get("stored_count", 0)
    except Exception as e:
        logger.warning(f"对话后记忆抽取失败（不影响响应）: {e}")

    # ----------------------------------------------------------
    # 步骤 5：存储对话轮次到历史记录
    # ----------------------------------------------------------
    try:
        compressor.store_turn(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
            assistant_response=final_response,
            tool_calls=all_tool_calls if all_tool_calls else None,
        )
    except Exception as e:
        logger.warning(f"存储对话历史失败（不影响响应）: {e}")

    return {
        "success": True,
        "response": final_response,
        "tool_calls": all_tool_calls,
        "memories_extracted": memories_extracted,
        "memory_context_used": memory_context_used,
        "session_id": session_id,
    }


# ============================================================
# 流式 Agent Loop
# ============================================================

def memory_aware_chat_stream(
    user_id: int,
    user_message: str,
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
    max_tool_rounds: int = 5,
) -> Generator[Dict[str, Any], None, None]:
    """
    记忆感知的流式 Agent 对话循环。

    与 memory_aware_chat 流程一致，但以 generator 方式逐步 yield 事件，
    最终文本响应使用 LLM 真流式输出。

    Yields:
        事件字典，类型包括：
        - phase: 阶段变更
        - memory_context: 记忆上下文详情
        - tool_call: 工具调用记录
        - token: 文本 token
        - memory: 记忆抽取结果
        - done: 完成信号
        - error: 错误信息
    """
    sdk = AgentMemoryClient(user_id)
    all_tool_calls: List[Dict[str, Any]] = []
    memory_context_used = False

    # ----------------------------------------------------------
    # 步骤 1：Context Compression + 分层记忆注入
    # ----------------------------------------------------------
    yield {"type": "phase", "phase": "memory_recall", "status": "start"}

    base_prompt = system_prompt or DEFAULT_SYSTEM_PROMPT
    from datetime import datetime
    now = datetime.now()
    weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    weekday_name = weekdays[now.weekday()]
    date_header = f"当前时间：{now.year}年{now.month}月{now.day}日 {weekday_name} {now.hour}:{now.minute:02d}\n\n"
    base_prompt = date_header + base_prompt

    _is_new_session = not session_id
    if not session_id:
        session_id = f"session_{user_id}_{uuid.uuid4().hex[:8]}"

    # 会话元数据管理
    compressor = _get_compressor()
    try:
        if _is_new_session:
            compressor.conversation_mgr.create_session(session_id, user_id, user_message[:20])
        else:
            compressor.conversation_mgr.touch_session(session_id)
    except Exception:
        pass

    # 使用 build_context_with_details 获取记忆详情
    context_result = compressor.build_context_with_details(
        user_id=user_id,
        session_id=session_id,
        user_query=user_message,
    )
    compressed_context = context_result.get("context_text", "")
    memories_used = context_result.get("memories_used", [])

    if compressed_context:
        memory_context_used = True
        augmented_prompt = (
            f"{base_prompt}\n\n"
            f"---\n"
            f"以下是与该用户相关的历史记忆和对话记录，请在回答时参考：\n\n"
            f"{compressed_context}\n"
            f"---"
        )
    else:
        augmented_prompt = base_prompt

    yield {"type": "phase", "phase": "memory_recall", "status": "done", "context_used": memory_context_used}

    # 推送记忆上下文详情
    if memories_used:
        yield {"type": "memory_context", "memories": memories_used}

    # ----------------------------------------------------------
    # 构建初始 messages
    # ----------------------------------------------------------
    messages: List[Dict[str, str]] = [
        {"role": "system", "content": augmented_prompt},
        {"role": "user", "content": user_message},
    ]

    # ----------------------------------------------------------
    # 步骤 2-3：LLM 推理 + Tool Calling 循环
    # ----------------------------------------------------------
    yield {"type": "phase", "phase": "llm_thinking", "status": "start"}
    final_response = ""
    _has_non_stream_result = False

    for round_idx in range(max_tool_rounds + 1):
        # 工具调用轮次使用非流式（需要完整 tool_calls 响应）
        llm_result = llm_chat(
            user_id=user_id,
            messages=messages,
            tools=MEMORY_TOOLS,
        )

        if not llm_result.get("success"):
            error_msg = llm_result.get('error', '未知错误')
            yield {"type": "error", "message": f"AI 服务暂时不可用：{error_msg}"}
            return

        tool_calls = llm_result.get("tool_calls")

        if not tool_calls:
            # LLM 返回了最终文本，保存非流式结果供后续分块输出
            final_response = llm_result.get("content", "")
            if llm_result.get("finish_reason") == "length":
                final_response += "\n\n[提示：响应内容过长，可能被截断]"
            _has_non_stream_result = True
            break

        # 有 tool_calls → 执行工具
        assistant_msg = {
            "role": "assistant",
            "content": llm_result.get("content", "") or "",
            "tool_calls": tool_calls,
        }
        messages.append(assistant_msg)

        for tc in tool_calls:
            func_name = tc.get("function", {}).get("name", "")
            func_args_str = tc.get("function", {}).get("arguments", "{}")
            tool_call_id = tc.get("id", "")

            try:
                func_args = json.loads(func_args_str) if isinstance(func_args_str, str) else func_args_str
            except json.JSONDecodeError:
                func_args = {}

            tool_result = _handle_tool_call(sdk, func_name, func_args)

            all_tool_calls.append({
                "tool": func_name,
                "arguments": func_args,
                "result": tool_result,
            })

            # 实时推送工具调用事件
            yield {"type": "tool_call", "tool": func_name, "arguments": func_args, "result": tool_result}

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": tool_result,
            })
    else:
        # 超过最大轮数
        content = llm_result.get("content", "抱歉，工具调用轮数已达上限。")
        # 按 4 字符分块 yield
        chunk_size = 4
        for i in range(0, len(content), chunk_size):
            yield {"type": "token", "content": content[i:i + chunk_size]}
        final_response = content

    # ----------------------------------------------------------
    # 最终文本响应输出
    # ----------------------------------------------------------
    if not final_response and not _has_non_stream_result:
        # 没有非流式结果（工具循环结束后未得到文本），用流式接口请求
        yield {"type": "phase", "phase": "streaming", "status": "start"}

        for event in llm_chat_stream(
            user_id=user_id,
            messages=messages,
            tools=MEMORY_TOOLS,
        ):
            event_type = event.get("type")

            if event_type == "content_delta":
                content = event.get("content", "")
                final_response += content
                yield {"type": "token", "content": content}

            elif event_type == "finish":
                if event.get("finish_reason") == "length":
                    truncation_notice = "\n\n[提示：响应内容过长，可能被截断]"
                    final_response += truncation_notice
                    yield {"type": "token", "content": truncation_notice}

            elif event_type == "error":
                yield {"type": "error", "message": event.get("error", "流式输出错误")}
                return

    elif _has_non_stream_result and final_response:
        # 已有非流式结果，分块 yield
        yield {"type": "phase", "phase": "streaming", "status": "start"}
        chunk_size = 4
        for i in range(0, len(final_response), chunk_size):
            yield {"type": "token", "content": final_response[i:i + chunk_size]}

    # ----------------------------------------------------------
    # 步骤 4：从对话中抽取新记忆
    # ----------------------------------------------------------
    memories_extracted = 0
    extraction_details: List[Dict[str, str]] = []
    try:
        conversation_for_extraction = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": final_response},
        ]
        extract_result = llm_extract_memories(
            user_id=user_id,
            conversation=conversation_for_extraction,
            auto_store=True,
        )
        memories_extracted = extract_result.get("stored_count", 0)
        # 抽取详情：从 variables/facts/preferences/plans 构建
        for var in extract_result.get("variables", []):
            key = var.get("key", "") if isinstance(var, dict) else str(var)
            value = var.get("value", "") if isinstance(var, dict) else str(var)
            if key and value:
                extraction_details.append({"content": f"{key}: {value}", "type": "info"})
        for fact in extract_result.get("facts", []):
            if fact:
                extraction_details.append({"content": str(fact)[:200], "type": "info"})
        for pref in extract_result.get("preferences", []):
            if pref:
                extraction_details.append({"content": str(pref)[:200], "type": "preference"})
        for plan in extract_result.get("plans", []):
            if plan:
                extraction_details.append({"content": str(plan)[:200], "type": "plan"})
    except Exception as e:
        logger.warning(f"对话后记忆抽取失败（不影响响应）: {e}")

    yield {
        "type": "memory",
        "extracted": memories_extracted,
        "context_used": memory_context_used,
        "details": extraction_details,
    }

    # ----------------------------------------------------------
    # 步骤 5：存储对话轮次到历史记录
    # ----------------------------------------------------------
    try:
        compressor.store_turn(
            session_id=session_id,
            user_id=user_id,
            user_message=user_message,
            assistant_response=final_response,
            tool_calls=all_tool_calls if all_tool_calls else None,
        )
    except Exception as e:
        logger.warning(f"存储对话历史失败（不影响响应）: {e}")

    yield {"type": "done", "session_id": session_id}
