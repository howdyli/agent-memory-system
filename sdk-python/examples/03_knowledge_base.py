"""03 知识库构建 — pip install 即可运行，无需服务端（嵌入模式）。"""

from agent_memory import quickstart

# 知识库预设：top_k=10、低阈值、长半衰期，召回更全面
mem = quickstart(preset="knowledge")

# 1. 批量写入知识片段
docs = [
    ("FastAPI 使用 Pydantic 做请求校验，性能接近 Node/Go", "info", 0.8),
    ("SQLite WAL 模式支持读写并发，适合单机中等负载", "info", 0.7),
    ("向量检索阈值过高会漏召回，知识库场景建议 0.2 左右", "info", 0.9),
    ("ChromaDB 支持内存与持久化两种模式", "info", 0.6),
]
for content, ftype, score in docs:
    mem.remember_fragment(content, fragment_type=ftype, importance_score=score)
print(f"已写入 {len(docs)} 条知识片段")

# 2. 语义搜索（top_k/threshold 自动使用 knowledge 预设）
results = mem.search("数据库并发能力")
print(f"\n搜索『数据库并发能力』命中 {len(results)} 条:")
for r in results:
    print(f"  - {r.get('content', '')[:40]}... (相似度 {r.get('similarity', 0):.2f})")

# 3. 一句话召回可注入 Prompt 的上下文
ctx = mem.recall_context("如何调向量检索参数")
print(f"\n召回上下文:\n{ctx or '(暂无)'}")

mem.close()
