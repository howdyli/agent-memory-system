"""
程序记忆提炼服务（P2 R-14）

从成功的工具调用轨迹中用 LLM 提炼可复用操作流程（procedure 记忆）：
- 数据源：agent_tool_traces（agent_loop 落库的工具调用轨迹）
- 候选：近 7 天未提炼过、工具调用数 >= PROCEDURE_MIN_TRACE_CALLS 的会话，
  全部调用成功的会话优先，单轮上限 PROCEDURE_MAX_SESSIONS_PER_RUN
- 提炼：LLM 输出严格 JSON（has_procedure/title/steps/applicable_scenario），
  markdown 代码块兜底解析；入库走语义去重（>=0.85 跳过）
- 降级：LLM 不可用/返回 mock/解析失败时跳过（不做规则式提炼），
  仍标记 extracted 避免反复扫描
"""
import logging
import json
import re
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from app.core.db_client import get_db_client
from app.core.config import get_settings


_PROCEDURE_PROMPT = """你是一个操作流程提炼助手。以下是一次 Agent 会话中的工具调用序列，请判断它是否构成一个可复用的操作流程。

要求：
1. 只有当调用序列体现出有明确目标、可泛化复用的多步操作时才算流程
2. 步骤描述要泛化（不要照抄具体参数值），保留工具名与操作意图
3. 不得虚构调用序列中不存在的步骤
4. 输出严格 JSON（不要任何额外说明文字）：
{"has_procedure": true/false, "title": "流程标题", "steps": ["步骤1", "步骤2"], "applicable_scenario": "适用场景（一句话）"}

工具调用序列：
"""


def _parse_llm_json(content: str) -> Optional[Dict[str, Any]]:
    """解析 LLM 返回的 JSON，带 ```json 代码块正则提取兜底"""
    if not content:
        return None
    try:
        return json.loads(content)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # 裸 JSON 对象兜底
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def build_procedure_content(title: str,
                            steps: List[str],
                            applicable_scenario: Optional[str] = None) -> str:
    """组装 procedure 片段的标准 content 格式（提炼与手动录入共用）"""
    lines = [title.strip()]
    if applicable_scenario:
        lines.append(f"适用场景: {applicable_scenario.strip()}")
    lines.append("步骤:")
    for idx, step in enumerate(steps, 1):
        lines.append(f"{idx}. {step.strip()}")
    return "\n".join(lines)


# ============================================================
# 候选会话
# ============================================================

def _get_candidate_sessions(user_id: int) -> List[Dict[str, Any]]:
    """获取待提炼会话：近 7 天、未提炼、调用数达标；全部成功的会话优先"""
    settings = get_settings()
    from app.services.tool_trace_service import _ensure_trace_table
    _ensure_trace_table()
    db = get_db_client()
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()

    rows = db.execute(
        '''SELECT session_id,
                  COUNT(*) AS call_count,
                  MIN(success) AS all_success
           FROM agent_tool_traces
           WHERE user_id = ? AND extracted = 0 AND created_at >= ?
           GROUP BY session_id
           HAVING call_count >= ?
           ORDER BY all_success DESC, MAX(id) DESC
           LIMIT ?''',
        (user_id, cutoff, settings.PROCEDURE_MIN_TRACE_CALLS,
         settings.PROCEDURE_MAX_SESSIONS_PER_RUN)
    )
    return [dict(r) for r in rows] if rows else []


def _format_traces_for_llm(traces: List[Dict[str, Any]]) -> str:
    """将轨迹序列格式化为 LLM 输入（参数要点 + 结果摘要截断）"""
    lines = []
    for i, t in enumerate(traces, 1):
        args = t.get("arguments") or "{}"
        result = (t.get("result_summary") or "")[:150]
        status = "成功" if t.get("success") else "失败"
        lines.append(f"{i}. 工具: {t['tool_name']} | 参数: {args[:200]} | 结果({status}): {result}")
    return "\n".join(lines)


# ============================================================
# 核心：提炼
# ============================================================

