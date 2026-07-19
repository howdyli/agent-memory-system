"""Embedded Transport implementation — direct service layer calls."""

from typing import Any, Dict, Iterator, Optional

from agent_memory.exceptions import EmbeddedModeError
from agent_memory.transport.base import Transport


class EmbeddedTransport(Transport):
    """
    嵌入模式传输层。

    直接调用本地 service 层函数，跳过 HTTP。
    适用于开发、测试、单机部署场景。

    需要安装可选依赖: pip install agent-memory-sdk[embedded]
    """

    def __init__(
        self,
        db_path: str = "agent_memory.db",
        vector_backend: str = "chroma",
        user_id: int = 1,
        workspace_id: Optional[int] = None,
    ):
        self.db_path = db_path
        self.vector_backend = vector_backend
        self.user_id = user_id
        self.workspace_id = workspace_id
        self._initialized = False

    def _ensure_initialized(self) -> None:
        """延迟初始化 service 层依赖。"""
        if self._initialized:
            return
        try:
            # 尝试导入 backend service 层
            from app.services import memory_variable_service  # noqa: F401
            self._initialized = True
        except ImportError:
            raise EmbeddedModeError(
                "嵌入模式需要 backend 包可用。"
                "请确保 app.services 模块可导入，或使用 HTTP 模式。"
            )

    def request(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        self._ensure_initialized()
        return self._dispatch(method, path, json=json, params=params)

    def request_stream(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
    ) -> Iterator[str]:
        raise EmbeddedModeError("嵌入模式不支持流式请求")

    def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Internal dispatch — route path to service function
    # ------------------------------------------------------------------

    def _dispatch(
        self,
        method: str,
        path: str,
        json: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> Any:
        """将 API 路径映射到 service 层函数调用。"""
        from app.services import memory_variable_service as var_svc
        from app.services import memory_fragment_service as frag_svc
        from app.services import memory_table_service as tbl_svc

        uid = self.user_id
        wid = self.workspace_id

        # ---- Variables ----
        if path == "/memory/variables" and method == "POST":
            return var_svc.set_memory_variable(
                user_id=uid, key=json["key"], value=json["value"],
                ttl=json.get("ttl"), workspace_id=wid,
            )
        if path == "/memory/variables" and method == "GET":
            return var_svc.list_memory_variables(
                user_id=uid,
                session_id=(params or {}).get("session_id"),
                workspace_id=wid,
            )
        if path.startswith("/memory/variables/") and method == "GET":
            key = path.split("/")[-1]
            return var_svc.get_memory_variable(user_id=uid, key=key, workspace_id=wid)
        if path.startswith("/memory/variables/") and method == "DELETE":
            key = path.split("/")[-1]
            return var_svc.delete_memory_variable(user_id=uid, key=key, workspace_id=wid)

        # ---- Fragments ----
        if path == "/memory/fragments/" and method == "POST":
            return frag_svc.create_fragment(
                user_id=uid,
                fragment_type=json.get("fragment_type", "fact"),
                content=json["content"],
                importance_score=json.get("importance_score", 0.5),
                ttl=json.get("ttl"),
                workspace_id=wid,
            )
        if path == "/memory/fragments/search" and method == "POST":
            return frag_svc.search_fragments_by_semantic(
                user_id=uid, query=json["query"],
                top_k=json.get("top_k", 5),
                threshold=json.get("threshold", 0.3),
                workspace_id=wid,
            )

        # ---- Tables ----
        if path == "/memory/tables/" and method == "POST":
            return tbl_svc.create_memory_table(
                user_id=uid, table_name=json["table_name"],
                fields=json["fields"], workspace_id=wid,
            )
        if path == "/memory/tables/" and method == "GET":
            return tbl_svc.list_tables(user_id=uid, workspace_id=wid)

        raise EmbeddedModeError(f"嵌入模式未实现: {method} {path}")
