"""
自动记忆召回服务（Auto Memory Recall）

实现对话历史自动摘要、相关性检索、上下文注入、召回优先级排序、配置管理
"""
import logging
import json
import re
import math
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.redis_client import get_redis_client
from app.core.chromadb_client import get_chromadb_client
from app.core.tracing import get_tracer
from app.services.memory_fragment_service import (
    generate_summary,
    extract_fragments,
    create_fragment,
    list_fragments,
    cleanup_expired_fragments,
)
from app.services.memory_variable_service import (
    list_memory_variables,
    get_memory_variable,
)
from app.services.memory_observability_service import record_trace_event, update_extraction_metrics

# 延迟导入 RecallEngine 避免循环依赖
_RecallEngine = None
_MemoryValueScorer = None


def _get_recall_engine():
    global _RecallEngine
    if _RecallEngine is None:
        from app.services.recall_engine import RecallEngine
        _RecallEngine = RecallEngine
    return _RecallEngine


def _get_value_scorer() -> type:
    global _MemoryValueScorer
    if _MemoryValueScorer is None:
        from app.services.context_compressor import MemoryValueScorer
        _MemoryValueScorer = MemoryValueScorer
    return _MemoryValueScorer


def _recall_config_to_engine_config(recall_cfg: Dict[str, Any]) -> Dict[str, Any]:
    """将 recall_config 表配置映射到 RecallEngine 参数"""
    return {
        "semantic_top_k": recall_cfg.get("top_k", 5) * 2,
        "similarity_threshold": recall_cfg.get("similarity_threshold", 0.3),
        "use_hybrid_search": recall_cfg.get("use_hybrid_search", True),
        "hybrid_search_alpha": recall_cfg.get("hybrid_alpha"),
        "hybrid_search_beta": recall_cfg.get("hybrid_beta"),
        "hybrid_search_gamma": recall_cfg.get("hybrid_gamma"),
        "hybrid_search_delta": recall_cfg.get("hybrid_delta"),
    }


# ============================================================
# 默认配置
# ============================================================

DEFAULT_RECALL_CONFIG = {
    "enabled": True,
    "top_k": 5,
    "similarity_threshold": 0.3,
    "importance_weight": 0.4,
    "recency_weight": 0.3,
    "similarity_weight": 0.3,
    "max_context_length": 2000,
    "context_format": "structured",  # structured | narrative
    "use_hybrid_search": True,
}

# 配置存储键前缀
RECALL_CONFIG_KEY = "recall_config"
SUMMARY_CACHE_KEY = "summary_cache"
RECENT_QUERIES_KEY = "recent_queries"


