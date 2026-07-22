"""
统一错误处理模型（P0 基础设施）

提供全应用一致的错误响应结构：
- ErrorCode：稳定的机器可读错误码枚举
- ErrorResponse：标准化错误响应体（Pydantic）
- AppException：应用异常基类及常用派生类
- get_trace_id()：优先从 OTel span context 提取 trace_id，降级为 UUID4

所有 API 错误最终都会被 main.py 的全局异常处理器转换为 ErrorResponse。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ErrorCode(str, Enum):
    """机器可读的稳定错误码（格式：DOMAIN_DESCRIPTION）"""

    AUTH_INVALID_CREDENTIALS = "AUTH_INVALID_CREDENTIALS"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    FORBIDDEN = "FORBIDDEN"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    WEBHOOK_DELIVERY_FAILED = "WEBHOOK_DELIVERY_FAILED"


# 错误码 -> 默认 HTTP 状态码映射
ERROR_CODE_STATUS: dict[ErrorCode, int] = {
    ErrorCode.AUTH_INVALID_CREDENTIALS: 401,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.VALIDATION_ERROR: 400,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.WEBHOOK_DELIVERY_FAILED: 502,
}


class ErrorResponse(BaseModel):
    """标准化错误响应体"""

    code: str = Field(..., description="机器可读错误码")
    message: str = Field(..., description="人类可读错误描述")
    details: Optional[Any] = Field(None, description="附加错误详情（字段级校验错误等）")
    trace_id: str = Field(..., description="请求链路追踪 ID，用于日志关联")
    timestamp: str = Field(..., description="ISO8601 UTC 时间戳")

    @classmethod
    def build(
        cls,
        code: ErrorCode | str,
        message: str,
        *,
        details: Optional[Any] = None,
        trace_id: Optional[str] = None,
    ) -> "ErrorResponse":
        """构造 ErrorResponse，自动填充 trace_id 与 timestamp"""
        return cls(
            code=str(code.value if isinstance(code, ErrorCode) else code),
            message=message,
            details=details,
            trace_id=trace_id or get_trace_id(),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )


def get_trace_id() -> str:
    """
    获取当前请求的 trace_id。

    优先从 OpenTelemetry span context 提取（32 位 hex）；
    当 OTel 未安装或无活跃 span 时，降级为 UUID4。
    """
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        ctx = span.get_span_context() if span else None
        if ctx is not None and getattr(ctx, "is_valid", False) and ctx.trace_id:
            return format(ctx.trace_id, "032x")
    except Exception:  # pragma: no cover - OTel 未安装或运行期异常时降级
        pass
    return uuid.uuid4().hex


class AppException(Exception):
    """
    应用异常基类。

    携带稳定错误码、HTTP 状态码、人类可读消息与可选详情。
    由 main.py 的全局异常处理器统一转换为 ErrorResponse。
    """

    error_code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = 500

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        details: Optional[Any] = None,
        error_code: Optional[ErrorCode] = None,
        status_code: Optional[int] = None,
    ) -> None:
        if error_code is not None:
            self.error_code = error_code
        if status_code is not None:
            self.status_code = status_code
        else:
            self.status_code = ERROR_CODE_STATUS.get(self.error_code, self.status_code)
        self.message = message or self.error_code.value
        self.details = details
        super().__init__(self.message)

    def to_response(self, trace_id: Optional[str] = None) -> ErrorResponse:
        """转换为标准错误响应体"""
        return ErrorResponse.build(
            self.error_code,
            self.message,
            details=self.details,
            trace_id=trace_id,
        )


class NotFoundError(AppException):
    """资源不存在（404）"""

    error_code = ErrorCode.NOT_FOUND
    status_code = 404

    def __init__(self, message: str = "Resource not found", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class ConflictError(AppException):
    """资源冲突（409），如唯一约束冲突"""

    error_code = ErrorCode.CONFLICT
    status_code = 409

    def __init__(self, message: str = "Resource conflict", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class ForbiddenError(AppException):
    """无权访问资源（403）"""

    error_code = ErrorCode.FORBIDDEN
    status_code = 403

    def __init__(self, message: str = "Forbidden", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class ValidationError(AppException):
    """业务参数校验失败（400）。注：请求体 schema 校验错误由
    FastAPI RequestValidationError 处理器返回 422，两者区分。"""

    error_code = ErrorCode.VALIDATION_ERROR
    status_code = 400

    def __init__(self, message: str = "Validation error", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


class AuthError(AppException):
    """认证失败（401）"""

    error_code = ErrorCode.AUTH_INVALID_CREDENTIALS
    status_code = 401

    def __init__(self, message: str = "Invalid credentials", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)


__all__ = [
    "ErrorCode",
    "ERROR_CODE_STATUS",
    "ErrorResponse",
    "get_trace_id",
    "AppException",
    "NotFoundError",
    "ConflictError",
    "ForbiddenError",
    "ValidationError",
    "AuthError",
    "handle_service_result",
]


# ============================================================
# API 错误处理装饰器
# ============================================================

from functools import wraps
from fastapi import HTTPException


def handle_service_result(func):
    """
    消除 API 层重复的 try/except/HTTPException 模式。

    自动处理 service 函数返回的 {"success": False, "error": "..."} 格式，
    将其转换为适当的 HTTP 异常。

    用法：
        @router.post("/items")
        @handle_service_result
        async def create_item(request: ItemRequest, principal: ...):
            return create_item_service(user_id=principal.user_id, ...)

    等价于：
        @router.post("/items")
        async def create_item(request: ItemRequest, principal: ...):
            try:
                result = create_item_service(user_id=principal.user_id, ...)
                if not result.get("success"):
                    raise HTTPException(status_code=400, detail=result.get("error"))
                return result
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=str(e))
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            result = await func(*args, **kwargs)
            # 检查 service 返回的 success 字段
            if isinstance(result, dict) and result.get("success") is False:
                error_msg = result.get("error", "Service error")
                # 根据错误内容推断状态码
                status_code = 400
                error_lower = str(error_msg).lower()
                if "not found" in error_lower or "不存在" in error_msg or "未找到" in error_msg:
                    status_code = 404
                elif "conflict" in error_lower or "冲突" in error_msg or "已存在" in error_msg:
                    status_code = 409
                elif "forbidden" in error_lower or "无权" in error_msg or "权限" in error_msg:
                    status_code = 403
                raise HTTPException(status_code=status_code, detail=error_msg)
            return result
        except HTTPException:
            raise
        except AppException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    return wrapper
