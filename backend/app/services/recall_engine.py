"""
RecallEngine - 统一记忆召回引擎

从 ContextCompressor 提取的核心召回逻辑，为 ContextCompressor 和 AutoRecallService 提供统一接口。

功能：
- 语义搜索（支持混合检索）
- 生命周期过滤
- MemoryValueScorer 预算控制选择
- 生命周期更新（last_recalled_at + 冷记忆标记）
- 观测性埋点
- 实体图谱扩展
"""
import logging
import time as _time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.services.memory_fragment_service import search_fragments_by_semantic
from app.services.hybrid_search_service import hybrid_search
from app.services.memory_lifecycle_service import (
    calculate_decay_score,
    mark_cold,
    get_half_life,
)
from app.services.memory_observability_service import record_trace_event

# 延迟导入避免循环依赖
_MemoryValueScorer = None
_EntityGraphTraverser = None


def _get_scorer():
    global _MemoryValueScorer
    if _MemoryValueScorer is None:
        from app.services.context_compressor import MemoryValueScorer
        _MemoryValueScorer = MemoryValueScorer
    return _MemoryValueScorer


def _get_entity_traverser():
    global _EntityGraphTraverser
    if _EntityGraphTraverser is None:
        from app.services.context_compressor import EntityGraphTraverser
        _EntityGraphTraverser = EntityGraphTraverser
    return _EntityGraphTraverser


# ============================================================
# 数据类
# ============================================================

@dataclass
class RecallResult:
    """召回结果"""
    memories: List[Dict[str, Any]] = field(default_factory=list)
    context_text: str = ""
    total_candidates: int = 0
    token_used: int = 0
    source: str = "recall_engine"


# ============================================================
# 默认配置
# ============================================================

DEFAULT_ENGINE_CONFIG = {
    "semantic_top_k": 10,
    "entity_top_k": 5,
    "entity_relevance_threshold": 0.3,
    "use_hybrid_search": True,
    "hybrid_search_alpha": None,
    "hybrid_search_beta": None,
    "hybrid_search_gamma": None,
    "hybrid_search_delta": None,
    "hybrid_search_top_k": None,
    "lifecycle_recall_update": True,
    "lifecycle_cold_threshold": 0.3,
}


# ============================================================
# RecallEngine
# ============================================================

