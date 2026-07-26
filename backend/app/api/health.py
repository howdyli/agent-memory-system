"""
健康检查模块

提供 liveness 和 readiness 两个端点：
- /health/live: 进程存活即 OK（不检查依赖）
- /health/ready: 检查所有依赖组件是否可用
- /health: 保留原有兼容端点
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Response, status

router = APIRouter()


@router.get("/health/live")
async def liveness():
    """Liveness — 进程存活即 OK（不检查依赖）"""
    return {"status": "alive"}


@router.get("/health/ready")
async def readiness(response: Response):
    """Readiness — 检查所有依赖组件是否可用"""
    checks: dict = {}
    all_ok = True

    # 关系型数据库（SQLite）
    try:
        from app.core.db_client import get_db_client
        db = get_db_client()
        db.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"fail: {e}"
        all_ok = False

    # Redis
    try:
        from app.core.redis_client import get_redis_client
        rc = get_redis_client()
        rc.get_connection().ping()
        checks["redis"] = "ok" if not rc._is_fake else "ok (fakeredis)"
    except Exception as e:
        checks["redis"] = f"fail: {e}"
        all_ok = False

    # 向量数据库（ChromaDB）
    try:
        from app.core.chromadb_client import get_chromadb_client
        chroma = get_chromadb_client()
        if chroma is not None:
            count = chroma.count()
            checks["vector_store"] = f"ok (count={count})"
        else:
            checks["vector_store"] = "unavailable"
            all_ok = False
    except Exception as e:
        checks["vector_store"] = f"fail: {e}"
        all_ok = False

    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if all_ok else "not_ready",
        "checks": checks,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health")
async def health_check():
    """兼容原有健康检查端点"""
    return {
        "status": "healthy",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "agent-memory-backend",
    }


@router.get("/health/compression")
async def compression_health():
    """
    压缩引擎健康检查。

    返回断路器状态、成功/失败统计、fallback 使用情况。
    """
    from app.core.circuit_breaker import CircuitBreaker

    # 获取压缩引擎专用断路器状态
    try:
        from app.services.context_compressor import _compression_circuit_breaker
        breaker = _compression_circuit_breaker
        snapshot = breaker.snapshot()
        breaker_status = {
            "state": snapshot.get("state", "unknown"),
            "failure_count": snapshot.get("failure_count", 0),
            "success_count": snapshot.get("success_count", 0),
            "failure_threshold": snapshot.get("failure_threshold", 3),
            "recovery_timeout": snapshot.get("recovery_timeout", 60.0),
            "last_failure_time": snapshot.get("last_failure_time"),
        }
    except Exception as e:
        breaker_status = {"error": str(e)}

    # 判断健康状态
    is_healthy = breaker_status.get("state") == "closed"
    degraded = breaker_status.get("state") == "half_open"

    status_label = "healthy"
    if degraded:
        status_label = "degraded"
    elif not is_healthy:
        status_label = "unhealthy"

    return {
        "status": status_label,
        "circuit_breaker": breaker_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
