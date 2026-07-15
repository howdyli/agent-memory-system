"""Core layer end-to-end tests — MemoryEngine standalone usage."""

import os
import tempfile

from agent_memory_core import MemoryEngine, CoreConfig, MemoryEventType


class TestMemoryEngineE2E:
    """Test MemoryEngine with in-memory SQLite and NullVectorStore."""

    def setup_method(self):
        self.db_path = tempfile.mktemp(suffix=".db")
        self.config = CoreConfig(
            database_url=f"sqlite:///{self.db_path}",
            vector_backend="none",
            cache_backend="fakeredis",
        )
        self.engine = MemoryEngine.from_config(self.config)

    def teardown_method(self):
        self.engine.close()
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    # ── Variable CRUD ───────────────────────────────────────────

    def test_remember_and_recall_variable(self):
        """remember() sets a variable, recall() retrieves it."""
        assert self.engine.remember(1, "name", "Alice") is True

        results = self.engine.recall(1, "name")
        assert len(results) >= 1
        var_result = [r for r in results if r["type"] == "variable"]
        assert len(var_result) == 1
        assert var_result[0]["key"] == "name"
        assert var_result[0]["value"] == "Alice"

    def test_remember_upsert(self):
        """remember() with same key updates value."""
        self.engine.remember(1, "age", "25")
        self.engine.remember(1, "age", "26")

        results = self.engine.recall(1, "age")
        var_result = [r for r in results if r["type"] == "variable"]
        assert var_result[0]["value"] in ("26", 26)

    def test_remember_complex_value(self):
        """remember() with dict/list value auto-serializes."""
        self.engine.remember(1, "projects", ["Project A", "Project B"])

        results = self.engine.recall(1, "projects")
        var_result = [r for r in results if r["type"] == "variable"]
        assert var_result[0]["value"] == ["Project A", "Project B"]

    def test_forget_variable(self):
        """forget() deletes a variable."""
        self.engine.remember(1, "temp", "value")
        assert self.engine.forget(1, "temp") is True

        # After delete, recall should not find it as variable
        results = self.engine.recall(1, "temp")
        var_result = [r for r in results if r["type"] == "variable"]
        assert len(var_result) == 0

    def test_list_variables(self):
        """list_variables returns all variables for a workspace."""
        self.engine.remember(1, "k1", "v1")
        self.engine.remember(1, "k2", "v2")

        vars_list = self.engine.relational_store.list_variables(1)
        assert len(vars_list) >= 2

    # ── Fragment CRUD ───────────────────────────────────────────

    def test_remember_fragment(self):
        """remember_fragment() creates a fragment."""
        fid = self.engine.remember_fragment(1, "Alice likes minimalist design", fragment_type="preference")
        assert isinstance(fid, int)
        assert fid > 0

    def test_get_fragment(self):
        """get_fragment retrieves a fragment by ID."""
        fid = self.engine.remember_fragment(1, "Test content", fragment_type="info")
        fragment = self.engine.relational_store.get_fragment(1, fid)
        assert fragment is not None
        assert fragment["content"] == "Test content"
        assert fragment["fragment_type"] == "info"

    def test_list_fragments(self):
        """list_fragments with type filter."""
        self.engine.remember_fragment(1, "Like coffee", fragment_type="preference")
        self.engine.remember_fragment(1, "Work at company", fragment_type="info")

        prefs = self.engine.relational_store.list_fragments(1, fragment_type="preference")
        assert len(prefs) >= 1
        assert all(f["fragment_type"] == "preference" for f in prefs)

    # ── Recall Engine ───────────────────────────────────────────

    def test_recall_returns_vector_and_fts(self):
        """recall() combines vector + FTS + variable results."""
        self.engine.remember(1, "color", "blue")
        self.engine.remember_fragment(1, "Alice prefers blue color", fragment_type="preference")

        results = self.engine.recall(1, "blue", top_k=5)
        assert len(results) >= 1

    # ── Context Assembly ────────────────────────────────────────

    def test_get_context(self):
        """get_context() returns assembled memory context."""
        self.engine.remember(1, "role", "PM")
        self.engine.remember_fragment(1, "Works on project X", fragment_type="info")

        ctx = self.engine.get_context(1)
        assert ctx["workspace_id"] == 1
        assert len(ctx["variables"]) >= 1
        assert len(ctx["fragments"]) >= 1

    # ── Dynamic Tables ──────────────────────────────────────────

    def test_create_and_query_table(self):
        """Dynamic table CRUD."""
        schema = {"fields": [{"name": "project", "type": "TEXT"}, {"name": "status", "type": "TEXT"}]}
        assert self.engine.relational_store.create_table(1, "projects", schema) is True

        table = self.engine.relational_store.get_table(1, "projects")
        assert table is not None
        assert table["table_name"] == "projects"

    # ── Graph Memory ────────────────────────────────────────────

    def test_ensure_entity(self):
        """Entity creation with upsert."""
        eid = self.engine.relational_store.ensure_entity(1, "Alice", "person")
        assert isinstance(eid, int)
        assert eid > 0

    def test_add_relationship(self):
        """Relationship between entities."""
        eid1 = self.engine.relational_store.ensure_entity(1, "Alice", "person")
        eid2 = self.engine.relational_store.ensure_entity(1, "CompanyX", "organization")
        rid = self.engine.relational_store.add_relationship(1, eid1, eid2, "works_at")
        assert isinstance(rid, int)
        assert rid > 0

    # ── Lifecycle ───────────────────────────────────────────────

    def test_mark_cold_and_restore(self):
        """Lifecycle: cold → active."""
        fid = self.engine.remember_fragment(1, "Old memory", fragment_type="info")
        lc_id = self.engine.relational_store.mark_cold(1, "fragment", str(fid), reason="age")
        assert lc_id > 0

        status = self.engine.relational_store.get_lifecycle_status(1, "fragment", str(fid))
        assert status["lifecycle_status"] == "cold"

        assert self.engine.relational_store.mark_active(1, "fragment", str(fid)) is True

    # ── Event Hooks ─────────────────────────────────────────────

    def test_event_emitter_on_remember(self):
        """remember() emits VARIABLE_SET event."""
        events_received = []
        self.engine.on("variable_set", lambda e: events_received.append(e))

        self.engine.remember(1, "test_key", "test_value")
        assert len(events_received) == 1
        assert events_received[0].event_type == MemoryEventType.VARIABLE_SET
        assert events_received[0].workspace_id == 1

    def test_event_emitter_on_fragment(self):
        """remember_fragment() emits FRAGMENT_CREATED event."""
        events_received = []
        self.engine.on("fragment_created", lambda e: events_received.append(e))

        self.engine.remember_fragment(1, "Event test", fragment_type="info")
        assert len(events_received) == 1
        assert events_received[0].event_type == MemoryEventType.FRAGMENT_CREATED

    # ── Observability ───────────────────────────────────────────

    def test_trace_event(self):
        """Log trace event."""
        tid = self.engine.relational_store.log_trace_event(
            1, event_type="recall", latency_ms=45.2, memory_type="fragment",
        )
        assert tid > 0

    # ── Workspace Isolation ─────────────────────────────────────

    def test_workspace_isolation(self):
        """Different workspaces have separate memories."""
        self.engine.remember(1, "shared_key", "workspace_1_value")
        self.engine.remember(2, "shared_key", "workspace_2_value")

        val1 = self.engine.relational_store.get_variable(1, "shared_key")
        val2 = self.engine.relational_store.get_variable(2, "shared_key")
        assert val1 == "workspace_1_value"
        assert val2 == "workspace_2_value"

    # ── Factory Config ──────────────────────────────────────────

    def test_from_config_defaults(self):
        """from_config() works with default config."""
        engine2 = MemoryEngine.from_config(CoreConfig(database_url=f"sqlite:///{tempfile.mktemp(suffix='.db')}", vector_backend="none"))
        assert engine2.config.database_url.startswith("sqlite")
        engine2.close()
