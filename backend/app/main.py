import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import health, auth, memory_variables, memory_extraction, memory_tables, memory_fragments, auto_recall, long_term_memory, system_integration, agent, memory_lifecycle, graph_memory, hybrid_search, memory_observability, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理（替代已弃用的 on_event）"""
    from app.services.memory_lifecycle_service import start_lifecycle_scheduler, stop_lifecycle_scheduler
    start_lifecycle_scheduler()
    yield
    stop_lifecycle_scheduler()


app = FastAPI(
    title="Agent Memory System",
    description="Agent Memory System Backend API",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置 — 通过环境变量控制，默认仅允许本地开发
_cors_env = os.environ.get("CORS_ORIGINS", "")
if _cors_env:
    _allowed_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
else:
    # 开发环境默认值
    _allowed_origins = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:4173",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["authentication"])
app.include_router(memory_variables.router, prefix="/api/v1/memory", tags=["memory-variables"])
app.include_router(memory_extraction.router, prefix="/api/v1/memory/extraction", tags=["memory-extraction"])
app.include_router(memory_tables.router, prefix="/api/v1/memory/tables", tags=["memory-tables"])
app.include_router(memory_fragments.router, prefix="/api/v1/memory/fragments", tags=["memory-fragments"])
app.include_router(auto_recall.router, prefix="/api/v1/memory/recall", tags=["auto-recall"])
app.include_router(long_term_memory.router, prefix="/api/v1/memory/long-term", tags=["long-term-memory"])
app.include_router(system_integration.router, prefix="/api/v1/system", tags=["system-integration"])
app.include_router(agent.router, prefix="/api/v1/agent", tags=["agent"])
app.include_router(memory_lifecycle.router, prefix="/api/v1", tags=["memory-lifecycle"])
app.include_router(graph_memory.router, prefix="/api/v1", tags=["memory-graph"])
app.include_router(hybrid_search.router, prefix="/api/v1", tags=["hybrid-search"])
app.include_router(memory_observability.router, prefix="/api/v1", tags=["memory-observability"])
app.include_router(sessions.router, prefix="/api/v1/agent", tags=["sessions"])


@app.get("/")
async def root():
    return {
        "message": "Agent Memory System API",
        "version": "0.1.0",
        "status": "running"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
