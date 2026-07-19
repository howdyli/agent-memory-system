"""
Extraction — LLM-driven memory extraction from conversations.

Core-layer extraction logic. Uses injected LLMBackend + VariableManager + FragmentManager
instead of global singletons. Template persistence belongs to Server layer.

Usage:
    from .modules.llm_backend import LLMBackend, OpenAIBackend
    from .modules.variables import VariableManager
    from .modules.fragments import FragmentManager

    extractor = ExtractionManager(
        llm_backend=OpenAIBackend({"api_key": "sk-xxx"}),
        variable_manager=var_mgr,
        fragment_manager=frag_mgr,
    )
    result = extractor.extract(conversation=[...], workspace_id=1)
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

from ..events import EventEmitter, MemoryEvent, MemoryEventType


# ─────────────────────────────────────────────────────────────────
# Extraction Prompts
# ─────────────────────────────────────────────────────────────────

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

COMPRESSION_SUMMARY_PROMPT = """请将以下对话历史压缩为一段简洁的中文摘要（不超过 200 字）。
保留以下关键信息：
1. 用户的基本信息（姓名、角色、组织等）
2. 用户的偏好和习惯
3. 用户的计划和待办事项
4. 已完成的对话主题
5. 重要的约定和决定

对话历史：
{conversation_text}

