"""公开逃生舱 request() 与 307 修复相关测试（全 mock，不起网络）。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_memory.async_client import AsyncMemoryClient, _AsyncHttpTransport
from agent_memory.api.tables import TablesAPI
from agent_memory.client import MemoryClient
from agent_memory.exceptions import HTTPError, TransportError
from agent_memory.transport.http import HttpTransport


class TestAsyncPublicRequest:
    """AsyncMemoryClient.request() 透传与异常透传。"""

    def _make_client(self) -> AsyncMemoryClient:
        client = AsyncMemoryClient(base_url="http://localhost:8000")
        client._transport = MagicMock()
        return client

    def test_passes_through_to_transport(self):
        client = self._make_client()
        client._transport.request = AsyncMock(return_value={"results": [1, 2]})

        result = asyncio.run(
            client.request("POST", "/memory/hybrid-search", json={"query": "q"})
        )

        assert result == {"results": [1, 2]}
        client._transport.request.assert_awaited_once_with(
            "POST", "/memory/hybrid-search", json={"query": "q"}, params=None
        )

    def test_passes_params(self):
        client = self._make_client()
        client._transport.request = AsyncMock(return_value=[])

        asyncio.run(client.request("GET", "/memory/graph/query", params={"q": "e"}))

        client._transport.request.assert_awaited_once_with(
            "GET", "/memory/graph/query", json=None, params={"q": "e"}
        )

    def test_exception_propagates_unchanged(self):
        client = self._make_client()
        err = HTTPError(status_code=503, detail="unavailable")
        client._transport.request = AsyncMock(side_effect=err)

        with pytest.raises(HTTPError) as exc_info:
            asyncio.run(client.request("POST", "/memory/hybrid-search"))
        assert exc_info.value is err

    def test_absolute_url_raises_value_error(self):
        # 防御在网络调用前触发，走真实 transport 不起网络
        client = AsyncMemoryClient(base_url="http://localhost:8000")
        with pytest.raises(ValueError, match="相对路径"):
            asyncio.run(client.request("GET", "https://evil.example.com/steal"))
        asyncio.run(client.close())


class TestSyncPublicRequest:
    """MemoryClient.request()（http 模式）透传与异常透传。"""

    def _make_client(self) -> MemoryClient:
        client = MemoryClient(base_url="http://localhost:8000")
        client._transport = MagicMock()
        return client

    def test_passes_through_to_transport(self):
        client = self._make_client()
        client._transport.request.return_value = {"ok": True}

        result = client.request("POST", "/memory/hybrid-search", json={"query": "q"})

        assert result == {"ok": True}
        client._transport.request.assert_called_once_with(
            "POST", "/memory/hybrid-search", json={"query": "q"}, params=None
        )

    def test_exception_propagates_unchanged(self):
        client = self._make_client()
        err = TransportError("boom")
        client._transport.request.side_effect = err

        with pytest.raises(TransportError) as exc_info:
            client.request("GET", "/memory/tables")
        assert exc_info.value is err

    def test_absolute_url_raises_value_error(self):
        # 防御在网络调用前触发，走真实 transport 不起网络
        client = MemoryClient(base_url="http://localhost:8000")
        with pytest.raises(ValueError, match="相对路径"):
            client.request("GET", "http://evil.example.com/steal")
        client._transport.close()


class TestTablesCreateNoTrailingSlash:
    """tables.create() 使用无尾斜杠路径，避免 FastAPI 307。"""

    def test_create_posts_without_trailing_slash(self):
        transport = MagicMock()
        transport.request.return_value = {"success": True}
        api = TablesAPI(transport)

        api.create(table_name="books", fields=[{"name": "title", "type": "TEXT"}])

        transport.request.assert_called_once_with(
            "POST",
            "/memory/tables",
            json={"table_name": "books", "fields": [{"name": "title", "type": "TEXT"}]},
        )


class TestFollowRedirects:
    """httpx 客户端启用 follow_redirects，防御尾斜杠 307/308。"""

    def test_sync_transport_follows_redirects(self):
        t = HttpTransport(base_url="http://localhost:8000")
        assert t._client.follow_redirects is True
        t.close()

    def test_async_transport_follows_redirects(self):
        t = _AsyncHttpTransport(base_url="http://localhost:8000")
        assert t._client.follow_redirects is True
        asyncio.run(t.close())
