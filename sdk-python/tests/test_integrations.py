"""Tests for SDK integrations (LangChain, MCP)."""

import pytest
from unittest.mock import MagicMock, patch

from agent_memory import MemoryClient


class TestLangChainIntegration:
    def test_get_memory_tools_returns_tools(self):
        """Test that get_memory_tools returns 13 tools."""
        try:
            from agent_memory.integrations.langchain import get_memory_tools
        except ImportError:
            pytest.skip("langchain-core not installed")

        client = MemoryClient(base_url="http://localhost:8000")
        # Mock the API submodules to avoid actual HTTP calls
        client.variables = MagicMock()
        client.fragments = MagicMock()
        client.tables = MagicMock()
        client.graph = MagicMock()
        client.recall = MagicMock()
        client.recall.auto.return_value = {"context": ""}
        client.get_context = MagicMock(return_value="")

        tools = get_memory_tools(client)
        assert len(tools) == 13

        tool_names = [t.name for t in tools]
        assert "memory_recall" in tool_names
        assert "memory_remember" in tool_names
        assert "memory_forget" in tool_names
        assert "memory_search" in tool_names
        assert "memory_get_context" in tool_names
        assert "memory_create_table" in tool_names
        assert "memory_add_record" in tool_names
        assert "graph_add_entity" in tool_names
        assert "graph_add_relationship" in tool_names
        assert "graph_search_entities" in tool_names
        assert "graph_query_neighbors" in tool_names
        assert "graph_analyze" in tool_names
        assert "graph_extract_from_text" in tool_names
        client.close()


class TestLangChainMemoryIntegration:
    def test_create_langchain_memory(self):
        from agent_memory.integrations.langchain_memory import (
            AgentMemoryLangChain,
            create_langchain_memory,
        )

        client = MemoryClient(base_url="http://localhost:8000")
        client.recall_context = MagicMock(return_value="test context")

        memory = create_langchain_memory(client, session_id="test-session")
        assert isinstance(memory, AgentMemoryLangChain)
        assert memory.session_id == "test-session"
        assert memory.memory_variables == ["memory_context", "chat_history"]
        client.close()

    def test_load_memory_variables(self):
        from agent_memory.integrations.langchain_memory import AgentMemoryLangChain

        client = MemoryClient(base_url="http://localhost:8000")
        client.recall_context = MagicMock(return_value="test context")

        memory = AgentMemoryLangChain(client)
        result = memory.load_memory_variables({"input": "hello"})
        assert "memory_context" in result
        assert "chat_history" in result
        client.close()

    def test_save_context_appends_history(self):
        from agent_memory.integrations.langchain_memory import AgentMemoryLangChain

        client = MemoryClient(base_url="http://localhost:8000")
        client._transport = MagicMock()
        memory = AgentMemoryLangChain(client)

        memory.save_context({"input": "hello"}, {"output": "hi"})
        assert len(memory._chat_history) == 2
        assert memory._chat_history[0]["role"] == "user"
        assert memory._chat_history[1]["role"] == "assistant"
        client.close()

    def test_clear_resets_history(self):
        from agent_memory.integrations.langchain_memory import AgentMemoryLangChain

        client = MemoryClient(base_url="http://localhost:8000")
        client._transport = MagicMock()
        memory = AgentMemoryLangChain(client)

        memory.save_context({"input": "hello"}, {"output": "hi"})
        memory.clear()
        assert len(memory._chat_history) == 0
        client.close()


class TestMCPIntegration:
    def test_create_mcp_server(self):
        try:
            from agent_memory.integrations.mcp import HAS_MCP
            if not HAS_MCP:
                pytest.skip("mcp package not installed")
            from agent_memory.integrations.mcp import create_mcp_server
        except ImportError:
            pytest.skip("mcp not installed")

        client = MemoryClient(base_url="http://localhost:8000")
        client._transport = MagicMock()

        server = create_mcp_server(client=client)
        assert server is not None
        client.close()
