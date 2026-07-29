"""
Embedding Provider 工厂（P0：Embedding 可插拔）

EMBEDDING_PROVIDER=default 时返回 None，ChromaDB 使用内置嵌入函数
（all-MiniLM-L6-v2），与历史行为完全一致；
EMBEDDING_PROVIDER=local 时使用 sentence-transformers 本地模型
（默认 BAAI/bge-small-zh-v1.5），中文检索质量显著优于内置模型。

关键设计：
- 不同模型的向量维度不同，通过 get_collection_suffix() 让每个模型使用
  独立的 Chroma 集合（如 memory_fragments__bge-small-zh-v1-5），
  切换模型后用 scripts/reindex_vectors.py 重建索引，天然规避维度冲突。
- bge 中文系列模型的检索约定：查询侧需加指令前缀，文档侧不加。
  Chroma 的 EmbeddingFunction 协议无法区分查询/文档，因此文档侧走
  collection 的 embedding_function，查询侧由 ChromaDBClient 调用
  provider.embed_query() 预计算向量。
"""
from __future__ import annotations

import logging
import re
import threading
from typing import List, Optional

logger = logging.getLogger(__name__)

# bge 中文系列检索查询指令（官方推荐，仅查询侧使用）
_BGE_ZH_QUERY_INSTRUCTION = "为这个句子生成表示以用于检索相关文章："
# bge 英文系列检索查询指令（v1.5 官方说明可省略，保留以对齐官方评测设置）
_BGE_EN_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

_lock = threading.Lock()
_provider_instance: Optional["LocalEmbeddingProvider"] = None
_provider_cache_key: Optional[str] = None


class LocalEmbeddingProvider:
    """sentence-transformers 本地嵌入模型封装（懒加载）。"""

    def __init__(self, model_name: str, device: str = "cpu", batch_size: int = 32):
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self._model = None
        self._model_lock = threading.Lock()

    def _get_model(self):
        """懒加载模型（首次调用时下载/加载，进程内单例）。"""
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    try:
                        from sentence_transformers import SentenceTransformer
                    except ImportError:
                        raise ImportError(
                            "sentence-transformers 未安装，无法使用 local Embedding。"
                            "请执行: pip install sentence-transformers"
                        )
                    logger.info(f"加载本地 Embedding 模型: {self.model_name} (device={self.device})")
                    self._model = SentenceTransformer(self.model_name, device=self.device)
                    logger.info(f"✓ Embedding 模型加载完成，维度: {self._model.get_sentence_embedding_dimension()}")
        return self._model

    @property
    def query_instruction(self) -> str:
        """按模型系列返回查询指令前缀（非 bge 检索模型返回空串）。"""
        name = self.model_name.lower()
        if "bge" in name and "-zh" in name:
            return _BGE_ZH_QUERY_INSTRUCTION
        if "bge" in name and "-en" in name:
            return _BGE_EN_QUERY_INSTRUCTION
        return ""

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """文档嵌入（不加指令前缀）。"""
        model = self._get_model()
        vectors = model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> List[float]:
        """查询嵌入（bge 系列自动加检索指令前缀）。"""
        model = self._get_model()
        vector = model.encode(
            self.query_instruction + text,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return vector.tolist()


class ChromaEmbeddingFunction:
    """Chroma EmbeddingFunction 协议包装（文档侧嵌入）。"""

    def __init__(self, provider: LocalEmbeddingProvider):
        self._provider = provider

    def __call__(self, input) -> List[List[float]]:  # noqa: A002 — Chroma 协议要求参数名为 input
        return self._provider.embed_documents(list(input))

    def name(self) -> str:
        return f"local:{self._provider.model_name}"


def _model_slug(model_name: str) -> str:
    """模型名转集合后缀 slug（BAAI/bge-small-zh-v1.5 → bge-small-zh-v1-5）。"""
    tail = model_name.split("/")[-1]
    return re.sub(r"[^a-zA-Z0-9]+", "-", tail).strip("-").lower()


def get_embedding_provider() -> Optional[LocalEmbeddingProvider]:
    """返回当前配置的 Embedding Provider（default 模式返回 None）。

    进程内按 (provider, model, device) 缓存单例，配置变化后自动重建。
    """
    global _provider_instance, _provider_cache_key
    from app.core.config import get_settings

    settings = get_settings()
    if settings.EMBEDDING_PROVIDER.lower() != "local":
        return None

    cache_key = f"local|{settings.EMBEDDING_MODEL}|{settings.EMBEDDING_DEVICE}"
    if _provider_instance is None or _provider_cache_key != cache_key:
        with _lock:
            if _provider_instance is None or _provider_cache_key != cache_key:
                _provider_instance = LocalEmbeddingProvider(
                    model_name=settings.EMBEDDING_MODEL,
                    device=settings.EMBEDDING_DEVICE,
                    batch_size=settings.EMBEDDING_BATCH_SIZE,
                )
                _provider_cache_key = cache_key
    return _provider_instance


def get_embedding_function() -> Optional[ChromaEmbeddingFunction]:
    """返回 Chroma 兼容的嵌入函数（default 模式返回 None，走 Chroma 内置）。"""
    provider = get_embedding_provider()
    if provider is None:
        return None
    return ChromaEmbeddingFunction(provider)


def get_collection_suffix() -> str:
    """返回集合名后缀：local 模式为 __<model-slug>，default 模式为空串。"""
    from app.core.config import get_settings

    settings = get_settings()
    if settings.EMBEDDING_PROVIDER.lower() != "local":
        return ""
    return f"__{_model_slug(settings.EMBEDDING_MODEL)}"


def reset_embedding_provider() -> None:
    """清除进程内 provider 缓存（测试/配置热切换用）。"""
    global _provider_instance, _provider_cache_key
    with _lock:
        _provider_instance = None
        _provider_cache_key = None


__all__ = [
    "LocalEmbeddingProvider",
    "ChromaEmbeddingFunction",
    "get_embedding_provider",
    "get_embedding_function",
    "get_collection_suffix",
    "reset_embedding_provider",
]
