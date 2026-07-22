"""
统一响应信封（Response Envelope）

标准响应格式：
{
    "success": bool,           # 请求是否成功
    "data": Any | None,        # 成功时的数据载荷
    "error": str | None,       # 失败时的错误信息
    "trace_id": str | None,    # 请求追踪 ID（自动从上下文填充）
    "meta": dict | None        # 元信息（分页、计数等）
}

使用方式：
    from app.core.response import ok, fail, paginate

    @router.get("/items")
    async def list_items():
        items = [...]
        return ok(data=items, meta={"count": len(items)})

    @router.get("/items/{item_id}")
    async def get_item(item_id: int):
        item = find_item(item_id)
        if not item:
            return fail(error="项目不存在", code="not_found")
        return ok(data=item)

    @router.get("/items")
    async def list_items_paginated(page: int = 1, page_size: int = 20):
        items, total = get_items(page, page_size)
        return paginate(items, total, page, page_size)

注意：
- 新接口应使用此标准格式
- 现有接口可逐步迁移，迁移时保持向后兼容
- trace_id 自动从 request_id_middleware 上下文获取
- 错误响应仍由全局异常处理器通过 ErrorResponse 统一处理
"""
import uuid
from typing import Any, Optional, Dict, List


def _get_trace_id() -> Optional[str]:
    """从请求上下文中获取 trace_id（由 request_id_middleware 设置）"""
    try:
        from app.main import _request_id_ctx
        tid = _request_id_ctx.get()
        return tid if tid else None
    except Exception:
        return None


def ok(
    data: Any = None,
    meta: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构造成功响应。

    Args:
        data: 响应数据载荷（可以是 dict、list、str 等）
        meta: 元信息（如 count、page 等）
        trace_id: 请求追踪 ID（不传则自动从上下文获取）

    Returns:
        标准响应字典
    """
    return {
        "success": True,
        "data": data,
        "error": None,
        "trace_id": trace_id or _get_trace_id(),
        "meta": meta,
    }


def fail(
    error: str,
    code: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构造失败响应（用于 handler 内直接返回，不抛异常的场景）。

    Args:
        error: 错误信息
        code: 错误代码（如 "not_found", "validation_error"）
        meta: 元信息
        trace_id: 请求追踪 ID

    Returns:
        标准响应字典
    """
    meta_out = meta or {}
    if code:
        meta_out["error_code"] = code
    return {
        "success": False,
        "data": None,
        "error": error,
        "trace_id": trace_id or _get_trace_id(),
        "meta": meta_out if meta_out else None,
    }


def paginate(
    items: List[Any],
    total: int,
    page: int = 1,
    page_size: int = 20,
    trace_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    构造分页响应。

    Args:
        items: 当前页数据列表
        total: 总记录数
        page: 当前页码（从 1 开始）
        page_size: 每页条数
        trace_id: 请求追踪 ID

    Returns:
        标准分页响应字典
    """
    total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
    return {
        "success": True,
        "data": items,
        "error": None,
        "trace_id": trace_id or _get_trace_id(),
        "meta": {
            "count": len(items),
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
    }


def generate_trace_id() -> str:
    """生成请求追踪 ID"""
    return uuid.uuid4().hex[:16]
