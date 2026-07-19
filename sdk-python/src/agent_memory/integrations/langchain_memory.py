"""
LangChain Memory 集成

实现兼容 LangChain 记忆接口的 AgentMemoryLangChain 类，
可作为 LangChain Agent 的 memory 参数使用。

用法：
    from agent_memory import MemoryClient
    from agent_memory.integrations.langchain_memory import AgentMemoryLangChain

    client = MemoryClient(base_url="https://mem.example.com", api_key="amk_xxx")
    memory = AgentMemoryLangChain(client)

    agent = create_react_agent(llm, tools, memory=memory)
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

    内部使用 MemoryClient 进行实际的记忆操作。
    """

    def __init__(self, client, session_id: Optional[str] = None):
        """
        Args:
            client: MemoryClient 实例
            session_id: 会话 ID（可选，用于会话级记忆隔离）
        """
        self.client = client
        self.session_id = session_id
        self._chat_history: List[Dict[str, str]] = []

    @property
    def memory_variables(self) -> List[str]:
        """此 Memory 会注入到 chain 的变量列表。"""
        return ["memory_context", "chat_history"]

    def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        加载记忆变量，注入到 chain 的上下文中。

        根据当前输入自动召回相关记忆。
        """
        query = inputs.get("input", "")
        memory_context = self.client.recall_context(query, top_k=5)
        chat_history = self._format_chat_history()

        return {
            "memory_context": memory_context,
            "chat_history": chat_history,
        }

    def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, str]) -> None:
        """
        保存对话上下文。

        将本轮对话追加到历史记录，并使用 LLM 抽取新记忆。
        """
        user_input = inputs.get("input", "")
        assistant_output = outputs.get("output", "")

        if not user_input:
            return

        self._chat_history.append({"role": "user", "content": user_input})
        self._chat_history.append({"role": "assistant", "content": assistant_output})

        # 尝试通过 API 抽取记忆
        try:
            self.client._transport.request("POST", "/agent/extract", json={
                "conversation": self._chat_history[-2:],
                "auto_store": True,
            })
        except Exception as e:
            logger.warning(f"save_context 记忆抽取失败（不影响对话）: {e}")

    def clear(self) -> None:
        """清除对话历史缓存。"""
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


def create_langchain_memory(client, session_id: Optional[str] = None) -> AgentMemoryLangChain:
    """
    创建 LangChain 记忆实例的工厂函数。

    Args:
        client: MemoryClient 实例
        session_id: 会话 ID（可选）

    Returns:
        AgentMemoryLangChain 实例
    """
    return AgentMemoryLangChain(client=client, session_id=session_id)
