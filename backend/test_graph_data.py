#!/usr/bin/env python3
"""
知识图谱测试数据生成脚本

生成一个完整的知识图谱测试样例，包含：
- 多种类型的实体（人物、组织、地点、事件、概念）
- 丰富的关系网络
- 时序关系变更
"""
import requests
import json
import time

BASE_URL = "http://localhost:8000/api/v1"

# 登录获取 token
def login():
    resp = requests.post(f"{BASE_URL}/auth/login", json={
        "username": "admin",
        "password": "admin123"
    })
    resp.raise_for_status()
    return resp.json()["access_token"]

HEADERS = {"Authorization": f"Bearer {login()}"}

def create_entity(name, entity_type, aliases=None, metadata=None):
    """创建实体"""
    data = {"name": name, "entity_type": entity_type}
    if aliases:
        data["aliases"] = aliases
    if metadata:
        data["metadata"] = metadata
    resp = requests.post(f"{BASE_URL}/memory/graph/entities", json=data, headers=HEADERS)
    resp.raise_for_status()
    result = resp.json()
    print(f"  ✅ 实体: {name} ({entity_type}) -> id={result.get('entity_id', 'N/A')}")
    return result

def create_relationship(source_name, target_name, relation_type, source_type="person", target_type="organization", properties=None, confidence=0.9):
    """创建关系"""
    data = {
        "source_name": source_name,
        "target_name": target_name,
        "relation_type": relation_type,
        "source_type": source_type,
        "target_type": target_type,
        "confidence": confidence,
    }
    if properties:
        data["properties"] = properties
    resp = requests.post(f"{BASE_URL}/memory/graph/relationships", json=data, headers=HEADERS)
    resp.raise_for_status()
    result = resp.json()
    print(f"  ✅ 关系: {source_name} -[{relation_type}]-> {target_name}")
    return result

