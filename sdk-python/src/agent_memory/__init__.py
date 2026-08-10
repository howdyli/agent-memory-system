"""Agent Memory SDK — unified client for HTTP and embedded modes."""

__version__ = "0.1.1"

from agent_memory.client import MemoryClient
from agent_memory.async_client import AsyncMemoryClient
from agent_memory.quickstart import quickstart
from agent_memory.presets import PRESETS, get_preset

__all__ = ["MemoryClient", "AsyncMemoryClient", "quickstart", "PRESETS", "get_preset"]
