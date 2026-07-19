"""Agent Memory SDK — unified client for HTTP and embedded modes."""

__version__ = "0.1.0"

from agent_memory.client import MemoryClient
from agent_memory.async_client import AsyncMemoryClient

__all__ = ["MemoryClient", "AsyncMemoryClient"]
