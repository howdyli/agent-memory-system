"""Tests for MemoryClient."""

import pytest
from unittest.mock import MagicMock, patch

from agent_memory import MemoryClient
from agent_memory.transport.http import HttpTransport
from agent_memory.transport.embedded import EmbeddedTransport


class TestMemoryClientInit:
    def test_http_mode_requires_base_url(self):
        with pytest.raises(ValueError, match="HTTP 模式需要 base_url"):
            MemoryClient(mode="http")

    def test_http_mode_creates_http_transport(self):
        client = MemoryClient(base_url="http://localhost:8000", api_key="amk_test")
        assert isinstance(client._transport, HttpTransport)
        client.close()

    def test_embedded_mode_creates_embedded_transport(self):
        client = MemoryClient(mode="embedded", user_id=1)
        assert isinstance(client._transport, EmbeddedTransport)
        client.close()

    def test_invalid_mode_raises_error(self):
        with pytest.raises(ValueError, match="未知 mode"):
            MemoryClient(mode="invalid")

    def test_workspace_id_passed_to_http_transport(self):
        client = MemoryClient(base_url="http://localhost:8000", workspace_id="42")
        assert client._transport.workspace_id == "42"
        client.close()

    def test_workspace_id_passed_to_embedded_transport(self):
        client = MemoryClient(mode="embedded", user_id=1, workspace_id="42")
        assert client._transport.workspace_id == 42
        client.close()


class TestMemoryClientSubmodules:
    def setup_method(self):
        self.client = MemoryClient(base_url="http://localhost:8000")

    def teardown_method(self):
        self.client.close()

    def test_has_variables_api(self):
        from agent_memory.api.variables import VariablesAPI
        assert isinstance(self.client.variables, VariablesAPI)

    def test_has_fragments_api(self):
        from agent_memory.api.fragments import FragmentsAPI
        assert isinstance(self.client.fragments, FragmentsAPI)

    def test_has_tables_api(self):
        from agent_memory.api.tables import TablesAPI
        assert isinstance(self.client.tables, TablesAPI)

    def test_has_graph_api(self):
        from agent_memory.api.graph import GraphAPI
        assert isinstance(self.client.graph, GraphAPI)

    def test_has_recall_api(self):
        from agent_memory.api.recall import RecallAPI
        assert isinstance(self.client.recall, RecallAPI)

    def test_has_events_api(self):
        from agent_memory.api.events import EventsAPI
        assert isinstance(self.client.events, EventsAPI)


class TestMemoryClientConvenienceMethods:
    """Test convenience methods by mocking the API submodules directly."""

    def setup_method(self):
        self.client = MemoryClient(base_url="http://localhost:8000")
        # Mock API submodules
        self.mock_variables = MagicMock()
        self.mock_fragments = MagicMock()
        self.mock_tables = MagicMock()
        self.mock_recall = MagicMock()
        self.client.variables = self.mock_variables
        self.client.fragments = self.mock_fragments
        self.client.tables = self.mock_tables
        self.client.recall = self.mock_recall

    def teardown_method(self):
        self.client.close()

    def test_remember_delegates_to_variables_set(self):
        self.mock_variables.set.return_value = True
        result = self.client.remember("key", "value")
        assert result is True
        self.mock_variables.set.assert_called_once_with("key", "value", ttl=None)

    def test_forget_delegates_to_variables_delete(self):
        self.mock_variables.delete.return_value = True
        result = self.client.forget("key")
        assert result is True
        self.mock_variables.delete.assert_called_once_with("key")

    def test_search_delegates_to_fragments_search(self):
        self.mock_fragments.semantic_search.return_value = [{"content": "test"}]
        results = self.client.search("query")
        assert len(results) == 1
        self.mock_fragments.semantic_search.assert_called_once_with("query", top_k=5, threshold=0.3)

    def test_recall_context_returns_context_string(self):
        self.mock_recall.auto.return_value = {"success": True, "context": "记忆上下文"}
        result = self.client.recall_context("query")
        assert result == "记忆上下文"

    def test_recall_context_returns_empty_on_failure(self):
        self.mock_recall.auto.side_effect = Exception("Network error")
        result = self.client.recall_context("query")
        assert result == ""

    def test_create_table_delegates_to_tables_create(self):
        self.mock_tables.create.return_value = {"success": True, "table_name": "contacts"}
        result = self.client.create_table("contacts", [{"name": "name", "type": "TEXT"}])
        assert result["success"] is True

    def test_remember_structured_delegates_to_tables_add_record(self):
        self.mock_tables.add_record.return_value = {"success": True}
        result = self.client.remember_structured("contacts", {"name": "Alice"})
        assert result["success"] is True

    def test_remember_fragment_delegates_to_fragments_create(self):
        self.mock_fragments.create.return_value = {"id": 1, "content": "test content"}
        result = self.client.remember_fragment("test content", importance_score=0.9)
        assert result["content"] == "test content"


class TestMemoryClientContextManager:
    def test_sync_context_manager(self):
        with MemoryClient(base_url="http://localhost:8000") as client:
            assert isinstance(client, MemoryClient)

    def test_repr_http(self):
        client = MemoryClient(base_url="http://localhost:8000")
        assert repr(client) == "MemoryClient(mode='http')"
        client.close()

    def test_repr_embedded(self):
        client = MemoryClient(mode="embedded", user_id=1)
        assert repr(client) == "MemoryClient(mode='embedded')"
        client.close()
