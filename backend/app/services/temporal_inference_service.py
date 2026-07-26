"""
时间有效性推断服务（W2-F2.2）

从记忆内容推断时间有效性窗口（valid_from / valid_until），
纯规则引擎实现，无需 LLM 调用。

规则：
- "今天/明天/后天" → valid_until = 当天/次日/第三天 23:59
- "下周X" → valid_until = 下周X 23:59
- "本周X/周X/星期X" → valid_until = 本周（或下周）X 23:59
- "本月" → valid_until = 本月最后一天 23:59
- "X月X日/X号" → valid_until = 该日期 23:59
- 无时间标记 → valid_until = None（永久有效）

同时提供：
- scan_and_expire(): 定时任务扫描过期记忆并标记为 expired
"""
import logging
import re
import calendar
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any

from app.core.db_client import get_db_client

logger = logging.getLogger(__name__)

# 时序自然失效状态（区别于 superseded 被取代）
EXPIRED_STATUS = "expired"

# 中文星期映射
_WEEKDAY_MAP = {
    "一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5,
    "日": 6, "天": 6, "1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6,
}

# 英文星期映射
_WEEKDAY_EN_MAP = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}


def _end_of_day(dt: datetime) -> datetime:
    """返回某天的 23:59:59"""
    return dt.replace(hour=23, minute=59, second=59, microsecond=0)


def _end_of_month(dt: datetime) -> datetime:
    """返回某月最后一天的 23:59:59"""
    last_day = calendar.monthrange(dt.year, dt.month)[1]
    return _end_of_day(dt.replace(day=last_day))


def infer_validity_window(
    content: str,
    created_at: Optional[datetime] = None,
) -> Tuple[datetime, Optional[datetime]]:
    """从记忆内容推断时间有效性窗口。

    Args:
        content: 记忆内容文本
        created_at: 记忆创建时间（默认 now）

    Returns:
        (valid_from, valid_until) 元组；valid_until=None 表示永久有效
    """
    base = created_at or datetime.now()
    valid_from = base
    valid_until: Optional[datetime] = None

    # 1. 今天/明天/后天
    if re.search(r'今天|today', content, re.IGNORECASE):
        valid_until = _end_of_day(base)
    elif re.search(r'明天|tomorrow', content, re.IGNORECASE):
        valid_until = _end_of_day(base + timedelta(days=1))
    elif re.search(r'后天', content):
        valid_until = _end_of_day(base + timedelta(days=2))

    # 2. 下周X
    if valid_until is None:
        m = re.search(r'下(?:个)?(?:周|星期)([一二三四五六日天1-7])', content)
        if m:
            target_wd = _WEEKDAY_MAP.get(m.group(1))
            if target_wd is not None:
                # 下周的目标星期：先跳到下周一，再偏移
                days_to_next_monday = (7 - base.weekday()) % 7 or 7
                next_monday = base + timedelta(days=days_to_next_monday)
                valid_until = _end_of_day(next_monday + timedelta(days=target_wd))
        else:
            m_en = re.search(r'next\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)', content, re.IGNORECASE)
            if m_en:
                target_wd = _WEEKDAY_EN_MAP[m_en.group(1).lower()]
                days_to_next_monday = (7 - base.weekday()) % 7 or 7
                next_monday = base + timedelta(days=days_to_next_monday)
                valid_until = _end_of_day(next_monday + timedelta(days=target_wd))

    # 3. 本周X / 周X / 星期X（如果已过则视为下周）
    if valid_until is None:
        m = re.search(r'(?:本周|这周|周|星期)([一二三四五六日天1-7])', content)
        if m:
            target_wd = _WEEKDAY_MAP.get(m.group(1))
            if target_wd is not None:
                delta = target_wd - base.weekday()
                if delta < 0:
                    delta += 7  # 已过 → 顺延到下周
                valid_until = _end_of_day(base + timedelta(days=delta))

    # 4. 本月
    if valid_until is None:
        if re.search(r'本月|这个月|this month', content, re.IGNORECASE):
            valid_until = _end_of_month(base)

    # 5. X月X日 / X月X号
    if valid_until is None:
        m = re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]', content)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            if 1 <= month <= 12 and 1 <= day <= 31:
                try:
                    target = base.replace(month=month, day=day)
                    if target < base:
                        # 日期已过 → 视为明年
                        target = target.replace(year=base.year + 1)
                    valid_until = _end_of_day(target)
                except ValueError:
                    pass  # 无效日期（如 2月30日）

    return valid_from, valid_until


