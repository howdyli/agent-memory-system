"""
LLM Backend — Abstract interface and concrete implementations for LLM providers.

Core-layer LLM abstraction. No DB-backed config management (that belongs to Server).
Provides ABC + OpenAI/Claude/Local implementations + factory function.

Usage:
    backend = OpenAIBackend({"api_key": "sk-xxx", "model": "gpt-4"})
    result = backend.chat([{"role": "user", "content": "Hello"}])
    embedding = backend.embed("test text")
"""

import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────
# LLMBackend ABC
# ─────────────────────────────────────────────────────────────────

class LLMBackend(ABC):
    """Abstract interface for LLM backends.

    All implementations must support:
    - chat(): synchronous chat completion
    - chat_stream(): streaming chat completion
    - embed(): text embedding
    - get_info(): backend metadata

    Implementations should gracefully degrade to mock responses
    when API keys are missing or services are unreachable.
    """

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """Send a chat completion request.

        Args:
            messages: List of message dicts with "role" and "content".
            **kwargs: Override model, temperature, max_tokens, tools, etc.

        Returns:
            Dict with keys: "content", "model", "usage", optionally "tool_calls",
            "mock" (True if mock response), "success" (True if real call).
        """
        ...

    @abstractmethod
    def chat_stream(self, messages: List[Dict[str, str]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        """Stream a chat completion, yielding delta events.

        Yields dicts with "type" key:
        - "content_delta": partial text content
        - "tool_call_delta": partial tool call
        - "finish": completion with "finish_reason"
        - "error": error event
        """
        ...

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """Generate an embedding vector for text.

        Returns:
            List of float values (embedding vector). Empty list on failure.
        """
        ...

    @abstractmethod
    def get_info(self) -> Dict[str, Any]:
        """Return backend metadata (name, model, configured status)."""
        ...


# ─────────────────────────────────────────────────────────────────
# OpenAI Backend
# ─────────────────────────────────────────────────────────────────

class OpenAIBackend(LLMBackend):
    """OpenAI-compatible backend (also works for DeepSeek, custom OpenAI APIs).

    Features:
    - Lazy client initialization with connection reuse
    - Retry with exponential backoff (max 3 attempts)
    - Graceful mock fallback when API key missing or library unavailable
    - Streaming support via openai stream=True
    """

    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gpt-4")
        self.embedding_model = config.get("embedding_model", "text-embedding-ada-002")
        self.temperature = config.get("temperature", 0.7)
        self.max_tokens = config.get("max_tokens", 8192)
        self.base_url = config.get("base_url", "https://api.openai.com/v1")
        self._client = None

    def _get_client(self):
        """Lazy-init and reuse the OpenAI client."""
        if self._client is None:
            import openai
            self._client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=30.0,
            )
        return self._client

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """Chat with retry (exponential backoff, max 3 attempts)."""
        if not self.api_key:
            logger.warning("LLM API Key not configured, using mock response")
            return self._mock_response(messages, "openai")

        max_retries = 3
        last_result = None
        for attempt in range(max_retries):
            result = self._chat_once(messages, **kwargs)
            if result.get("success") or result.get("mock"):
                return result
            last_result = result
            if attempt < max_retries - 1:
                retry_after = result.get("retry_after")
                wait_time = min(retry_after or (2 ** attempt), 60)
                logger.warning(
                    f"OpenAI request failed (attempt {attempt+1}/{max_retries}): "
                    f"{result.get('error')}, retrying in {wait_time}s"
                )
                time.sleep(wait_time)

        logger.error(f"OpenAI request failed after {max_retries} retries")
        last_result["retries_exhausted"] = True
        return last_result

    def _chat_once(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """Single chat request (no retry logic)."""
        try:
            client = self._get_client()

            tools = kwargs.get("tools")
            api_kwargs = {
                "model": kwargs.get("model", self.model),
                "messages": messages,
                "temperature": kwargs.get("temperature", self.temperature),
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            }
            if tools:
                api_kwargs["tools"] = tools

            response = client.chat.completions.create(**api_kwargs)

            choice = response.choices[0]
            result = {
                "success": True,
                "content": choice.message.content,
                "model": response.model,
                "finish_reason": choice.finish_reason,
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                    "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                    "total_tokens": response.usage.total_tokens if response.usage else 0,
                },
            }

            if choice.message.tool_calls:
                result["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": tc.type,
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in choice.message.tool_calls
                ]

            return result

        except ImportError:
            logger.warning("openai library not installed, returning mock response")
            return self._mock_response(messages, "openai")
        except Exception as e:
            logger.warning(f"OpenAI request error: {e}")
            error_info = {"success": False, "error": str(e)}
            if hasattr(e, 'response') and e.response is not None:
                headers = getattr(e.response, 'headers', {})
                retry_after = headers.get('retry-after')
                if retry_after:
                    error_info["retry_after"] = float(retry_after)
            return error_info

    def chat_stream(self, messages: List[Dict[str, str]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        """Stream chat with retry."""
        if not self.api_key:
            logger.warning("LLM API Key not configured, using mock stream")
            mock = self._mock_response(messages, "openai")
            yield {"type": "content_delta", "content": mock["content"]}
            yield {"type": "finish", "finish_reason": "stop"}
            return

        try:
            client = self._get_client()
            api_kwargs = {
                "model": kwargs.get("model", self.model),
                "messages": messages,
                "temperature": kwargs.get("temperature", self.temperature),
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "stream": True,
            }
            if kwargs.get("tools"):
                api_kwargs["tools"] = kwargs["tools"]

            stream = client.chat.completions.create(**api_kwargs)

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if delta.content:
                    yield {"type": "content_delta", "content": delta.content}
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        yield {
                            "type": "tool_call_delta",
                            "index": tc.index,
                            "id": tc.id,
                            "function_name": tc.function.name if tc.function else None,
                            "arguments_delta": tc.function.arguments if tc.function else None,
                        }
                if chunk.choices[0].finish_reason:
                    yield {"type": "finish", "finish_reason": chunk.choices[0].finish_reason}

        except ImportError:
            mock = self._mock_response(messages, "openai")
            yield {"type": "content_delta", "content": mock["content"]}
            yield {"type": "finish", "finish_reason": "stop"}
        except Exception as e:
            logger.error(f"OpenAI stream error: {e}")
            yield {"type": "error", "error": str(e)}

    def embed(self, text: str) -> List[float]:
        """Generate embedding vector."""
        if not self.api_key:
            logger.warning("LLM API Key not configured, using mock embedding")
            return self._mock_embedding(text)

        try:
            client = self._get_client()
            response = client.embeddings.create(
                model=self.embedding_model,
                input=text,
            )
            return response.data[0].embedding
        except ImportError:
            return self._mock_embedding(text)
        except Exception as e:
            logger.error(f"OpenAI embedding error: {e}")
            return []

    def get_info(self) -> Dict[str, Any]:
        return {
            "backend": "openai",
            "model": self.model,
            "embedding_model": self.embedding_model,
            "base_url": self.base_url,
            "configured": bool(self.api_key),
        }

    # ── Mock helpers ────────────────────────────────────────────

    def _mock_response(self, messages: List[Dict[str, str]], backend: str) -> Dict[str, Any]:
        last_msg = messages[-1]["content"] if messages else ""
        return {
            "success": True,
            "content": f"[{backend} mock] I received: {last_msg[:100]}...",
            "model": self.model,
            "usage": {
                "prompt_tokens": len(last_msg) // 4,
                "completion_tokens": 20,
                "total_tokens": len(last_msg) // 4 + 20,
            },
            "mock": True,
        }

    def _mock_embedding(self, text: str) -> List[float]:
        hash_val = hashlib.md5(text.encode()).hexdigest()
        return [int(hash_val[i:i2], 16) / 0xFFFFFFFF
                for i, i2 in zip(range(0, 64, 8), range(8, 72, 8))]


# ─────────────────────────────────────────────────────────────────
# Claude Backend
# ─────────────────────────────────────────────────────────────────

class ClaudeBackend(LLMBackend):
    """Anthropic Claude backend.

    Note: Claude does not provide a native embedding API — uses MD5 hash fallback.
    """

    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "claude-3-sonnet-20240229")
        self.max_tokens = config.get("max_tokens", 8192)
        self.temperature = config.get("temperature", 0.7)

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)

            system_msg = ""
            claude_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_msg = msg["content"]
                else:
                    claude_messages.append(msg)

            response = client.messages.create(
                model=kwargs.get("model", self.model),
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
                temperature=kwargs.get("temperature", self.temperature),
                system=system_msg,
                messages=claude_messages,
            )
            return {
                "success": True,
                "content": response.content[0].text,
                "model": response.model,
                "usage": {
                    "prompt_tokens": response.usage.input_tokens,
                    "completion_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
                },
            }
        except ImportError:
            logger.warning("anthropic library not installed, returning mock")
            last_msg = messages[-1]["content"] if messages else ""
            return {
                "success": True,
                "content": f"[claude mock] I received: {last_msg[:100]}...",
                "model": self.model,
                "usage": {"prompt_tokens": len(last_msg) // 4, "completion_tokens": 20,
                          "total_tokens": len(last_msg) // 4 + 20},
                "mock": True,
            }
        except Exception as e:
            logger.error(f"Claude request error: {e}")
            return {"success": False, "error": str(e)}

    def chat_stream(self, messages: List[Dict[str, str]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        result = self.chat(messages, **kwargs)
        if result.get("success") and result.get("content"):
            content = result["content"]
            for i in range(0, len(content), 4):
                yield {"type": "content_delta", "content": content[i:i + 4]}
            yield {"type": "finish", "finish_reason": "stop"}
        elif not result.get("success"):
            yield {"type": "error", "error": result.get("error", "Unknown error")}

    def embed(self, text: str) -> List[float]:
        # Claude has no embedding API — use hash-based mock
        hash_val = hashlib.md5(text.encode()).hexdigest()
        return [int(hash_val[i:i+4], 16) / 0xFFFF for i in range(0, 32, 4)]

    def get_info(self) -> Dict[str, Any]:
        return {"backend": "claude", "model": self.model, "configured": bool(self.api_key)}


# ─────────────────────────────────────────────────────────────────
# Local Model Backend
# ─────────────────────────────────────────────────────────────────

class LocalModelBackend(LLMBackend):
    """Local model backend (Llama, ChatGLM, etc.) via HTTP API."""

    def __init__(self, config: Dict[str, Any]):
        self.model_path = config.get("model_path", "")
        self.model_type = config.get("model_type", "llama")
        self.api_url = config.get("api_url", "http://localhost:8080")
        self.max_tokens = config.get("max_tokens", 8192)
        self.temperature = config.get("temperature", 0.7)

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        try:
            import urllib.request
            import urllib.error

            payload = json.dumps({
                "model": self.model_type,
                "messages": messages,
                "temperature": kwargs.get("temperature", self.temperature),
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.api_url}/v1/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json"},
            )

            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    return {
                        "success": True,
                        "content": result["choices"][0]["message"]["content"],
                        "model": result.get("model", self.model_type),
                        "usage": result.get("usage", {}),
                    }
            except (urllib.error.URLError, ConnectionError):
                logger.warning(f"Local model unreachable ({self.api_url}), mock fallback")
                last_msg = messages[-1]["content"] if messages else ""
                return {
                    "success": True,
                    "content": f"[local mock] I received: {last_msg[:100]}...",
                    "model": self.model_type,
                    "usage": {"prompt_tokens": len(last_msg) // 4, "completion_tokens": 20,
                              "total_tokens": len(last_msg) // 4 + 20},
                    "mock": True,
                }
        except Exception as e:
            logger.error(f"Local model request error: {e}")
            return {"success": False, "error": str(e)}

    def chat_stream(self, messages: List[Dict[str, str]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        result = self.chat(messages, **kwargs)
        if result.get("success") and result.get("content"):
            content = result["content"]
            for i in range(0, len(content), 4):
                yield {"type": "content_delta", "content": content[i:i + 4]}
            yield {"type": "finish", "finish_reason": "stop"}
        elif not result.get("success"):
            yield {"type": "error", "error": result.get("error", "Unknown error")}

    def embed(self, text: str) -> List[float]:
        try:
            import urllib.request
            payload = json.dumps({"input": text}).encode("utf-8")
            req = urllib.request.Request(
                f"{self.api_url}/v1/embeddings",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    return result["data"][0]["embedding"]
            except Exception:
                hash_val = hashlib.md5(text.encode()).hexdigest()
                return [int(hash_val[i:i2], 16) / 0xFFFFFFFF
                        for i, i2 in zip(range(0, 64, 8), range(8, 72, 8))]
        except Exception:
            return []

    def get_info(self) -> Dict[str, Any]:
        return {
            "backend": "local",
            "model_type": self.model_type,
            "model_path": self.model_path,
            "api_url": self.api_url,
            "configured": bool(self.api_url),
        }


# ─────────────────────────────────────────────────────────────────
# Backend Registry & Factory
# ─────────────────────────────────────────────────────────────────

BACKEND_REGISTRY: Dict[str, type] = {
    "openai": OpenAIBackend,
    "deepseek": OpenAIBackend,  # DeepSeek is OpenAI-compatible
    "custom": OpenAIBackend,    # Any OpenAI-compatible endpoint
    "claude": ClaudeBackend,
    "anthropic": ClaudeBackend,
    "local": LocalModelBackend,
}


def create_backend(
    backend_type: str = "openai",
    config: Optional[Dict[str, Any]] = None,
) -> LLMBackend:
    """Factory: create an LLMBackend instance by type.

    Args:
        backend_type: One of BACKEND_REGISTRY keys.
        config: Backend configuration dict (api_key, model, etc.).

    Returns:
        LLMBackend instance.

    Raises:
        ValueError: If backend_type is not in registry.
    """
    if backend_type not in BACKEND_REGISTRY:
        raise ValueError(
            f"Unknown backend type: {backend_type}. "
            f"Supported: {list(BACKEND_REGISTRY.keys())}"
        )
    cls = BACKEND_REGISTRY[backend_type]
    return cls(config or {})


def mask_api_key(key: Optional[str]) -> str:
    """Mask an API key for display (show first 4 and last 4 chars)."""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"
