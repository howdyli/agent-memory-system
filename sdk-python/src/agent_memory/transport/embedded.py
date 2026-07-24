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
        from app.services import memory_extraction_service as ext_svc
        from app.services import auto_recall_service as recall_svc
        from app.services import graph_memory_service as graph_svc

        uid = self.user_id
        wid = self.workspace_id
        p = params or {}

        # 规范化路径：去除尾部斜杠（保留根路径 "/"）
        path = path.rstrip("/") or "/"

        # ---- Variables ----
        if path == "/memory/variables" and method == "POST":
            return var_svc.set_memory_variable(
                user_id=uid, key=json["key"], value=json["value"],
                ttl=json.get("ttl"), workspace_id=wid,
            )
        if path == "/memory/variables" and method == "GET":
            return var_svc.list_memory_variables(
                user_id=uid,
                session_id=p.get("session_id"),
                workspace_id=wid,
            )
        if path.startswith("/memory/variables/") and method == "GET":
            key = path.split("/")[-1]
            return var_svc.get_memory_variable(user_id=uid, key=key, workspace_id=wid)
        if path.startswith("/memory/variables/") and method == "DELETE":
            key = path.split("/")[-1]
            return var_svc.delete_memory_variable(user_id=uid, key=key, workspace_id=wid)
        if path.startswith("/memory/variables/") and method == "PUT":
            key = path.split("/")[-1]
            return var_svc.set_memory_variable(
                user_id=uid, key=key, value=json.get("value"), workspace_id=wid,
            )

        # ---- Fragments ----
        if path == "/memory/fragments" and method == "POST":
            return frag_svc.create_fragment(
                user_id=uid,
                fragment_type=json.get("fragment_type", "fact"),
                content=json["content"],
                importance_score=json.get("importance_score", 0.5),
                ttl=json.get("ttl"),
                workspace_id=wid,
            )
        if path == "/memory/fragments" and method == "GET":
            return frag_svc.list_fragments(
                user_id=uid,
                fragment_type=p.get("type"),
                workspace_id=wid,
            )
        if path.startswith("/memory/fragments/") and method == "GET":
            frag_id = int(path.split("/")[-1])
            return frag_svc.get_fragment(user_id=uid, fragment_id=frag_id, workspace_id=wid)
        if path.startswith("/memory/fragments/") and method == "PUT":
            frag_id = int(path.split("/")[-1])
            return frag_svc.update_fragment(
                user_id=uid, fragment_id=frag_id,
                content=json.get("content"),
                importance_score=json.get("importance_score"),
                ttl=json.get("ttl"),
                workspace_id=wid,
            )
        if path.startswith("/memory/fragments/") and method == "DELETE":
            frag_id = int(path.split("/")[-1])
            return frag_svc.delete_fragment(user_id=uid, fragment_id=frag_id, workspace_id=wid)
        if path == "/memory/fragments/search" and method == "POST":
            return frag_svc.search_fragments_by_semantic(
                user_id=uid, query=json["query"],
                top_k=json.get("top_k", 5),
                threshold=json.get("threshold", 0.3),
                workspace_id=wid,
            )

        # ---- Tables ----
        if path == "/memory/tables" and method == "POST":
            return tbl_svc.create_memory_table(
                user_id=uid, table_name=json["table_name"],
                fields=json["fields"], workspace_id=wid,
            )
        if path == "/memory/tables" and method == "GET":
            return tbl_svc.list_tables(user_id=uid, workspace_id=wid)

        # ---- Extraction ----
        if path == "/memory/extraction/context" and method == "GET":
            context_str = ext_svc.get_user_context_for_llm(
                user_id=uid, session_id=p.get("session_id"), workspace_id=wid,
            )
            return {"context": context_str, "success": True}

        # ---- Recall ----
        if path == "/memory/recall" and method == "POST":
            return recall_svc.auto_recall(
                user_id=uid, query=json["query"],
                workspace_id=wid,
                top_k=json.get("top_k"),
            )
        if path == "/memory/recall/search" and method == "POST":
            return recall_svc.search_relevant_memories(
                user_id=uid, query=json["query"],
                top_k=json.get("top_k", 5),
                threshold=json.get("threshold", 0.3),
                workspace_id=wid,
            )
        if path == "/memory/recall/config" and method == "GET":
            return recall_svc.get_recall_config(user_id=uid, workspace_id=wid)
        if path == "/memory/recall/config" and method == "PUT":
            return recall_svc.update_recall_config(user_id=uid, config=json, workspace_id=wid)

        # ---- Graph ----
        if path == "/memory/graph/entities" and method == "GET":
            return graph_svc.search_entities(
                user_id=uid, query=p.get("query", ""),
                entity_type=p.get("entity_type"),
                limit=int(p.get("limit", 20)),
                workspace_id=wid,
            )
        if path.startswith("/memory/graph/entities/") and method == "GET":
            entity_id = int(path.split("/")[-1])
            return graph_svc.get_entity(user_id=uid, entity_id=entity_id, workspace_id=wid)
        if path == "/memory/graph/entities" and method == "POST":
            return graph_svc.ensure_entity(
                user_id=uid, name=json["name"],
                entity_type=json["entity_type"],
                aliases=json.get("aliases"),
                metadata=json.get("metadata"),
                workspace_id=wid,
            )
        if path.startswith("/memory/graph/entities/") and method == "PUT":
            entity_id = int(path.split("/")[-1])
            return graph_svc.update_entity(
                user_id=uid, entity_id=entity_id,
                name=json.get("name"),
                entity_type=json.get("entity_type"),
                metadata=json.get("metadata"),
                workspace_id=wid,
            )
        if path.startswith("/memory/graph/entities/") and method == "DELETE":
            entity_id = int(path.split("/")[-1])
            return graph_svc.delete_entity(user_id=uid, entity_id=entity_id, workspace_id=wid)
        if path == "/memory/graph/relationships" and method == "POST":
            return graph_svc.add_relationship(
                user_id=uid,
                source_name=json["source_name"],
                target_name=json["target_name"],
                relation_type=json["relation_type"],
                source_type=json.get("source_type", "person"),
                target_type=json.get("target_type", "organization"),
                properties=json.get("properties"),
                confidence=json.get("confidence", 0.5),
                workspace_id=wid,
            )
        if path == "/memory/graph/relationships" and method == "GET":
            return graph_svc.list_relationships(
                user_id=uid,
                source_name=p.get("source_name"),
                target_name=p.get("target_name"),
                relation_type=p.get("relation_type"),
                is_active=p.get("is_active"),
                limit=int(p.get("limit", 50)),
                offset=int(p.get("offset", 0)),
                workspace_id=wid,
            )
        if path.startswith("/memory/graph/relationships/") and method == "DELETE":
            rel_id = int(path.split("/")[-1])
            return graph_svc.deactivate_relationship(user_id=uid, relationship_id=rel_id, workspace_id=wid)
        if path == "/memory/graph/neighbors" and method == "GET":
            return graph_svc.get_neighbors(
                user_id=uid,
                entity_id=p.get("entity_id"),
                entity_name=p.get("entity_name"),
                entity_type=p.get("entity_type", "person"),
                relation_type=p.get("relation_type"),
                depth=int(p.get("depth", 1)),
                workspace_id=wid,
            )
        if path == "/memory/graph/extract" and method == "POST":
            return graph_svc.extract_entities_from_text(user_id=uid, text=json["text"], workspace_id=wid)
        if path == "/memory/graph/query" and method == "GET":
            return graph_svc.query_graph(user_id=uid, query=p.get("q", ""), workspace_id=wid)
        if path == "/memory/graph/statistics" and method == "GET":
            return graph_svc.get_graph_statistics(user_id=uid, workspace_id=wid)

        raise EmbeddedModeError(f"嵌入模式未实现: {method} {path}")
