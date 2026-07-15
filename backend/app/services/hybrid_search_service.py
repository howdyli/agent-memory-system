"""
Hybrid Search - 多信号混合检索模块

融合检索公式:
  final_score = alpha * semantic_score + beta * bm25_score
               + gamma * entity_boost + delta * recency_score

核心组件:
1. BM25 索引 (SQLite FTS5)
2. 实体加权 (EntityGraphTraverser)
3. 时间衰减 (记忆半衰期)
4. LLM 重排序 (Reranker)
"""
import logging
import json
import re
import math
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.redis_client import get_redis_client
from app.services.memory_fragment_service import search_fragments_by_semantic

# 内存配置缓存（Redis 不可用时的回退）
_memory_config_cache = None

# ============================================================
# 默认融合权重
# ============================================================

HYBRID_SEARCH_CONFIG = {
    # 融合权重
    "alpha": 0.35,    # 语义搜索权重
    "beta": 0.30,     # BM25 全文搜索权重
    "gamma": 0.20,    # 实体加权权重
    "delta": 0.15,    # 时间衰减权重

    # 检索参数
    "top_k_initial": 30,    # 融合前候选数
    "top_k_final": 10,      # 最终返回数
    "bm25_top_k": 30,       # BM25 召回数
    "semantic_top_k": 20,   # 语义召回数

    # 衰减参数
    "recency_half_life": 90,     # 时间衰减半衰期（天）
    "recency_min_score": 0.30,   # 时间衰减最低值

    # 实体加权
    "entity_boost_person": 0.15,
    "entity_boost_organization": 0.10,
    "entity_boost_location": 0.05,
    "entity_boost_event": 0.08,

    # 重排序
    "rerank_enabled": True,
    "rerank_top_k": 10,
}

# 融合权重键列表
WEIGHT_KEYS = ["alpha", "beta", "gamma", "delta"]


def get_config() -> Dict[str, Any]:
    """获取当前混合检索配置"""
    global _memory_config_cache
    try:
        redis = get_redis_client()
        if redis:
            stored = redis.get("hybrid_search_config")
            if stored:
                try:
                    stored_config = json.loads(stored)
                    merged = {**HYBRID_SEARCH_CONFIG, **stored_config}
                    _memory_config_cache = merged
                    return merged
                except (json.JSONDecodeError, TypeError):
                    pass
        if _memory_config_cache:
            return {**HYBRID_SEARCH_CONFIG, **_memory_config_cache}
        return dict(HYBRID_SEARCH_CONFIG)
    except Exception:
        if _memory_config_cache:
            return {**HYBRID_SEARCH_CONFIG, **_memory_config_cache}
        return dict(HYBRID_SEARCH_CONFIG)


