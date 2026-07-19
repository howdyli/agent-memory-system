"""SDK exception hierarchy."""


class AgentMemoryError(Exception):
    """Base exception for Agent Memory SDK."""
    pass


class TransportError(AgentMemoryError):
    """Transport layer error (network, connection, etc.)."""
    pass


class HTTPError(TransportError):
    """HTTP request failed."""

    def __init__(self, status_code: int, detail: str = ""):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"HTTP {status_code}: {detail}")


class AuthenticationError(HTTPError):
    """Authentication failed (401)."""

    def __init__(self, detail: str = "Authentication failed"):
        super().__init__(status_code=401, detail=detail)


class PermissionDeniedError(HTTPError):
    """Permission denied (403)."""

    def __init__(self, detail: str = "Permission denied"):
        super().__init__(status_code=403, detail=detail)


class NotFoundError(HTTPError):
    """Resource not found (404)."""

    def __init__(self, detail: str = "Not found"):
        super().__init__(status_code=404, detail=detail)


class ValidationError(AgentMemoryError):
    """Invalid parameters or configuration."""
    pass


class EmbeddedModeError(AgentMemoryError):
    """Operation not supported in embedded mode."""
    pass
