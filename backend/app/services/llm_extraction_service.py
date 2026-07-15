"""
LLM 驱动的记忆抽取服务

用 LLM 替代正则匹配，从对话中智能抽取需要长期记忆的信息，
并自动存储到对应的记忆层（KV 变量 / 语义片段）。
"""
import logging
import json
import re
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

from app.services.llm_backend_service import llm_chat
from app.services.memory_variable_service import set_memory_variable
from app.services.memory_fragment_service import create_fragment, search_fragments_by_semantic

# ============================================================
# 抽取 Prompt
# ============================================================

EXTRACTION_SYSTEM_PROMPT = """你是一个记忆抽取引擎。你的任务是从对话中识别需要长期记忆的信息。

请从对话中抽取以下类型的信息：
1. **variables**: 用户的基本信息、偏好设置等 KV 型数据（如姓名、角色、项目名、技术栈偏好等）
2. **facts**: 重要的事实性信息（如"用户在做一个叫源启的项目"）
3. **preferences**: 用户的偏好和习惯（如"用户喜欢用 React"、"偏好简洁的代码风格"）
4. **plans**: 用户的计划和目标（如"下周要完成原型设计"）

规则：
- 只抽取有**长期价值**的信息，忽略临时性内容（如"今天天气不错"、"帮我写个函数"）
- 如果对话中没有值得记忆的信息，返回空对象
- 每个字段要简洁、准确
- 必须返回合法的 JSON，不要包含其他文字

返回格式：
```json
{
  "variables": [
    {"key": "user_name", "value": "鑫海"},
    {"key": "user_role", "value": "产品经理"}
  ],
  "facts": [
    "用户正在开发一个叫源启·智能体工厂的项目",
    "用户负责 Agent 记忆系统的架构设计"
  ],
  "preferences": [
    "用户偏好使用 Python 和 FastAPI"
  ],
  "plans": [
    "下周完成原型设计"
  ]
}
```"""


def get_default_extraction_prompt() -> str:
    """返回默认的抽取系统 Prompt。"""
    return EXTRACTION_SYSTEM_PROMPT


