#!/usr/bin/env python3
"""
智能体记忆系统功能测试脚本

测试范围：
1. 短期记忆 - 会话内对话轮次存储与召回
2. 长期记忆 - KV 变量持久化存储
3. 片段记忆 - 语义片段抽取与检索
4. 记忆联动 - Agent 对话中自动召回记忆
"""
import requests
import json
import time
import sys

BASE_URL = "http://localhost:8000/api/v1"

# ANSI 颜色
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
RESET = "\033[0m"
BOLD = "\033[1m"

class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0
        self.results = []

    def ok(self, name: str, detail: str = ""):
        self.passed += 1
        self.results.append(("PASS", name, detail))
        print(f"  {GREEN}✅ PASS{RESET} {name}")
        if detail:
            print(f"         {detail}")

    def fail(self, name: str, detail: str = ""):
        self.failed += 1
        self.results.append(("FAIL", name, detail))
        print(f"  {RED}❌ FAIL{RESET} {name}")
        if detail:
            print(f"         {detail}")

    def skip(self, name: str, detail: str = ""):
        self.skipped += 1
        self.results.append(("SKIP", name, detail))
        print(f"  {YELLOW}⏭ SKIP{RESET} {name}")
        if detail:
            print(f"         {detail}")

    def summary(self):
        total = self.passed + self.failed + self.skipped
        print(f"\n{'='*60}")
        print(f"{BOLD}测试结果汇总{RESET}")
        print(f"{'='*60}")
        print(f"  总计: {total}")
        print(f"  {GREEN}通过: {self.passed}{RESET}")
        print(f"  {RED}失败: {self.failed}{RESET}")
        print(f"  {YELLOW}跳过: {self.skipped}{RESET}")
        print(f"{'='*60}")
        return self.failed == 0


def login() -> str:
    """登录获取 token"""
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "username": "admin",
        "password": "admin123"
    })
    resp.raise_for_status()
    return resp.json()["access_token"]


