"""
agent-memory-core: Pure Python memory logic library.

No HTTP/auth dependencies. Can be embedded directly or used via Server.
"""

__version__ = "0.1.0"

from .config import CoreConfig
from .engine import MemoryEngine
from .events import EventEmitter, MemoryEvent, MemoryEventType

__all__ = [
    "CoreConfig",
    "MemoryEngine",
    "EventEmitter",
    "MemoryEvent",
    "MemoryEventType",
]
