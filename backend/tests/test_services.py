"""
Comprehensive unit tests for all Agent Memory System services.
Each test calls the embedded test function from the respective service module.
"""
import pytest
import sys
import os

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.mark.unit
class TestMemoryVariables:
    """Memory Variables service tests (Task 4-6)."""

    def test_memory_variable_service(self):
        """Test CRUD operations for memory variables."""
        from app.services.memory_variable_service import test_memory_variable_service
        test_memory_variable_service()


@pytest.mark.unit
class TestMemoryExtraction:
    """Memory Extraction service tests (Task 5)."""

    def test_memory_extraction(self):
        """Test extraction and injection of memory variables."""
        from app.services.memory_extraction_service import test_memory_extraction_service
        test_memory_extraction_service()


@pytest.mark.unit
class TestMemoryTable:
    """Memory Table service tests (Task 7-8)."""

    def test_memory_table_service(self):
        """Test dynamic table creation, CRUD, batch operations."""
        from app.services.memory_table_service import test_memory_table_service
        test_memory_table_service()


@pytest.mark.unit
class TestNaturalLanguageQuery:
    """Natural Language Query service tests (Task 9)."""

    def test_natural_language_query(self):
        """Test NL->SQL conversion and safe query execution."""
        from app.services.natural_language_query_service import test_natural_language_query
        test_natural_language_query()


@pytest.mark.unit
class TestMemoryFragment:
    """Memory Fragment service tests (Task 11-14)."""

    def test_memory_fragment_service(self):
        """Test fragment creation, TTL, semantic search."""
        from app.services.memory_fragment_service import test_memory_fragments
        test_memory_fragments()


@pytest.mark.unit
class TestAutoRecall:
    """Auto Memory Recall service tests (Task 16-20)."""

    def test_auto_recall_service(self):
        """Test auto summary, relevance search, context injection, priority ranking."""
        from app.services.auto_recall_service import test_auto_recall
        test_auto_recall()


@pytest.mark.unit
class TestLongTermMemory:
    """Long-term Memory Management tests (Task 22-24)."""

    def test_long_term_memory(self):
        """Test version control, self-improving, batch operations."""
        from app.services.long_term_memory_service import test_long_term_memory
        test_long_term_memory()


@pytest.mark.unit
class TestLLMBackend:
    """LLM Backend Integration tests (Task 25)."""

    def test_llm_backend(self):
        """Test OpenAI, Claude, and local model backends."""
        from app.services.llm_backend_service import test_llm_backend
        test_llm_backend()


@pytest.mark.unit
class TestPluginService:
    """Plugin Architecture tests (Task 26)."""

    def test_plugin_service(self):
        """Test plugin registration, discovery, enable/disable."""
        from app.services.plugin_service import test_plugin_service
        test_plugin_service()


@pytest.mark.unit
class TestPerformance:
    """Performance Optimization tests (Task 27)."""

    def test_performance_service(self):
        """Test caching, query logging, slow query analysis, batch processing."""
        from app.services.performance_service import test_performance_service
        test_performance_service()


@pytest.mark.unit
class TestSecurity:
    """Security Hardening tests (Task 28)."""

    def test_security_service(self):
        """Test SQL injection detection, XSS, CSRF, rate limiting."""
        from app.services.security_service import test_security_service
        test_security_service()
