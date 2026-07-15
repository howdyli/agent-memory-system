"""
EventEmitter — Core-layer synchronous event hooks.

Pure Python callback mechanism with no async/event bus dependency.
Server layer wraps this with EventBus for async + Webhook delivery.

Usage:
    engine = MemoryEngine(...)
    engine.on("fragment_created", my_handler)
    engine.remember_fragment(...)  # triggers "fragment_created" event
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class MemoryEventType(str, Enum):
    """All event types emitted by Core modules."""

    # Variable events
    VARIABLE_SET = "variable_set"
    VARIABLE_DELETED = "variable_deleted"

    # Fragment events
    FRAGMENT_CREATED = "fragment_created"
    FRAGMENT_UPDATED = "fragment_updated"
    FRAGMENT_DELETED = "fragment_deleted"
    FRAGMENT_EXPIRED = "fragment_expired"

    # Table events
    TABLE_CREATED = "table_created"
    TABLE_DELETED = "table_deleted"
    RECORD_ADDED = "record_added"
    RECORD_UPDATED = "record_updated"
    RECORD_DELETED = "record_deleted"

    # Graph events
    ENTITY_CREATED = "entity_created"
    ENTITY_UPDATED = "entity_updated"
    ENTITY_DELETED = "entity_deleted"
    RELATIONSHIP_CREATED = "relationship_created"
    RELATIONSHIP_DEACTIVATED = "relationship_deactivated"

    # Lifecycle events
    MEMORY_COLD = "memory_cold"
    MEMORY_RESTORED = "memory_restored"
    MEMORY_SOFT_DELETED = "memory_soft_deleted"

    # Recall events
    RECALL_TRIGGERED = "recall_triggered"
    RECALL_COMPLETED = "recall_completed"

    # Search events
    SEARCH_TRIGGERED = "search_triggered"
    SEARCH_COMPLETED = "search_completed"

    # Extraction events
    EXTRACTION_TRIGGERED = "extraction_triggered"
    EXTRACTION_COMPLETED = "extraction_completed"

    # Context events
    CONTEXT_COMPRESSED = "context_compressed"

    # Lifecycle maintenance
    LIFECYCLE_MAINTENANCE = "lifecycle_maintenance"


@dataclass
class MemoryEvent:
    """Event payload emitted by Core modules."""

    event_type: MemoryEventType
    workspace_id: int
    memory_type: Optional[str] = None
    memory_id: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)
    timestamp: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            from datetime import datetime
            self.timestamp = datetime.now().isoformat()


# Type alias for event handlers
EventHandler = Callable[[MemoryEvent], None]


class EventEmitter:
    """Synchronous event emitter for Core-layer hooks.

    - Pure Python, no async/event bus dependency
    - Handlers are called synchronously in registration order
    - Server layer wraps with EventBus for async delivery + Webhook
    - Errors in handlers are logged but do not propagate to the caller

    Usage:
        emitter = EventEmitter()
        emitter.on("fragment_created", lambda e: print(e))
        emitter.emit(MemoryEvent(event_type=MemoryEventType.FRAGMENT_CREATED, ...))
    """

    def __init__(self):
        self._handlers: Dict[str, List[EventHandler]] = {}

    def on(self, event_type: str, handler: EventHandler) -> None:
        """Register a handler for an event type.

        Args:
            event_type: Event type name (e.g. "fragment_created")
            handler: Callback function receiving MemoryEvent
        """
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def off(self, event_type: str, handler: EventHandler) -> None:
        """Remove a handler for an event type."""
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h != handler
            ]

    def emit(self, event: MemoryEvent) -> None:
        """Emit an event to all registered handlers.

        Handlers are called synchronously. Errors are caught and logged.
        """
        event_type = event.event_type.value if isinstance(event.event_type, MemoryEventType) else str(event.event_type)
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # Log but don't propagate — emitter should never break business logic
                import logging
                logging.getLogger(__name__).exception(
                    f"Event handler error for {event_type}: {handler}"
                )

    def has_handlers(self, event_type: str) -> bool:
        """Check if any handlers are registered for an event type."""
        return bool(self._handlers.get(event_type))

    def clear(self) -> None:
        """Remove all handlers."""
        self._handlers.clear()