def _get_recall_config_table() -> None:
    """确保召回配置表存在"""
    db = get_db_client()
    db.execute('''
        CREATE TABLE IF NOT EXISTS recall_config (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            config TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')


# ============================================================
# Task 16: 对话历史自动摘要生成
# ============================================================

def get_recall_config(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    获取用户的自动召回配置
    
    Args:
        user_id: 用户 ID
        
    Returns:
        配置字典
    """
    try:
        _get_recall_config_table()
        db = get_db_client()
        
        rows = db.execute(
            'SELECT config FROM recall_config WHERE user_id = ?',
            (user_id,)
        )
        
        if rows:
            config = json.loads(rows[0]["config"])
            return {**DEFAULT_RECALL_CONFIG, **config}
        
        return DEFAULT_RECALL_CONFIG.copy()
        
    except Exception as e:
        logger.error(f"✗ 获取召回配置失败: {e}")
        return DEFAULT_RECALL_CONFIG.copy()


def update_recall_config(user_id: int, config: Dict[str, Any], workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    更新用户的自动召回配置
    
    Args:
        user_id: 用户 ID
        config: 配置字典
        
    Returns:
        更新结果
    """
    try:
        _get_recall_config_table()
        db = get_db_client()
        
        # 合并默认配置
        current_config = get_recall_config(user_id)
        current_config.update(config)
        config_str = json.dumps(current_config, ensure_ascii=False)
        
        # Upsert
        db.execute('''
            INSERT INTO recall_config (user_id, config)
            VALUES (?, ?)
            ON CONFLICT(user_id)
            DO UPDATE SET config = excluded.config, updated_at = CURRENT_TIMESTAMP
        ''', (user_id, config_str))
        
        logger.info(f"✓ 更新用户 {user_id} 的召回配置")
        
        return {
            "success": True,
            "config": current_config,
            "message": "Recall configuration updated successfully"
        }
        
    except Exception as e:
        logger.error(f"✗ 更新召回配置失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


def generate_auto_summary(user_id: int, messages: List[Dict[str, str]], workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    生成对话历史自动摘要（带增量更新和缓存）
    
    Args:
        user_id: 用户 ID
        messages: 对话历史
        
    Returns:
        摘要结果
    """
    try:
        redis = get_redis_client()
        
        # 1. 检查缓存
        cache_key = f"{SUMMARY_CACHE_KEY}:{user_id}"
        cached = redis.get(cache_key)
        
        # 2. 检查是否有新消息（增量更新）
        last_msg_count_key = f"{SUMMARY_CACHE_KEY}:{user_id}:msg_count"
        last_count = redis.get(last_msg_count_key)
        last_count = int(last_count) if last_count else 0
        
        current_count = len(messages)
        
        # 如果消息数量没变，直接返回缓存
        if cached and current_count == last_count:
            logger.info(f"✓ 使用缓存的摘要（消息数未变）")
            return {
                "success": True,
                "summary": json.loads(cached) if isinstance(cached, str) else cached,
                "cached": True,
                "message_count": current_count
            }
        
        # 3. 生成新摘要（只处理新增的消息）
        new_messages = messages[last_count:] if last_count < current_count else messages
        
        summary_result = generate_summary(new_messages, max_length=500)
        
        if not summary_result["success"]:
            return summary_result
        
        # 4. 合并旧摘要和新摘要（如果有缓存）
        if cached and last_count > 0:
            old_summary = json.loads(cached) if isinstance(cached, str) else cached
            # 合并关键信息
            merged_preferences = list(set(
                old_summary.get("preferences", []) + summary_result.get("preferences", [])
            ))
            merged_plans = list(set(
                old_summary.get("plans", []) + summary_result.get("plans", [])
            ))
            merged_key_facts = list(set(
                old_summary.get("key_facts", []) + summary_result.get("key_facts", [])
            ))
            
            # 合并用户信息
            merged_user_info = {**old_summary.get("user_info", {}), **summary_result.get("user_info", {})}
            
            # 重新生成摘要文本
            summary_parts = []
            if merged_user_info:
                info_str = "、".join([f"{k}:{v}" for k, v in merged_user_info.items()])
                summary_parts.append(f"用户信息({info_str})")
            if merged_preferences:
                summary_parts.append(f"偏好({', '.join(merged_preferences[:3])})")
            if merged_plans:
                summary_parts.append(f"计划({', '.join(merged_plans[:3])})")
            if merged_key_facts:
                summary_parts.append(f"关键信息({', '.join(merged_key_facts[:3])})")
            
            merged_summary_text = "。".join(summary_parts)
            
            summary_data = {
                "summary": merged_summary_text,
                "user_info": merged_user_info,
                "preferences": merged_preferences,
                "plans": merged_plans,
                "key_facts": merged_key_facts,
                "message_count": current_count,
                "generated_at": datetime.now().isoformat()
            }
        else:
            summary_data = {
                "summary": summary_result["summary"],
                "user_info": summary_result.get("user_info", {}),
                "preferences": summary_result.get("preferences", []),
                "plans": summary_result.get("plans", []),
                "key_facts": summary_result.get("key_facts", []),
                "message_count": current_count,
                "generated_at": datetime.now().isoformat()
            }
        
        # 5. 缓存摘要
        redis.set(cache_key, json.dumps(summary_data, ensure_ascii=False))
        redis.set(last_msg_count_key, str(current_count))
        
        logger.info(f"✓ 生成自动摘要（{current_count} 条消息，新增 {len(new_messages)} 条）")
        
        return {
            "success": True,
            "summary": summary_data,
            "cached": False,
            "incremental": last_count > 0,
            "message_count": current_count,
            "new_messages": len(new_messages)
        }
        
    except Exception as e:
        logger.error(f"✗ 自动摘要生成失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# Task 17: 相关性检索（Similarity Search）
# ============================================================

def calculate_relevance(query: str, fragment: Dict[str, Any]) -> float:
    """
    计算查询与记忆片段的相关性（基于关键词匹配 + 向量相似性）
    
    Args:
        query: 查询文本
        fragment: 记忆片段
        
    Returns:
        相关性评分（0.0 - 1.0）
    """
    try:
        content = fragment.get("content", "")
        
        # 1. 关键词匹配
        query_words = set(re.findall(r'\w+', query.lower()))
        content_words = set(re.findall(r'\w+', content.lower()))
        
        if not query_words or not content_words:
            keyword_score = 0.0
        else:
            overlap = query_words & content_words
            keyword_score = len(overlap) / len(query_words) if query_words else 0.0
        
        # 2. 向量相似性（如果有）
        vector_similarity = fragment.get("similarity", 0)
        if vector_similarity is None:
            vector_similarity = 0.0
        
        # 3. 综合评分
        relevance = 0.5 * keyword_score + 0.5 * vector_similarity
        
        return min(relevance, 1.0)
        
    except Exception:
        return 0.0


def search_relevant_memories(user_id: int,
                             query: str,
                             top_k: int = 5,
                             threshold: float = 0.3,
                             workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    检索与查询相关的记忆（Top-K 相关性检索）
    
    内部委托给 RecallEngine 统一召回。
    
    Args:
        user_id: 用户 ID
        query: 查询文本
        top_k: 返回 Top-K 结果
        threshold: 相关性阈值
        
    Returns:
        检索结果
    """
    try:
        config = get_recall_config(user_id)
        engine_config = _recall_config_to_engine_config(config)
        engine = _get_recall_engine()(engine_config)
        result = engine.recall(
            user_id=user_id,
            query=query,
            budget_tokens=2000,
            top_k=top_k,
            update_lifecycle=False,
            record_traces=False,
        )

        # 转为旧格式保持兼容
        top_memories = result.memories[:top_k]
        # 补充 relevance 字段（旧接口依赖）
        for mem in top_memories:
            if "relevance" not in mem:
                mem["relevance"] = mem.get("similarity", 0)

        logger.info(f"✓ 相关性检索: '{query}' -> {len(top_memories)} 条结果（阈值 {threshold}）")

        return {
            "success": True,
            "memories": top_memories,
            "count": len(top_memories),
            "query": query,
            "top_k": top_k,
            "threshold": threshold
        }

    except Exception as e:
        logger.error(f"✗ 相关性检索失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# Task 18: 上下文注入机制
# ============================================================

def inject_memory_context(user_id: int,
                          query: str,
                          max_length: int = 2000,
                          format: str = "structured",
                          workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    将召回的记忆注入到对话上下文中
    
    Args:
        user_id: 用户 ID
        query: 当前查询
        max_length: 最大上下文长度
        format: 注入格式（structured | narrative）
        
    Returns:
        注入结果（包含格式化的上下文）
    """
    try:
        config = get_recall_config(user_id)
        
        if not config.get("enabled", True):
            return {
                "success": True,
                "context": "",
                "injected": False,
                "message": "Auto recall is disabled"
            }
        
        # 1. 检索相关记忆
        top_k = config.get("top_k", 5)
        threshold = config.get("similarity_threshold", 0.3)
        
        recall_result = search_relevant_memories(
            user_id=user_id,
            query=query,
            top_k=top_k,
            threshold=threshold
        )
        
        if not recall_result["success"] or recall_result["count"] == 0:
            return {
                "success": True,
                "context": "",
                "injected": False,
                "message": "No relevant memories found"
            }
        
        memories = recall_result["memories"]
        
        # 2. 格式化上下文
        if format == "narrative":
            # 叙事格式
            context_parts = []
            for mem in memories:
                context_parts.append(mem.get("content", ""))
            context = " ".join(context_parts)
        else:
            # 结构化格式
            context_lines = ["[相关记忆]"]
            for i, mem in enumerate(memories, 1):
                mem_type = mem.get("fragment_type", mem.get("type", "memory"))
                content = mem.get("content", "")
                context_lines.append(f"{i}. [{mem_type}] {content}")
            context = "\n".join(context_lines)
        
        # 3. 截断到最大长度
        if len(context) > max_length:
            context = context[:max_length - 3] + "..."
        
        logger.info(f"✓ 上下文注入: {len(memories)} 条记忆，{len(context)} 字符")
        
        return {
            "success": True,
            "context": context,
            "injected": True,
            "memory_count": len(memories),
            "context_length": len(context),
            "format": format
        }
        
    except Exception as e:
        logger.error(f"✗ 上下文注入失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# Task 19: 召回优先级排序
# ============================================================

def calculate_importance_score(fragment: Dict[str, Any]) -> float:
    """
    计算记忆片段的重要性评分
    
    综合考虑：基础重要性、时间衰减、用户反馈
    
    Args:
        fragment: 记忆片段
        
    Returns:
        重要性评分（0.0 - 1.0）
    """
    try:
        # 1. 基础重要性
        base_score = fragment.get("importance_score", 0.5)
        
        # 2. 时间衰减（半衰期 30 天）
        created_at = fragment.get("created_at")
        if created_at:
            try:
                if isinstance(created_at, str):
                    created_time = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                else:
                    created_time = created_at
                
                days_since = (datetime.now(created_time.tzinfo) - created_time).days if hasattr(created_time, 'tzinfo') and created_time.tzinfo else (datetime.now() - created_time).days
                half_life = 30  # 30天半衰期
                decay_factor = math.pow(0.5, days_since / half_life)
            except Exception:
                decay_factor = 1.0
        else:
            decay_factor = 1.0
        
        # 3. 用户反馈调整
        feedback_score = fragment.get("feedback_score", 0)  # 正反馈 +1, 负反馈 -1
        
        # 4. 综合评分
        final_score = base_score * decay_factor + feedback_score * 0.1
        final_score = max(0.0, min(1.0, final_score))
        
        return final_score
        
    except Exception:
        return 0.5


def rank_memories_by_priority(memories: List[Dict[str, Any]],
                              query: str = "",
                              config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    按优先级排序记忆
    
    内部使用 MemoryValueScorer 统一评分体系。
    
    Args:
        memories: 记忆列表
        query: 当前查询（用于计算相关性）
        config: 权重配置
        
    Returns:
        排序后的记忆列表
    """
    try:
        scorer_cls = _get_value_scorer()

        for mem in memories:
            total = scorer_cls.score_memory_value(mem, query)
            mem["total_score"] = total
            mem["priority_score"] = total
            # 兼容旧字段
            mem["importance_score_final"] = mem.get("importance_score", 0.5)
            mem["recency_score"] = 0.5  # 已融入 total_score
            mem["similarity_score_final"] = mem.get("similarity", mem.get("relevance", 0))

        memories.sort(key=lambda x: x.get("priority_score", 0), reverse=True)
        return memories

    except Exception as e:
        logger.error(f"✗ 优先级排序失败: {e}")
        return memories


# ============================================================
# 综合召回流程
# ============================================================

def auto_recall(user_id: int, query: str, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    自动记忆召回完整流程
    
    1. 检查是否启用
    2. 检索相关记忆
    3. 优先级排序
    4. 上下文注入
    
    Args:
        user_id: 用户 ID
        query: 当前查询
        
    Returns:
        召回结果
    """
    try:
        # Phase 5: tracing span
        tracer = get_tracer()
        span = tracer.start_span("auto_recall")
        span.set_attribute("user.id", user_id)
        span.set_attribute("recall.query", query[:200])
        if workspace_id:
            span.set_attribute("workspace.id", workspace_id)

        config = get_recall_config(user_id)
        
        if not config.get("enabled", True):
            return {
                "success": True,
                "enabled": False,
                "context": "",
                "message": "Auto recall is disabled"
            }
        
        # 直接用 RecallEngine 统一召回
        top_k = config.get("top_k", 5)
        max_length = config.get("max_context_length", 2000)
        context_format = config.get("context_format", "structured")

        engine_config = _recall_config_to_engine_config(config)
        engine = _get_recall_engine()(engine_config)
        result = engine.recall(
            user_id=user_id,
            query=query,
            budget_tokens=max_length,
            top_k=top_k,
            update_lifecycle=True,
            record_traces=True,
        )

        top_memories = result.memories

        # 自定义格式（保持兼容）
        if context_format == "narrative":
            context = " ".join(m.get("content", "") for m in top_memories)
        else:
            context = result.context_text  # RecallEngine 已格式化
        
        if len(context) > max_length:
            context = context[:max_length - 3] + "..."
        
        logger.info(f"✓ 自动召回: 查询='{query}', 召回={len(top_memories)} 条")
        
        # 观测性埋点
        try:
            update_extraction_metrics(user_id, "auto_recall", query_snippet=query[:200])
        except Exception:
            pass
        
        return {
            "success": True,
            "enabled": True,
            "context": context,
            "memories": top_memories,
            "memory_count": len(top_memories),
            "config": config,
            "query": query
        }
        
    except Exception as e:
        span.record_exception(e)
        logger.error(f"✗ 自动召回失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }
    finally:
        span.end()


# ============================================================
# Task 20: 召回效果评估
# ============================================================

def get_recall_stats(user_id: int, workspace_id: Optional[int] = None) -> Dict[str, Any]:
    """
    获取召回效果统计
    
    Args:
        user_id: 用户 ID
        
    Returns:
        统计数据
    """
    try:
        db = get_db_client()
        config = get_recall_config(user_id)
        
        # 统计记忆片段数量
        fragment_count = db.execute(
            'SELECT COUNT(*) as count FROM memory_fragments WHERE user_id = ?',
            (user_id,)
        )
        total_fragments = fragment_count[0]["count"] if fragment_count else 0
        
        # 按类型统计
        type_stats = db.execute(
            'SELECT fragment_type, COUNT(*) as count FROM memory_fragments WHERE user_id = ? GROUP BY fragment_type',
            (user_id,)
        )
        by_type = {row["fragment_type"]: row["count"] for row in type_stats} if type_stats else {}
        
        # 平均重要性
        avg_importance = db.execute(
            'SELECT AVG(importance_score) as avg FROM memory_fragments WHERE user_id = ?',
            (user_id,)
        )
        avg_score = avg_importance[0]["avg"] if avg_importance and avg_importance[0]["avg"] else 0.0
        
        # 过期片段数量
        now = datetime.now().isoformat()
        expired_count = db.execute(
            'SELECT COUNT(*) as count FROM memory_fragments WHERE user_id = ? AND expires_at IS NOT NULL AND expires_at < ?',
            (user_id, now)
        )
        expired = expired_count[0]["count"] if expired_count else 0
        
        return {
            "success": True,
            "stats": {
                "total_fragments": total_fragments,
                "by_type": by_type,
                "avg_importance": round(avg_score, 3),
                "expired_fragments": expired,
                "config": config,
                "recall_enabled": config.get("enabled", True)
            }
        }
        
    except Exception as e:
        logger.error(f"✗ 获取召回统计失败: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# ============================================================
# 测试函数
# ============================================================

def test_auto_recall():
    """测试自动记忆召回服务"""
    print("\n" + "="*60)
    print("测试自动记忆召回服务")
    print("="*60 + "\n")
    
    user_id = 999
    
    # 清理
    db = get_db_client()
    _get_recall_config_table()  # 确保 recall_config 表存在
    db.execute('DELETE FROM memory_fragments WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM recall_config WHERE user_id = ?', (user_id,))
    
    # 准备测试数据
    print("0. 准备测试数据...")
    from app.services.memory_fragment_service import create_fragment
    
    create_fragment(user_id, "info", "用户名叫鑫海", ttl=None, importance_score=0.95)
    create_fragment(user_id, "preference", "喜欢极简设计风格", ttl=None, importance_score=0.8)
    create_fragment(user_id, "plan", "计划明天完成架构设计", ttl=7*24*3600, importance_score=0.85)
    create_fragment(user_id, "info", "在腾讯工作，负责PM", ttl=None, importance_score=0.7)
    print("   ✓ 插入 4 条测试片段\n")
    
    # Task 16: 自动摘要
    print("--- Task 16: 对话历史自动摘要 ---\n")
    
    print("1. 测试自动摘要生成...")
    messages = [
        {"role": "user", "content": "我叫鑫海，在腾讯工作"},
        {"role": "assistant", "content": "你好！"},
        {"role": "user", "content": "我喜欢极简设计风格"},
    ]
    result = generate_auto_summary(user_id, messages)
    print(f"   生成结果: {result.get('success')}")
    print(f"   摘要: {result.get('summary', {}).get('summary', 'N/A')[:80]}")
    print(f"   缓存: {result.get('cached')}")
    assert result["success"] == True
    print(f"   ✓ 自动摘要生成成功\n")
    
    print("2. 测试增量更新...")
    messages.append({"role": "user", "content": "我计划明天完成架构设计"})
    result = generate_auto_summary(user_id, messages)
    print(f"   增量更新: {result.get('incremental')}")
    print(f"   新消息数: {result.get('new_messages')}")
    assert result["success"] == True
    print(f"   ✓ 增量更新成功\n")
    
    print("3. 测试缓存命中...")
    result = generate_auto_summary(user_id, messages)  # 相同消息
    print(f"   缓存命中: {result.get('cached')}")
    assert result["cached"] == True
    print(f"   ✓ 缓存命中成功\n")
    
    # Task 17: 相关性检索
    print("--- Task 17: 相关性检索 ---\n")
    
    print("4. 测试相关性检索...")
    result = search_relevant_memories(user_id, "设计风格", top_k=3, threshold=0.1)
    print(f"   检索结果: {result.get('count', 0)} 条")
    for m in result.get("memories", []):
        print(f"   - {m.get('content', '')[:30]}... (相关性: {m.get('relevance', 0):.3f})")
    assert result["success"] == True
    print(f"   ✓ 相关性检索成功\n")
    
    # Task 18: 上下文注入
    print("--- Task 18: 上下文注入 ---\n")
    
    print("5. 测试上下文注入...")
    result = inject_memory_context(user_id, "设计风格", max_length=500)
    print(f"   注入结果: {result.get('injected')}")
    print(f"   上下文长度: {result.get('context_length', 0)}")
    print(f"   上下文内容:\n{result.get('context', 'N/A')[:200]}")
    assert result["success"] == True
    print(f"   ✓ 上下文注入成功\n")
    
    # Task 19: 优先级排序
    print("--- Task 19: 优先级排序 ---\n")
    
    print("6. 测试优先级排序...")
    # 获取所有片段
    fragments_result = list_fragments(user_id)
    memories = fragments_result.get("fragments", [])
    ranked = rank_memories_by_priority(memories, "设计风格")
    print(f"   排序结果:")
    for m in ranked:
        print(f"   - {m.get('content', '')[:30]}... (优先级: {m.get('priority_score', 0):.3f})")
    assert len(ranked) > 0
    print(f"   ✓ 优先级排序成功\n")
    
    # 综合召回
    print("7. 测试综合自动召回...")
    result = auto_recall(user_id, "设计风格")
    print(f"   召回结果: {result.get('memory_count', 0)} 条")
    print(f"   上下文:\n{result.get('context', 'N/A')[:200]}")
    assert result["success"] == True
    print(f"   ✓ 综合自动召回成功\n")
    
    # Task 20: 配置和统计
    print("--- Task 20: 配置和统计 ---\n")
    
    print("8. 测试配置管理...")
    result = update_recall_config(user_id, {"top_k": 10, "similarity_threshold": 0.5})
    print(f"   更新结果: {result.get('success')}")
    print(f"   配置: top_k={result['config']['top_k']}, threshold={result['config']['similarity_threshold']}")
    assert result["success"] == True
    print(f"   ✓ 配置管理成功\n")
    
    print("9. 测试统计...")
    result = get_recall_stats(user_id)
    print(f"   总片段数: {result['stats']['total_fragments']}")
    print(f"   按类型: {result['stats']['by_type']}")
    print(f"   平均重要性: {result['stats']['avg_importance']}")
    print(f"   召回启用: {result['stats']['recall_enabled']}")
    assert result["success"] == True
    print(f"   ✓ 统计成功\n")
    
    # 清理
    print("--- 清理测试数据 ---")
    db.execute('DELETE FROM memory_fragments WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM recall_config WHERE user_id = ?', (user_id,))
    print("   ✓ 清理完成")
    
    print("\n" + "="*60)
    print("✅ 自动记忆召回服务测试完成！")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    test_auto_recall()
