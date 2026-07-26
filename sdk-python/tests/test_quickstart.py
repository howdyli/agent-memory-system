"""Tests for quickstart() factory and presets (W3-F3.1 / F3.2)."""

import importlib

import pytest

from agent_memory import MemoryClient, quickstart
from agent_memory.presets import DEFAULT_PRESET, PRESETS, get_preset
from agent_memory.quickstart import DEFAULT_DB_PATH, ENV_API_KEY, ENV_URL, _prepare_embedded_db
from agent_memory.transport import EmbeddedTransport, HttpTransport

# 注意：包属性 agent_memory.quickstart 已被 __init__ 中的同名函数覆盖，
# 需用 importlib 获取模块对象
qs_mod = importlib.import_module("agent_memory.quickstart")

# 在 autouse fixture 替换前保存真实实现，供目录创建测试使用
_REAL_PREPARE = _prepare_embedded_db


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """默认清空环境变量，避免宿主环境干扰。"""
    monkeypatch.delenv(ENV_URL, raising=False)
    monkeypatch.delenv(ENV_API_KEY, raising=False)


@pytest.fixture(autouse=True)
def _no_sqlite_preinit(monkeypatch):
    """跳过 SQLite 单例预置，避免测试进程切换全局 db 路径。"""
    monkeypatch.setattr(qs_mod, "_prepare_embedded_db", lambda p, user_id=1: None)


# ==================== Presets ====================

class TestPresets:
    def test_three_presets_exist(self):
        assert set(PRESETS) == {"chatbot", "knowledge", "assistant"}
        assert DEFAULT_PRESET == "assistant"

    def test_preset_values_match_prd(self):
        assert PRESETS["chatbot"]["recall_top_k"] == 5
        assert PRESETS["chatbot"]["semantic_threshold"] == 0.4
        assert PRESETS["chatbot"]["plan_half_life_days"] == 30
        assert PRESETS["knowledge"]["recall_top_k"] == 10
        assert PRESETS["knowledge"]["semantic_threshold"] == 0.2
        assert PRESETS["knowledge"]["preference_half_life_days"] == 30
        assert PRESETS["assistant"]["semantic_threshold"] == 0.3
        assert PRESETS["assistant"]["plan_half_life_days"] == 90

    def test_get_preset_returns_copy(self):
        p = get_preset("chatbot")
        p["recall_top_k"] = 999
        assert PRESETS["chatbot"]["recall_top_k"] == 5

    def test_get_preset_unknown_raises(self):
        with pytest.raises(ValueError, match="未知预设"):
            get_preset("nonexistent")


# ==================== quickstart() ====================

class TestQuickstartEmbedded:
    def test_zero_config_returns_embedded_client(self):
        mem = quickstart()
        assert isinstance(mem, MemoryClient)
        assert isinstance(mem._transport, EmbeddedTransport)
        # 默认 db 路径
        assert mem._transport.db_path == str(DEFAULT_DB_PATH)

    def test_default_preset_applied(self):
        mem = quickstart()
        assert mem.settings["recall_top_k"] == 5
        assert mem.settings["semantic_threshold"] == 0.3

    def test_custom_preset_applied(self):
        mem = quickstart(preset="knowledge")
        assert mem.settings["recall_top_k"] == 10
        assert mem.settings["semantic_threshold"] == 0.2

    def test_overrides_win_over_preset(self):
        mem = quickstart(preset="chatbot", recall_top_k=42)
        assert mem.settings["recall_top_k"] == 42
        assert mem.settings["semantic_threshold"] == 0.4  # 其余保持预设

    def test_custom_db_path(self, tmp_path):
        db = tmp_path / "custom.db"
        mem = quickstart(db_path=str(db))
        assert mem._transport.db_path == str(db)

    def test_unknown_preset_raises(self):
        with pytest.raises(ValueError, match="未知预设"):
            quickstart(preset="bogus")

    def test_db_dir_auto_created(self, tmp_path, monkeypatch):
        """真实实现自动创建数据库目录（屏蔽 backend 导入副作用）。"""
        import sys

        monkeypatch.setattr(qs_mod, "_prepare_embedded_db", _REAL_PREPARE)
        # 让 from app.core.db_client import ... 抛 ImportError，避免切换全局 SQLite 单例
        monkeypatch.setitem(sys.modules, "app.core.db_client", None)
        db = tmp_path / "nested" / "dir" / "mem.db"
        quickstart(db_path=str(db))
        assert db.parent.is_dir()


class TestQuickstartHttp:
    def test_env_url_switches_to_http(self, monkeypatch):
        monkeypatch.setenv(ENV_URL, "http://example.com/api/v1")
        monkeypatch.setenv(ENV_API_KEY, "amk_test123")
        mem = quickstart()
        assert isinstance(mem._transport, HttpTransport)

    def test_env_url_without_key(self, monkeypatch):
        monkeypatch.setenv(ENV_URL, "http://example.com/api/v1")
        mem = quickstart()
        assert isinstance(mem._transport, HttpTransport)

    def test_blank_env_url_falls_back_to_embedded(self, monkeypatch):
        monkeypatch.setenv(ENV_URL, "   ")
        mem = quickstart()
        assert isinstance(mem._transport, EmbeddedTransport)

    def test_preset_applied_in_http_mode(self, monkeypatch):
        monkeypatch.setenv(ENV_URL, "http://example.com/api/v1")
        mem = quickstart(preset="knowledge")
        assert mem.settings["recall_top_k"] == 10


# ==================== configure() ====================

class TestConfigure:
    def _client(self):
        return MemoryClient(mode="embedded", db_path="test.db")

    def test_defaults(self):
        c = self._client()
        assert c.settings == {
            "recall_top_k": 5,
            "semantic_threshold": 0.3,
            "preference_half_life_days": 1,
            "plan_half_life_days": 90,
        }

    def test_configure_overrides_and_chains(self):
        c = self._client()
        assert c.configure(recall_top_k=8) is c
        assert c.settings["recall_top_k"] == 8

    def test_configure_unknown_key_raises(self):
        with pytest.raises(ValueError, match="未知配置项"):
            self._client().configure(bogus_key=1)

    def test_configure_ignores_none(self):
        c = self._client().configure(recall_top_k=None)
        assert c.settings["recall_top_k"] == 5

    def test_settings_returns_copy(self):
        c = self._client()
        c.settings["recall_top_k"] = 999
        assert c.settings["recall_top_k"] == 5

    def test_search_uses_settings(self, monkeypatch):
        c = self._client().configure(recall_top_k=7, semantic_threshold=0.15)
        captured = {}

        def fake_search(query, top_k=None, threshold=None):
            captured.update(top_k=top_k, threshold=threshold)
            return []

        monkeypatch.setattr(c.fragments, "semantic_search", fake_search)
        c.search("test")
        assert captured == {"top_k": 7, "threshold": 0.15}

    def test_search_explicit_args_win(self, monkeypatch):
        c = self._client().configure(recall_top_k=7)
        captured = {}

        def fake_search(query, top_k=None, threshold=None):
            captured.update(top_k=top_k, threshold=threshold)
            return []

        monkeypatch.setattr(c.fragments, "semantic_search", fake_search)
        c.search("test", top_k=3, threshold=0.9)
        assert captured == {"top_k": 3, "threshold": 0.9}
