"""02 对话记忆集成 — pip install 即可运行，无需服务端；设置 OPENAI_API_KEY 可启用真实 LLM。"""

import os

from agent_memory import quickstart

mem = quickstart(preset="chatbot")  # 对话场景预设：偏好权重高、短半衰期


def chat(user_input: str) -> str:
    """带记忆的对话：召回上下文 → 调用 LLM → 沉淀新记忆。"""
    # 1. 召回相关记忆，注入 system prompt
    memory_ctx = mem.recall_context(user_input)
    system_prompt = f"你是贴心助手。已知用户信息:\n{memory_ctx or '(暂无)'}"

    # 2. 调用 LLM（OpenAI 兼容；无 API Key 时离线回显演示）
    if os.environ.get("OPENAI_API_KEY"):
        from openai import OpenAI

        resp = OpenAI().chat.completions.create(
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
        )
        answer = resp.choices[0].message.content
    else:
        answer = f"[离线演示] 收到: {user_input}（记忆上下文 {len(memory_ctx)} 字符）"

    # 3. 将用户表达沉淀为记忆片段（偏好类）
    mem.remember_fragment(user_input, fragment_type="preference", importance_score=0.7)
    return answer


if __name__ == "__main__":
    print(chat("我平时喜欢喝手冲咖啡，不加糖"))
    print(chat("周末推荐我做点什么？"))
    mem.close()
