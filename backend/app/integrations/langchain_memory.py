"""
LangChain Memory 集成

实现一个兼容 LangChain 记忆接口的 AgentMemoryLangChain 类，
可作为 LangChain Agent 的 memory 参数使用。

用法：
    from app.integrations.langchain_memory import AgentMemoryLangChain

    memory = AgentMemoryLangChain(user_id=1)

    # 作为 Agent 的 memory 使用
    agent = create_react_agent(llm, tools, memory=memory)

    # 手动使用
    ctx = memory.load_memory_variables({"input": "你好"})
    memory.save_context({"input": "我叫鑫海"}, {"output": "你好鑫海"})
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class AgentMemoryLangChain:
    """
    LangChain 记忆集成。

    实现 load_memory_variables / save_context 接口，
    兼容 LangChain Agent 的 memory 参数。

    内部使用 AgentMemoryClient 进行实际的记忆操作。
    """

    def __init__(self, user_id: int, session_id: Optional[str] = None):
        """
        Args:
            user_id: 用户 ID
            session_id: 会话 ID（可选，用于会话级记忆隔离）
        """
        self.user_id = user_id
        self.session_id = session_id

        # 延迟导入
        from app.services.agent_memory_sdk import AgentMemoryClient
        self.sdk = AgentMemoryClient(user_id)

        # 对话历史缓存（用于 save_context 时的抽取）
        self._chat_history: List[Dict[str, str]] = []

    @property
    def memory_variables(self) -> List[str]:
        """此 Memory 会注入到 chain 的变量列表。"""
        return ["memory_context", "chat_history"]

    def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        加载记忆变量，注入到 chain 的上下文中。

        根据当前输入自动召回相关记忆。

        Args:
            inputs: chain 输入，通常包含 "input" 键

        Returns:
            记忆变量字典
        """
        query = inputs.get("input", "")

        # 1. 召回相关记忆
        memory_context = self.sdk.recall(query, top_k=5)

        # 2. 获取对话历史
        chat_history = self._format_chat_history()

        return {
            "memory_context": memory_context,
            "chat_history": chat_history,
        }

    def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, str]) -> None:
        """
        保存对话上下文。

        将本轮对话追加到历史记录，并使用 LLM 抽取新记忆。

        Args:
            inputs: chain 输入 {"input": "..."}
            outputs: chain 输出 {"output": "..."}
        """
        user_input = inputs.get("input", "")
        assistant_output = outputs.get("output", "")

        if not user_input:
            return

        # 1. 追加到历史
        self._chat_history.append({"role": "user", "content": user_input})
        self._chat_history.append({"role": "assistant", "content": assistant_output})

        # 2. 使用 LLM 抽取记忆
        try:
            from app.services.llm_extraction_service import llm_extract_memories

            # 只取最近一轮对话进行抽取
            recent_conversation = self._chat_history[-2:]
            llm_extract_memories(
                user_id=self.user_id,
                conversation=recent_conversation,
                auto_store=True,
            )
        except Exception as e:
            logger.warning(f"save_context 记忆抽取失败（不影响对话）: {e}")

    def clear(self) -> None:
        """清除对话历史缓存（不影响持久化记忆）。"""
        self._chat_history.clear()

    def _format_chat_history(self) -> str:
        """将对话历史格式化为字符串。"""
        if not self._chat_history:
            return ""
        lines = []
        for msg in self._chat_history:
            role = "用户" if msg["role"] == "user" else "助手"
            lines.append(f"{role}: {msg['content']}")
        return "\n".join(lines)


# ============================================================
# 便捷工厂函数
# ============================================================

def create_langchain_memory(user_id: int, session_id: Optional[str] = None) -> AgentMemoryLangChain:
    """
    创建 LangChain 记忆实例的工厂函数。

    Args:
        user_id: 用户 ID
        session_id: 会话 ID（可选）

    Returns:
        AgentMemoryLangChain 实例
    """
    return AgentMemoryLangChain(user_id=user_id, session_id=session_id)
