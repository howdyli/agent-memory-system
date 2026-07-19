"""Events API submodule — Phase 4 事件查询 + SSE 订阅 + Webhook 管理。"""

import json
from typing import Any, Dict, Iterator, List, Optional

from agent_memory.transport.base import Transport


class EventsAPI:
    """
    事件查询与 SSE 实时订阅接口。
    """

    def __init__(self, transport: Transport):
        self._t = transport

    def list_events(
        self,
        event_type: Optional[str] = None,
        days: int = 7,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        查询事件历史。

        Args:
            event_type: 过滤事件类型（如 "memory.created"）
            days: 查询最近 N 天
            limit: 最大返回数量

        Returns:
            事件列表
        """
        params: Dict[str, Any] = {"days": days, "limit": limit}
        if event_type:
            params["event_type"] = event_type
        result = self._t.request("GET", "/events", params=params)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("events", result.get("data", []))
        return []

    def list_event_types(self) -> List[str]:
        """列出所有支持的事件类型"""
        result = self._t.request("GET", "/events/types")
        if isinstance(result, dict):
            return result.get("event_types", [])
        return []

    def subscribe(
        self,
        event_types: Optional[List[str]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """
        SSE 实时事件流订阅。

        返回一个迭代器，每个元素是一个事件字典。
        连接断开时迭代结束。

        Args:
            event_types: 关注的事件类型列表，None 表示全部

        Yields:
            事件字典
        """
        params = {}
        if event_types:
            params["event_types"] = ",".join(event_types)

        for line in self._t.request_stream("GET", "/events/stream", params=params):
            if line.startswith("data: "):
                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                    yield data
                except json.JSONDecodeError:
                    continue
            elif line.startswith("event: "):
                # event type line, next line will be data
                continue
            elif line.startswith(": "):
                # heartbeat comment, skip
                continue


class WebhooksAPI:
    """
    Webhook 管理接口。
    """

    def __init__(self, transport: Transport):
        self._t = transport

    def list(
        self,
        workspace_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """列出当前用户的 Webhooks"""
        params: Dict[str, Any] = {}
        if workspace_id:
            params["workspace_id"] = workspace_id
        result = self._t.request("GET", "/webhooks", params=params)
        return result if isinstance(result, list) else []

    def create(
        self,
        url: str,
        event_types: List[str],
        workspace_id: Optional[int] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        创建 Webhook 订阅。

        Args:
            url: 回调 URL
            event_types: 关注的事件类型列表
            workspace_id: 关联 workspace
            description: 描述

        Returns:
            Webhook 对象（含 secret）
        """
        body: Dict[str, Any] = {"url": url, "event_types": event_types}
        if workspace_id is not None:
            body["workspace_id"] = workspace_id
        if description is not None:
            body["description"] = description
        result = self._t.request("POST", "/webhooks", json=body)
        return result if isinstance(result, dict) else {}

    def get(self, webhook_id: int) -> Dict[str, Any]:
        """获取 Webhook 详情"""
        result = self._t.request("GET", f"/webhooks/{webhook_id}")
        return result if isinstance(result, dict) else {}

    def update(
        self,
        webhook_id: int,
        url: Optional[str] = None,
        event_types: Optional[List[str]] = None,
        active: Optional[bool] = None,
        description: Optional[str] = None,
    ) -> Dict[str, Any]:
        """更新 Webhook"""
        body: Dict[str, Any] = {}
        if url is not None:
            body["url"] = url
        if event_types is not None:
            body["event_types"] = event_types
        if active is not None:
            body["active"] = active
        if description is not None:
            body["description"] = description
        result = self._t.request("PUT", f"/webhooks/{webhook_id}", json=body)
        return result if isinstance(result, dict) else {}

    def delete(self, webhook_id: int) -> bool:
        """删除 Webhook"""
        result = self._t.request("DELETE", f"/webhooks/{webhook_id}")
        if isinstance(result, dict):
            return result.get("success", False)
        return True

    def test(self, webhook_id: int) -> Dict[str, Any]:
        """发送测试事件到 Webhook"""
        result = self._t.request("POST", f"/webhooks/{webhook_id}/test")
        return result if isinstance(result, dict) else {}

    def deliveries(
        self,
        webhook_id: int,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """查询 Webhook 投递记录"""
        result = self._t.request(
            "GET", f"/webhooks/{webhook_id}/deliveries", params={"limit": limit}
        )
        return result if isinstance(result, list) else []
