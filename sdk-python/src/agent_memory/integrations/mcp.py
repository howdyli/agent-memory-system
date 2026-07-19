"""
MCP Server 集成 (Model Context Protocol)

实现 MCP 服务端，让任何支持 MCP 协议的 Agent（如 Claude Desktop、
Cursor、Qoder 等）通过标准协议访问记忆系统。

独立运行：
    python -m agent_memory.integrations.mcp

也可作为模块导入：
    from agent_memory.integrations.mcp import create_mcp_server
"""
import logging
import json
import asyncio
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    HAS_MCP = True
except ImportError:
    HAS_MCP = False

# 默认 user_id（可通过环境变量或启动参数覆盖）
DEFAULT_USER_ID = 1


def create_mcp_server(client=None, user_id: Optional[int] = None):
    """
    创建 MCP Server 实例并注册记忆工具。

    Args:
        client: MemoryClient 实例（优先）
        user_id: 用户 ID（兼容旧接口，当 client 为 None 时使用）

    Returns:
        配置好的 MCP Server 实例
    """
    if not HAS_MCP:
        raise ImportError(
            "mcp 未安装。请运行: pip install agent-memory-sdk[mcp]"
        )

    # 如果没有传入 client，使用 user_id 创建嵌入模式 client
    if client is None:
        from agent_memory import MemoryClient
        uid = user_id or DEFAULT_USER_ID
        client = MemoryClient(mode="embedded", user_id=uid)

    server = Server("agent-memory")

    @server.list_tools()
    async def list_tools() -> List[Tool]:
        """返回可用的记忆工具列表。"""
        return [
            Tool(
                name="memory_recall",
                description="召回与查询相关的历史记忆信息，返回格式化的记忆上下文。当你需要回忆之前聊过的内容时使用。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "要回忆/搜索的内容描述"},
                        "top_k": {"type": "integer", "description": "返回记忆条数，默认 5", "default": 5},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="memory_remember",
                description="记住一条新的信息，供未来对话使用。当用户告诉你重要信息（如姓名、偏好、项目等）时使用。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "记忆的名称/键"},
                        "value": {"type": "string", "description": "记忆的内容/值"},
                    },
                    "required": ["key", "value"],
                },
            ),
            Tool(
                name="memory_forget",
                description="删除一条已存储的记忆变量。当用户要求忘记某些信息时使用。",
                inputSchema={
                    "type": "object",
                    "properties": {"key": {"type": "string", "description": "要删除的记忆名称/键"}},
                    "required": ["key"],
                },
            ),
            Tool(
                name="memory_search",
                description="语义搜索记忆片段，返回匹配的记忆列表及其相关度评分。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "搜索查询"},
                        "top_k": {"type": "integer", "description": "返回条数，默认 5", "default": 5},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="memory_get_context",
                description="获取当前用户的完整记忆上下文。当你需要了解用户的所有已知信息时使用。",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="memory_create_table",
                description="创建一个结构化记忆表，用于存储多条同类信息（如联系人、任务清单、项目信息、会议记录等）。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "description": "表名，如 contacts、tasks、projects"},
                        "fields": {
                            "type": "array",
                            "description": "字段定义列表",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "type": {"type": "string", "description": "TEXT/INTEGER/REAL/BOOLEAN/DATE/DATETIME/JSON"},
                                },
                                "required": ["name", "type"],
                            },
                        },
                    },
                    "required": ["table_name", "fields"],
                },
            ),
            Tool(
                name="memory_add_record",
                description="向已存在的记忆表中添加一条结构化记录。",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "table_name": {"type": "string", "description": "目标表名"},
                        "record": {"type": "object", "description": "记录数据，key 为字段名，value 为字段值"},
                    },
                    "required": ["table_name", "record"],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: Dict[str, Any]) -> List[TextContent]:
        """处理工具调用。"""
        try:
            if name == "memory_recall":
                query = arguments.get("query", "")
                top_k = arguments.get("top_k", 5)
                context = client.recall_context(query=query, top_k=top_k)
                result_text = context if context else "没有找到相关的历史记忆。"

            elif name == "memory_remember":
                key = arguments.get("key", "")
                value = arguments.get("value", "")
                ok = client.remember(key=key, value=value)
                result_text = f"已记住: {key} = {value}" if ok else f"记忆存储失败: {key}"

            elif name == "memory_forget":
                key = arguments.get("key", "")
                ok = client.forget(key=key)
                result_text = f"已删除记忆: {key}" if ok else f"删除失败或记忆不存在: {key}"

            elif name == "memory_search":
                query = arguments.get("query", "")
                top_k = arguments.get("top_k", 5)
                results = client.search(query=query, top_k=top_k)
                if not results:
                    result_text = "没有找到匹配的记忆片段。"
                else:
                    lines = []
                    for i, mem in enumerate(results, 1):
                        content = mem.get("content", mem.get("document", ""))
                        score = mem.get("similarity_score", mem.get("score", "N/A"))
                        lines.append(f"{i}. [相关度: {score}] {content}")
                    result_text = "\n".join(lines)

            elif name == "memory_get_context":
                ctx = client.get_context()
                result_text = ctx if ctx else "当前没有存储的记忆信息。"

            elif name == "memory_create_table":
                result = client.create_table(
                    table_name=arguments.get("table_name", ""),
                    fields=arguments.get("fields", []),
                )
                result_text = json.dumps(result, ensure_ascii=False)

            elif name == "memory_add_record":
                result = client.remember_structured(
                    table_name=arguments.get("table_name", ""),
                    record=arguments.get("record", {}),
                )
                result_text = json.dumps(result, ensure_ascii=False)

            else:
                result_text = f"未知工具: {name}"

        except Exception as e:
            logger.error(f"MCP 工具调用失败 [{name}]: {e}")
            result_text = f"工具执行失败: {str(e)}"

        return [TextContent(type="text", text=result_text)]

    return server


async def main():
    """MCP Server 主入口（stdio 传输模式）。"""
    import os

    if not HAS_MCP:
        print("Error: mcp package not installed. Run: pip install agent-memory-sdk[mcp]")
        return

    user_id = int(os.environ.get("AGENT_MEMORY_USER_ID", str(DEFAULT_USER_ID)))
    logger.info(f"启动 Agent Memory MCP Server (user_id={user_id})")

    server = create_mcp_server(user_id=user_id)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    asyncio.run(main())
