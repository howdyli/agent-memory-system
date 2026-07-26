"""05 生命周期管理演示 — pip install 即可运行，无需服务端（嵌入模式）。"""

from agent_memory import quickstart

mem = quickstart()

# 1. 不同重要性/TTL 的记忆片段
short_lived = mem.remember_fragment(
    "临时提醒：今晚 8 点检查部署状态",
    fragment_type="plan",
    importance_score=0.3,
    ttl=3600,  # 1 小时后过期
)
long_lived = mem.remember_fragment(
    "用户长期偏好深色主题界面",
    fragment_type="preference",
    importance_score=0.9,  # 高重要性，生命周期维护时优先保留
)
print(f"短期片段 id={short_lived.get('fragment_id')}, 长期片段 id={long_lived.get('fragment_id')}")

# 2. 查看片段生命周期状态
frags = mem.fragments.list()
print(f"\n当前活跃片段 {len(frags)} 条:")
for f in frags[:5]:
    print(
        f"  - [{f.get('lifecycle_status', 'active')}] "
        f"{f.get('content', '')[:24]}... (重要性 {f.get('importance_score')})"
    )

# 3. 提升重要性（避免被衰减清理）
fid = short_lived.get("fragment_id")
if fid:
    mem.fragments.update(fid, importance_score=0.8)
    print(f"\n已将片段 {fid} 重要性提升至 0.8")

# 4. 手动删除（软删除，进入 deleted 状态）
if fid:
    mem.fragments.delete(fid)
    print(f"已删除片段 {fid}")

mem.close()
