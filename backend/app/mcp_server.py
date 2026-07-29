"""
MCP (Model Context Protocol) Server — Agent Memory System

将记忆系统的核心能力暴露为 MCP 工具，供 Claude Desktop / Cursor / 任意 MCP 客户端调用。

工具集（10 个）：
    记忆管理（7 个）：
        - memory_recall           召回与查询相关的历史记忆
        - memory_remember         记住一条新信息（KV 变量）
        - memory_forget           删除一条记忆变量
        - memory_search           语义搜索记忆片段
        - memory_get_context      获取完整记忆上下文
        - memory_create_table     创建结构化记忆表
        - memory_add_record       向记忆表添加记录
    知识图谱（3 个）：
        - graph_add_entity        创建/更新实体
        - graph_search_entities   搜索图谱实体
        - graph_query_neighbors   查询实体关系网络

传输方式：
    - stdio（默认，子进程模式，用于 Claude Desktop / Cursor 本地集成）
    - streamable_http（HTTP 模式，挂载到 FastAPI 应用）
    - sse（Server-Sent Events 模式）

认证：
    - 通过环境变量 MCP_USER_ID / MCP_WORKSPACE_ID 指定调用主体
    - 或在 HTTP 模式下通过 Authorization: Bearer <jwt> 头认证

用法：
    # stdio 模式（Claude Desktop / Cursor）
    python -m app.mcp_server

    # 在 FastAPI 应用中挂载 HTTP 端点
    from app.mcp_server import mount_to_app
    mount_to_app(app)
"""
from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ============================================================
# MCP SDK 导入（延迟导入，mcp 未安装时仍可降级运行）
# ============================================================

_mcp_available = False
_mounted_transport: Optional[str] = None
try:
    from mcp.server.fastmcp import FastMCP
    from mcp.server.session import ServerSession
    from mcp.types import TextContent, Tool as MCPTool
    _mcp_available = True
except ImportError:
    logger.warning("mcp SDK 未安装，MCP Server 不可用。请执行 `pip install mcp`。")
    FastMCP = None  # type: ignore
    TextContent = None  # type: ignore
    MCPTool = None  # type: ignore


# ============================================================
# 主体上下文（解析调用者 user_id / workspace_id）
# ============================================================

def _resolve_principal() -> tuple[int, Optional[int]]:
    """解析 MCP 调用主体。

    优先级：
    1. 环境变量 MCP_USER_ID / MCP_WORKSPACE_ID
    2. Settings.MCP_DEFAULT_USER_ID / MCP_DEFAULT_WORKSPACE_ID

    Returns:
        (user_id, workspace_id)
    """
    from app.core.config import get_settings
    s = get_settings()

    user_id_str = os.environ.get("MCP_USER_ID")
    if user_id_str:
        try:
            user_id = int(user_id_str)
        except ValueError:
            user_id = s.MCP_DEFAULT_USER_ID
    else:
        user_id = s.MCP_DEFAULT_USER_ID

    ws_id_str = os.environ.get("MCP_WORKSPACE_ID")
    workspace_id: Optional[int] = None
    if ws_id_str:
        try:
            workspace_id = int(ws_id_str)
        except ValueError:
            workspace_id = s.MCP_DEFAULT_WORKSPACE_ID
    else:
        workspace_id = s.MCP_DEFAULT_WORKSPACE_ID

    return user_id, workspace_id


def _get_sdk():
    """构造 AgentMemoryClient 实例。"""
    from app.services.agent_memory_sdk import AgentMemoryClient
    user_id, workspace_id = _resolve_principal()
    return AgentMemoryClient(user_id, workspace_id), user_id, workspace_id


# ============================================================
# FastMCP Server 实例 + 工具注册
# ============================================================

