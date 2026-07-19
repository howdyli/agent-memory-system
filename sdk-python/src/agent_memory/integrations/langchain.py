"""
LangChain Tool 集成

将记忆系统的工具封装为 LangChain StructuredTool，
可直接传给 LangChain Agent 使用。

用法：
    from agent_memory import MemoryClient
    from agent_memory.integrations.langchain import get_memory_tools

    client = MemoryClient(base_url="https://mem.example.com", api_key="amk_xxx")
    tools = get_memory_tools(client)
    agent = create_react_agent(llm, tools, ...)
"""
import json
import logging
from typing import List, Optional

logger = logging.getLogger(__name__)

try:
    from langchain_core.tools import StructuredTool
except ImportError:
    StructuredTool = None  # type: ignore


def get_memory_tools(client) -> List:
    """
    获取记忆系统和知识图谱的 LangChain 工具列表。

    Args:
        client: MemoryClient 实例

    Returns:
        13 个 StructuredTool 实例列表（7 记忆 + 6 图谱）
    """
    if StructuredTool is None:
        raise ImportError(
            "langchain-core 未安装。请运行: pip install agent-memory-sdk[langchain]"
        )

    sdk = client

    # ----------------------------------------------------------
    # 1. memory_recall
    # ----------------------------------------------------------
    def _memory_recall(query: str, top_k: int = 5) -> str:
        """召回与查询相关的历史记忆信息。"""
        context = sdk.recall_context(query=query, top_k=top_k)
        if context:
            return context
        return "没有找到相关的历史记忆。"

    # ----------------------------------------------------------
    # 2. memory_remember
    # ----------------------------------------------------------
    def _memory_remember(key: str, value: str) -> str:
        """记住一条新的信息，供未来对话使用。"""
        ok = sdk.remember(key=key, value=value)
        if ok:
            return f"已记住: {key} = {value}"
        return f"记忆存储失败: {key}"

    # ----------------------------------------------------------
    # 3. memory_forget
    # ----------------------------------------------------------
    def _memory_forget(key: str) -> str:
        """删除一条已存储的记忆变量。"""
        ok = sdk.forget(key=key)
        if ok:
            return f"已删除记忆: {key}"
        return f"删除失败或记忆不存在: {key}"

    # ----------------------------------------------------------
    # 4. memory_search
    # ----------------------------------------------------------
    def _memory_search(query: str, top_k: int = 5) -> str:
        """语义搜索记忆片段，返回匹配的记忆列表。"""
        results = sdk.search(query=query, top_k=top_k)
        if not results:
            return "没有找到匹配的记忆片段。"
        lines = []
        for i, mem in enumerate(results, 1):
            content = mem.get("content", mem.get("document", ""))
            score = mem.get("similarity_score", mem.get("score", "N/A"))
            lines.append(f"{i}. [相关度: {score}] {content}")
        return "\n".join(lines)

    # ----------------------------------------------------------
    # 5. memory_get_context
    # ----------------------------------------------------------
    def _memory_get_context() -> str:
        """获取当前用户的完整记忆上下文。"""
        ctx = sdk.get_context()
        if ctx:
            return ctx
        return "当前没有存储的记忆信息。"

    # ----------------------------------------------------------
    # 6. memory_create_table
    # ----------------------------------------------------------
    def _memory_create_table(table_name: str, fields: List[dict]) -> str:
        """创建一个结构化记忆表。"""
        result = sdk.create_table(table_name=table_name, fields=fields)
        return json.dumps(result, ensure_ascii=False)

    # ----------------------------------------------------------
    # 7. memory_add_record
    # ----------------------------------------------------------
    def _memory_add_record(table_name: str, record: dict) -> str:
        """向已存在的记忆表中添加一条记录。"""
        result = sdk.remember_structured(table_name=table_name, record=record)
        return json.dumps(result, ensure_ascii=False)

    # ----------------------------------------------------------
    # 8-13. Graph tools (通过 SDK graph 子模块)
    # ----------------------------------------------------------
    def _graph_add_entity(name: str, entity_type: str, aliases: Optional[List[str]] = None, metadata: Optional[dict] = None) -> str:
        """在知识图谱中创建或更新一个实体。"""
        result = sdk.graph.create_entity(name=name, entity_type=entity_type, properties=metadata)
        return json.dumps(result, ensure_ascii=False, default=str)

    def _graph_add_relationship(
        source_name: str, target_name: str, relation_type: str,
        source_type: str = "person", target_type: str = "organization",
        confidence: float = 0.8,
    ) -> str:
        """在知识图谱中创建两个实体之间的关系。"""
        # 通过 HTTP API 的 graph 端点
        result = sdk._transport.request("POST", "/memory/graph/entities/relationship", json={
            "source_name": source_name, "target_name": target_name,
            "relation_type": relation_type, "source_type": source_type,
            "target_type": target_type, "confidence": confidence,
            "extraction_source": "agent_tool",
        })
        return json.dumps(result, ensure_ascii=False, default=str)

    def _graph_search_entities(query: str, entity_type: Optional[str] = None, limit: int = 10) -> str:
        """按名称模糊搜索知识图谱中的实体。"""
        result = sdk.graph.search_entities(query=query)
        return json.dumps(result, ensure_ascii=False, default=str)

    def _graph_query_neighbors(
        entity_name: str, entity_type: str = "person",
        relation_type: Optional[str] = None, depth: int = 1,
    ) -> str:
        """查询知识图谱中某个实体的关联邻居。"""
        # 先搜索实体获取 ID
        entities = sdk.graph.search_entities(query=entity_name)
        if not entities:
            return json.dumps({"error": "未找到实体"}, ensure_ascii=False)
        entity_id = entities[0].get("id", entity_name)
        result = sdk.graph.get_neighbors(entity_id=entity_id, depth=depth)
        return json.dumps(result, ensure_ascii=False, default=str)

    def _graph_analyze(query: str) -> str:
        """对知识图谱进行自然语言查询和关系分析。"""
        result = sdk.graph.query_graph(query=query)
        return json.dumps(result, ensure_ascii=False, default=str)

    def _graph_extract_from_text(text: str) -> str:
        """从文本中自动抽取实体和关系存入知识图谱。"""
        result = sdk.graph.extract_entities(text=text)
        return json.dumps(result, ensure_ascii=False, default=str)

    # ----------------------------------------------------------
    # 构建 StructuredTool 列表
    # ----------------------------------------------------------
    tools = [
        StructuredTool.from_function(
            func=_memory_recall,
            name="memory_recall",
            description="召回与查询相关的历史记忆信息，返回格式化的记忆上下文。当你需要回忆之前聊过的内容时使用此工具。",
        ),
        StructuredTool.from_function(
            func=_memory_remember,
            name="memory_remember",
            description="记住一条新的信息，供未来对话使用。当用户告诉你重要信息（如姓名、偏好、项目等）时使用此工具。",
        ),
        StructuredTool.from_function(
            func=_memory_forget,
            name="memory_forget",
            description="删除一条已存储的记忆变量。当用户要求忘记某些信息时使用此工具。",
        ),
        StructuredTool.from_function(
            func=_memory_search,
            name="memory_search",
            description="语义搜索记忆片段，返回匹配的记忆列表及其相关度评分。",
        ),
        StructuredTool.from_function(
            func=_memory_get_context,
            name="memory_get_context",
            description="获取当前用户的完整记忆上下文。当你需要了解用户的所有已知信息时使用此工具。",
        ),
        StructuredTool.from_function(
            func=_memory_create_table,
            name="memory_create_table",
            description="创建一个结构化记忆表，用于存储多条同类信息（如联系人、任务清单、项目信息、会议记录等）。创建后可用 memory_add_record 添加数据。",
        ),
        StructuredTool.from_function(
            func=_memory_add_record,
            name="memory_add_record",
            description="向已存在的记忆表中添加一条结构化记录。",
        ),
        StructuredTool.from_function(
            func=_graph_add_entity,
            name="graph_add_entity",
            description="在知识图谱中创建或更新一个实体（人物person/组织organization/地点location/事件event）。当用户提到重要的人、公司、地点时使用。",
        ),
        StructuredTool.from_function(
            func=_graph_add_relationship,
            name="graph_add_relationship",
            description="在知识图谱中创建两个实体之间的关系。支持：colleague(同事), friend(朋友), superior(上级), subordinate(下属), project_member(项目成员), family(家人), classmate(同学), mentor(导师)。",
        ),
        StructuredTool.from_function(
            func=_graph_search_entities,
            name="graph_search_entities",
            description="按名称模糊搜索知识图谱中的实体。当用户问'有没有叫XX的人'或'XX公司'时使用。",
        ),
        StructuredTool.from_function(
            func=_graph_query_neighbors,
            name="graph_query_neighbors",
            description="查询知识图谱中某个实体的关联邻居（关系网络）。当用户问'张三认识谁'、'张三的同事有哪些'时使用。",
        ),
        StructuredTool.from_function(
            func=_graph_analyze,
            name="graph_analyze",
            description="对知识图谱进行自然语言查询和关系分析。支持查询关系历史、关系网络、实体搜索等。",
        ),
        StructuredTool.from_function(
            func=_graph_extract_from_text,
            name="graph_extract_from_text",
            description="从文本中自动抽取实体（人物、组织、地点）和关系，并存入知识图谱。当用户提供描述性文本并希望自动构建知识图谱时使用。",
        ),
    ]

    return tools
