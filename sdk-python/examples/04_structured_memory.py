"""04 结构化记忆表 — pip install 即可运行，无需服务端（嵌入模式）。"""

from agent_memory import quickstart

mem = quickstart()
TABLE = "reading_list"

# 1. 建表（重复运行时已存在则跳过）
if not any(t.get("table_name") == TABLE for t in mem.list_tables()):
    mem.create_table(TABLE, fields=[
        {"name": "title", "type": "text", "required": True},
        {"name": "author", "type": "text"},
        {"name": "rating", "type": "number"},
    ])
    print(f"已创建记忆表: {TABLE}")

# 2. 写入结构化记录
mem.remember_structured(TABLE, {"title": "设计中的设计", "author": "原研哉", "rating": 9})
mem.remember_structured(TABLE, {"title": "SQL 反模式", "author": "Bill Karwin", "rating": 8})

# 3. 查询
records = mem.tables.query_records(TABLE)
print(f"当前书单 {len(records)} 条:")
for r in records:
    print(f"  - {r.get('title')} / {r.get('author')} / 评分 {r.get('rating')}")

mem.close()
