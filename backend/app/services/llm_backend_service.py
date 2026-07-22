"""
LLM 后端集成服务

支持多种 LLM 后端（OpenAI、Claude、本地模型），提供统一接口和动态切换
"""
import logging
import json
import os
import hashlib
import time
from typing import Optional, Dict, Any, List, Generator
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client


# ============================================================
# 抽象 LLM 后端接口
# ============================================================

class LLMBackend(ABC):
    """LLM 后端抽象基类"""

    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """发送聊天请求"""
        pass

    @abstractmethod
    def chat_stream(self, messages: List[Dict[str, str]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        """流式聊天请求，逐 token yield"""
        pass

    @abstractmethod
    def embed(self, text: str) -> List[float]:
        """生成文本嵌入向量"""
        pass

    @abstractmethod
    def get_info(self) -> Dict[str, Any]:
        """获取后端信息"""
        pass


# ============================================================
# OpenAI 后端
# ============================================================

class OpenAIBackend(LLMBackend):
    """OpenAI GPT 后端"""

    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "gpt-4")
        self.embedding_model = config.get("embedding_model", "text-embedding-ada-002")
        self.temperature = config.get("temperature", 0.7)
        self.max_tokens = config.get("max_tokens", 8192)
        self.base_url = config.get("base_url", "https://api.openai.com/v1")
        self._client = None

    def _get_client(self):
        """懒初始化并复用 OpenAI client，超时从配置读取"""
        if self._client is None:
            import openai
            from app.core.config import get_settings
            timeout = get_settings().LLM_TIMEOUT_SECONDS
            self._client = openai.OpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=float(timeout),
            )
        return self._client

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """发送聊天请求（含 retry 指数退避）"""
        max_retries = 3
        last_result = None
        for attempt in range(max_retries):
            result = self._chat_once(messages, **kwargs)
            if result.get("success") or result.get("mock"):
                return result
            last_result = result
            if attempt < max_retries - 1:
                retry_after = result.get("retry_after")
                if retry_after:
                    wait_time = min(retry_after, 60)
                    logger.warning(f"⚠ 429 rate-limit, 等待 {wait_time}s 后重试 (attempt {attempt+1}/{max_retries})")
                else:
                    wait_time = 2 ** attempt
                    logger.warning(f"⚠ OpenAI 请求失败 (attempt {attempt+1}/{max_retries}): {result.get('error')}, {wait_time}s 后重试")
                time.sleep(wait_time)
        logger.error(f"✗ OpenAI 请求失败（重试 {max_retries} 次后仍失败）")
        last_result["retries_exhausted"] = True
        return last_result

    def _chat_once(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        """单次 chat 请求（不含 retry 逻辑）"""
        # 未配置 API Key → 离线优雅降级到 Mock 响应（与 FakeRedis 等降级策略一致）
        if not self.api_key:
            logger.warning("⚠ 未配置 LLM API Key，使用 Mock 响应（离线降级）")
            return self._mock_response(messages, "openai")
        try:
            # 尝试使用 openai 库
            try:
                client = self._get_client()
                
                # 解析 tools 参数
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
                finish_reason = choice.finish_reason
                if finish_reason == "length":
                    logger.warning(f"⚠ LLM 响应被截断（达到 max_tokens 上限），内容可能不完整")

                result = {
                    "success": True,
                    "content": choice.message.content,
                    "model": response.model,
                    "finish_reason": finish_reason,
                    "usage": {
                        "prompt_tokens": response.usage.prompt_tokens if response.usage else 0,
                        "completion_tokens": response.usage.completion_tokens if response.usage else 0,
                        "total_tokens": response.usage.total_tokens if response.usage else 0
                    }
                }
                
                # 处理 tool_calls
                if response.choices[0].message.tool_calls:
                    tool_calls = []
                    for tc in response.choices[0].message.tool_calls:
                        tool_calls.append({
                            "id": tc.id,
                            "type": tc.type,
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            }
                        })
                    result["tool_calls"] = tool_calls
                
                return result
            except ImportError:
                # openai 库未安装，返回模拟响应
                logger.warning("openai 库未安装，返回模拟响应")
                return self._mock_response(messages, "openai")
        except Exception as e:
            logger.warning(f"⚠ OpenAI 请求异常: {e}")
            error_info = {"success": False, "error": str(e)}
            if hasattr(e, 'response') and e.response is not None:
                headers = getattr(e.response, 'headers', {})
                retry_after = headers.get('retry-after')
                if retry_after:
                    error_info["retry_after"] = float(retry_after)
            return error_info

    def chat_stream(self, messages: List[Dict[str, str]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        """流式聊天请求，使用 OpenAI stream=True"""
        # 未配置 API Key → 离线优雅降级到 Mock 流式响应
        if not self.api_key:
            logger.warning("⚠ 未配置 LLM API Key，使用 Mock 流式响应（离线降级）")
            mock = self._mock_response(messages, "openai")
            yield {"type": "content_delta", "content": mock["content"]}
            yield {"type": "finish", "finish_reason": "stop"}
            return
        try:
            client = self._get_client()

            tools = kwargs.get("tools")
            api_kwargs = {
                "model": kwargs.get("model", self.model),
                "messages": messages,
                "temperature": kwargs.get("temperature", self.temperature),
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                "stream": True,
            }
            if tools:
                api_kwargs["tools"] = tools

            max_retries = 3
            stream = None
            last_error = None
            for attempt in range(max_retries):
                try:
                    stream = client.chat.completions.create(**api_kwargs)
                    break
                except Exception as e:
                    last_error = e
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt
                        logger.warning(f"⚠ OpenAI 流式连接失败 (attempt {attempt+1}/{max_retries}): {e}, {wait_time}s 后重试")
                        time.sleep(wait_time)
                    else:
                        logger.error(f"✗ OpenAI 流式连接失败（重试 {max_retries} 次后仍失败）: {e}")
                        yield {"type": "error", "error": str(e)}
                        return

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # 文本内容增量
                if delta.content:
                    yield {"type": "content_delta", "content": delta.content}

                # tool_calls 增量
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        yield {
                            "type": "tool_call_delta",
                            "index": tc.index,
                            "id": tc.id,
                            "function_name": tc.function.name if tc.function else None,
                            "arguments_delta": tc.function.arguments if tc.function else None,
                        }

                # finish_reason
                if chunk.choices[0].finish_reason:
                    yield {"type": "finish", "finish_reason": chunk.choices[0].finish_reason}

        except Exception as e:
            logger.error(f"✗ OpenAI 流式请求失败: {e}")
            yield {"type": "error", "error": str(e)}

    def embed(self, text: str) -> List[float]:
        # 未配置 API Key → 离线优雅降级到 Mock 嵌入向量（与 chat 降级策略一致）
        if not self.api_key:
            logger.warning("⚠ 未配置 LLM API Key，使用 Mock 嵌入向量（离线降级）")
            return self._mock_embedding(text)
        try:
            try:
                client = self._get_client()
                response = client.embeddings.create(
                    model=self.embedding_model,
                    input=text
                )
                return response.data[0].embedding
            except ImportError:
                # 返回简单哈希向量作为模拟
                return self._mock_embedding(text)
        except Exception as e:
            logger.error(f"✗ OpenAI 嵌入失败: {e}")
            return []

    def get_info(self) -> Dict[str, Any]:
        return {
            "backend": "openai",
            "model": self.model,
            "embedding_model": self.embedding_model,
            "base_url": self.base_url,
            "configured": bool(self.api_key)
        }

    def _mock_response(self, messages: List[Dict[str, str]], backend: str) -> Dict[str, Any]:
        last_msg = messages[-1]["content"] if messages else ""
        return {
            "success": True,
            "content": f"[{backend} mock] I received: {last_msg[:100]}...",
            "model": self.model,
            "usage": {"prompt_tokens": len(last_msg) // 4, "completion_tokens": 20, "total_tokens": len(last_msg) // 4 + 20},
            "mock": True
        }

    def _mock_embedding(self, text: str) -> List[float]:
        hash_val = hashlib.md5(text.encode()).hexdigest()
        return [int(hash_val[i:i2], 16) / 0xFFFFFFFF for i, i2 in zip(range(0, 64, 8), range(8, 72, 8))]


# ============================================================
# Claude 后端
# ============================================================

class ClaudeBackend(LLMBackend):
    """Anthropic Claude 后端"""

    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get("api_key", "")
        self.model = config.get("model", "claude-3-sonnet-20240229")
        self.max_tokens = config.get("max_tokens", 8192)
        self.temperature = config.get("temperature", 0.7)

    def chat(self, messages: List[Dict[str, str]], **kwargs) -> Dict[str, Any]:
        try:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=self.api_key)
                # Claude 格式转换
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
                    messages=claude_messages
                )
                return {
                    "success": True,
                    "content": response.content[0].text,
                    "model": response.model,
                    "usage": {
                        "prompt_tokens": response.usage.input_tokens,
                        "completion_tokens": response.usage.output_tokens,
                        "total_tokens": response.usage.input_tokens + response.usage.output_tokens
                    }
                }
            except ImportError:
                logger.warning("anthropic 库未安装，返回模拟响应")
                last_msg = messages[-1]["content"] if messages else ""
                return {
                    "success": True,
                    "content": f"[claude mock] I received: {last_msg[:100]}...",
                    "model": self.model,
                    "usage": {"prompt_tokens": len(last_msg) // 4, "completion_tokens": 20, "total_tokens": len(last_msg) // 4 + 20},
                    "mock": True
                }
        except Exception as e:
            logger.error(f"✗ Claude 请求失败: {e}")
            return {"success": False, "error": str(e)}

    def chat_stream(self, messages: List[Dict[str, str]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        """Claude 流式聊天（简化实现：先获取完整响应，再逐段 yield）"""
        result = self.chat(messages, **kwargs)
        if result.get("success") and result.get("content"):
            content = result["content"]
            chunk_size = 4
            for i in range(0, len(content), chunk_size):
                yield {"type": "content_delta", "content": content[i:i + chunk_size]}
            yield {"type": "finish", "finish_reason": "stop"}
        elif not result.get("success"):
            yield {"type": "error", "error": result.get("error", "Unknown error")}

    def embed(self, text: str) -> List[float]:
        # Claude 不提供嵌入 API，使用模拟（基于 MD5 hash 生成 8 维向量）
        hash_val = hashlib.md5(text.encode()).hexdigest()
        return [int(hash_val[i:i+4], 16) / 0xFFFF for i in range(0, 32, 4)]

    def get_info(self) -> Dict[str, Any]:
        return {
            "backend": "claude",
            "model": self.model,
            "configured": bool(self.api_key)
        }


# ============================================================
# 本地模型后端
# ============================================================

class LocalModelBackend(LLMBackend):
    """本地模型后端（如 Llama、ChatGLM）"""

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
                "max_tokens": kwargs.get("max_tokens", self.max_tokens)
            }).encode("utf-8")

            req = urllib.request.Request(
                f"{self.api_url}/v1/chat/completions",
                data=payload,
                headers={"Content-Type": "application/json"}
            )

            try:
                from app.core.config import get_settings
                _llm_timeout = get_settings().LLM_TIMEOUT_SECONDS
                with urllib.request.urlopen(req, timeout=_llm_timeout) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    return {
                        "success": True,
                        "content": result["choices"][0]["message"]["content"],
                        "model": result.get("model", self.model_type),
                        "usage": result.get("usage", {})
                    }
            except (urllib.error.URLError, ConnectionError):
                logger.warning(f"本地模型服务不可达 ({self.api_url})，返回模拟响应")
                last_msg = messages[-1]["content"] if messages else ""
                return {
                    "success": True,
                    "content": f"[local mock] I received: {last_msg[:100]}...",
                    "model": self.model_type,
                    "usage": {"prompt_tokens": len(last_msg) // 4, "completion_tokens": 20, "total_tokens": len(last_msg) // 4 + 20},
                    "mock": True
                }
        except Exception as e:
            logger.error(f"✗ 本地模型请求失败: {e}")
            return {"success": False, "error": str(e)}

    def chat_stream(self, messages: List[Dict[str, str]], **kwargs) -> Generator[Dict[str, Any], None, None]:
        """本地模型流式聊天（简化实现：先获取完整响应，再逐段 yield）"""
        result = self.chat(messages, **kwargs)
        if result.get("success") and result.get("content"):
            content = result["content"]
            chunk_size = 4
            for i in range(0, len(content), chunk_size):
                yield {"type": "content_delta", "content": content[i:i + chunk_size]}
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
                headers={"Content-Type": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                    return result["data"][0]["embedding"]
            except (urllib.error.URLError, ConnectionError):
                hash_val = hashlib.md5(text.encode()).hexdigest()
                return [int(hash_val[i:i2], 16) / 0xFFFFFFFF for i, i2 in zip(range(0, 64, 8), range(8, 72, 8))]
        except Exception:
            return []

    def get_info(self) -> Dict[str, Any]:
        return {
            "backend": "local",
            "model_type": self.model_type,
            "model_path": self.model_path,
            "api_url": self.api_url,
            "configured": bool(self.api_url)
        }


# ============================================================
# 后端工厂和管理器
# ============================================================

BACKEND_REGISTRY = {
    "openai": OpenAIBackend,
    "deepseek": OpenAIBackend,
    "custom": OpenAIBackend,
    "claude": ClaudeBackend,
    "anthropic": ClaudeBackend,
    "local": LocalModelBackend,
}


def _mask_api_key(key: Optional[str]) -> str:
    """对 API Key 做脱敏展示，保留前 4 位和后 4 位"""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}{'*' * (len(key) - 8)}{key[-4:]}"


def _ensure_llm_config_table() -> None:
    """确保 LLM 配置表存在"""
    db = get_db_client()
    db.execute('''
        CREATE TABLE IF NOT EXISTS llm_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            backend_name TEXT NOT NULL,
            backend_type TEXT NOT NULL,
            config TEXT NOT NULL,
            is_active INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, backend_name),
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    db.execute('CREATE INDEX IF NOT EXISTS idx_llm_config_user ON llm_configs(user_id)')


def get_llm_backend(user_id: int, backend_name: Optional[str] = None) -> Dict[str, Any]:
    """
    获取 LLM 后端实例

    Args:
        user_id: 用户 ID
        backend_name: 指定后端名称，None 则使用当前激活的后端

    Returns:
        后端实例和信息
    """
    try:
        _ensure_llm_config_table()
        db = get_db_client()

        if backend_name:
            rows = db.execute(
                'SELECT * FROM llm_configs WHERE user_id = ? AND backend_name = ?',
                (user_id, backend_name)
            )
        else:
            rows = db.execute(
                'SELECT * FROM llm_configs WHERE user_id = ? AND is_active = 1 ORDER BY updated_at DESC LIMIT 1',
                (user_id,)
            )

        if not rows:
            # 返回默认 DeepSeek 后端（OpenAI 兼容接口）
            default_config = {
                "api_key": os.environ.get("LLM_API_KEY", "sk-3cb5299e0bbc4456a05da93d1eff617f"),
                "model": os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
                "base_url": os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1"),
                "temperature": 0.7,
            }
            backend = OpenAIBackend(default_config)
            return {
                "success": True,
                "backend": backend,
                "info": backend.get_info(),
                "is_default": True
            }

        row = dict(rows[0])
        config = json.loads(row["config"])
        backend_type = row["backend_type"]

        if backend_type not in BACKEND_REGISTRY:
            return {"success": False, "error": f"Unknown backend type: {backend_type}"}

        backend = BACKEND_REGISTRY[backend_type](config)

        return {
            "success": True,
            "backend": backend,
            "info": backend.get_info(),
            "is_default": False,
            "backend_name": row["backend_name"]
        }

    except Exception as e:
        logger.error(f"✗ 获取 LLM 后端失败: {e}")
        return {"success": False, "error": str(e)}


def register_llm_backend(user_id: int,
                         backend_name: str,
                         backend_type: str,
                         config: Dict[str, Any],
                         set_active: bool = True) -> Dict[str, Any]:
    """
    注册/更新 LLM 后端配置

    Args:
        user_id: 用户 ID
        backend_name: 后端名称（如 "my-openai", "local-llama"）
        backend_type: 后端类型（openai, claude, local）
        config: 后端配置
        set_active: 是否设为当前激活后端

    Returns:
        注册结果
    """
    try:
        _ensure_llm_config_table()
        db = get_db_client()

        if backend_type not in BACKEND_REGISTRY:
            return {"success": False, "error": f"Unsupported backend type: {backend_type}. Supported: {list(BACKEND_REGISTRY.keys())}"}

        config_str = json.dumps(config, ensure_ascii=False)

        # 如果设为激活，先取消其他激活
        if set_active:
            db.execute(
                'UPDATE llm_configs SET is_active = 0 WHERE user_id = ?',
                (user_id,)
            )

        # 插入或更新
        existing = db.execute(
            'SELECT id FROM llm_configs WHERE user_id = ? AND backend_name = ?',
            (user_id, backend_name)
        )

        if existing:
            db.execute(
                'UPDATE llm_configs SET backend_type = ?, config = ?, is_active = ?, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND backend_name = ?',
                (backend_type, config_str, 1 if set_active else 0, user_id, backend_name)
            )
        else:
            db.execute(
                'INSERT INTO llm_configs (user_id, backend_name, backend_type, config, is_active) VALUES (?, ?, ?, ?, ?)',
                (user_id, backend_name, backend_type, config_str, 1 if set_active else 0)
            )

        logger.info(f"✓ 注册 LLM 后端: {backend_name} ({backend_type})")

        return {
            "success": True,
            "backend_name": backend_name,
            "backend_type": backend_type,
            "is_active": set_active,
            "message": f"Backend '{backend_name}' registered successfully"
        }

    except Exception as e:
        logger.error(f"✗ 注册 LLM 后端失败: {e}")
        return {"success": False, "error": str(e)}


def switch_backend(user_id: int, backend_name: str) -> Dict[str, Any]:
    """
    切换当前激活的 LLM 后端

    Args:
        user_id: 用户 ID
        backend_name: 要切换到的后端名称

    Returns:
        切换结果
    """
    try:
        _ensure_llm_config_table()
        db = get_db_client()

        # 检查后端是否存在
        rows = db.execute(
            'SELECT * FROM llm_configs WHERE user_id = ? AND backend_name = ?',
            (user_id, backend_name)
        )

        if not rows:
            return {"success": False, "error": f"Backend '{backend_name}' not found"}

        # 取消其他激活
        db.execute(
            'UPDATE llm_configs SET is_active = 0 WHERE user_id = ?',
            (user_id,)
        )

        # 激活指定后端
        db.execute(
            'UPDATE llm_configs SET is_active = 1, updated_at = CURRENT_TIMESTAMP WHERE user_id = ? AND backend_name = ?',
            (user_id, backend_name)
        )

        row = dict(rows[0])
        config = json.loads(row["config"])
        backend = BACKEND_REGISTRY[row["backend_type"]](config)

        logger.info(f"✓ 切换 LLM 后端: {backend_name}")

        return {
            "success": True,
            "backend_name": backend_name,
            "backend_type": row["backend_type"],
            "info": backend.get_info(),
            "message": f"Switched to backend '{backend_name}'"
        }

    except Exception as e:
        logger.error(f"✗ 切换后端失败: {e}")
        return {"success": False, "error": str(e)}


def list_backends(user_id: int) -> Dict[str, Any]:
    """列出用户的所有 LLM 后端配置（包含脱敏后的关键字段）"""
    try:
        _ensure_llm_config_table()
        db = get_db_client()

        rows = db.execute(
            'SELECT backend_name, backend_type, config, is_active, created_at, updated_at FROM llm_configs WHERE user_id = ? ORDER BY is_active DESC, updated_at DESC',
            (user_id,)
        )

        backends = []
        if rows:
            for row in rows:
                r = dict(row)
                config = json.loads(r["config"] or '{}')
                backends.append({
                    "name": r["backend_name"],
                    "type": r["backend_type"],
                    "is_active": bool(r["is_active"]),
                    "is_default": bool(r["is_active"]),
                    "model": config.get("model", ""),
                    "base_url": config.get("base_url", ""),
                    "api_key_masked": _mask_api_key(config.get("api_key")),
                    "timeout": config.get("timeout", 30),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"]
                })

        return {
            "success": True,
            "backends": backends,
            "count": len(backends),
            "available_types": list(BACKEND_REGISTRY.keys())
        }

    except Exception as e:
        logger.error(f"✗ 列出后端失败: {e}")
        return {"success": False, "error": str(e)}


def get_backend_details(user_id: int, backend_name: str) -> Dict[str, Any]:
    """获取单个 LLM 后端的完整配置（API Key 脱敏）"""
    try:
        _ensure_llm_config_table()
        db = get_db_client()

        rows = db.execute(
            'SELECT backend_name, backend_type, config, is_active, created_at, updated_at FROM llm_configs WHERE user_id = ? AND backend_name = ?',
            (user_id, backend_name)
        )

        if not rows:
            return {"success": False, "error": f"Backend '{backend_name}' not found"}

        r = dict(rows[0])
        config = json.loads(r["config"] or '{}')
        return {
            "success": True,
            "backend": {
                "name": r["backend_name"],
                "type": r["backend_type"],
                "is_active": bool(r["is_active"]),
                "is_default": bool(r["is_active"]),
                "config": {**config, "api_key": _mask_api_key(config.get("api_key"))},
                "created_at": r["created_at"],
                "updated_at": r["updated_at"]
            }
        }

    except Exception as e:
        logger.error(f"✗ 获取后端详情失败: {e}")
        return {"success": False, "error": str(e)}


def check_backend_health(user_id: int, backend_name: str) -> Dict[str, Any]:
    """检查指定 LLM 后端的健康状态"""
    try:
        _ensure_llm_config_table()
        db = get_db_client()

        rows = db.execute(
            'SELECT backend_type, config FROM llm_configs WHERE user_id = ? AND backend_name = ?',
            (user_id, backend_name)
        )

        if not rows:
            return {"success": False, "status": "unknown", "message": f"Backend '{backend_name}' not found"}

        r = dict(rows[0])
        backend_type = r["backend_type"]
        config = json.loads(r["config"] or '{}')

        if backend_type not in BACKEND_REGISTRY:
            return {"success": False, "status": "unhealthy", "message": f"Unknown backend type: {backend_type}"}

        backend = BACKEND_REGISTRY[backend_type](config)

        # 基础配置校验
        if backend_type in ("openai", "deepseek", "custom", "claude", "anthropic"):
            if not config.get("api_key"):
                return {"success": True, "status": "unhealthy", "message": "缺少 API Key"}

        if backend_type == "local":
            if not config.get("api_url"):
                return {"success": True, "status": "unhealthy", "message": "缺少本地模型 API URL"}

        # 轻量级连通性探测
        try:
            if backend_type in ("openai", "deepseek", "custom"):
                client = getattr(backend, "_get_client", lambda: None)()
                if client is not None:
                    # 尝试列出模型，失败不抛异常则视为可达
                    client.models.list()
                return {"success": True, "status": "healthy", "message": "连接正常"}
            elif backend_type in ("claude", "anthropic"):
                # Claude 不做真实请求以避免调用成本，仅校验 key 格式非空
                return {"success": True, "status": "healthy", "message": "配置有效（未发起真实请求）"}
            elif backend_type == "local":
                import urllib.request
                import urllib.error
                api_url = config.get("api_url", "http://localhost:8080")
                req = urllib.request.Request(f"{api_url}/v1/models", method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    if resp.status == 200:
                        return {"success": True, "status": "healthy", "message": "本地模型服务可达"}
                    return {"success": True, "status": "degraded", "message": f"返回状态 {resp.status}"}
            else:
                return {"success": True, "status": "unknown", "message": "未实现健康检查"}
        except Exception as e:
            logger.warning(f"⚠ 后端 {backend_name} 健康检查异常: {e}")
            return {"success": True, "status": "unhealthy", "message": f"连通性检查失败: {e}"}

    except Exception as e:
        logger.error(f"✗ 健康检查失败: {e}")
        return {"success": False, "status": "unhealthy", "message": str(e)}


def delete_backend(user_id: int, backend_name: str) -> Dict[str, Any]:
    """删除 LLM 后端配置"""
    try:
        _ensure_llm_config_table()
        db = get_db_client()

        db.execute(
            'DELETE FROM llm_configs WHERE user_id = ? AND backend_name = ?',
            (user_id, backend_name)
        )

        return {
            "success": True,
            "message": f"Backend '{backend_name}' deleted"
        }

    except Exception as e:
        logger.error(f"✗ 删除后端失败: {e}")
        return {"success": False, "error": str(e)}


def llm_chat(user_id: int, messages: List[Dict[str, str]], backend_name: Optional[str] = None, enqueue_on_failure: bool = False, **kwargs) -> Dict[str, Any]:
    """
    使用配置的 LLM 后端发送聊天请求（含断路器 + fallback chain + 降级）

    Args:
        user_id: 用户 ID
        messages: 消息列表
        backend_name: 指定后端（None 使用激活的后端）
        enqueue_on_failure: 全部后端失败时是否将任务入异步重试队列
            （仅建议后台非交互任务开启）
        **kwargs: 额外参数（temperature, max_tokens 等）

    Returns:
        聊天响应
    """
    from app.core.circuit_breaker import get_circuit_breaker_registry

    registry = get_circuit_breaker_registry()

    result = get_llm_backend(user_id, backend_name)
    if not result["success"]:
        return result

    backend = result["backend"]
    primary_name = result.get("backend_name") or "default"
    breaker = registry.get(user_id, primary_name)

    response = None
    if breaker.allow():
        response = backend.chat(messages, **kwargs)
        # mock 降级响应不计入断路器成败（无外部调用）
        if response.get("success") and not response.get("mock"):
            breaker.record_success()
        elif not response.get("success"):
            breaker.record_failure()
    else:
        logger.warning(f"⚠ 主后端 [{primary_name}] 断路器 OPEN，跳过直接进入 fallback（user_id={user_id}）")

    # 主后端失败或被熔断时，遍历其他已注册后端尝试 fallback
    if (response is None or not response.get("success")) and not backend_name:
        logger.warning(f"⚠ 主后端不可用，尝试 fallback chain (user_id={user_id})")
        try:
            _ensure_llm_config_table()
            db = get_db_client()
            other_backends = db.execute(
                'SELECT backend_name, backend_type, config FROM llm_configs WHERE user_id = ? AND is_active = 0 ORDER BY updated_at DESC',
                (user_id,)
            )
            if other_backends:
                for row in other_backends:
                    r = dict(row)
                    b_type = r["backend_type"]
                    if b_type not in BACKEND_REGISTRY:
                        continue
                    fb_breaker = registry.get(user_id, r["backend_name"])
                    if not fb_breaker.allow():
                        logger.info(f"→ fallback 后端 [{r['backend_name']}] 断路器 OPEN，跳过")
                        continue
                    try:
                        b_config = json.loads(r["config"])
                        fallback_backend = BACKEND_REGISTRY[b_type](b_config)
                        logger.info(f"→ 尝试 fallback 后端: {r['backend_name']} ({b_type})")
                        fb_response = fallback_backend.chat(messages, **kwargs)
                        if fb_response.get("success") and not fb_response.get("mock"):
                            fb_breaker.record_success()
                        elif not fb_response.get("success"):
                            fb_breaker.record_failure()
                        if fb_response.get("success"):
                            fb_response["fallback_backend"] = r["backend_name"]
                            logger.info(f"✓ Fallback 成功: {r['backend_name']}")
                            return fb_response
                        logger.warning(f"✗ Fallback 后端 {r['backend_name']} 也失败")
                    except Exception as e:
                        fb_breaker.record_failure()
                        logger.error(f"✗ Fallback 后端 {r['backend_name']} 异常: {e}")
        except Exception as e:
            logger.error(f"✗ Fallback chain 执行异常: {e}")

    # 主后端被熔断且无可用 fallback：返回统一降级响应
    if response is None:
        response = {
            "success": False,
            "error": f"主后端 [{primary_name}] 已熔断且无可用 fallback",
            "circuit_open": True,
        }

    # 全部失败：标记降级，并按需入异步重试队列
    if not response.get("success"):
        response["degraded"] = True
        if enqueue_on_failure:
            try:
                from app.services.llm_retry_queue import enqueue_retry
                enqueue_retry(user_id, messages, kwargs)
                response["enqueued_for_retry"] = True
            except Exception as e:
                logger.error(f"✗ LLM 重试入队失败: {e}")

    return response


def llm_chat_stream(user_id: int, messages: List[Dict[str, str]], backend_name: Optional[str] = None, **kwargs) -> Generator[Dict[str, Any], None, None]:
    """
    使用配置的 LLM 后端进行流式聊天

    Args:
        user_id: 用户 ID
        messages: 消息列表
        backend_name: 指定后端（None 使用激活的后端）
        **kwargs: 额外参数

    Yields:
        流式事件字典
    """
    result = get_llm_backend(user_id, backend_name)
    if not result["success"]:
        yield {"type": "error", "error": result.get("error", "Failed to get backend")}
        return

    backend = result["backend"]
    yield from backend.chat_stream(messages, **kwargs)


def llm_embed(user_id: int, text: str, backend_name: Optional[str] = None) -> Dict[str, Any]:
    """
    使用配置的 LLM 后端生成嵌入向量

    Args:
        user_id: 用户 ID
        text: 要嵌入的文本
        backend_name: 指定后端

    Returns:
        嵌入向量
    """
    result = get_llm_backend(user_id, backend_name)
    if not result["success"]:
        return result

    backend = result["backend"]
    embedding = backend.embed(text)

    return {
        "success": True,
        "embedding": embedding,
        "dimensions": len(embedding)
    }


# ============================================================
# 测试
# ============================================================

def test_llm_backend():
    """测试 LLM 后端集成"""
    print("\n" + "="*60)
    print("测试 LLM 后端集成服务")
    print("="*60 + "\n")

    user_id = 999

    # 清理
    db = get_db_client()
    _ensure_llm_config_table()
    db.execute('DELETE FROM llm_configs WHERE user_id = ?', (user_id,))

    print("1. 测试注册 OpenAI 后端...")
    result = register_llm_backend(user_id, "my-openai", "openai", {
        "api_key": "test-key",
        "model": "gpt-4",
        "temperature": 0.5
    })
    print(f"   结果: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ OpenAI 后端注册成功\n")

    print("2. 测试注册 Claude 后端...")
    result = register_llm_backend(user_id, "my-claude", "claude", {
        "api_key": "test-key",
        "model": "claude-3-sonnet-20240229"
    }, set_active=False)
    print(f"   结果: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ Claude 后端注册成功\n")

    print("3. 测试注册本地模型后端...")
    result = register_llm_backend(user_id, "local-llama", "local", {
        "model_type": "llama",
        "api_url": "http://localhost:8080"
    }, set_active=False)
    print(f"   结果: {result.get('success')}")
    assert result["success"] == True
    print(f"   ✓ 本地模型后端注册成功\n")

    print("4. 测试列出所有后端...")
    result = list_backends(user_id)
    print(f"   后端数: {result.get('count', 0)}")
    for b in result.get("backends", []):
        active = "✓" if b["is_active"] else " "
        print(f"   [{active}] {b['name']} ({b['type']})")
    assert result["count"] == 3
    print(f"   ✓ 列出后端成功\n")

    print("5. 测试获取当前激活后端...")
    result = get_llm_backend(user_id)
    print(f"   后端信息: {result.get('info')}")
    assert result["success"] == True
    print(f"   ✓ 获取激活后端成功\n")

    print("6. 测试切换后端...")
    result = switch_backend(user_id, "my-claude")
    print(f"   切换结果: {result.get('success')}")
    print(f"   新后端: {result.get('backend_type')}")
    assert result["success"] == True
    print(f"   ✓ 后端切换成功\n")

    print("7. 测试聊天（mock 模式）...")
    result = llm_chat(user_id, [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"}
    ])
    print(f"   响应: {result.get('content', '')[:80]}...")
    print(f"   Mock: {result.get('mock', False)}")
    assert result["success"] == True
    print(f"   ✓ 聊天请求成功\n")

    print("8. 测试嵌入向量生成...")
    result = llm_embed(user_id, "test text for embedding")
    print(f"   向量维度: {result.get('dimensions', 0)}")
    assert result["success"] == True
    assert result["dimensions"] > 0
    print(f"   ✓ 嵌入向量生成成功\n")

    print("9. 测试指定后端聊天...")
    result = llm_chat(user_id, [{"role": "user", "content": "Hi"}], backend_name="local-llama")
    print(f"   响应: {result.get('content', '')[:80]}...")
    assert result["success"] == True
    print(f"   ✓ 指定后端聊天成功\n")

    print("10. 测试删除后端...")
    result = delete_backend(user_id, "local-llama")
    print(f"   结果: {result.get('success')}")
    assert result["success"] == True
    result = list_backends(user_id)
    assert result["count"] == 2
    print(f"   ✓ 删除后端成功\n")

    # 清理
    db.execute('DELETE FROM llm_configs WHERE user_id = ?', (user_id,))

    print("="*60)
    print("✅ LLM 后端集成服务测试完成！")
    print("="*60 + "\n")

    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    test_llm_backend()