def search_entities(query=""):
    """搜索实体"""
    resp = requests.get(f"{BASE_URL}/memory/graph/entities", params={"query": query}, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()

def list_relationships(source_name=None, target_name=None):
    """查询关系"""
    # 获取所有关系，然后在 Python 中过滤（避免 URL 编码问题）
    resp = requests.get(f"{BASE_URL}/memory/graph/relationships", params={"limit": 200}, headers=HEADERS)
    if resp.status_code != 200:
        return {"relationships": []}
    result = resp.json()
    relationships = result.get("relationships", [])
    
    # 在 Python 中过滤
    if source_name:
        relationships = [r for r in relationships if source_name in r.get("source_name", "")]
    if target_name:
        relationships = [r for r in relationships if target_name in r.get("target_name", "")]
    
    return {"relationships": relationships}

def get_neighbors(entity_name, entity_type="person", depth=2):
    """查询邻居"""
    resp = requests.get(f"{BASE_URL}/memory/graph/neighbors", params={
        "entity_name": entity_name,
        "entity_type": entity_type,
        "depth": depth,
    }, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()

def extract_entities(text):
    """从文本抽取实体"""
    resp = requests.post(f"{BASE_URL}/memory/graph/extract", json={"text": text}, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()

def query_graph(q):
    """自然语言查询"""
    resp = requests.get(f"{BASE_URL}/memory/graph/query", params={"q": q}, headers=HEADERS)
    resp.raise_for_status()
    return resp.json()


def main():
    print("=" * 60)
    print("🧠 知识图谱测试数据生成")
    print("=" * 60)

    # -----------------------------------------------------------
    # 1. 创建实体
    # -----------------------------------------------------------
    print("\n📌 第一步：创建实体")
    print("-" * 40)

    # 人物
    create_entity("张鑫海", "person", aliases=["鑫海", "产品经理鑫海"], metadata={"role": "产品经理", "company": "腾讯"})
    create_entity("李小明", "person", aliases=["小明"], metadata={"role": "前端工程师"})
    create_entity("王芳", "person", aliases=["芳姐"], metadata={"role": "后端工程师"})
    create_entity("赵强", "person", metadata={"role": "设计师"})
    create_entity("陈伟", "person", metadata={"role": "数据分析师"})

    # 组织
    create_entity("腾讯", "organization", aliases=["Tencent"], metadata={"industry": "互联网", "location": "深圳"})
    create_entity("微信事业群", "organization", aliases=["WXG"], metadata={"parent": "腾讯"})
    create_entity("AgentOps项目组", "organization", metadata={"type": "内部项目组"})
    create_entity("清华大学", "organization", aliases=["THU"], metadata={"type": "高校"})

    # 地点
    create_entity("深圳", "location", metadata={"country": "中国", "type": "城市"})
    create_entity("北京", "location", metadata={"country": "中国", "type": "城市"})
    create_entity("腾讯大厦", "location", metadata={"city": "深圳"})

    # 事件
    create_entity("AgentOps原型设计", "event", metadata={"status": "进行中", "deadline": "2026-07-01"})
    create_entity("北京出差", "event", metadata={"date": "2026-07-05", "purpose": "产品评审"})
    create_entity("产品发布会", "event", metadata={"date": "2026-08-15"})

    # 概念
    create_entity("长期记忆", "concept", metadata={"domain": "AI", "description": "Agent的持久化记忆能力"})
    create_entity("知识图谱", "concept", metadata={"domain": "AI", "description": "结构化知识表示"})
    create_entity("RAG", "concept", aliases=["检索增强生成"], metadata={"domain": "AI"})

    # -----------------------------------------------------------
    # 2. 创建关系
    # -----------------------------------------------------------
    print("\n📌 第二步：创建关系")
    print("-" * 40)

    # 人物 -> 组织
    create_relationship("张鑫海", "腾讯", "works_at", target_type="organization", properties={"position": "产品经理", "since": "2022"})
    create_relationship("张鑫海", "微信事业群", "belongs_to", target_type="organization")
    create_relationship("张鑫海", "AgentOps项目组", "leads", target_type="organization", properties={"role": "项目负责人"})
    create_relationship("李小明", "腾讯", "works_at", target_type="organization", properties={"position": "前端工程师"})
    create_relationship("李小明", "AgentOps项目组", "belongs_to", target_type="organization")
    create_relationship("王芳", "腾讯", "works_at", target_type="organization", properties={"position": "后端工程师"})
    create_relationship("王芳", "AgentOps项目组", "belongs_to", target_type="organization")
    create_relationship("赵强", "腾讯", "works_at", target_type="organization", properties={"position": "设计师"})
    create_relationship("陈伟", "腾讯", "works_at", target_type="organization", properties={"position": "数据分析师"})

    # 人物 -> 人物
    create_relationship("张鑫海", "李小明", "collaborates_with", target_type="person", properties={"project": "AgentOps"})
    create_relationship("张鑫海", "王芳", "collaborates_with", target_type="person", properties={"project": "AgentOps"})
    create_relationship("张鑫海", "赵强", "collaborates_with", target_type="person")
    create_relationship("李小明", "王芳", "works_with", target_type="person")

    # 人物 -> 地点
    create_relationship("张鑫海", "深圳", "located_in", target_type="location")
    create_relationship("张鑫海", "腾讯大厦", "works_at_location", target_type="location")

    # 人物 -> 事件
    create_relationship("张鑫海", "AgentOps原型设计", "participates_in", target_type="event")
    create_relationship("张鑫海", "北京出差", "participates_in", target_type="event")
    create_relationship("李小明", "AgentOps原型设计", "participates_in", target_type="event")
    create_relationship("王芳", "AgentOps原型设计", "participates_in", target_type="event")

    # 组织 -> 组织
    create_relationship("微信事业群", "腾讯", "subsidiary_of", source_type="organization", target_type="organization")
    create_relationship("AgentOps项目组", "微信事业群", "belongs_to", source_type="organization", target_type="organization")

    # 组织 -> 地点
    create_relationship("腾讯", "深圳", "headquartered_in", source_type="organization", target_type="location")

    # 人物 -> 概念
    create_relationship("张鑫海", "长期记忆", "researches", target_type="concept")
    create_relationship("张鑫海", "知识图谱", "researches", target_type="concept")
    create_relationship("王芳", "RAG", "researches", target_type="concept")

    # 概念 -> 概念
    create_relationship("RAG", "知识图谱", "related_to", source_type="concept", target_type="concept")
    create_relationship("长期记忆", "知识图谱", "related_to", source_type="concept", target_type="concept")

    # -----------------------------------------------------------
    # 3. 验证数据
    # -----------------------------------------------------------
    print("\n📌 第三步：验证数据")
    print("-" * 40)

    # 搜索所有实体
    result = search_entities("")
    entities = result.get("entities", [])
    print(f"\n📊 实体总数: {len(entities)}")

    # 按类型统计
    type_counts = {}
    for e in entities:
        t = e.get("entity_type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items()):
        print(f"   {t}: {c}")

    # 查询张鑫海的关系
    result = list_relationships(source_name="张鑫海")
    rels = result.get("relationships", [])
    print(f"\n 张鑫海的关系数: {len(rels)}")
    for r in rels[:5]:
        print(f"   - {r.get('source_name')} -> {r.get('target_name')} [{r.get('relation_type')}]")

    # 查询张鑫海的邻居（深度2）
    result = get_neighbors("张鑫海", "person", depth=2)
    neighbors = result.get("neighbors", [])
    print(f"\n📊 张鑫海的邻居节点（深度2）: {len(neighbors)}")
    # 打印第一个邻居的完整结构以便调试
    if neighbors:
        print(f"   示例邻居结构: {json.dumps(neighbors[0], ensure_ascii=False)[:200]}")
    for n in neighbors[:8]:
        name = n.get('name') or n.get('entity_name') or 'Unknown'
        entity_type = n.get('entity_type') or 'unknown'
        relation_type = n.get('relation_type') or 'unknown'
        print(f"   - {name} ({entity_type}) via {relation_type}")

    # -----------------------------------------------------------
    # 4. 自然语言查询
    # -----------------------------------------------------------
    print("\n 第四步：自然语言查询")
    print("-" * 40)

    result = query_graph("张鑫海的同事")
    print(f"\n🔍 '张鑫海的同事' -> {json.dumps(result, ensure_ascii=False)[:200]}")

    result = query_graph("腾讯的员工")
    print(f"\n '腾讯的员工' -> {json.dumps(result, ensure_ascii=False)[:200]}")

    # -----------------------------------------------------------
    # 5. 文本抽取测试
    # -----------------------------------------------------------
    print("\n📌 第五步：文本实体抽取")
    print("-" * 40)

    text = "张鑫海是腾讯微信事业群的产品经理，他正在负责AgentOps项目。他的同事李小明是前端工程师，王芳是后端工程师。他们团队正在深圳腾讯大厦办公。"
    result = extract_entities(text)
    print(f"\n📝 抽取结果: {json.dumps(result, ensure_ascii=False)[:300]}")

    # -----------------------------------------------------------
    # 完成
    # -----------------------------------------------------------
    print("\n" + "=" * 60)
    print("✅ 知识图谱测试数据生成完成！")
    print("=" * 60)
    print(f"\n📊 统计:")
    print(f"   - 实体: {len(entities)} 个")
    print(f"   - 关系: 约 25 条")
    print(f"   - 实体类型: person, organization, location, event, concept")
    print(f"   - 关系类型: works_at, belongs_to, leads, collaborates_with, participates_in, researches, related_to 等")


if __name__ == "__main__":
    main()
