"""
API 版本管理模块（P0 基础设施）

轻量实现（不引入 fastapi-versioning）：
- API_VERSION：当前 API 语义化版本
- EndpointStability：端点稳定性等级枚举
- ENDPOINT_REGISTRY：端点路径前缀 -> 稳定性等级映射

由 main.py 的版本响应头中间件消费，为每个响应注入 API-Version / API-Stability，
并为 DEPRECATED 端点追加 Deprecation / Sunset 头。
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

# 当前 API 语义化版本
API_VERSION = "1.0.0"


class EndpointStability(str, Enum):
    """端点稳定性等级"""

    STABLE = "stable"
    BETA = "beta"
    DEPRECATED = "deprecated"


# 端点路径前缀 -> 稳定性等级
# 匹配规则：取 request.url.path 的最长前缀匹配，未命中默认为 STABLE。
ENDPOINT_REGISTRY: dict[str, EndpointStability] = {
    "/api/v1/auth": EndpointStability.STABLE,
    "/api/v1/memory": EndpointStability.STABLE,
    "/api/v1/agent": EndpointStability.STABLE,
    "/api/v1/workspaces": EndpointStability.STABLE,
    "/api/v1/webhooks": EndpointStability.BETA,
    "/api/v1/events": EndpointStability.BETA,
    "/api/v1/memory/graph": EndpointStability.BETA,
    "/api/v1/memory/hybrid": EndpointStability.BETA,
}

# DEPRECATED 端点的 Sunset 日期（RFC 8594，HTTP-date 或 ISO8601）。
# 当某端点标记为 DEPRECATED 时，在此登记其下线日期。
SUNSET_DATES: dict[str, str] = {}


def resolve_stability(path: str) -> EndpointStability:
    """按最长前缀匹配解析端点稳定性等级，未命中返回 STABLE。"""
    best: Optional[str] = None
    for prefix in ENDPOINT_REGISTRY:
        if path.startswith(prefix):
            if best is None or len(prefix) > len(best):
                best = prefix
    if best is None:
        return EndpointStability.STABLE
    return ENDPOINT_REGISTRY[best]


def resolve_sunset(path: str) -> Optional[str]:
    """返回匹配端点的 Sunset 日期（若已登记）。"""
    best: Optional[str] = None
    for prefix in SUNSET_DATES:
        if path.startswith(prefix):
            if best is None or len(prefix) > len(best):
                best = prefix
    return SUNSET_DATES.get(best) if best else None


__all__ = [
    "API_VERSION",
    "EndpointStability",
    "ENDPOINT_REGISTRY",
    "SUNSET_DATES",
    "resolve_stability",
    "resolve_sunset",
]