def apply_validity_to_fragment(
    fragment_id: int,
    user_id: int,
    content: str,
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """为记忆片段推断并写入时间有效性窗口。

    Args:
        fragment_id: 记忆片段 ID
        user_id: 用户 ID
        content: 记忆内容
        created_at: 创建时间

    Returns:
        {"success": bool, "valid_from": str, "valid_until": Optional[str]}
    """
    try:
        valid_from, valid_until = infer_validity_window(content, created_at)
        db = get_db_client()
        db.execute(
            """UPDATE memory_fragments
               SET valid_from = ?, valid_until = ?
               WHERE id = ? AND user_id = ?""",
            (
                valid_from.isoformat(),
                valid_until.isoformat() if valid_until else None,
                fragment_id,
                user_id,
            ),
        )
        if valid_until:
            logger.info(
                f"✓ 时序推断: fragment={fragment_id}, "
                f"valid_until={valid_until.isoformat()}"
            )
        return {
            "success": True,
            "valid_from": valid_from.isoformat(),
            "valid_until": valid_until.isoformat() if valid_until else None,
        }
    except Exception as e:
        logger.warning(f"时序推断写入失败: {e}")
        return {"success": False, "error": str(e)}


def scan_and_expire(
    user_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """扫描并标记已过 valid_until 的记忆为 expired。

    供定时任务调用（建议每小时一次）。

    Args:
        user_id: 限定用户（None = 全部用户）
        now: 当前时间（测试可注入）

    Returns:
        {"success": bool, "expired_count": int, "expired_ids": List[int]}
    """
    try:
        _now = (now or datetime.now()).isoformat()
        db = get_db_client()

        conditions = [
            "valid_until IS NOT NULL",
            "valid_until < ?",
            "lifecycle_status = 'active'",
        ]
        params: list = [_now]
        if user_id is not None:
            conditions.append("user_id = ?")
            params.append(user_id)

        rows = db.execute(
            f"SELECT id, user_id FROM memory_fragments WHERE {' AND '.join(conditions)}",
            tuple(params),
        )
        expired_ids = [row["id"] for row in (rows or [])]

        if expired_ids:
            for fid in expired_ids:
                db.execute(
                    "UPDATE memory_fragments SET lifecycle_status = ? WHERE id = ?",
                    (EXPIRED_STATUS, fid),
                )
            logger.info(f"✓ 时序失效扫描: 标记 {len(expired_ids)} 条记忆为 expired")

        return {
            "success": True,
            "expired_count": len(expired_ids),
            "expired_ids": expired_ids,
        }
    except Exception as e:
        logger.error(f"时序失效扫描失败: {e}")
        return {"success": False, "error": str(e), "expired_count": 0, "expired_ids": []}


def get_valid_fragments_at(
    user_id: int,
    valid_at: datetime,
    limit: int = 100,
) -> Dict[str, Any]:
    """查询某时刻有效的记忆片段（供 GET /memory/fragments?valid_at= 使用）。

    有效定义：valid_from <= valid_at AND (valid_until IS NULL OR valid_until >= valid_at)

    Args:
        user_id: 用户 ID
        valid_at: 查询时刻
        limit: 返回数量上限

    Returns:
        {"success": bool, "fragments": List[Dict], "total": int}
    """
    try:
        at_str = valid_at.isoformat()
        db = get_db_client()
        rows = db.execute(
            """SELECT * FROM memory_fragments
               WHERE user_id = ?
                 AND (valid_from IS NULL OR valid_from <= ?)
                 AND (valid_until IS NULL OR valid_until >= ?)
                 AND lifecycle_status NOT IN ('soft_deleted', 'archived', 'superseded')
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, at_str, at_str, limit),
        )
        fragments = [dict(r) for r in (rows or [])]
        return {"success": True, "fragments": fragments, "total": len(fragments)}
    except Exception as e:
        logger.error(f"时刻有效记忆查询失败: {e}")
        return {"success": False, "error": str(e), "fragments": [], "total": 0}
