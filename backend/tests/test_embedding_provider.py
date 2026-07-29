"""
Embedding Provider 工厂单测（P0：Embedding 可插拔）

覆盖：
- 工厂选择逻辑（default 返回 None / local 返回 provider）
- 集合命名后缀（default 空串 / local 按模型 slug）
- BGE 查询指令前缀（zh / en / 非 bge 模型）
- provider 进程内单例与配置变化重建
- default 模式行为不变（ChromaDBClient 集合名不带后缀）
- local 模式实际嵌入（@pytest.mark.slow，需模型下载）
"""
import pytest

from app.core.config import get_settings
from app.core.embedding import (
    ChromaEmbeddingFunction,
    LocalEmbeddingProvider,
    _model_slug,
    get_collection_suffix,
    get_embedding_function,
    get_embedding_provider,
    reset_embedding_provider,
)


@pytest.fixture(autouse=True)
def _clean_embedding_state(monkeypatch):
    """每个用例前后清理 settings 缓存与 provider 单例，避免串扰。"""
    get_settings.cache_clear()
    reset_embedding_provider()
    yield
    get_settings.cache_clear()
    reset_embedding_provider()


def _set_provider(monkeypatch, provider: str, model: str = None):
    monkeypatch.setenv("EMBEDDING_PROVIDER", provider)
    if model:
        monkeypatch.setenv("EMBEDDING_MODEL", model)
    get_settings.cache_clear()
    reset_embedding_provider()


# ===== 工厂选择逻辑 =====

@pytest.mark.unit
def test_default_provider_returns_none(monkeypatch):
    _set_provider(monkeypatch, "default")
    assert get_embedding_provider() is None
    assert get_embedding_function() is None


@pytest.mark.unit
def test_local_provider_returns_instance(monkeypatch):
    _set_provider(monkeypatch, "local", "BAAI/bge-small-zh-v1.5")
    provider = get_embedding_provider()
    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.model_name == "BAAI/bge-small-zh-v1.5"
    # 不触发模型加载（懒加载）
    assert provider._model is None
    ef = get_embedding_function()
    assert isinstance(ef, ChromaEmbeddingFunction)
    assert ef.name() == "local:BAAI/bge-small-zh-v1.5"


@pytest.mark.unit
def test_provider_singleton_and_rebuild_on_config_change(monkeypatch):
    _set_provider(monkeypatch, "local", "BAAI/bge-small-zh-v1.5")
    p1 = get_embedding_provider()
    p2 = get_embedding_provider()
    assert p1 is p2  # 进程内单例

    # 配置变化后重建
    _set_provider(monkeypatch, "local", "BAAI/bge-small-en-v1.5")
    p3 = get_embedding_provider()
    assert p3 is not p1
    assert p3.model_name == "BAAI/bge-small-en-v1.5"


@pytest.mark.unit
def test_provider_case_insensitive(monkeypatch):
    _set_provider(monkeypatch, "LOCAL", "BAAI/bge-small-zh-v1.5")
    assert get_embedding_provider() is not None


# ===== 集合命名 =====

@pytest.mark.unit
def test_collection_suffix_default_empty(monkeypatch):
    _set_provider(monkeypatch, "default")
    assert get_collection_suffix() == ""


@pytest.mark.unit
def test_collection_suffix_local(monkeypatch):
    _set_provider(monkeypatch, "local", "BAAI/bge-small-zh-v1.5")
    assert get_collection_suffix() == "__bge-small-zh-v1-5"


@pytest.mark.unit
def test_model_slug():
    assert _model_slug("BAAI/bge-small-zh-v1.5") == "bge-small-zh-v1-5"
    assert _model_slug("BAAI/bge-small-en-v1.5") == "bge-small-en-v1-5"
    assert _model_slug("sentence-transformers/all-MiniLM-L6-v2") == "all-minilm-l6-v2"


# ===== BGE 查询指令 =====

@pytest.mark.unit
def test_query_instruction_bge_zh():
    p = LocalEmbeddingProvider("BAAI/bge-small-zh-v1.5")
    assert p.query_instruction == "为这个句子生成表示以用于检索相关文章："


@pytest.mark.unit
def test_query_instruction_bge_en():
    p = LocalEmbeddingProvider("BAAI/bge-small-en-v1.5")
    assert p.query_instruction.startswith("Represent this sentence")


@pytest.mark.unit
def test_query_instruction_non_bge():
    p = LocalEmbeddingProvider("sentence-transformers/all-MiniLM-L6-v2")
    assert p.query_instruction == ""


# ===== default 模式行为不变 =====

@pytest.mark.unit
def test_default_mode_collection_name_unchanged(monkeypatch):
    """default 模式下 ChromaDBClient 集合名保持历史值（零回归）。"""
    _set_provider(monkeypatch, "default")
    from app.core.chromadb_client import ChromaDBClient

    client = ChromaDBClient.__new__(ChromaDBClient)  # 不触发真实初始化
    client.collection_name = "memory_fragments"
    suffix = get_collection_suffix()
    assert suffix == ""
    assert f"{client.collection_name}{suffix}" == "memory_fragments"


# ===== local 模式实际嵌入（需模型下载，标记 slow）=====

@pytest.mark.slow
def test_local_embedding_real_model(monkeypatch):
    pytest.importorskip("sentence_transformers")
    _set_provider(monkeypatch, "local", "BAAI/bge-small-zh-v1.5")
    provider = get_embedding_provider()

    docs = provider.embed_documents(["我喜欢喝美式咖啡", "项目使用 FastAPI 框架"])
    assert len(docs) == 2
    assert len(docs[0]) == 512  # bge-small 维度

    query = provider.embed_query("用户的咖啡偏好是什么")
    assert len(query) == 512

    # 查询向量与相关文档的相似度应高于不相关文档
    def dot(a, b):
        return sum(x * y for x, y in zip(a, b))

    assert dot(query, docs[0]) > dot(query, docs[1])
