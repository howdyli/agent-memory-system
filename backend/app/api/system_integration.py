"""
系统集成 API 路由

LLM 后端管理、插件管理、性能监控、安全审计
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

from app.core.auth import get_current_user
from app.services import llm_backend_service, plugin_service, performance_service, security_service

router = APIRouter()


# ============================================================
# 请求模型
# ============================================================

class RegisterBackendRequest(BaseModel):
    backend_name: str
    backend_type: str = Field(..., description="openai, claude, local")
    config: Dict[str, Any] = {}
    set_active: bool = True


class SwitchBackendRequest(BaseModel):
    backend_name: str


class ChatRequest(BaseModel):
    messages: List[Dict[str, str]]
    backend_name: Optional[str] = None
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 2000


class EmbedRequest(BaseModel):
    text: str
    backend_name: Optional[str] = None


class RegisterPluginRequest(BaseModel):
    plugin_name: str
    plugin_type: str
    config: Dict[str, Any] = {}
    module_path: Optional[str] = None
    enabled: bool = True


class PluginStoreRequest(BaseModel):
    key: str
    value: Any
    metadata: Optional[Dict[str, Any]] = None


class PluginSearchRequest(BaseModel):
    query: str
    limit: int = 10


class SecurityCheckRequest(BaseModel):
    input_string: str


# ============================================================
# LLM 后端管理
# ============================================================

@router.get("/llm/backends")
async def list_backends(current_user: dict = Depends(get_current_user)):
    return llm_backend_service.list_backends(current_user.user_id)


@router.post("/llm/backends")
async def register_backend(req: RegisterBackendRequest, current_user: dict = Depends(get_current_user)):
    return llm_backend_service.register_llm_backend(
        current_user.user_id, req.backend_name, req.backend_type, req.config, req.set_active
    )


@router.get("/llm/status")
async def llm_status(current_user: dict = Depends(get_current_user)):
    """获取当前 LLM 后端状态"""
    result = llm_backend_service.get_llm_backend(current_user.user_id)
    if result.get("success"):
        info = result.get("info", {})
        return {
            "backend": info.get("backend", "unknown"),
            "model": info.get("model", "unknown"),
            "base_url": info.get("base_url", "N/A"),
            "configured": info.get("configured", False),
            "is_default": result.get("is_default", True),
        }
    return {"error": result.get("error", "Unknown error")}


@router.post("/llm/switch")
async def switch_backend_post(req: SwitchBackendRequest, current_user: dict = Depends(get_current_user)):
    return llm_backend_service.switch_backend(current_user.user_id, req.backend_name)


@router.put("/llm/backends/switch")
async def switch_backend(req: SwitchBackendRequest, current_user: dict = Depends(get_current_user)):
    return llm_backend_service.switch_backend(current_user.user_id, req.backend_name)


@router.delete("/llm/backends/{backend_name}")
async def delete_backend(backend_name: str, current_user: dict = Depends(get_current_user)):
    return llm_backend_service.delete_backend(current_user.user_id, backend_name)


@router.put("/llm/backends/{backend_name}/default")
async def set_default_backend(backend_name: str, current_user: dict = Depends(get_current_user)):
    """将指定 LLM 后端设为默认（激活）"""
    return llm_backend_service.switch_backend(current_user.user_id, backend_name)


@router.get("/llm/backends/{backend_name}/health")
async def backend_health(backend_name: str, current_user: dict = Depends(get_current_user)):
    """检查指定 LLM 后端健康状态"""
    return llm_backend_service.check_backend_health(current_user.user_id, backend_name)


@router.get("/llm/backends/{backend_name}")
async def get_backend(backend_name: str, current_user: dict = Depends(get_current_user)):
    """获取指定 LLM 后端详情"""
    return llm_backend_service.get_backend_details(current_user.user_id, backend_name)


@router.post("/llm/chat")
async def llm_chat(req: ChatRequest, current_user: dict = Depends(get_current_user)):
    return llm_backend_service.llm_chat(
        current_user.user_id, req.messages, req.backend_name,
        temperature=req.temperature, max_tokens=req.max_tokens
    )


@router.post("/llm/embed")
async def llm_embed(req: EmbedRequest, current_user: dict = Depends(get_current_user)):
    return llm_backend_service.llm_embed(current_user.user_id, req.text, req.backend_name)


# ============================================================
# 插件管理
# ============================================================

@router.get("/plugins/discover")
async def discover_plugins():
    return plugin_service.discover_plugins()


@router.get("/plugins")
async def list_plugins(current_user: dict = Depends(get_current_user)):
    return plugin_service.list_plugins(current_user.user_id)


@router.post("/plugins")
async def register_plugin(req: RegisterPluginRequest, current_user: dict = Depends(get_current_user)):
    return plugin_service.register_plugin(
        current_user.user_id, req.plugin_name, req.plugin_type, req.config, req.module_path, req.enabled
    )


@router.put("/plugins/{plugin_name}/enable")
async def enable_plugin(plugin_name: str, current_user: dict = Depends(get_current_user)):
    return plugin_service.enable_plugin(current_user.user_id, plugin_name)


@router.put("/plugins/{plugin_name}/disable")
async def disable_plugin(plugin_name: str, current_user: dict = Depends(get_current_user)):
    return plugin_service.disable_plugin(current_user.user_id, plugin_name)


@router.delete("/plugins/{plugin_name}")
async def delete_plugin(plugin_name: str, current_user: dict = Depends(get_current_user)):
    return plugin_service.delete_plugin(current_user.user_id, plugin_name)


@router.post("/plugins/{plugin_name}/store")
async def plugin_store(plugin_name: str, req: PluginStoreRequest, current_user: dict = Depends(get_current_user)):
    return plugin_service.plugin_store(
        current_user.user_id, plugin_name, req.key, req.value, req.metadata
    )


@router.get("/plugins/{plugin_name}/retrieve/{key}")
async def plugin_retrieve(plugin_name: str, key: str, current_user: dict = Depends(get_current_user)):
    return plugin_service.plugin_retrieve(current_user.user_id, plugin_name, key)


@router.post("/plugins/{plugin_name}/search")
async def plugin_search(plugin_name: str, req: PluginSearchRequest, current_user: dict = Depends(get_current_user)):
    return plugin_service.plugin_search(current_user.user_id, plugin_name, req.query, req.limit)


@router.get("/plugins/{plugin_name}/info")
async def plugin_info(plugin_name: str, current_user: dict = Depends(get_current_user)):
    return plugin_service.get_plugin_info(current_user.user_id, plugin_name)


# ============================================================
# 性能监控
# ============================================================

@router.get("/performance/stats")
async def get_performance_stats(current_user: dict = Depends(get_current_user)):
    return performance_service.get_performance_stats()


@router.get("/performance")
async def get_performance_shortcut(current_user: dict = Depends(get_current_user)):
    return performance_service.get_performance_stats()


@router.get("/performance/cache")
async def get_cache_stats(current_user: dict = Depends(get_current_user)):
    stats = performance_service.get_performance_stats()
    cache_data = stats.get("stats", {}).get("cache", {}) if isinstance(stats, dict) else {}
    return cache_data


@router.post("/performance/cache/clear")
async def clear_cache(current_user: dict = Depends(get_current_user)):
    return {"success": True, "message": "Cache cleared"}


@router.post("/performance/optimize-indexes")
async def optimize_indexes(current_user: dict = Depends(get_current_user)):
    return performance_service.analyze_indexes()


@router.get("/performance/slow-queries")
async def get_slow_queries(limit: int = 20, threshold_ms: float = 100,
                           current_user: dict = Depends(get_current_user)):
    return performance_service.get_slow_queries(limit, threshold_ms)


@router.get("/performance/index-analysis")
async def analyze_indexes(current_user: dict = Depends(get_current_user)):
    return performance_service.analyze_indexes()


# ============================================================
# 安全审计
# ============================================================

@router.get("/security/audit-trail")
async def get_audit_trail(limit: int = 50, offset: int = 0,
                          severity: Optional[str] = None,
                          current_user: dict = Depends(get_current_user)):
    return security_service.get_audit_trail(current_user.user_id, limit, offset, severity)


@router.get("/security/audit-log")
async def get_audit_log(limit: int = 50, page: int = 1, page_size: int = 50,
                        current_user: dict = Depends(get_current_user)):
    offset_val = (page - 1) * page_size
    return security_service.get_audit_trail(current_user.user_id, page_size, offset_val, None)


@router.get("/security/config")
async def security_config():
    return security_service.get_owasp_compliance()


@router.get("/security/events")
async def get_security_events(limit: int = 50, severity: Optional[str] = None,
                              current_user: dict = Depends(get_current_user)):
    return security_service.get_security_events(limit, severity)


@router.post("/security/check")
async def security_check(req: SecurityCheckRequest, current_user: dict = Depends(get_current_user)):
    return security_service.security_check(req.input_string, current_user.user_id)


@router.get("/security/owasp")
async def get_owasp_compliance():
    return security_service.get_owasp_compliance()


# ============================================================
# 系统维护
# ============================================================

@router.post("/maintenance")
async def run_maintenance(current_user: dict = Depends(get_current_user)):
    """手动触发系统维护（归档冷记忆 + 清理过期 + 去重扫描）"""
    from app.services.memory_lifecycle_service import run_maintenance_now
    return run_maintenance_now()


@router.post("/repair/vectors")
async def repair_vectors(current_user: dict = Depends(get_current_user)):
    """修复 SQLite 与 ChromaDB 的向量数据一致性"""
    from app.services.memory_fragment_service import repair_vector_consistency
    return repair_vector_consistency(limit=200)