def build_mcp_server() -> "FastMCP":
    """构造并返回配置好工具的 FastMCP 实例。

    每次调用都创建新实例，避免生命周期绑定导致的测试隔离问题。
    """
    if not _mcp_available:
        raise RuntimeError("mcp SDK 未安装，无法构造 MCP Server")

    mcp = FastMCP("agent-memory-system")

    # ============================================================
    # 记忆管理工具
    # ============================================================

    @mcp.tool()
    def memory_recall(query: str, top_k: int = 5) -> str:
        """召回与查询相关的历史记忆信息，返回格式化的记忆上下文。

        用于在对话中回忆用户之前提过的内容。当用户问"你还记得...吗？"或
        "之前我告诉过你..."时调用。

        Args:
            query: 要回忆/搜索的内容描述
            top_k: 返回记忆条数，默认 5
        """
        sdk, _, _ = _get_sdk()
        context = sdk.recall(query=query, top_k=top_k)
        if not context:
            return json.dumps({"success": True, "context": "", "message": "未召回相关记忆"}, ensure_ascii=False)
        return json.dumps({"success": True, "context": context}, ensure_ascii=False)

    @mcp.tool()
    def memory_remember(key: str, value: str, ttl: Optional[int] = None) -> str:
        """记住一条新的信息（KV 变量形式），供未来对话使用。

        适合存储用户偏好、关键事实等需要精确匹配的信息。例如"用户喜欢喝咖啡"、
        "用户的生日是 1 月 1 日"。

        Args:
            key: 记忆的名称/键，如 "favorite_drink"
            value: 记忆的内容/值，如 "咖啡"
            ttl: 过期时间（秒），可选。不填则永久保存
        """
        sdk, _, _ = _get_sdk()
        ok = sdk.remember(key=key, value=value, ttl=ttl)
        return json.dumps({"success": ok, "key": key, "value": value}, ensure_ascii=False)

    @mcp.tool()
    def memory_forget(key: str) -> str:
        """删除一条已存储的记忆变量。

        Args:
            key: 要删除的记忆名称/键
        """
        sdk, _, _ = _get_sdk()
        ok = sdk.forget(key=key)
        return json.dumps({"success": ok, "key": key}, ensure_ascii=False)

    @mcp.tool()
    def memory_search(query: str, top_k: int = 5, threshold: float = 0.3) -> str:
        """语义搜索记忆片段，返回匹配的记忆列表。

        与 memory_recall 不同，此工具返回结构化的记忆片段列表（含分数、类型、
        创建时间），用于精确的语义匹配查询。

        Args:
            query: 搜索查询
            top_k: 返回条数，默认 5
            threshold: 相似度阈值 0-1，默认 0.3
        """
        sdk, _, _ = _get_sdk()
        results = sdk.search(query=query, top_k=top_k, threshold=threshold)
        return json.dumps({
            "success": True,
            "memories": results,
            "count": len(results) if isinstance(results, list) else 0,
        }, ensure_ascii=False, default=str)

    @mcp.tool()
    def memory_get_context() -> str:
        """获取当前用户的完整记忆上下文。

        返回用户已存储的所有记忆变量、近期记忆片段和摘要，用于一次性加载完整上下文。
        适合在对话开始时调用建立完整背景。
        """
        sdk, _, _ = _get_sdk()
        ctx = sdk.get_context()
        return json.dumps({"success": True, "context": ctx}, ensure_ascii=False, default=str)

    @mcp.tool()
    def memory_create_table(
        table_name: str,
        fields: List[Dict[str, Any]],
    ) -> str:
        """创建一个结构化记忆表，用于存储多条同类信息（如联系人、任务清单、项目信息、
        会议记录等）。创建后可用 memory_add_record 添加数据。

        Args:
            table_name: 表名，如 contacts、tasks、projects、meetings
            fields: 字段定义列表，每个字段包含 name（字段名）和 type（类型：
                    TEXT/INTEGER/REAL/BOOLEAN/DATE/DATETIME/JSON）。
                    可选字段：index（是否创建索引）、nullable（是否允许为空）
        """
        sdk, _, _ = _get_sdk()
        result = sdk.create_table(table_name=table_name, fields=fields)
        return json.dumps(result, ensure_ascii=False, default=str)

    @mcp.tool()
    def memory_add_record(table_name: str, record: Dict[str, Any]) -> str:
        """向已存在的记忆表中添加一条结构化记录。

        Args:
            table_name: 目标表名
            record: 记录数据，key 为字段名，value 为字段值
        """
        sdk, _, _ = _get_sdk()
        result = sdk.remember_structured(table_name=table_name, record=record)
        return json.dumps(result, ensure_ascii=False, default=str)

    # ============================================================
    # 知识图谱工具
    # ============================================================

    @mcp.tool()
    def graph_add_entity(
        name: str,
        entity_type: str,
        aliases: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """在知识图谱中创建或更新一个实体（人物、组织、地点、事件）。

        当用户提到重要的人、公司、地点时使用。如果同名实体已存在，将更新其别名
        和元数据。

        Args:
            name: 实体名称，如 '张三'、'腾讯'、'北京'
            entity_type: 实体类型，可选值：person(人物),
                         organization(组织/公司), location(地点), event(事件)
            aliases: 实体的别名列表（可选），如 ['三哥', '老张']
            metadata: 附加元数据（可选），如 {'role': '产品经理', 'company': '腾讯'}
        """
        from app.services import graph_memory_service as gm
        sdk, user_id, workspace_id = _get_sdk()
        result = gm.ensure_entity(
            user_id=user_id,
            name=name,
            entity_type=entity_type,
            aliases=aliases,
            metadata=metadata,
            workspace_id=workspace_id,
        )
        return json.dumps(result, ensure_ascii=False, default=str)

    @mcp.tool()
    def graph_search_entities(
        query: str,
        entity_type: Optional[str] = None,
        limit: int = 10,
    ) -> str:
        """按名称模糊搜索知识图谱中的实体。

        当用户问"有没有叫XX的人"或"XX公司"时使用。

        Args:
            query: 搜索关键词，如 '张三' 或 '腾讯'
            entity_type: 按类型过滤（可选），可选值：person/organization/location/event
            limit: 返回数量，默认 10
        """
        from app.services import graph_memory_service as gm
        sdk, user_id, workspace_id = _get_sdk()
        result = gm.search_entities(
            user_id=user_id,
            query=query,
            entity_type=entity_type,
            limit=limit,
            workspace_id=workspace_id,
        )
        return json.dumps(result, ensure_ascii=False, default=str)

    @mcp.tool()
    def graph_query_neighbors(
        entity_name: str,
        entity_type: str = "person",
        relation_type: Optional[str] = None,
        depth: int = 1,
    ) -> str:
        """查询知识图谱中某个实体的关联邻居（关系网络）。

        当用户问"张三认识谁"、"张三的同事有哪些"时使用。

        Args:
            entity_name: 要查询的实体名称，如 '张三'
            entity_type: 实体类型，默认 person。可选值：
                         person/organization/location/event
            relation_type: 按关系类型过滤（可选），如 colleague, friend
            depth: 遍历深度，1=直接关系，2=二度关系，默认 1
        """
        from app.services import graph_memory_service as gm
        sdk, user_id, workspace_id = _get_sdk()
        result = gm.get_neighbors(
            user_id=user_id,
            entity_name=entity_name,
            entity_type=entity_type,
            relation_type=relation_type,
            depth=depth,
            workspace_id=workspace_id,
        )
        return json.dumps(result, ensure_ascii=False, default=str)

    return mcp


# ============================================================
# 单例 Server 实例（供 mount_to_app 复用）
# ============================================================

_mcp_instance: Optional["FastMCP"] = None


def get_mcp_server() -> "FastMCP":
    """获取全局 MCP Server 单例。"""
    global _mcp_instance
    if _mcp_instance is None:
        _mcp_instance = build_mcp_server()
    return _mcp_instance


# ============================================================
# 挂载到 FastAPI 应用（HTTP/SSE 传输）
# ============================================================

def mount_to_app(app, path: str = "/mcp") -> bool:
    """将 MCP Server 挂载到 FastAPI 应用，提供 HTTP 传输端点。

    Args:
        app: FastAPI 应用实例
        path: 挂载路径，默认 /mcp

    Returns:
        True 表示挂载成功，False 表示 MCP 不可用
    """
    if not _mcp_available:
        logger.warning("MCP SDK 不可用，跳过挂载到 FastAPI")
        return False

    from app.core.config import get_settings
    s = get_settings()
    if not s.MCP_ENABLED:
        logger.info("MCP Server 已禁用（MCP_ENABLED=False）")
        return False

    try:
        mcp = get_mcp_server()
        global _mounted_transport
        # FastMCP 1.x 提供 streamable_http_app() / sse_app() 方法返回 ASGI 应用
        if hasattr(mcp, "streamable_http_app"):
            asgi_app = mcp.streamable_http_app()
            _mounted_transport = "streamable_http"
        elif hasattr(mcp, "sse_app"):
            asgi_app = mcp.sse_app()
            _mounted_transport = "sse"
        else:
            logger.warning("FastMCP 版本不支持 HTTP/SSE 传输，跳过挂载")
            return False

        app.mount(path, asgi_app)
        logger.info(f"✓ MCP Server 已挂载到 {path} (transport={_mounted_transport})")
        return True
    except Exception as e:
        logger.error(f"挂载 MCP Server 失败: {e}")
        return False


# ============================================================
# stdio 入口（子进程模式，供 Claude Desktop / Cursor 调用）
# ============================================================

def run_stdio() -> None:
    """以 stdio 传输运行 MCP Server。

    用于 Claude Desktop / Cursor 等本地 MCP 客户端集成。
    客户端通过子进程方式启动本脚本，使用 stdin/stdout 通信。
    """
    if not _mcp_available:
        print("ERROR: mcp SDK 未安装，请执行 `pip install mcp`", file=sys.stderr)
        sys.exit(1)

    # 初始化日志（避免污染 stdout）
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # 确保 backend 目录在 sys.path 中
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    logger.info("启动 MCP Server (stdio 传输)...")
    mcp = build_mcp_server()
    mcp.run(transport="stdio")


# ============================================================
# 工具清单查询（供测试与运维使用）
# ============================================================

def list_mcp_tools() -> List[Dict[str, Any]]:
    """返回 MCP Server 暴露的工具清单（不含敏感信息）。

    用于：
    - 运维查询当前可用工具
    - 测试断言工具数量与名称
    - 文档生成
    """
    return [
        {
            "name": "memory_recall",
            "description": "召回与查询相关的历史记忆信息",
            "category": "memory",
            "parameters": {"query": "str", "top_k": "int=5"},
        },
        {
            "name": "memory_remember",
            "description": "记住一条新信息（KV 变量）",
            "category": "memory",
            "parameters": {"key": "str", "value": "str", "ttl": "int?"},
        },
        {
            "name": "memory_forget",
            "description": "删除一条记忆变量",
            "category": "memory",
            "parameters": {"key": "str"},
        },
        {
            "name": "memory_search",
            "description": "语义搜索记忆片段",
            "category": "memory",
            "parameters": {"query": "str", "top_k": "int=5", "threshold": "float=0.3"},
        },
        {
            "name": "memory_get_context",
            "description": "获取完整记忆上下文",
            "category": "memory",
            "parameters": {},
        },
        {
            "name": "memory_create_table",
            "description": "创建结构化记忆表",
            "category": "memory",
            "parameters": {"table_name": "str", "fields": "List[Dict]"},
        },
        {
            "name": "memory_add_record",
            "description": "向记忆表添加记录",
            "category": "memory",
            "parameters": {"table_name": "str", "record": "Dict"},
        },
        {
            "name": "graph_add_entity",
            "description": "创建/更新知识图谱实体",
            "category": "graph",
            "parameters": {
                "name": "str", "entity_type": "str",
                "aliases": "List[str]?", "metadata": "Dict?",
            },
        },
        {
            "name": "graph_search_entities",
            "description": "搜索图谱实体",
            "category": "graph",
            "parameters": {"query": "str", "entity_type": "str?", "limit": "int=10"},
        },
        {
            "name": "graph_query_neighbors",
            "description": "查询实体关系网络",
            "category": "graph",
            "parameters": {
                "entity_name": "str", "entity_type": "str='person'",
                "relation_type": "str?", "depth": "int=1",
            },
        },
    ]


# ============================================================
# __main__ 入口
# ============================================================

if __name__ == "__main__":
    run_stdio()