def extract_procedures_for_user(user_id: int) -> Dict[str, Any]:
    """
    对单个用户执行轨迹提炼

    Returns:
        {"success": True, "sessions_scanned", "procedures_created", "skipped_reason_stats"}
    """
    try:
        settings = get_settings()
        if not settings.PROCEDURE_EXTRACTION_ENABLED:
            return {"success": True, "skipped": True, "reason": "PROCEDURE_EXTRACTION_ENABLED=False"}

        from app.services.tool_trace_service import get_session_traces, mark_session_extracted
        from app.services.llm_backend_service import llm_chat

        sessions = _get_candidate_sessions(user_id)
        created = 0
        skipped_stats: Dict[str, int] = {}

        for sess in sessions:
            session_id = sess["session_id"]
            traces = get_session_traces(session_id, user_id=user_id)
            if not traces:
                mark_session_extracted(session_id, user_id)
                continue

            # 含失败调用的会话不提炼（只从成功轨迹学习）
            if any(not t.get("success") for t in traces):
                skipped_stats["has_failed_calls"] = skipped_stats.get("has_failed_calls", 0) + 1
                mark_session_extracted(session_id, user_id)
                continue

            messages = [
                {"role": "system", "content": "你是一个严谨的操作流程提炼助手，只输出 JSON。"},
                {"role": "user", "content": _PROCEDURE_PROMPT + _format_traces_for_llm(traces)},
            ]
            llm_result = llm_chat(user_id=user_id, messages=messages, temperature=0.1)

            # LLM 不可用/mock → 跳过且不标记（真实 LLM 可用后仍有机会提炼）
            if not llm_result.get("success") or llm_result.get("mock"):
                skipped_stats["llm_unavailable"] = skipped_stats.get("llm_unavailable", 0) + 1
                continue

            parsed = _parse_llm_json(llm_result.get("content", ""))
            if parsed is None:
                skipped_stats["parse_failed"] = skipped_stats.get("parse_failed", 0) + 1
                mark_session_extracted(session_id, user_id)
                continue

            if not parsed.get("has_procedure") or not parsed.get("title") or not parsed.get("steps"):
                skipped_stats["no_procedure"] = skipped_stats.get("no_procedure", 0) + 1
                mark_session_extracted(session_id, user_id)
                continue

            content = build_procedure_content(
                title=str(parsed["title"]),
                steps=[str(s) for s in parsed["steps"]],
                applicable_scenario=parsed.get("applicable_scenario"),
            )

            store_result = _store_procedure_with_dedup(
                user_id=user_id,
                content=content,
                metadata={"source": "trace_extraction", "session_id": session_id},
            )
            if store_result.get("skipped"):
                skipped_stats["duplicate"] = skipped_stats.get("duplicate", 0) + 1
            elif store_result.get("success"):
                created += 1
            else:
                skipped_stats["create_failed"] = skipped_stats.get("create_failed", 0) + 1

            mark_session_extracted(session_id, user_id)

        if sessions:
            logger.info(f"✓ 程序记忆提炼: user={user_id}, 会话 {len(sessions)}, "
                        f"新建 {created}, 跳过 {skipped_stats}")
        return {
            "success": True,
            "sessions_scanned": len(sessions),
            "procedures_created": created,
            "skipped_reason_stats": skipped_stats,
        }

    except Exception as e:
        logger.error(f"✗ 程序记忆提炼失败: {e}")
        return {"success": False, "error": str(e)}


def _store_procedure_with_dedup(user_id: int,
                                content: str,
                                metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """入库 procedure 片段（语义 + bigram Jaccard 去重，>=0.85 跳过）"""
    try:
        from app.services.memory_lifecycle_service import _text_similarity
        from app.services.memory_fragment_service import search_fragments_by_semantic

        semantic_result = search_fragments_by_semantic(
            user_id=user_id, query=content, top_k=5, threshold=0.3,
        )
        if semantic_result.get("success"):
            for frag in semantic_result.get("fragments", []):
                combined_sim = max(
                    frag.get("similarity", 0),
                    _text_similarity(content, frag.get("content", "")),
                )
                if combined_sim >= 0.85:
                    logger.info(f"↩ 跳过重复 procedure (sim={combined_sim:.2f}): {content[:50]}...")
                    return {"success": True, "skipped": True, "similarity": round(combined_sim, 4)}
    except Exception as e:
        logger.warning(f"procedure 去重检查异常，fallback 到直接创建: {e}")

    from app.services.memory_fragment_service import create_fragment
    return create_fragment(
        user_id=user_id,
        fragment_type="procedure",
        content=content,
        importance_score=0.7,
        metadata=metadata,
    )


# ============================================================
# 调度入口
# ============================================================

def run_scheduled_procedure_extraction() -> Dict[str, Any]:
    """调度入口：对所有存在未提炼轨迹的用户执行提炼（供 run_maintenance_now 调用）"""
    try:
        settings = get_settings()
        if not settings.PROCEDURE_EXTRACTION_ENABLED:
            return {"success": True, "skipped": True, "reason": "PROCEDURE_EXTRACTION_ENABLED=False"}

        from app.services.tool_trace_service import _ensure_trace_table
        _ensure_trace_table()
        db = get_db_client()
        cutoff = (datetime.now() - timedelta(days=7)).isoformat()
        rows = db.execute(
            '''SELECT DISTINCT user_id FROM agent_tool_traces
               WHERE extracted = 0 AND created_at >= ?''',
            (cutoff,)
        )
        user_ids = [r["user_id"] for r in rows] if rows else []

        results = []
        for uid in user_ids:
            result = extract_procedures_for_user(uid)
            results.append({"user_id": uid, **{k: v for k, v in result.items() if k != "success"}})

        return {"success": True, "users_processed": len(user_ids), "results": results}
    except Exception as e:
        logger.error(f"✗ 调度程序记忆提炼失败: {e}")
        return {"success": False, "error": str(e)}