class RecallEngine:
    """
    统一记忆召回引擎

    整合语义搜索、评分、预算控制、生命周期更新、观测性埋点。
    为 ContextCompressor 和 AutoRecallService 提供统一的召回接口。
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = {**DEFAULT_ENGINE_CONFIG, **(config or {})}

    # ----------------------------------------------------------
    # 核心召回
    # ----------------------------------------------------------

    def recall(
        self,
        user_id: int,
        query: str,
        budget_tokens: int = 2000,
        top_k: Optional[int] = None,
        use_hybrid_search: Optional[bool] = None,
        update_lifecycle: Optional[bool] = None,
        record_traces: bool = True,
    ) -> RecallResult:
        """
        统一语义召回接口。

        流程：
        1. 语义搜索（或混合检索）
        2. 生命周期过滤
        3. MemoryValueScorer 预算控制选择
        4. 生命周期更新（可选）
        5. 观测性埋点（可选）
        6. 格式化上下文文本

        Args:
            user_id: 用户 ID
            query: 查询文本
            budget_tokens: Token 预算
            top_k: 搜索召回数量（None 使用配置）
            use_hybrid_search: 是否使用混合检索（None 使用配置）
            update_lifecycle: 是否更新生命周期（None 使用配置）
            record_traces: 是否记录观测性埋点

        Returns:
            RecallResult
        """
        try:
            _top_k = top_k or self.config["semantic_top_k"]
            _use_hybrid = use_hybrid_search if use_hybrid_search is not None else self.config.get("use_hybrid_search", False)
            _update_lifecycle = update_lifecycle if update_lifecycle is not None else self.config.get("lifecycle_recall_update", True)

            # 1. 语义搜索
            if _use_hybrid:
                search_result = hybrid_search(
                    user_id=user_id,
                    query=query,
                    alpha=self.config.get("hybrid_search_alpha"),
                    beta=self.config.get("hybrid_search_beta"),
                    gamma=self.config.get("hybrid_search_gamma"),
                    delta=self.config.get("hybrid_search_delta"),
                    top_k=self.config.get("hybrid_search_top_k", _top_k),
                )
            else:
                search_result = search_fragments_by_semantic(
                    user_id=user_id,
                    query=query,
                    top_k=_top_k,
                    threshold=0.2,
                )

            all_memories = search_result.get("fragments", [])
            total_candidates = len(all_memories)

            # 2. 生命周期过滤
            active_memories = [
                mem for mem in all_memories
                if mem.get("lifecycle_status", "active") not in ("soft_deleted", "archived")
            ]

            # 3. 预算控制选择
            scorer = _get_scorer()
            selected = scorer.select_top_by_budget(
                memories=active_memories,
                query=query,
                budget_tokens=budget_tokens,
            )

            if not selected:
                return RecallResult(
                    memories=[],
                    context_text="",
                    total_candidates=total_candidates,
                    token_used=0,
                )

            # 4. 生命周期更新
            if _update_lifecycle:
                self._update_lifecycle(user_id, selected)

            # 5. 观测性埋点
            if record_traces:
                self._record_traces(user_id, query, selected)

            # 6. 格式化上下文
            context_text = self._format_context(selected)

            # 估算 token
            from app.services.context_compressor import estimate_tokens
            token_used = estimate_tokens(context_text)

            return RecallResult(
                memories=selected,
                context_text=context_text,
                total_candidates=total_candidates,
                token_used=token_used,
            )

        except Exception as e:
            logger.warning(f"RecallEngine.recall 失败: {e}")
            return RecallResult()

    # ----------------------------------------------------------
    # 实体扩展召回
    # ----------------------------------------------------------

    def recall_with_entities(
        self,
        user_id: int,
        query: str,
        budget_tokens: int = 1200,
        top_k: Optional[int] = None,
        threshold: Optional[float] = None,
    ) -> RecallResult:
        """
        实体图谱扩展召回（Level 3）。

        从查询中提取实体，通过图谱遍历扩展关联记忆。

        Args:
            user_id: 用户 ID
            query: 查询文本
            budget_tokens: Token 预算
            top_k: 返回数量
            threshold: 相关度阈值

        Returns:
            RecallResult
        """
        try:
            traverser = _get_entity_traverser()
            scorer = _get_scorer()

            _top_k = top_k or self.config["entity_top_k"]
            _threshold = threshold if threshold is not None else self.config["entity_relevance_threshold"]

            # 1. 提取实体
            entities = traverser.extract_entities(query)
            if not entities:
                return RecallResult()

            # 2. 搜索关联记忆
            related = traverser.search_related_memories(
                user_id=user_id,
                entities=entities,
                top_k=_top_k,
                threshold=_threshold,
            )

            # 3. 生命周期过滤
            active_related = [
                mem for mem in related
                if mem.get("lifecycle_status", "active") not in ("soft_deleted", "archived")
            ]

            # 4. 预算选择
            selected = scorer.select_top_by_budget(
                memories=active_related,
                query=query,
                budget_tokens=budget_tokens,
            )

            if not selected:
                return RecallResult(memories=[], context_text="", total_candidates=len(related))

            # 5. 格式化（实体风格）
            lines = ["[关联信息]"]
            seen_entities = set()
            for i, mem in enumerate(selected, 1):
                content = mem.get("content", "").strip()
                entity = mem.get("source_entity", "")
                if entity and entity not in seen_entities:
                    lines.append(f"  [{entity}]")
                    seen_entities.add(entity)
                lines.append(f"  {i}. {content}")

            context_text = "\n".join(lines)

            from app.services.context_compressor import estimate_tokens
            return RecallResult(
                memories=selected,
                context_text=context_text,
                total_candidates=len(related),
                token_used=estimate_tokens(context_text),
                source="entity_graph",
            )

        except Exception as e:
            logger.warning(f"RecallEngine.recall_with_entities 失败: {e}")
            return RecallResult()

    # ----------------------------------------------------------
    # Profile 召回
    # ----------------------------------------------------------

    def recall_profile(self, user_id: int, budget: int = 600) -> str:
        """
        用户 Profile 召回（Level 1）。

        从 KV 记忆变量中提取用户基本信息。

        Args:
            user_id: 用户 ID
            budget: Token 预算

        Returns:
            格式化的 Profile 文本
        """
        try:
            from app.services.memory_variable_service import list_memory_variables
            from app.services.context_compressor import estimate_tokens

            variables = list_memory_variables(user_id)
            if not variables or not isinstance(variables, dict):
                return ""

            profile_keys = [
                "user_name", "name", "username",
                "user_role", "role",
                "organization", "company", "org",
                "email", "phone", "location",
                "department", "title", "position",
            ]

            key_labels = {
                "user_name": "姓名", "name": "姓名", "username": "用户名",
                "user_role": "角色", "role": "角色",
                "organization": "组织", "company": "公司", "org": "组织",
                "department": "部门", "title": "头衔", "position": "职位",
                "email": "邮箱", "phone": "电话", "location": "位置",
            }

            profile_lines = []
            used_tokens = 30

            for key in profile_keys:
                value = variables.get(key)
                if value is not None and used_tokens < budget:
                    value_str = str(value)
                    token_cost = estimate_tokens(f"{key}: {value_str}")
                    if used_tokens + token_cost <= budget:
                        label = key_labels.get(key, key)
                        profile_lines.append(f"- {label}: {value_str}")
                        used_tokens += token_cost

            if profile_lines:
                return "[用户基本信息]\n" + "\n".join(profile_lines)

        except Exception as e:
            logger.debug(f"RecallEngine.recall_profile 失败: {e}")

        return ""

    # ----------------------------------------------------------
    # 内部辅助
    # ----------------------------------------------------------

    def _update_lifecycle(self, user_id: int, selected: List[Dict[str, Any]]):
        """更新选中记忆的生命周期信息"""
        try:
            db = get_db_client()
            now = datetime.now().isoformat()
            cold_threshold = self.config.get("lifecycle_cold_threshold", 0.3)

            for mem in selected:
                memory_id = mem.get("id")
                if not memory_id:
                    continue

                # 更新 last_recalled_at
                db.execute(
                    'UPDATE memory_fragments SET last_recalled_at = ? WHERE id = ?',
                    (now, memory_id)
                )
                db.execute(
                    '''UPDATE memory_lifecycle SET last_recalled_at = ?
                       WHERE user_id = ? AND memory_type = 'fragment' AND memory_id = ?''',
                    (now, user_id, str(memory_id))
                )

                # 自动标记冷记忆
                importance = mem.get("importance_score", 0.5)
                if isinstance(importance, str):
                    try:
                        importance = float(importance)
                    except (ValueError, TypeError):
                        importance = 0.5

                fragment_type = mem.get("fragment_type", "")
                half_life_days = get_half_life(fragment_type)
                decay = calculate_decay_score(mem.get("created_at"), half_life_days)
                if decay < cold_threshold:
                    try:
                        mark_cold(
                            user_id=user_id,
                            memory_type="fragment",
                            memory_id=str(memory_id),
                            reason="decay_below_threshold",
                        )
                    except Exception:
                        pass

        except Exception as e:
            logger.debug(f"生命周期更新失败: {e}")

    def _record_traces(self, user_id: int, query: str, selected: List[Dict[str, Any]]):
        """记录观测性埋点"""
        try:
            for mem in selected:
                memory_id = str(mem.get("id", ""))
                if memory_id:
                    record_trace_event(
                        user_id=user_id,
                        memory_id=memory_id,
                        memory_type="fragment",
                        event_type="recalled",
                        event_source="recall_engine",
                        metadata={
                            "query": query[:200],
                            "similarity": mem.get("similarity", 0),
                            "importance": mem.get("importance_score", 0.5),
                        }
                    )
        except Exception:
            pass

    def _format_context(self, selected: List[Dict[str, Any]]) -> str:
        """格式化召回结果为上下文文本"""
        lines = ["[相关记忆]"]
        for i, mem in enumerate(selected, 1):
            content = mem.get("content", "").strip()
            mem_type = mem.get("fragment_type", "memory")
            importance = mem.get("importance_score", 0.5)
            similarity = mem.get("similarity", 0)

            if isinstance(importance, str):
                try:
                    importance = float(importance)
                except (ValueError, TypeError):
                    importance = 0.5
            if isinstance(similarity, str):
                try:
                    similarity = float(similarity)
                except (ValueError, TypeError):
                    similarity = 0.0

            type_label = {
                "info": "信息",
                "preference": "偏好",
                "plan": "计划",
                "fact": "事实",
            }.get(mem_type, mem_type)

            lines.append(
                f"  {i}. [{type_label}] {content} "
                f"(相关度: {similarity:.2f}, 重要: {importance:.2f})"
            )

        return "\n".join(lines)

    def extract_memory_details(self, selected: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        从选中的记忆列表中提取详情（供 build_context_with_details 使用）。

        Returns:
            [{"content": str, "type": str, "score": float}]
        """
        details = []
        for mem in selected:
            content = mem.get("content", "").strip()
            mem_type = mem.get("fragment_type", "memory")
            similarity = mem.get("similarity", 0)

            if isinstance(similarity, str):
                try:
                    similarity = float(similarity)
                except (ValueError, TypeError):
                    similarity = 0.0

            details.append({
                "content": content[:200],
                "type": mem_type,
                "score": round(similarity, 4),
            })
        return details
