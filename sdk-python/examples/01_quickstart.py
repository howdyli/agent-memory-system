"""01 零配置入门 — pip install 即可运行，无需启动服务端（嵌入模式）。"""

from agent_memory import quickstart

mem = quickstart()  # 自动使用嵌入模式 + ~/.agent-memory/default.db

# 记住
mem.remember("user_name", "鑫海")
mem.remember("preferred_style", "极简设计")

# 召回
ctx = mem.recall_context("鑫海的信息")
print(f"召回上下文:\n{ctx or '(暂无相关记忆)'}")

# 遗忘
mem.forget("preferred_style")
variables = mem.list_variables()
# 嵌入模式返回纯 {key: value} 字典，HTTP 模式返回带 count 的包装结构
count = variables.get("count", len(variables)) if isinstance(variables, dict) else len(variables)
print(f"当前变量: {count} 条")

mem.close()
