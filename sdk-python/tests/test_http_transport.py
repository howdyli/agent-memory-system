"""Tests for HttpTransport."""

import pytest
from unittest.mock import MagicMock, patch

from agent_memory.transport.http import HttpTransport
from agent_memory.exceptions import (
    AuthenticationError,
    HTTPError,
    NotFoundError,
    PermissionDeniedError,
    TransportError,
)


class TestHttpTransportInit:
    def test_creates_client_with_base_url(self):
        t = HttpTransport(base_url="http://localhost:8000")
        assert t.base_url == "http://localhost:8000"
        t.close()

    def test_strips_trailing_slash(self):
        t = HttpTransport(base_url="http://localhost:8000/")
        assert t.base_url == "http://localhost:8000"
        t.close()

    def test_sets_api_key_header(self):
        t = HttpTransport(base_url="http://localhost:8000", api_key="amk_test")
        assert t._client.headers.get("Authorization") == "Bearer amk_test"
        t.close()

    def test_sets_token_header(self):
        t = HttpTransport(base_url="http://localhost:8000", token="jwt_token")
        assert t._client.headers.get("Authorization") == "Bearer jwt_token"
        t.close()

    def test_api_key_takes_precedence_over_token(self):
        t = HttpTransport(base_url="http://localhost:8000", api_key="amk_key", token="jwt")
        assert t._client.headers.get("Authorization") == "Bearer amk_key"
        t.close()

    def test_sets_workspace_header(self):
        t = HttpTransport(base_url="http://localhost:8000", workspace_id="42")
        assert t._client.headers.get("X-Workspace-Id") == "42"
        t.close()


class TestHttpTransportRequest:
    def setup_method(self):
        self.transport = HttpTransport(base_url="http://localhost:8000")

    def teardown_method(self):
        self.transport.close()

    @patch("agent_memory.transport.http.httpx.Client")
    def test_successful_json_response(self, MockClient):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"key": "value"}
        self.transport._client = MagicMock()
        self.transport._client.request.return_value = mock_response

        result = self.transport.request("GET", "/test")
        assert result == {"key": "value"}

    @patch("agent_memory.transport.http.httpx.Client")
    def test_204_returns_none(self, MockClient):
        mock_response = MagicMock()
        mock_response.status_code = 204
        self.transport._client = MagicMock()
        self.transport._client.request.return_value = mock_response

        result = self.transport.request("DELETE", "/test")
        assert result is None

    def test_401_raises_auth_error(self):
        self.transport._client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        self.transport._client.request.return_value = mock_response

        with pytest.raises(AuthenticationError):
            self.transport.request("GET", "/test")

    def test_403_raises_permission_error(self):
        self.transport._client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        self.transport._client.request.return_value = mock_response

        with pytest.raises(PermissionDeniedError):
            self.transport.request("GET", "/test")

    def test_404_raises_not_found(self):
        self.transport._client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not found"
        self.transport._client.request.return_value = mock_response

        with pytest.raises(NotFoundError):
            self.transport.request("GET", "/test")

    def test_500_raises_http_error(self):
        self.transport._client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Internal Server Error"
        self.transport._client.request.return_value = mock_response

        with pytest.raises(HTTPError) as exc_info:
            self.transport.request("GET", "/test")
        assert exc_info.value.status_code == 500

    def test_connection_error_raises_transport_error(self):
        import httpx
        self.transport._client = MagicMock()
        self.transport._client.request.side_effect = httpx.ConnectError("Connection refused")

        with pytest.raises(TransportError):
            self.transport.request("GET", "/test")


class TestHttpTransportStream:
    def test_request_stream_yields_lines(self):
        transport = HttpTransport(base_url="http://localhost:8000")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.iter_lines.return_value = ["data: line1", "data: line2", ""]
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        transport._client = MagicMock()
        transport._client.stream.return_value = mock_response

        lines = list(transport.request_stream("POST", "/stream"))
        assert "data: line1" in lines
        assert "data: line2" in lines
        transport.close()