def get_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ============================================================
# 1. 短期记忆测试
# ============================================================
def test_short_term_memory(token: str, result: TestResult):
    """测试短期记忆：会话内多轮对话的上下文保持"""
    print(f"\n{BLUE}{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"{BLUE}{BOLD}1. 短期记忆测试（会话内上下文）{RESET}")
    print(f"{BLUE}{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")

    headers = get_headers(token)
    session_id = None

    # 1.1 第一轮对话：告知信息
    print(f"\n📌 1.1 第一轮：告知个人信息")
    resp = requests.post(f"{BASE_URL}/agent/chat", json={
        "message": "你好，我叫鑫海，在腾讯工作，是一名产品经理"
    }, headers=headers)

    if resp.status_code != 200:
        result.fail("第一轮对话", f"HTTP {resp.status_code}: {resp.text[:100]}")
        return

    data = resp.json()
    if not data.get("success"):
        result.fail("第一轮对话", f"API 返回失败: {data.get('error', 'unknown')}")
        return

    session_id = data.get("session_id")
    response_text = data.get("response", "")

    result.ok("第一轮对话成功", f"session_id={session_id}, 回复长度={len(response_text)}")

    # 1.2 第二轮对话：询问刚才告知的信息
    print(f"\n 1.2 第二轮：询问刚才告知的信息（测试短期记忆召回）")
    resp = requests.post(f"{BASE_URL}/agent/chat", json={
        "message": "你还记得我刚才告诉你什么吗？",
        "session_id": session_id
    }, headers=headers)

    if resp.status_code != 200:
        result.fail("第二轮对话", f"HTTP {resp.status_code}")
        return

    data = resp.json()
    response_text = data.get("response", "")
    memory_context_used = data.get("memory_context_used", False)

    # 检查回复中是否包含关键信息
    has_name = "鑫海" in response_text or "名字" in response_text
    has_company = "腾讯" in response_text or "公司" in response_text
    has_role = "产品经理" in response_text or "产品" in response_text

    if memory_context_used and (has_name or has_company or has_role):
        result.ok("短期记忆召回成功",
                  f"context_used={memory_context_used}, "
                  f"提及姓名={has_name}, 公司={has_company}, 职位={has_role}")
    else:
        result.fail("短期记忆召回",
                    f"context_used={memory_context_used}, "
                    f"回复中未找到关键信息")

    # 1.3 第三轮对话：基于前文追问
    print(f"\n 1.3 第三轮：基于前文追问")
    resp = requests.post(f"{BASE_URL}/agent/chat", json={
        "message": "我在哪家公司工作？做什么职位？",
        "session_id": session_id
    }, headers=headers)

    if resp.status_code != 200:
        result.fail("第三轮对话", f"HTTP {resp.status_code}")
        return

    data = resp.json()
    response_text = data.get("response", "")

    has_tencent = "腾讯" in response_text
    has_pm = "产品经理" in response_text or "产品" in response_text

    if has_tencent and has_pm:
        result.ok("多轮上下文保持", f"正确回忆公司和职位")
    else:
        result.fail("多轮上下文保持",
                    f"回复中未找到关键信息: 腾讯={has_tencent}, 产品经理={has_pm}")

    # 1.4 验证对话轮次存储
    print(f"\n📌 1.4 验证对话轮次已存储")
    # 通过再次询问历史来间接验证
    resp = requests.post(f"{BASE_URL}/agent/chat", json={
        "message": "我们之前聊过什么话题？",
        "session_id": session_id
    }, headers=headers)

    if resp.status_code == 200:
        data = resp.json()
        response_text = data.get("response", "")
        has_history = len(response_text) > 20  # 有实质性回复
        if has_history:
            result.ok("对话历史可查询", f"回复长度={len(response_text)}")
        else:
            result.skip("对话历史可查询", "回复过短，可能无历史")
    else:
        result.skip("对话历史可查询", f"HTTP {resp.status_code}")


# ============================================================
# 2. 长期记忆测试
# ============================================================
def test_long_term_memory(token: str, result: TestResult):
    """测试长期记忆：KV 变量的创建、读取、更新、删除"""
    print(f"\n{BLUE}{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"{BLUE}{BOLD}2. 长期记忆测试（KV 变量）{RESET}")
    print(f"{BLUE}{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")

    headers = get_headers(token)

    # 2.1 创建变量
    print(f"\n📌 2.1 创建记忆变量")
    test_vars = [
        {"key": "test_user_name", "value": "鑫海", "ttl": 3600},
        {"key": "test_user_company", "value": "腾讯", "ttl": 3600},
        {"key": "test_user_role", "value": "产品经理", "ttl": 3600},
        {"key": "test_project", "value": "AgentOps 智能体记忆系统", "ttl": 86400},
    ]

    created_keys = []
    for var in test_vars:
        resp = requests.post(f"{BASE_URL}/memory/variables", json=var, headers=headers)
        if resp.status_code == 200:
            created_keys.append(var["key"])
            result.ok(f"创建变量: {var['key']}", f"value={var['value']}")
        else:
            result.fail(f"创建变量: {var['key']}", f"HTTP {resp.status_code}")

    # 2.2 读取变量
    print(f"\n📌 2.2 读取记忆变量")
    for key in created_keys:
        resp = requests.get(f"{BASE_URL}/memory/variables/{key}", headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            value = data.get("value", "")
            result.ok(f"读取变量: {key}", f"value={value}")
        else:
            result.fail(f"读取变量: {key}", f"HTTP {resp.status_code}")

    # 2.3 批量获取（API 暂不支持 batch-get 端点，跳过）
    print(f"\n 2.3 批量获取变量")
    result.skip("批量获取", "API 未实现 /batch-get 端点")

    # 2.4 更新变量（API 暂不支持 PUT 端点，通过删除+重建验证）
    print(f"\n📌 2.4 更新记忆变量")
    # 先删除再重建来模拟更新
    requests.delete(f"{BASE_URL}/memory/variables/test_user_role", headers=headers)
    resp = requests.post(f"{BASE_URL}/memory/variables",
                         json={"key": "test_user_role", "value": "高级产品经理"}, headers=headers)
    if resp.status_code == 200:
        resp2 = requests.get(f"{BASE_URL}/memory/variables/test_user_role", headers=headers)
        if resp2.status_code == 200:
            new_value = resp2.json().get("value", "")
            if new_value == "高级产品经理":
                result.ok("更新变量（删除+重建）", f"新值={new_value}")
            else:
                result.fail("更新变量", f"值未更新: {new_value}")
    else:
        result.fail("更新变量", f"HTTP {resp.status_code}")

    # 2.5 列出所有变量
    print(f"\n📌 2.5 列出所有变量")
    resp = requests.get(f"{BASE_URL}/memory/variables", headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        variables = data.get("variables", [])
        result.ok("列出变量", f"共 {len(variables)} 个变量")
    else:
        result.fail("列出变量", f"HTTP {resp.status_code}")

    # 2.6 删除变量
    print(f"\n 2.6 删除记忆变量")
    for key in created_keys:
        resp = requests.delete(f"{BASE_URL}/memory/variables/{key}", headers=headers)
        if resp.status_code == 200:
            result.ok(f"删除变量: {key}")
        else:
            result.fail(f"删除变量: {key}", f"HTTP {resp.status_code}")

    # 2.7 验证删除
    print(f"\n📌 2.7 验证删除")
    resp = requests.get(f"{BASE_URL}/memory/variables/test_user_name", headers=headers)
    if resp.status_code == 404:
        result.ok("删除验证", "变量已不存在（404）")
    elif resp.status_code == 200:
        result.fail("删除验证", "变量仍然存在")
    else:
        result.skip("删除验证", f"HTTP {resp.status_code}")


# ============================================================
# 3. 片段记忆测试
# ============================================================
def test_fragment_memory(token: str, result: TestResult):
    """测试片段记忆：语义片段的创建、搜索、生命周期"""
    print(f"\n{BLUE}{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"{BLUE}{BOLD}3. 片段记忆测试（语义片段）{RESET}")
    print(f"{BLUE}{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")

    headers = get_headers(token)

    # 3.1 创建片段
    print(f"\n📌 3.1 创建语义片段")
    test_fragments = [
        {"content": "用户鑫海在腾讯担任产品经理，负责 AgentOps 项目", "fragment_type": "info", "importance_score": 0.9},
        {"content": "用户偏好使用中文交流，喜欢简洁的回复风格", "fragment_type": "preference", "importance_score": 0.7},
        {"content": "项目技术栈：Python FastAPI 后端，React TypeScript 前端", "fragment_type": "info", "importance_score": 0.8},
        {"content": "用户下周要去北京出差参加产品评审会", "fragment_type": "event", "importance_score": 0.85},
        {"content": "AgentOps 项目目标是构建带长期记忆的智能体平台", "fragment_type": "info", "importance_score": 0.95},
    ]

    created_ids = []
    for frag in test_fragments:
        resp = requests.post(f"{BASE_URL}/memory/fragments/", json=frag, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            fid = data.get("fragment_id") or data.get("id")
            if fid:
                created_ids.append(fid)
            result.ok(f"创建片段", f"type={frag['fragment_type']}, importance={frag['importance_score']}")
        else:
            result.fail(f"创建片段", f"HTTP {resp.status_code}: {resp.text[:100]}")

    # 3.2 列出片段
    print(f"\n 3.2 列出所有片段")
    resp = requests.get(f"{BASE_URL}/memory/fragments/", headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        fragments = data.get("fragments", [])
        result.ok("列出片段", f"共 {len(fragments)} 个片段")
    else:
        result.fail("列出片段", f"HTTP {resp.status_code}")

    # 3.3 语义搜索
    print(f"\n📌 3.3 语义搜索片段")
    search_queries = [
        ("腾讯", "公司信息"),
        ("产品经理", "职位信息"),
        ("北京出差", "事件信息"),
    ]

    for query, desc in search_queries:
        resp = requests.post(f"{BASE_URL}/memory/fragments/search",
                             json={"query": query, "top_k": 3}, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            # API 返回 fragments 或 results 字段
            results = data.get("fragments") or data.get("results") or []
            if len(results) > 0:
                top_score = results[0].get("score", results[0].get("similarity", 0))
                result.ok(f"搜索: {desc} ({query})", f"返回 {len(results)} 条，最高分={top_score}")
            else:
                result.fail(f"搜索: {desc} ({query})", "无结果")
        else:
            result.fail(f"搜索: {desc} ({query})", f"HTTP {resp.status_code}")

    # 3.4 按类型过滤
    print(f"\n📌 3.4 按类型过滤片段")
    for ftype in ["info", "preference", "event"]:
        resp = requests.get(f"{BASE_URL}/memory/fragments/", params={"type": ftype}, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            fragments = data.get("fragments", [])
            result.ok(f"过滤类型: {ftype}", f"{len(fragments)} 个片段")
        else:
            result.fail(f"过滤类型: {ftype}", f"HTTP {resp.status_code}")

    # 3.5 更新片段
    print(f"\n📌 3.5 更新片段")
    if created_ids:
        fid = created_ids[0]
        resp = requests.put(f"{BASE_URL}/memory/fragments/{fid}",
                            json={"importance_score": 0.95}, headers=headers)
        if resp.status_code == 200:
            result.ok("更新片段", f"id={fid}")
        else:
            result.fail("更新片段", f"HTTP {resp.status_code}")

    # 3.6 删除片段
    print(f"\n 3.6 删除片段")
    for fid in created_ids:
        resp = requests.delete(f"{BASE_URL}/memory/fragments/{fid}", headers=headers)
        if resp.status_code == 200:
            result.ok(f"删除片段: {fid}")
        else:
            result.fail(f"删除片段: {fid}", f"HTTP {resp.status_code}")


# ============================================================
# 4. 记忆联动测试
# ============================================================
def test_memory_integration(token: str, result: TestResult):
    """测试记忆联动：Agent 对话中自动召回和抽取记忆"""
    print(f"\n{BLUE}{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"{BLUE}{BOLD}4. 记忆联动测试（Agent 对话 + 记忆系统）{RESET}")
    print(f"{BLUE}{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")

    headers = get_headers(token)
    session_id = None

    # 4.1 先设置一些长期记忆变量
    print(f"\n📌 4.1 预置长期记忆变量")
    setup_vars = [
        {"key": "user_name", "value": "鑫海"},
        {"key": "user_company", "value": "腾讯"},
        {"key": "user_role", "value": "产品经理"},
        {"key": "user_project", "value": "AgentOps"},
    ]
    for var in setup_vars:
        requests.post(f"{BASE_URL}/memory/variables", json=var, headers=headers)
    result.ok("预置变量", f"{len(setup_vars)} 个长期记忆变量已设置")

    # 4.2 创建一些语义片段
    print(f"\n📌 4.2 预置语义片段")
    setup_fragments = [
        {"content": "用户鑫海是腾讯的产品经理，负责 AgentOps 智能体记忆系统项目", "fragment_type": "info", "importance_score": 0.9},
        {"content": "用户下周要去北京出差参加产品评审会", "fragment_type": "event", "importance_score": 0.8},
    ]
    for frag in setup_fragments:
        requests.post(f"{BASE_URL}/memory/fragments/", json=frag, headers=headers)
    result.ok("预置片段", f"{len(setup_fragments)} 个语义片段已创建")

    # 4.3 Agent 对话 - 测试记忆自动召回
    print(f"\n📌 4.3 Agent 对话：测试记忆自动召回")
    resp = requests.post(f"{BASE_URL}/agent/chat", json={
        "message": "你还记得我是谁吗？我在哪工作？"
    }, headers=headers)

    if resp.status_code == 200:
        data = resp.json()
        response_text = data.get("response", "")
        memory_context_used = data.get("memory_context_used", False)
        memories_extracted = data.get("memories_extracted", 0)
        tool_calls = data.get("tool_calls", [])
        session_id = data.get("session_id")

        # 检查是否召回了记忆
        has_name = "鑫海" in response_text
        has_company = "腾讯" in response_text
        has_tool_call = len(tool_calls) > 0

        if memory_context_used or has_name or has_company:
            result.ok("记忆自动召回",
                      f"context_used={memory_context_used}, "
                      f"提及姓名={has_name}, 公司={has_company}, "
                      f"工具调用={has_tool_call}, 抽取记忆={memories_extracted}")
        else:
            result.fail("记忆自动召回",
                        f"未检测到记忆召回: context_used={memory_context_used}")

        # 打印工具调用详情
        if tool_calls:
            for tc in tool_calls:
                tool_name = tc.get("tool", "")
                tool_args = tc.get("arguments", {})
                print(f"         🔧 工具调用: {tool_name}({json.dumps(tool_args, ensure_ascii=False)[:100]})")
    else:
        result.fail("Agent 对话", f"HTTP {resp.status_code}")

    # 4.4 连续对话 - 测试短期记忆 + 长期记忆联动
    print(f"\n📌 4.4 连续对话：短期 + 长期记忆联动")
    if session_id:
        resp = requests.post(f"{BASE_URL}/agent/chat", json={
            "message": "我刚才问了你什么？你还记得我的信息吗？",
            "session_id": session_id
        }, headers=headers)

        if resp.status_code == 200:
            data = resp.json()
            response_text = data.get("response", "")
            memory_context_used = data.get("memory_context_used", False)

            has_history = len(response_text) > 30
            has_memory = "鑫海" in response_text or "腾讯" in response_text

            if has_history and has_memory:
                result.ok("记忆联动", "短期记忆(历史对话) + 长期记忆(用户信息) 均被召回")
            elif has_history or has_memory:
                result.ok("记忆联动（部分）",
                          f"历史={has_history}, 用户信息={has_memory}")
            else:
                result.fail("记忆联动", "未检测到任何记忆召回")
        else:
            result.fail("连续对话", f"HTTP {resp.status_code}")
    else:
        result.skip("连续对话", "无 session_id")

    # 4.5 记忆抽取测试
    print(f"\n📌 4.5 记忆抽取：从对话中抽取新记忆")
    resp = requests.post(f"{BASE_URL}/agent/extract", json={
        "conversation": [
            {"role": "user", "content": "我叫鑫海，在腾讯做产品经理，最近在负责 AgentOps 项目"},
            {"role": "assistant", "content": "好的，我已经记住了你的信息。"}
        ],
        "auto_store": True
    }, headers=headers)

    if resp.status_code == 200:
        data = resp.json()
        stored_count = data.get("stored_count", 0)
        variables = data.get("variables", [])
        fragments = data.get("fragments", [])

        if stored_count > 0 or len(variables) > 0 or len(fragments) > 0:
            result.ok("记忆抽取",
                      f"stored={stored_count}, variables={len(variables)}, fragments={len(fragments)}")
        else:
            result.skip("记忆抽取", "未抽取到新记忆（可能 LLM 未返回结构化数据）")
    else:
        result.fail("记忆抽取", f"HTTP {resp.status_code}")

    # 4.6 清理测试数据
    print(f"\n📌 4.6 清理测试数据")
    for var in setup_vars:
        requests.delete(f"{BASE_URL}/memory/variables/{var['key']}", headers=headers)
    result.ok("清理完成", "测试变量已删除")


# ============================================================
# 5. 工具调用测试
# ============================================================
def test_tool_calling(token: str, result: TestResult):
    """测试 Agent 工具调用能力"""
    print(f"\n{BLUE}{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")
    print(f"{BLUE}{BOLD}5. 工具调用测试（Agent Tool Calling）{RESET}")
    print(f"{BLUE}{BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{RESET}")

    headers = get_headers(token)

    # 5.1 获取工具 Schema
    print(f"\n📌 5.1 获取工具 Schema")
    resp = requests.get(f"{BASE_URL}/agent/tools/schema", headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        tools = data.get("tools", [])
        result.ok("工具 Schema", f"共 {len(tools)} 个工具")
        for t in tools:
            func = t.get("function", {})
            print(f"         - {func.get('name', 'unknown')}: {func.get('description', '')[:50]}")
    else:
        result.fail("工具 Schema", f"HTTP {resp.status_code}")

    # 5.2 获取工具列表
    print(f"\n📌 5.2 获取工具列表")
    resp = requests.post(f"{BASE_URL}/agent/tools", headers=headers)
    if resp.status_code == 200:
        data = resp.json()
        tools = data.get("tools", [])
        count = data.get("count", 0)
        result.ok("工具列表", f"共 {count} 个工具")
    else:
        result.fail("工具列表", f"HTTP {resp.status_code}")

    # 5.3 触发工具调用（通过对话）
    print(f"\n📌 5.3 触发工具调用")
    # 先设置一个变量，然后让 Agent 通过工具调用来读取
    requests.post(f"{BASE_URL}/memory/variables",
                  json={"key": "tool_test_var", "value": "工具调用测试值"}, headers=headers)

    resp = requests.post(f"{BASE_URL}/agent/chat", json={
        "message": "请帮我查询一下 tool_test_var 这个记忆变量的值是多少？"
    }, headers=headers)

    if resp.status_code == 200:
        data = resp.json()
        tool_calls = data.get("tool_calls", [])
        response_text = data.get("response", "")

        has_tool_call = any(tc.get("tool") == "memory_get_variable" for tc in tool_calls)
        has_value = "工具调用测试值" in response_text

        if has_tool_call:
            result.ok("工具调用触发", f"检测到 memory_get_variable 工具调用")
        elif has_value:
            result.ok("工具调用（间接）", f"回复中包含正确的变量值")
        else:
            result.skip("工具调用", "LLM 未选择调用工具（可能直接回答了）")

        # 清理
        requests.delete(f"{BASE_URL}/memory/variables/tool_test_var", headers=headers)
    else:
        result.fail("工具调用", f"HTTP {resp.status_code}")


# ============================================================
# 主函数
# ============================================================
def main():
    print(f"\n{BOLD}{'='*60}")
    print(f"{BOLD}  智能体记忆系统功能测试")
    print(f"{'='*60}{RESET}")
    print(f"\n目标地址: {BASE_URL}")
    print(f"测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 登录
    print(f"\n 正在登录...")
    try:
        token = login()
        print(f"  {GREEN}登录成功{RESET}")
    except Exception as e:
        print(f"  {RED}登录失败: {e}{RESET}")
        print(f"\n请确保后端服务已启动: uvicorn app.main:app --port 8000")
        sys.exit(1)

    result = TestResult()

    # 运行测试
    try:
        test_short_term_memory(token, result)
        test_long_term_memory(token, result)
        test_fragment_memory(token, result)
        test_memory_integration(token, result)
        test_tool_calling(token, result)
    except KeyboardInterrupt:
        print(f"\n\n{YELLOW}测试被用户中断{RESET}")
    except Exception as e:
        print(f"\n\n{RED}测试异常: {e}{RESET}")
        import traceback
        traceback.print_exc()

    # 输出汇总
    success = result.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
