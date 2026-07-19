"""Core modules — business logic extracted from backend/services/."""

from .variables import VariableManager
from .fragments import FragmentManager
from .tables import TableManager
from .observability import ObservabilityManager
from .graph import GraphManager
from .lifecycle import LifecycleManager
from .recall import RecallManager, RecallResult
from .llm_backend import LLMBackend, OpenAIBackend, ClaudeBackend, LocalModelBackend, create_backend
from .security import SecurityManager, RateLimiter
from .extraction import ExtractionManager, ExtractionResult
from .compression import (
    ContextCompressor,
    MemoryValueScorer,
    EntityExtractor,
    LifecycleHalfLifeCalculator,
    estimate_tokens,
)
from .search import HybridSearchManager

__all__ = [
    # Core business modules
    "VariableManager",
    "FragmentManager",
    "TableManager",
    "ObservabilityManager",
    "GraphManager",
    "LifecycleManager",
    "RecallManager",
    "RecallResult",
    # LLM abstraction
    "LLMBackend",
    "OpenAIBackend",
    "ClaudeBackend",
    "LocalModelBackend",
    "create_backend",
    # Security
    "SecurityManager",
    "RateLimiter",
    # Extraction
    "ExtractionManager",
    "ExtractionResult",
    # Compression & context
    "ContextCompressor",
    "MemoryValueScorer",
    "EntityExtractor",
    "LifecycleHalfLifeCalculator",
    "estimate_tokens",
    # Search
    "HybridSearchManager",
]