def update_config(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    更新混合检索配置。

    支持动态调整融合权重 (alpha/beta/gamma/delta)
    和检索参数 (top_k, 衰减等)。

    Args:
        updates: 配置更新字典

    Returns:
        更新后的完整配置
    """
    global _memory_config_cache
    try:
        current = get_config()
        current.update(updates)
        _memory_config_cache = dict(current)

        redis = get_redis_client()
        if redis:
            redis.set("hybrid_search_config", json.dumps(current, ensure_ascii=False))

        logger.info(f"✓ 更新混合检索配置: {updates}")
        return {"success": True, "config": current}
    except Exception as e:
        logger.error(f"✗ 更新配置失败: {e}")
        return {"success": False, "error": str(e)}


def get_weights() -> Dict[str, float]:
    """获取当前融合权重"""
    config = get_config()
    return {k: config.get(k, HYBRID_SEARCH_CONFIG[k]) for k in WEIGHT_KEYS}


def set_weights(alpha: Optional[float] = None,
                beta: Optional[float] = None,
                gamma: Optional[float] = None,
                delta: Optional[float] = None) -> Dict[str, Any]:
    """
    设置融合权重。

    Args:
        alpha: 语义搜索权重
        beta: BM25 全文搜索权重
        gamma: 实体加权权重
        delta: 时间衰减权重

    Returns:
        更新结果
    """
    updates = {}
    if alpha is not None:
        updates["alpha"] = max(0.0, min(1.0, alpha))
    if beta is not None:
        updates["beta"] = max(0.0, min(1.0, beta))
    if gamma is not None:
        updates["gamma"] = max(0.0, min(1.0, gamma))
    if delta is not None:
        updates["delta"] = max(0.0, min(1.0, delta))
    return update_config(updates)


# ============================================================
# 1. BM25 全文搜索 (SQLite FTS5)
# ============================================================

def rebuild_fts_index() -> Dict[str, Any]:
    """
    重建 FTS5 全文索引。

    从 memory_fragments 表重新填充 fragments_fts 虚拟表。
    用于索引损坏或首次部署后的初始化。

    Returns:
        重建结果（含索引条目数）
    """
    try:
        db = get_db_client()
        # 清空并重建
        try:
            db.execute("DELETE FROM fragments_fts")
        except Exception:
            pass
        count = db.execute(
            '''INSERT INTO fragments_fts(rowid, content, fragment_type)
               SELECT id, content, fragment_type FROM memory_fragments'''
        )
        total_rows = db.execute(
            'SELECT COUNT(*) as cnt FROM fragments_fts'
        )
        total = total_rows[0]["cnt"] if total_rows else 0
        logger.info(f"✓ FTS5 索引重建完成: {total} 条")
        return {"success": True, "total": total}
    except Exception as e:
        logger.error(f"✗ FTS5 索引重建失败: {e}")
        return {"success": False, "error": str(e)}


def _has_cjk(text: str) -> bool:
    """检查文本是否包含中日韩表意文字"""
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f':
            return True
    return False


def _tokenize_query(query: str) -> str:
    """
    对查询文本进行分词，适配 FTS5 查询语法。

    对英文查询使用空格分割，FTS5 的 unicode61 tokenizer
    会正确处理英文。中文查询由 LIKE 回退处理，因此这里
    保持原样或简单空格分词即可。

    Args:
        query: 原始查询

    Returns:
        分词后的 FTS5 查询字符串
    """
    if not query or not query.strip():
        return ""

    parts = query.strip().split()
    if not parts:
        return ""

    # 英文用空格 AND 连接，FTS5 默认 AND 语义
    return " AND ".join(parts)


def search_bm25(
    query: str,
    user_id: int,
    top_k: int = 20,
) -> Dict[str, Any]:
    """
    使用 SQLite FTS5 BM25 算法进行全文搜索。

    Args:
        query: 查询文本
        user_id: 用户 ID
        top_k: 返回 Top-K 结果

    Returns:
        BM25 搜索结果列表
    """
    try:
        db = get_db_client()

        # 检查 FTS5 表是否存在且有数据
        try:
            count_check = db.execute("SELECT COUNT(*) as cnt FROM fragments_fts")
            if not count_check or count_check[0]["cnt"] == 0:
                # 索引为空，尝试重建
                rebuild_fts_index()
                count_check = db.execute("SELECT COUNT(*) as cnt FROM fragments_fts")
                if not count_check or count_check[0]["cnt"] == 0:
                    return {"success": True, "fragments": [], "count": 0,
                            "message": "FTS5 索引为空，请先写入记忆片段"}
        except Exception:
            # FTS5 表可能不存在，尝试重建
            try:
                db.execute('''CREATE VIRTUAL TABLE IF NOT EXISTS fragments_fts
                              USING fts5(content, fragment_type, tokenize='unicode61')''')
                rebuild_fts_index()
            except Exception:
                return {"success": False, "error": "FTS5 表创建失败"}

        fts_query = _tokenize_query(query)
        has_cjk = _has_cjk(query)

        # 尝试 FTS5 MATCH（英文关键词匹配）
        fragments = []
        try:
            rows = db.execute(
                '''SELECT f.id, f.content, f.fragment_type, f.importance_score,
                          f.created_at, f.user_id,
                          bm25(fragments_fts) as bm25_score
                   FROM fragments_fts
                   JOIN memory_fragments f ON fragments_fts.rowid = f.id
                   WHERE fragments_fts MATCH ? AND f.user_id = ?
                   ORDER BY bm25_score ASC
                   LIMIT ?''',
                (fts_query, user_id, top_k)
            )
            if rows:
                for r in rows:
                    d = dict(r)
                    raw_score = d.pop("bm25_score", 0)
                    normalized_score = max(0.0, min(1.0, 1.0 / (1.0 + raw_score)))
                    d["bm25_score"] = round(normalized_score, 4)
                    fragments.append(d)
        except Exception as e:
            logger.debug(f"FTS5 MATCH 失败: {e}")

        # 如果 FTS5 返回结果少且查询包含中文，使用 LIKE 回退
        if len(fragments) < top_k and has_cjk:
            keyword_fragments = _search_with_like(query, user_id, top_k)
            existing_ids = {f["id"] for f in fragments}
            for kf in keyword_fragments:
                if kf["id"] not in existing_ids:
                    fragments.append(kf)
                    existing_ids.add(kf["id"])

        logger.info(f"✓ BM25 搜索: '{query}' -> {len(fragments)} 条")
        return {
            "success": True,
            "fragments": fragments,
            "count": len(fragments),
            "query": query,
        }

    except Exception as e:
        logger.error(f"✗ BM25 搜索失败: {e}")
        return {"success": False, "error": str(e)}


def _search_with_like(query: str, user_id: int, top_k: int) -> List[Dict[str, Any]]:
    """
    使用 SQL LIKE 进行中文关键词匹配（FTS5 中文回退方案）。

    对中文查询进行逐词匹配，计算术语频率（TF）作为相关性分数。

    Args:
        query: 查询文本
        user_id: 用户 ID
        top_k: 返回 Top-K 结果

    Returns:
        关键词匹配结果列表
    """
    try:
        db = get_db_client()

        # 提取查询词（中文逐字符 bigram，英文按空格分割）
        terms = []
        current = ""
        for ch in query:
            if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f':
                if current:
                    terms.append(current.lower())
                    current = ""
                # 对中文添加字符级 bigram
                terms.append(ch)
            elif ch.isalnum() or ch in '-_.':
                current += ch
            else:
                if current:
                    terms.append(current.lower())
                    current = ""
        if current:
            terms.append(current.lower())

        # 去重
        terms = list(dict.fromkeys(terms))
        if not terms:
            return []

        # 使用 LIKE 搜索，每个 term 是一个条件
        conditions = []
        params = [user_id]
        for term in terms:
            if len(term) >= 1:
                pattern = f"%{term}%"
                conditions.append("f.content LIKE ?")
                params.append(pattern)

        if not conditions:
            return []

        where_clause = " OR ".join(conditions)
        rows = db.execute(
            f'''SELECT f.id, f.content, f.fragment_type, f.importance_score,
                       f.created_at, f.user_id
                FROM memory_fragments f
                WHERE f.user_id = ? AND ({where_clause})
                ORDER BY f.importance_score DESC
                LIMIT ?''',
            tuple(params) + (top_k,)
        )

        results = []
        if rows:
            for r in rows:
                d = dict(r)
                content = d.get("content", "") or ""
                # 计算简单 TF 分数
                match_count = 0
                for term in terms:
                    match_count += content.lower().count(term.lower())
                # 归一化 BM25 分数
                d["bm25_score"] = round(min(1.0, match_count / (match_count + 5)), 4)
                results.append(d)

        results.sort(key=lambda x: x.get("bm25_score", 0), reverse=True)
        return results[:top_k]

    except Exception as e:
        logger.debug(f"LIKE 搜索失败: {e}")
        return []


# ============================================================
# 2. 实体加权
# ============================================================

def compute_entity_boost(
    query: str,
    fragment: Dict[str, Any],
) -> float:
    """
    计算实体加权的提升分数。

    从 query 中提取实体，检查 fragment content 中是否匹配，
    根据实体类型给予不同权重加成。

    Args:
        query: 用户查询
        fragment: 记忆片段

    Returns:
        实体加分 (0.0 ~ 0.30)
    """
    try:
        content = (fragment.get("content") or "").lower()
        boost = 0.0

        type_weights = {
            "person": HYBRID_SEARCH_CONFIG["entity_boost_person"],
            "organization": HYBRID_SEARCH_CONFIG["entity_boost_organization"],
            "location": HYBRID_SEARCH_CONFIG["entity_boost_location"],
            "event": HYBRID_SEARCH_CONFIG["entity_boost_event"],
        }

        def _match_entity(name: str, etype: str) -> float:
            name_lower = name.lower().strip()
            if not name_lower or len(name_lower) < 2:
                return 0.0
            if name_lower in content:
                count = content.count(name_lower)
                weight = type_weights.get(etype, 0.05)
                return weight * min(2.0, 1.0 + count * 0.5)
            return 0.0

        # 来源1: EntityGraphTraverser 自然语言实体抽取
        try:
            from app.services.context_compressor import EntityGraphTraverser
            extracted = EntityGraphTraverser.extract_entities_with_types(query)
            for ent in extracted:
                boost += _match_entity(ent.get("name", ""), ent.get("type", "person"))
        except Exception:
            pass

        # 来源2: 直接基于 query 的字符 bigram 匹配（处理简洁关键词如 "张三腾讯北京"）
        # 提取所有 2-4 字中文子串去重后尝试匹配
        seen_names = set()
        for i in range(len(query)):
            for j in range(2, min(5, len(query) - i + 1)):
                sub = query[i:i+j]
                if all('\u4e00' <= ch <= '\u9fff' for ch in sub):
                    if sub not in seen_names:
                        seen_names.add(sub)
                        # 带公司/集团等后缀的优先当作 organization
                        if re.search(r'(公司|集团|学院|大学|银行)$', sub):
                            boost += _match_entity(sub, "organization")
                        else:
                            boost += _match_entity(sub, "person")

        # info 类型的人名信息额外加分
        fragment_type = fragment.get("fragment_type", "")
        if fragment_type == "info" and boost > 0:
            boost += 0.05

        return min(0.30, boost)

    except Exception as e:
        logger.debug(f"实体加权计算失败: {e}")
        return 0.0


# ============================================================
# 3. 时间衰减
# ============================================================

def compute_recency_score(fragment: Dict[str, Any]) -> float:
    """
    计算记忆片段的时间衰减分数。

    公式: max(recency_min_score, e^(-days_since / recency_half_life))

    - info 类型（永久记忆）不衰减
    - 其他类型按半衰期指数衰减

    Args:
        fragment: 记忆片段

    Returns:
        时间衰减分数 (recency_min_score ~ 1.0)
    """
    try:
        # info 类型永久保留
        if fragment.get("fragment_type") == "info":
            return 1.0

        created_at = fragment.get("created_at")
        if not created_at:
            return 1.0

        if isinstance(created_at, str):
            created_time = datetime.fromisoformat(
                created_at.replace("Z", "+00:00").split(".")[0]
            )
        else:
            created_time = created_at

        days_since = max(0, (datetime.now() - created_time).days)
        half_life = get_config()["recency_half_life"]
        min_score = get_config()["recency_min_score"]

        score = math.exp(-days_since / half_life)
        return max(min_score, min(1.0, score))

    except Exception:
        return 1.0


# ============================================================
# 4. LLM 重排序
# ============================================================

RERANK_SYSTEM_PROMPT = """你是一个记忆检索重排序引擎。
给定用户查询和一组候选记忆片段，请按相关性从高到低重新排序。

仅返回排序后的片段编号列表（使用原始序号），格式为 JSON 数组：
[3, 1, 5, 2, 4]

规则：
1. 与查询直接相关的排在前面
2. 信息更具体的排在前面
3. 保留 top-5 最相关的结果
4. 如果都不相关，返回空数组 []"""


def rerank_with_llm(
    query: str,
    fragments: List[Dict[str, Any]],
    user_id: int,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    使用 LLM 对候选记忆进行重排序。

    当 LLM 不可用时，按融合分数降序排列作为回退。

    Args:
        query: 用户查询
        fragments: 候选记忆片段列表
        user_id: 用户 ID
        top_k: 保留前 K 条

    Returns:
        重排序后的记忆列表
    """
    if not fragments:
        return []

    config = get_config()
    if not config.get("rerank_enabled", True):
        # 重排序关闭，按融合分数排列
        fragments.sort(key=lambda x: x.get("_fusion_score", 0), reverse=True)
        return fragments[:top_k]

    try:
        from app.services.llm_backend_service import llm_chat

        # 构建候选列表文本
        candidates_text = ""
        for i, frag in enumerate(fragments[:top_k * 2], 1):
            content = (frag.get("content") or "")[:200]
            frag_type = frag.get("fragment_type", "记忆")
            score = frag.get("_fusion_score", 0)
            candidates_text += f"{i}. [{frag_type}] {content} (score: {score:.3f})\n"

        msg = [
            {"role": "system", "content": RERANK_SYSTEM_PROMPT},
            {"role": "user", "content": f"用户查询: {query}\n\n候选记忆:\n{candidates_text}\n\n请输出排序后的序号数组:"},
        ]

        result = llm_chat(user_id=user_id, messages=msg, temperature=0.1)
        if result.get("success") and result.get("content"):
            import json as _json
            text = result["content"]

            # 提取 JSON 数组
            code_match = re.search(r'```(?:json)?\s*\n?\[.*?\]\n?```', text, re.DOTALL)
            if code_match:
                text = code_match.group(0).replace('```json', '').replace('```', '').strip()
            array_match = re.search(r'\[[\d,\s]+\]', text)
            if array_match:
                order = _json.loads(array_match.group(0))
                reranked = []
                seen = set()
                for idx in order:
                    idx = int(idx) - 1
                    if 0 <= idx < len(fragments) and idx not in seen:
                        seen.add(idx)
                        reranked.append(fragments[idx])
                # 补充未在 LLM 结果中的
                for i in range(len(fragments)):
                    if i not in seen:
                        reranked.append(fragments[i])
                logger.info(f"✓ LLM 重排序: {len(reranked[:top_k])} 条")
                return reranked[:top_k]

    except Exception as e:
        logger.warning(f"LLM 重排序失败，回退融合排序: {e}")

    # 回退：按融合分数排序
    fragments.sort(key=lambda x: x.get("_fusion_score", 0), reverse=True)
    return fragments[:top_k]


# ============================================================
# 5. 混合检索融合
# ============================================================

def _normalize_scores(fragments: List[Dict[str, Any]],
                      score_key: str) -> List[Dict[str, Any]]:
    """对指定分数进行 min-max 归一化到 [0, 1]"""
    if not fragments:
        return fragments

    scores = [f.get(score_key, 0) or 0 for f in fragments]
    if not scores:
        return fragments

    min_s = min(scores)
    max_s = max(scores)
    range_s = max_s - min_s if max_s > min_s else 1.0

    for f in fragments:
        raw = f.get(score_key, 0) or 0
        f[f"{score_key}_norm"] = (raw - min_s) / range_s
    return fragments


def hybrid_search(
    user_id: int,
    query: str,
    alpha: Optional[float] = None,
    beta: Optional[float] = None,
    gamma: Optional[float] = None,
    delta: Optional[float] = None,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """
    多信号混合检索主入口。

    融合公式:
      final_score = alpha * semantic_score + beta * bm25_score
                   + gamma * entity_boost + delta * recency_score

    流程:
    1. 语义搜索 (ChromaDB) → 归一化 semantic_score
    2. BM25 搜索 (FTS5) → 归一化 bm25_score
    3. 实体加权 → entity_boost
    4. 时间衰减 → recency_score
    5. 融合计算 → final_score
    6. 重排序 (LLM Reranker)

    Args:
        user_id: 用户 ID
        query: 查询文本
        alpha: 语义权重（覆盖配置）
        beta: BM25 权重
        gamma: 实体权重
        delta: 时间权重
        top_k: 最终返回数

    Returns:
        混合检索结果
    """
    try:
        config = get_config()
        w_alpha = alpha if alpha is not None else config["alpha"]
        w_beta = beta if beta is not None else config["beta"]
        w_gamma = gamma if gamma is not None else config["gamma"]
        w_delta = delta if delta is not None else config["delta"]
        top_k_final = top_k if top_k is not None else config["top_k_final"]

        logger.info(f"混合检索: '{query}' 权重=({w_alpha:.2f}, {w_beta:.2f}, {w_gamma:.2f}, {w_delta:.2f})")

        # ================================================================
        # 步骤 1: 语义搜索 (ChromaDB)
        # ================================================================
        semantic_result = search_fragments_by_semantic(
            user_id=user_id,
            query=query,
            top_k=config["semantic_top_k"],
            threshold=0.1,  # 低阈值以扩大候选集
        )
        semantic_map = {}  # id -> fragment
        if semantic_result.get("success"):
            for frag in semantic_result.get("fragments", []):
                fid = frag.get("id")
                if fid:
                    frag["semantic_score"] = frag.get("similarity", 0.5)
                    semantic_map[fid] = frag

        # ================================================================
        # 步骤 2: BM25 搜索 (FTS5)
        # ================================================================
        bm25_result = search_bm25(query=query, user_id=user_id, top_k=config["bm25_top_k"])
        bm25_map = {}
        if bm25_result.get("success"):
            for frag in bm25_result.get("fragments", []):
                fid = frag.get("id")
                if fid:
                    bm25_map[fid] = frag

        # ================================================================
        # 步骤 3: 合并候选集（并集）
        # ================================================================
        all_ids = set(semantic_map.keys()) | set(bm25_map.keys())
        if not all_ids:
            return {
                "success": True,
                "fragments": [],
                "count": 0,
                "query": query,
                "weights_used": {"alpha": w_alpha, "beta": w_beta, "gamma": w_gamma, "delta": w_delta},
                "signals": {"semantic": 0, "bm25": 0, "entity": 0, "recency": 0},
            }

        # 从 DB 获取完整信息
        db = get_db_client()
        fused_fragments = []
        for fid in all_ids:
            rows = db.execute(
                'SELECT * FROM memory_fragments WHERE id = ?',
                (int(fid),)
            )
            if rows:
                frag = dict(rows[0])

                # 语义分
                if fid in semantic_map:
                    frag["semantic_score"] = semantic_map[fid].get("semantic_score", 0.5)
                    # 保留原始 chroma metadata
                    frag["vector_document"] = semantic_map[fid].get("vector_document", "")
                else:
                    frag["semantic_score"] = 0.0

                # BM25 分
                if fid in bm25_map:
                    frag["bm25_score"] = bm25_map[fid].get("bm25_score", 0.0)
                else:
                    frag["bm25_score"] = 0.0

                # 实体加权
                frag["entity_boost"] = compute_entity_boost(query, frag)

                # 时间衰减
                frag["recency_score"] = compute_recency_score(frag)

                fused_fragments.append(frag)

        # ================================================================
        # 步骤 4: 归一化各信号分数
        # ================================================================
        for key in ["semantic_score", "bm25_score"]:
            fused_fragments = _normalize_scores(fused_fragments, key)
            for f in fused_fragments:
                f[key] = f.get(f"{key}_norm", f.get(key, 0))

        # ================================================================
        # 步骤 5: 融合计算
        # ================================================================
        for frag in fused_fragments:
            s_semantic = frag.get("semantic_score", 0) or 0
            s_bm25 = frag.get("bm25_score", 0) or 0
            s_entity = frag.get("entity_boost", 0) or 0
            s_recency = frag.get("recency_score", 0) or 0

            final_score = (
                w_alpha * s_semantic
                + w_beta * s_bm25
                + w_gamma * s_entity
                + w_delta * s_recency
            )
            frag["_fusion_score"] = round(final_score, 4)
            frag["_signal_breakdown"] = {
                "semantic": round(s_semantic, 4),
                "bm25": round(s_bm25, 4),
                "entity": round(s_entity, 4),
                "recency": round(s_recency, 4),
            }

        # 按融合分降序排列
        fused_fragments.sort(key=lambda x: x.get("_fusion_score", 0), reverse=True)

        # ================================================================
        # 步骤 6: LLM 重排序
        # ================================================================
        reranked = rerank_with_llm(
            query=query,
            fragments=fused_fragments[:config["rerank_top_k"]],
            user_id=user_id,
            top_k=top_k_final,
        )

        # 如果重排序没有返回结果，使用融合排序的前 top_k
        if not reranked:
            reranked = fused_fragments[:top_k_final]

        # 统计信号贡献
        signal_stats = {"semantic": 0, "bm25": 0, "entity": 0, "recency": 0}
        for frag in reranked:
            sd = frag.get("_signal_breakdown", {})
            for k in signal_stats:
                signal_stats[k] += sd.get(k, 0)
        if reranked:
            for k in signal_stats:
                signal_stats[k] = round(signal_stats[k] / len(reranked), 4)

        logger.info(f"✓ 混合检索完成: {len(reranked)} 条")
        return {
            "success": True,
            "fragments": reranked,
            "count": len(reranked),
            "query": query,
            "weights_used": {"alpha": w_alpha, "beta": w_beta, "gamma": w_gamma, "delta": w_delta},
            "signals": signal_stats,
        }

    except Exception as e:
        logger.error(f"✗ 混合检索失败: {e}")
        return {"success": False, "error": str(e)}


# ============================================================
# 测试
# ============================================================

def test_hybrid_search():
    """测试混合检索模块"""
    from datetime import timedelta

    print("\n" + "=" * 60)
    print("测试 多信号混合检索 模块")
    print("=" * 60 + "\n")

    test_user_id = 997
    db = get_db_client()

    # 清理
    db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (test_user_id,))
    try:
        db.execute("DELETE FROM fragments_fts")
    except Exception:
        pass
    print("  清理完成\n")

    # 创建测试数据
    from app.services.memory_fragment_service import create_fragment

    print("--- 准备测试数据 ---")
    test_fragments = [
        ("info", "用户叫张三，是一名产品经理", 0.9, None),
        ("info", "用户叫李四，是一名软件工程师", 0.8, None),
        ("plan", "张三计划明天完成架构设计文档", 0.7, None),
        ("preference", "张三喜欢极简设计风格", 0.6, None),
        ("plan", "李四下周开始开发用户模块", 0.7, None),
        ("info", "张三在北京的腾讯公司工作", 0.85, None),
        ("preference", "李四习惯用Python编程", 0.6, None),
        ("plan", "王五是张三的领导，负责项目评审", 0.75, None),
        ("info", "旧的项目资料已经归档", 0.3, timedelta(days=200)),  # 旧数据
        ("preference", "张三曾经喜欢喝咖啡（旧偏好）", 0.4, timedelta(days=150)),  # 旧数据
    ]

    fragment_ids = []
    for ftype, content, importance, age in test_fragments:
        kwargs = {"ttl": None, "importance_score": importance}
        created_at = None
        if age:
            created_at = (datetime.now() - age).isoformat()
        result = create_fragment(test_user_id, ftype, content, **kwargs)
        fid = result.get("fragment_id")
        if fid:
            fragment_ids.append(fid)
            if created_at:
                db.execute(
                    "UPDATE memory_fragments SET created_at = ? WHERE id = ?",
                    (created_at, fid)
                )

    # 重建 FTS5 索引
    rebuild_fts_index()
    print(f"  创建 {len(fragment_ids)} 条测试记忆\n")

    # ================================================================
    # 1. BM25 搜索
    # ================================================================
    print("--- 1. BM25 全文搜索 ---\n")

    print("1.1 BM25 搜索 '张三'...")
    r = search_bm25("张三", test_user_id, top_k=10)
    assert r.get("success"), f"BM25 搜索失败: {r}"
    print(f"  结果: {r['count']} 条")
    for f in r.get("fragments", []):
        print(f"    [{f['fragment_type']}] {f['content']} (bm25={f['bm25_score']:.3f})")
    assert r["count"] >= 3, f"预期 >=3, 实际 {r['count']}"
    print("  ✓ BM25 搜索完成\n")

    print("1.2 BM25 搜索 '产品经理'...")
    r = search_bm25("产品经理", test_user_id, top_k=10)
    print(f"  结果: {r['count']} 条")
    assert r["count"] >= 1
    print("  ✓ BM25 中文搜索完成\n")

    print("1.3 BM25 搜索不存在的词...")
    r = search_bm25("xyznonexistentkeyword", test_user_id, top_k=10)
    print(f"  结果: {r['count']} 条（预期 0）")
    print("  ✓ BM25 零结果处理完成\n")

    # ================================================================
    # 2. 实体加权
    # ================================================================
    print("--- 2. 实体加权 ---\n")

    print("2.1 计算实体加权分数...")
    frag_test = {"content": "张三在北京的腾讯公司工作", "fragment_type": "info"}
    boost = compute_entity_boost("张三腾讯北京", frag_test)
    print(f"  query='张三腾讯北京', content='张三在北京的腾讯公司工作'")
    print(f"  实体加分: {boost:.3f} (预期 > 0)")
    assert boost > 0.0, f"预期 > 0, 实际 {boost}"
    print("  ✓ 实体加权完成\n")

    print("2.2 无实体匹配...")
    boost = compute_entity_boost("完全无关的话题", frag_test)
    print(f"  无匹配实体加分: {boost:.3f} (预期 0.0)")
    assert boost == 0.0
    print("  ✓ 无实体匹配处理完成\n")

    # ================================================================
    # 3. 时间衰减
    # ================================================================
    print("--- 3. 时间衰减 ---\n")

    print("3.1 info 类型不衰减...")
    fresh_frag = {"fragment_type": "info", "created_at": datetime.now().isoformat()}
    old_info = {"fragment_type": "info", "created_at": (datetime.now() - timedelta(days=500)).isoformat()}
    assert compute_recency_score(fresh_frag) == 1.0
    assert compute_recency_score(old_info) == 1.0
    print("  info 类型: fresh=1.0, old=1.0 ✓")

    print("3.2 plan 类型时间衰减...")
    old_plan = {"fragment_type": "plan", "created_at": (datetime.now() - timedelta(days=200)).isoformat()}
    score = compute_recency_score(old_plan)
    print(f"  200天前的 plan: {score:.3f} (预期 0.3~0.5)")
    assert 0.2 <= score <= 0.6
    print("  ✓ 时间衰减完成\n")

    # ================================================================
    # 4. 混合检索端到端
    # ================================================================
    print("--- 4. 混合检索端到端 ---\n")

    print("4.1 混合检索 '张三 产品经理'...")
    r = hybrid_search(test_user_id, "张三 产品经理", top_k=5)
    assert r.get("success"), f"混合检索失败: {r}"
    print(f"  结果: {r['count']} 条")
    print(f"  权重: {r.get('weights_used')}")
    print(f"  信号统计: {r.get('signals')}")
    for i, f in enumerate(r.get("fragments", []), 1):
        sd = f.get("_signal_breakdown", {})
        print(f"  {i}. [{f['fragment_type']}] {f['content']}")
        print(f"     fusion={f['_fusion_score']:.3f} semantic={sd.get('semantic',0):.3f} "
              f"bm25={sd.get('bm25',0):.3f} entity={sd.get('entity',0):.3f} "
              f"recency={sd.get('recency',0):.3f}")
    assert r["count"] >= 1
    print("  ✓ 混合检索完成\n")

    print("4.2 混合检索 '李四 Python开发'...")
    r = hybrid_search(test_user_id, "李四 Python开发", top_k=5)
    assert r.get("success")
    print(f"  结果: {r['count']} 条")
    assert r["count"] >= 1
    print("  ✓ 混合检索多查询完成\n")

    print("4.3 调整权重测试...")
    r = hybrid_search(test_user_id, "张三", alpha=0.5, beta=0.5, gamma=0.0, delta=0.0, top_k=5)
    print(f"  仅 semantic+bm25: {r['count']} 条")
    assert r.get("weights_used", {}).get("alpha") == 0.5
    print("  ✓ 权重调整完成\n")

    # ================================================================
    # 5. 配置管理
    # ================================================================
    print("--- 5. 配置管理 ---\n")

    print("5.1 获取当前配置...")
    config = get_config()
    print(f"  alpha={config.get('alpha')}, beta={config.get('beta')}, "
          f"gamma={config.get('gamma')}, delta={config.get('delta')}")

    print("5.2 更新权重...")
    r = set_weights(alpha=0.5, beta=0.2, gamma=0.2, delta=0.1)
    assert r["success"]
    updated = get_config()
    print(f"  更新后: alpha={updated.get('alpha')}, beta={updated.get('beta')}, "
          f"gamma={updated.get('gamma')}, delta={updated.get('delta')}")
    assert updated["alpha"] == 0.5

    # 恢复默认
    set_weights(**{k: HYBRID_SEARCH_CONFIG[k] for k in WEIGHT_KEYS})
    print("  ✓ 配置管理完成\n")

    # ================================================================
    # 6. 重排序
    # ================================================================
    print("--- 6. LLM 重排序 ---\n")

    print("6.1 回退排序（无 LLM 时按融合分）...")
    test_frags = [
        {"content": "张三喜欢编程", "fragment_type": "preference", "_fusion_score": 0.8},
        {"content": "李四喜欢设计", "fragment_type": "preference", "_fusion_score": 0.6},
        {"content": "张三喜欢音乐", "fragment_type": "preference", "_fusion_score": 0.9},
    ]
    result = rerank_with_llm("张三", test_frags, test_user_id, top_k=3)
    print(f"  重排序结果: {len(result)} 条")
    assert len(result) == 3
    # 按 fusion 排序的话第一条应该是 "张三喜欢音乐" (0.9)
    print(f"  第一条: {result[0]['content']}")
    print("  ✓ 重排序完成\n")

    # 清理
    print("--- 清理测试数据 ---")
    db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (test_user_id,))
    try:
        db.execute("DELETE FROM fragments_fts")
    except Exception:
        pass
    print("  清理完成")

    print("\n" + "=" * 60)
    print("✅ 混合检索模块测试完成！")
    print("=" * 60 + "\n")

    return True


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    )
    test_hybrid_search()
