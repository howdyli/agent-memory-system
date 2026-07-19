"""
Agent Memory SDK 快速上手示例
================================

演示如何通过 Python SDK 调用记忆系统的核心能力：
  1. 记忆变量（KV）    —— 存/取/列/删
  2. 记忆片段（语义）  —— 创建 + 语义搜索
  3. 记忆表（结构化）  —— 建表 + 写入 + 查询
  4. 知识图谱          —— 实体 + 关系
  5. 自动召回          —— 一句话召回相关记忆
  6. 事件与 Webhook    —— 事件历史 + Webhook 订阅

运行前置条件：
  - 后端服务已启动：http://localhost:8000
  - 已创建 API Key（在「Workspace 设置 → API Key 管理」中创建）

运行方式：
  cd sdk-python
  pip install -e .
  python examples/quickstart.py
"""

from agent_memory import MemoryClient

# ────────────────────────────────────────────────────────────
# 配置：替换为你自己的 API Key
# 注意 base_url 需带上 /api/v1 前缀
# ────────────────────────────────────────────────────────────
BASE_URL = "http://localhost:8000/api/v1"
API_KEY = "amk_c6d60c23b4a496cbc63911077445f9d4655692e246924dc8b499783e338edcd4"


def section(title: str) -> None:
    """打印分节标题。"""
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def main() -> None:
    # with 语法确保结束时自动释放连接
    with MemoryClient(base_url=BASE_URL, api_key=API_KEY) as client:

        # ── 1. 记忆变量（KV 存储）──────────────────────────────
        section("1. 记忆变量 (Variables)")

        client.remember("user_name", "鑫海")
        client.remember("preferred_style", "极简设计", ttl=86400)  # TTL 1 天

        name = client.variables.get("user_name")
        print(f"读取 user_name -> {name}")

        all_vars = client.list_variables()
        print(f"当前变量总数 -> {all_vars.get('count', 0)}")

        # ── 2. 记忆片段（语义记忆）─────────────────────────────
        section("2. 记忆片段 (Fragments)")

        frag = client.remember_fragment(
            content="用户喜欢在周末进行户外徒步，偏好高强度路线",
            fragment_type="preference",
            importance_score=0.9,
        )
        print(f"创建片段 -> id={frag.get('id')}")

        results = client.search("用户的运动偏好", top_k=3)
        print(f"语义搜索命中 {len(results)} 条")
        for r in results:
            print(f"  - {r.get('content', '')[:40]}")

        # ── 3. 记忆表（结构化记忆）─────────────────────────────
        section("3. 记忆表 (Tables)")

        client.create_table(
            table_name="contacts",
            fields=[
                {"name": "name", "type": "string"},
                {"name": "role", "type": "string"},
                {"name": "priority", "type": "integer"},
            ],
        )
        client.remember_structured(
            "contacts",
            {"name": "张伟", "role": "产品经理", "priority": 1},
        )
        records = client.tables.query_records("contacts")
        print(f"contacts 表记录数 -> {len(records)}")

        # ── 4. 知识图谱（实体 + 关系）──────────────────────────
        section("4. 知识图谱 (Graph)")

        alice = client.graph.create_entity(name="Alice", entity_type="person")
        acme = client.graph.create_entity(name="Acme Corp", entity_type="organization")
        print(f"创建实体 -> {alice.get('name')} / {acme.get('name')}")

        # 关系基于实体名称创建（后端会自动关联同名实体）
        client.graph.create_relationship(
            source_name="Alice",
            target_name="Acme Corp",
            relation_type="works_at",
            source_type="person",
            target_type="organization",
        )
        print("创建关系 -> Alice --works_at--> Acme Corp")

        stats = client.graph.get_statistics()
        print(f"图谱统计 -> {stats}")

        # ── 5. 自动召回（一句话召回上下文）─────────────────────
        section("5. 自动召回 (Recall)")

        context = client.recall_context("鑫海周末喜欢做什么？")
        print(f"召回上下文:\n{context or '（暂无相关记忆）'}")

        # ── 6. 事件与 Webhook ──────────────────────────────────
        section("6. 事件与 Webhook (Events / Webhooks)")

        event_types = client.events.list_event_types()
        print(f"支持的事件类型总数 -> {len(event_types)}")

        recent = client.events.list_events(days=1, limit=5)
        print(f"最近 1 天事件 -> {len(recent)} 条")

        # 创建一个 Webhook 订阅（示例回调地址）
        hook = client.webhooks.create(
            url="https://example.com/webhook",
            event_types=["memory.created", "memory.updated"],
            description="SDK quickstart demo",
        )
        hook_id = hook.get("id")
        print(f"创建 Webhook -> id={hook_id}")

        if hook_id:
            # 清理：删除刚创建的 Webhook
            client.webhooks.delete(hook_id)
            print(f"已清理 Webhook id={hook_id}")

        section("✅ 全部示例执行完毕")


if __name__ == "__main__":
    main()