def get_active_extraction_prompt(user_id: int) -> str:
    """
    获取用户当前生效的抽取 Prompt。

    优先返回用户自定义的 active 模板，否则返回默认 Prompt。
    """
    try:
        from app.core.db_client import get_db_client
        db = get_db_client()
        rows = db.execute(
            "SELECT content FROM extraction_prompt_templates WHERE user_id = ? AND is_active = 1 ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        )
        if rows:
            return rows[0]["content"]
    except Exception as e:
        logger.warning(f"获取自定义抽取 Prompt 失败，使用默认: {e}")
    return EXTRACTION_SYSTEM_PROMPT


def list_extraction_templates(user_id: int) -> List[Dict[str, Any]]:
    """列出用户的所有抽取 Prompt 模板。"""
    try:
        from app.core.db_client import get_db_client
        db = get_db_client()
        rows = db.execute(
            "SELECT name, content, is_active, created_at, updated_at FROM extraction_prompt_templates WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,),
        )
        templates = []
        if rows:
            for r in rows:
                templates.append({
                    "name": r["name"],
                    "content": r["content"],
                    "is_active": bool(r["is_active"]),
                    "is_default": False,
                    "created_at": str(r["created_at"]),
                    "updated_at": str(r["updated_at"]),
                })
        # 始终附带默认模板
        templates.append({
            "name": "default",
            "content": EXTRACTION_SYSTEM_PROMPT,
            "is_active": not any(t["is_active"] for t in templates),
            "is_default": True,
            "created_at": None,
            "updated_at": None,
        })
        return templates
    except Exception as e:
        logger.error(f"列出抽取模板失败: {e}")
        return [{
            "name": "default",
            "content": EXTRACTION_SYSTEM_PROMPT,
            "is_active": True,
            "is_default": True,
            "created_at": None,
            "updated_at": None,
        }]


def upsert_extraction_template(user_id: int, name: str, content: str, set_active: bool = True) -> Dict[str, Any]:
    """
    创建或更新抽取 Prompt 模板。

    Args:
        user_id: 用户 ID
        name: 模板名称
        content: 模板内容
        set_active: 是否同时设为当前生效模板

    Returns:
        操作结果
    """
    try:
        if not name or not name.strip():
            return {"success": False, "error": "模板名称不能为空"}
        name = name.strip()
        if name == "default":
            return {"success": False, "error": "不能修改默认模板"}

        from app.core.db_client import get_db_client
        db = get_db_client()
        db.execute(
            """INSERT INTO extraction_prompt_templates (user_id, name, content, is_active)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, name)
               DO UPDATE SET content = excluded.content, updated_at = CURRENT_TIMESTAMP""",
            (user_id, name, content, 1 if set_active else 0),
        )
        if set_active:
            # 取消其他模板的 active 状态
            db.execute(
                "UPDATE extraction_prompt_templates SET is_active = 0 WHERE user_id = ? AND name != ?",
                (user_id, name),
            )
        logger.info(f"✓ 保存抽取模板 '{name}' (active={set_active})")
        return {"success": True, "name": name, "active": set_active}
    except Exception as e:
        logger.error(f"保存抽取模板失败: {e}")
        return {"success": False, "error": str(e)}


def reset_extraction_template(user_id: int) -> Dict[str, Any]:
    """重置为默认模板（取消所有自定义模板的 active 状态）。"""
    try:
        from app.core.db_client import get_db_client
        db = get_db_client()
        db.execute(
            "UPDATE extraction_prompt_templates SET is_active = 0 WHERE user_id = ?",
            (user_id,),
        )
        return {"success": True, "message": "已恢复默认模板"}
    except Exception as e:
        logger.error(f"重置抽取模板失败: {e}")
        return {"success": False, "error": str(e)}


def _build_extraction_messages(
    conversation: List[Dict[str, str]],
    system_prompt: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    构建抽取请求的 messages 列表。

    Args:
        conversation: 对话历史 [{"role": "user/assistant", "content": "..."}]
        system_prompt: 自定义系统 Prompt（可选），默认使用 EXTRACTION_SYSTEM_PROMPT

    Returns:
        用于 LLM 抽取的 messages
    """
    # 拼接对话文本
    dialogue_parts = []
    for msg in conversation:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if role == "user":
            dialogue_parts.append(f"用户: {content}")
        elif role == "assistant":
            dialogue_parts.append(f"助手: {content}")

    dialogue_text = "\n".join(dialogue_parts)

    return [
        {"role": "system", "content": system_prompt or EXTRACTION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": f"请从以下对话中抽取需要长期记忆的信息：\n\n{dialogue_text}",
        },
    ]


def _parse_llm_response(response_text: str) -> Dict[str, Any]:
    """
    解析 LLM 返回的 JSON 抽取结果。

    支持处理 markdown 代码块包裹的 JSON。

    Args:
        response_text: LLM 返回的文本

    Returns:
        解析后的字典，解析失败返回空字典
    """
    if not response_text:
        return {}

    text = response_text.strip()

    # 尝试提取 markdown 代码块中的 JSON
    code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if code_block_match:
        text = code_block_match.group(1).strip()

    # 尝试直接提取 JSON 对象
    json_match = re.search(r"\{.*\}", text, re.DOTALL)
    if json_match:
        text = json_match.group(0)

    try:
        # 清理控制字符
        text = ''.join(c if c.isprintable() or c in '\n\r\t' else ' ' for c in text)
        result = json.loads(text, strict=False)
        if isinstance(result, dict):
            return result
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"LLM 抽取结果解析失败: {response_text[:200]}")

    return {}


def _store_with_dedup(
    user_id: int,
    fragment_type: str,
    content: str,
    importance_score: float = 0.5,
    dedup_threshold: float = 0.85,
) -> Dict[str, Any]:
    """
    存储记忆片段前进行去重检查。

    1. 调用 search_fragments_by_semantic() 查找相似记忆
    2. 用 _text_similarity() (bigram Jaccard) 补充精确比较
    3. 取语义相似度和文本相似度的最大值 combined_sim
    4. combined_sim >= dedup_threshold → 跳过创建
    5. < dedup_threshold → 正常 create_fragment()
    6. 异常时 fallback 到直接创建（不影响主流程）
    """
    try:
        from app.services.memory_lifecycle_service import _text_similarity

        semantic_result = search_fragments_by_semantic(
            user_id=user_id,
            query=content,
            top_k=5,
            threshold=0.3,
        )

        if semantic_result.get("success"):
            fragments = semantic_result.get("fragments", [])
            for frag in fragments:
                existing_content = frag.get("content", "")
                semantic_sim = frag.get("similarity", 0)
                text_sim = _text_similarity(content, existing_content)
                combined_sim = max(semantic_sim, text_sim)

                if combined_sim >= dedup_threshold:
                    logger.info(f"↩ 跳过重复记忆 (sim={combined_sim:.2f}): {content[:50]}...")
                    return {
                        "success": True,
                        "skipped": True,
                        "reason": "duplicate",
                        "similarity": round(combined_sim, 4),
                    }
    except Exception as e:
        logger.warning(f"去重检查异常，fallback 到直接创建: {e}")

    result = create_fragment(
        user_id=user_id,
        fragment_type=fragment_type,
        content=content,
        importance_score=importance_score,
    )
    return result


def _heuristic_extract(conversation: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    离线/Mock 模式的记忆抽取兜底。

    当 LLM 后端未配置（Mock 模式）时，llm_chat 返回的是非 JSON 的模拟文本，
    无法被 _parse_llm_response 解析，导致记忆管线完全失效（stored_count=0）。

    本函数用轻量正则从用户发言中抽取少量高价值记忆（姓名、负责事项、偏好等），
    保证「无 API Key」环境下「Agent 记忆」功能仍可端到端演示。
    仅在 Mock 模式下触发，不影响真实 LLM 的抽取结果。
    """
    text = " ".join(m.get("content", "") for m in conversation if m.get("role") == "user")
    variables, facts, preferences, plans = [], [], [], []

    # 姓名：我叫X / 我的名字是X / 我的名字叫X / 我是X（短词）
    for pat in [r"我叫([一-龥A-Za-z]{1,8})",
                r"我的名字叫?是?([一-龥A-Za-z]{1,8})"]:
        m = re.search(pat, text)
        if m:
            name = m.group(1).strip("，。, ")
            if 1 <= len(name) <= 6 and name not in ("做", "在", "负", "一", "了"):
                variables.append({"key": "user_name", "value": name})
                break

    # 负责事项 → 事实片段
    m = re.search(r"负责([一-龥A-Za-z0-9·]{2,30}?)(?:[，。,\s]|$)", text)
    if m:
        facts.append(f"用户负责{m.group(1).strip('，。, ')}")

    # 提及重点项目 → 事实片段
    for kw in ["源启", "智能体工厂", "GienWork", "代理记忆系统", "Agent"]:
        if kw in text:
            facts.append(f"用户提及{kw}相关项目/系统")

    # 偏好：喜欢/偏好/习惯用/倾向于 X
    m = re.search(r"(?:喜欢|偏好|习惯用|倾向于)([一-龥A-Za-z0-9]{2,20}?)(?:[，。,\s]|$)", text)
    if m:
        preferences.append(f"用户偏好{m.group(1).strip('，。, ')}")

    return {"variables": variables, "facts": facts, "preferences": preferences, "plans": plans}


def llm_extract_memories(
    user_id: int,
    conversation: List[Dict[str, str]],
    auto_store: bool = True,
    session_id: Optional[str] = None,
    system_prompt: Optional[str] = None,
) -> Dict[str, Any]:
    """
    用 LLM 从对话中智能抽取记忆。

    Args:
        user_id: 用户 ID
        conversation: 对话历史 [{"role": "user/assistant", "content": "..."}]
        auto_store: 是否自动存储抽取结果（默认 True）
        session_id: 会话 ID（可选），传递后变量会与会话关联
        system_prompt: 自定义系统 Prompt（可选），不传则使用用户当前生效的模板

    Returns:
        抽取结果字典：
        {
            "success": True,
            "variables": [{"key": ..., "value": ...}],
            "facts": [...],
            "preferences": [...],
            "plans": [...],
            "stored_count": N
        }
    """
    try:
        if not conversation:
            return {"success": True, "variables": [], "facts": [], "preferences": [], "plans": [], "stored_count": 0}

        # 1. 获取生效的抽取 Prompt（优先使用传入的，其次查用户自定义，最后默认）
        active_prompt = system_prompt or get_active_extraction_prompt(user_id)

        # 2. 构建抽取请求
        messages = _build_extraction_messages(conversation, system_prompt=active_prompt)

        # 3. 调用 LLM
        llm_result = llm_chat(user_id=user_id, messages=messages, temperature=0.1)

        if not llm_result.get("success"):
            logger.warning(f"LLM 抽取调用失败: {llm_result.get('error', 'unknown')}")
            return {
                "success": False,
                "error": llm_result.get("error", "LLM call failed"),
                "variables": [],
                "facts": [],
                "preferences": [],
                "plans": [],
                "stored_count": 0,
            }

        # 4. 解析结果
        # 离线/Mock 模式（未配置 LLM）→ 用启发式抽取兜底，保证记忆管线离线可演示
        if llm_result.get("mock"):
            logger.warning("⚠ LLM 未配置（Mock 模式），使用启发式抽取兜底以保证离线记忆演示")
            extracted = _heuristic_extract(conversation)
        else:
            response_text = llm_result.get("content", "")
            extracted = _parse_llm_response(response_text)

        variables = extracted.get("variables", [])
        facts = extracted.get("facts", [])
        preferences = extracted.get("preferences", [])
        plans = extracted.get("plans", [])

        stored_count = 0
        dedup_skipped = 0

        # 5. 自动存储
        if auto_store:
            # 5a. 存储 KV 变量
            for var in variables:
                key = var.get("key", "").strip()
                value = var.get("value", "").strip()
                if key and value:
                    ok = set_memory_variable(
                        user_id=user_id, key=key, value=value,
                        session_id=session_id,
                    )
                    if ok:
                        stored_count += 1

            # 5b. 存储事实片段（含去重）
            for fact in facts:
                if fact and str(fact).strip():
                    result = _store_with_dedup(
                        user_id=user_id,
                        fragment_type="info",
                        content=str(fact).strip(),
                        importance_score=0.6,
                    )
                    if result.get("skipped"):
                        dedup_skipped += 1
                    elif result.get("success"):
                        stored_count += 1

            # 5c. 存储偏好片段（含去重）
            for pref in preferences:
                if pref and str(pref).strip():
                    result = _store_with_dedup(
                        user_id=user_id,
                        fragment_type="preference",
                        content=str(pref).strip(),
                        importance_score=0.5,
                    )
                    if result.get("skipped"):
                        dedup_skipped += 1
                    elif result.get("success"):
                        stored_count += 1

            # 5d. 存储计划片段（含去重）
            for plan in plans:
                if plan and str(plan).strip():
                    result = _store_with_dedup(
                        user_id=user_id,
                        fragment_type="plan",
                        content=str(plan).strip(),
                        importance_score=0.5,
                    )
                    if result.get("skipped"):
                        dedup_skipped += 1
                    elif result.get("success"):
                        stored_count += 1

        # 6. 抽取后去重检测
        if auto_store and facts:
            try:
                from app.services.memory_lifecycle_service import find_duplicates
                for fact in facts:
                    if fact and str(fact).strip():
                        dup_result = find_duplicates(user_id, str(fact).strip(), threshold=0.85, limit=3)
                        if dup_result.get("success") and dup_result.get("count", 0) > 0:
                            logger.info(f"🔍 去重检测: fact '{str(fact).strip()[:50]}...' 发现 {dup_result['count']} 条重复")
            except Exception as e:
                logger.warning(f"去重检测异常: {e}")

        logger.info(
            f"LLM 抽取完成: {len(variables)} 变量, {len(facts)} 事实, "
            f"{len(preferences)} 偏好, {len(plans)} 计划, 存储 {stored_count} 条, 去重跳过 {dedup_skipped} 条"
        )

        return {
            "success": True,
            "variables": variables,
            "facts": facts,
            "preferences": preferences,
            "plans": plans,
            "stored_count": stored_count,
            "dedup_skipped": dedup_skipped,
        }

    except Exception as e:
        logger.error(f"LLM 记忆抽取失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "variables": [],
            "facts": [],
            "preferences": [],
            "plans": [],
            "stored_count": 0,
            "dedup_skipped": 0,
        }