摘要："""


# ─────────────────────────────────────────────────────────────────
# Extraction Result
# ─────────────────────────────────────────────────────────────────

@dataclass
class ExtractionResult:
    """Result of memory extraction from a conversation."""
    variables: List[Dict[str, str]] = field(default_factory=list)
    facts: List[str] = field(default_factory=list)
    preferences: List[str] = field(default_factory=list)
    plans: List[str] = field(default_factory=list)
    stored_count: int = 0
    dedup_skipped: int = 0
    is_mock: bool = False


# ─────────────────────────────────────────────────────────────────
# ExtractionManager
# ─────────────────────────────────────────────────────────────────

class ExtractionManager:
    """LLM-driven memory extraction from conversations.

    Injects:
    - llm_backend: LLMBackend for calling LLM (required)
    - variable_manager: VariableManager for storing KV variables (optional)
    - fragment_manager: FragmentManager for storing fragments (optional)
    - event_emitter: EventEmitter for lifecycle hooks (optional)

    When variable_manager or fragment_manager is None, extraction
    still runs but auto_store=False (no persistence).
    """

    def __init__(
        self,
        llm_backend: Any,  # LLMBackend instance
        variable_manager: Optional[Any] = None,  # VariableManager
        fragment_manager: Optional[Any] = None,  # FragmentManager
        lifecycle_manager: Optional[Any] = None,  # LifecycleManager (for dedup)
        event_emitter: Optional[EventEmitter] = None,
        dedup_threshold: float = 0.85,
    ):
        self._llm = llm_backend
        self._variables = variable_manager
        self._fragments = fragment_manager
        self._lifecycle = lifecycle_manager
        self._events = event_emitter or EventEmitter()
        self._dedup_threshold = dedup_threshold

    # ── Main Extraction ──────────────────────────────────────────

    def extract(
        self,
        conversation: List[Dict[str, str]],
        workspace_id: int,
        auto_store: bool = True,
        session_id: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> ExtractionResult:
        """Extract memories from a conversation using LLM.

        Args:
            conversation: [{"role": "user/assistant", "content": "..."}]
            workspace_id: Workspace scope for storage.
            auto_store: Whether to auto-store extracted memories.
            session_id: Session scope for variables (optional).
            system_prompt: Override extraction prompt (optional).

        Returns:
            ExtractionResult with extracted items and storage stats.
        """
        if not conversation:
            return ExtractionResult()

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.EXTRACTION_TRIGGERED,
            workspace_id=workspace_id,
            data={"conversation_length": len(conversation)},
        ))

        # 1. Build messages
        prompt = system_prompt or EXTRACTION_SYSTEM_PROMPT
        messages = self._build_extraction_messages(conversation, prompt)

        # 2. Call LLM
        llm_result = self._llm.chat(messages, temperature=0.1)

        # 3. Parse or heuristic fallback
        extracted = ExtractionResult()
        if llm_result.get("mock"):
            logger.warning("LLM mock mode — using heuristic extraction fallback")
            heuristic = self._heuristic_extract(conversation)
            extracted.variables = heuristic.get("variables", [])
            extracted.facts = heuristic.get("facts", [])
            extracted.preferences = heuristic.get("preferences", [])
            extracted.plans = heuristic.get("plans", [])
            extracted.is_mock = True
        elif llm_result.get("success") and llm_result.get("content"):
            parsed = self._parse_llm_response(llm_result["content"])
            extracted.variables = parsed.get("variables", [])
            extracted.facts = parsed.get("facts", [])
            extracted.preferences = parsed.get("preferences", [])
            extracted.plans = parsed.get("plans", [])
        else:
            logger.warning(f"LLM extraction call failed: {llm_result.get('error', 'unknown')}")

        # 4. Auto-store
        if auto_store and (self._variables or self._fragments):
            extracted.stored_count, extracted.dedup_skipped = self._store_extracted(
                extracted, workspace_id, session_id,
            )

        self._events.emit(MemoryEvent(
            event_type=MemoryEventType.EXTRACTION_COMPLETED,
            workspace_id=workspace_id,
            data={
                "variables": len(extracted.variables),
                "facts": len(extracted.facts),
                "preferences": len(extracted.preferences),
                "plans": len(extracted.plans),
                "stored_count": extracted.stored_count,
            },
        ))

        return extracted

    # ── Conversation Summary ─────────────────────────────────────

    def summarize_conversation(
        self,
        conversation_text: str,
        workspace_id: int,
    ) -> str:
        """Generate a summary of conversation text using LLM.

        Args:
            conversation_text: Formatted conversation text.
            workspace_id: Workspace scope.

        Returns:
            Summary text string.
        """
        prompt = COMPRESSION_SUMMARY_PROMPT.format(conversation_text=conversation_text)
        result = self._llm.chat(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500,
        )

        if result.get("success") and result.get("content"):
            summary = result["content"].strip()
            if len(summary) > 500:
                summary = summary[:497] + "..."
            return summary

        # Fallback: rule-based summary
        return self._fallback_summary(conversation_text)

    # ── Storage ──────────────────────────────────────────────────

    def _store_extracted(
        self,
        result: ExtractionResult,
        workspace_id: int,
        session_id: Optional[str] = None,
    ) -> tuple:
        """Store extracted memories. Returns (stored_count, dedup_skipped)."""
        stored = 0
        dedup = 0

        # Variables
        if self._variables:
            for var in result.variables:
                key = var.get("key", "").strip()
                value = var.get("value", "").strip()
                if key and value:
                    try:
                        self._variables.set(
                            workspace_id=workspace_id,
                            key=key,
                            value=value,
                            session_id=session_id,
                        )
                        stored += 1
                    except Exception as e:
                        logger.warning(f"Failed to store variable '{key}': {e}")

        # Fragments (facts, preferences, plans) with dedup
        if self._fragments:
            fragment_items = [
                ("info", result.facts, 0.6),
                ("preference", result.preferences, 0.5),
                ("plan", result.plans, 0.5),
            ]
            for frag_type, items, importance in fragment_items:
                for item in items:
                    content = str(item).strip() if item else ""
                    if not content:
                        continue
                    try:
                        skip = self._check_dedup(workspace_id, content)
                        if skip:
                            dedup += 1
                            continue
                        self._fragments.create(
                            workspace_id=workspace_id,
                            fragment_type=frag_type,
                            content=content,
                            importance_score=importance,
                        )
                        stored += 1
                    except Exception as e:
                        logger.warning(f"Failed to store fragment: {e}")

        return stored, dedup

    def _check_dedup(self, workspace_id: int, content: str) -> bool:
        """Check if a similar fragment already exists."""
        if not self._fragments:
            return False

        try:
            # Semantic search for duplicates
            similar = self._fragments.search_by_semantic(
                workspace_id=workspace_id,
                query=content,
                top_k=5,
                threshold=0.3,
            )
            for frag in similar:
                semantic_sim = frag.get("similarity", 0)
                # Text similarity (bigram Jaccard)
                text_sim = self._text_similarity(content, frag.get("content", ""))
                combined_sim = max(semantic_sim, text_sim)
                if combined_sim >= self._dedup_threshold:
                    return True
        except Exception:
            pass

        return False

    # ── Response Parsing ─────────────────────────────────────────

    @staticmethod
    def _parse_llm_response(response_text: str) -> Dict[str, Any]:
        """Parse LLM JSON extraction result.

        Handles markdown code blocks and raw JSON objects.
        """
        if not response_text:
            return {}

        text = response_text.strip()

        # Extract from markdown code block
        code_block = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if code_block:
            text = code_block.group(1).strip()

        # Extract JSON object
        json_match = re.search(r"\{.*\}", text, re.DOTALL)
        if json_match:
            text = json_match.group(0)

        try:
            text = ''.join(c if c.isprintable() or c in '\n\r\t' else ' ' for c in text)
            result = json.loads(text, strict=False)
            if isinstance(result, dict):
                return result
        except (json.JSONDecodeError, ValueError):
            logger.warning(f"LLM extraction response parse failed: {response_text[:200]}")

        return {}

    # ── Heuristic Fallback ───────────────────────────────────────

    @staticmethod
    def _heuristic_extract(conversation: List[Dict[str, str]]) -> Dict[str, Any]:
        """Rule-based extraction fallback when LLM is unavailable (mock mode).

        Extracts: names, responsibilities, preferences using regex patterns.
        """
        text = " ".join(m.get("content", "") for m in conversation if m.get("role") == "user")
        variables, facts, preferences, plans = [], [], [], []

        # Name extraction
        for pat in [r"我叫([一-龥A-Za-z]{1,8})", r"我的名字叫?是?([一-龥A-Za-z]{1,8})"]:
            m = re.search(pat, text)
            if m:
                name = m.group(1).strip("，。, ")
                if 1 <= len(name) <= 6 and name not in ("做", "在", "负", "一", "了"):
                    variables.append({"key": "user_name", "value": name})
                    break

        # Responsibilities
        m = re.search(r"负责([一-龥A-Za-z0-9·]{2,30}?)(?:[，。,\s]|$)", text)
        if m:
            facts.append(f"用户负责{m.group(1).strip('，。, ')}")

        # Key project/system mentions
        for kw in ["源启", "智能体工厂", "Agent", "记忆系统"]:
            if kw in text:
                facts.append(f"用户提及{kw}相关项目/系统")

        # Preferences
        m = re.search(r"(?:喜欢|偏好|习惯用|倾向于)([一-龥A-Za-z0-9]{2,20}?)(?:[，。,\s]|$)", text)
        if m:
            preferences.append(f"用户偏好{m.group(1).strip('，。, ')}")

        return {"variables": variables, "facts": facts, "preferences": preferences, "plans": plans}

    # ── Helpers ──────────────────────────────────────────────────

    @staticmethod
    def _build_extraction_messages(
        conversation: List[Dict[str, str]],
        system_prompt: str,
    ) -> List[Dict[str, str]]:
        """Build LLM messages from conversation for extraction."""
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
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请从以下对话中抽取需要长期记忆的信息：\n\n{dialogue_text}"},
        ]

    @staticmethod
    def _fallback_summary(conversation_text: str) -> str:
        """Rule-based summary fallback."""
        lines = conversation_text.strip().split("\n")
        user_messages = [l[3:].strip() for l in lines if l.startswith("用户:")]
        total_turns = len(user_messages)
        if user_messages:
            recent = "；".join(user_messages[-3:])
            return f"对话共 {total_turns} 轮，最近内容：{recent}"
        return "(对话历史)"

    @staticmethod
    def _text_similarity(text1: str, text2: str) -> float:
        """Bigram Jaccard similarity for dedup check."""
        if not text1 or not text2:
            return 0.0
        def bigrams(t):
            return set(t[i:i+2] for i in range(len(t)-1)) if len(t) > 1 else set()
        b1 = bigrams(text1)
        b2 = bigrams(text2)
        if not b1 and not b2:
            return 1.0 if text1 == text2 else 0.0
        if not b1 or not b2:
            return 0.0
        intersection = b1 & b2
        union = b1 | b2
        return len(intersection) / len(union)
