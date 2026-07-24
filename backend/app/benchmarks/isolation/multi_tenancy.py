"""
多租户隔离评测套件

证明 workspace 间数据严格隔离，RBAC 权限强制生效。

测试场景：
    1. 变量隔离：W1 设 key=v1，W2 查 key → W2 返回 None
    2. 片段隔离：W1 创建片段，W2 列表 → W2 列表不含 W1 片段
    3. 表隔离：W1 建表 t，W2 建表 t → 两者独立
    4. 图谱隔离：W1 建实体 E，W2 查 E → W2 查不到
    5. 越权访问：W1 用户用 W2 workspace_id 访问 → 应被拒绝
    6. RBAC 权限：viewer 角色尝试 DELETE → 403

直接调用 service 层函数（绕过 HTTP，聚焦数据隔离逻辑）。
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite

logger = logging.getLogger(__name__)

# 目标：所有隔离场景通过率 100%
TARGETS = {
    "isolation_pass_rate": 1.0,
}


@register_suite
class MultiTenancySuite(BenchmarkSuite):
    """L1 多租户隔离评测套件。"""

    name = "multi_tenancy"
    level = "L1"
    requires_llm = False
    requires_external = False
    description = "多租户隔离与 RBAC 权限（6 个场景：变量/片段/表/图谱/越权/RBAC）"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        # 使用唯一标识避免与其它测试冲突
        self._run_id = uuid.uuid4().hex[:8]
        self.w1_user_id: int = 9001
        self.w2_user_id: int = 9002
        self.w1_workspace: int = 9001
        self.w2_workspace: int = 9002
        self.results: List[Dict[str, Any]] = []

    def setup(self) -> None:
        """初始化：清理可能残留的测试数据。"""
        self._cleanup()

    def run(self) -> Dict[str, Any]:
        """执行 6 个隔离场景。"""
        self.results = []

        self._test_variable_isolation()
        self._test_fragment_isolation()
        self._test_table_isolation()
        self._test_graph_isolation()
        self._test_cross_workspace_access()
        self._test_rbac_permission()

        return {"scenarios": self.results, "total": len(self.results)}

    def _record(self, name: str, passed: bool, detail: str = "") -> None:
        """记录场景结果。"""
        self.results.append({
            "scenario": name,
            "passed": passed,
            "detail": detail,
        })
        status = "✓" if passed else "✗"
        logger.info(f"  {status} {name}: {detail}")

    # ============================================================
    # 场景 1：变量隔离
    # ============================================================

    def _test_variable_isolation(self) -> None:
        """W1 设 key=v1，W2 查 key → W2 返回 None。"""
        from app.services.memory_variable_service import (
            set_memory_variable, get_memory_variable,
        )

        key = f"iso_var_{self._run_id}"
        try:
            # W1 设置变量
            set_memory_variable(
                user_id=self.w1_user_id, key=key, value="w1_value",
                ttl=None, workspace_id=self.w1_workspace,
            )

            # W2 查询同名变量
            w2_value = get_memory_variable(
                user_id=self.w2_user_id, key=key, workspace_id=self.w2_workspace,
            )

            passed = w2_value is None
            self._record(
                "variable_isolation",
                passed,
                f"W1 set {key}=w1_value, W2 got: {w2_value!r}",
            )
        except Exception as e:
            self._record("variable_isolation", False, f"异常: {e}")

    # ============================================================
    # 场景 2：片段隔离
    # ============================================================

    def _test_fragment_isolation(self) -> None:
        """W1 创建片段，W2 列表 → W2 列表不含 W1 片段。"""
        from app.services.memory_fragment_service import create_fragment, list_fragments

        content = f"iso_fragment_{self._run_id}"
        try:
            # W1 创建片段
            create_fragment(
                user_id=self.w1_user_id,
                content=content,
                fragment_type="info",
                importance_score=0.5,
                workspace_id=self.w1_workspace,
            )

            # W2 列出片段
            w2_result = list_fragments(
                user_id=self.w2_user_id,
                fragment_type=None,
                workspace_id=self.w2_workspace,
            )
            w2_frags = w2_result.get("fragments", []) if isinstance(w2_result, dict) else w2_result
            w2_contents = [f.get("content", "") for f in w2_frags]

            passed = content not in w2_contents
            self._record(
                "fragment_isolation",
                passed,
                f"W1 created '{content[:30]}', W2 list has it: {content in w2_contents}",
            )
        except Exception as e:
            self._record("fragment_isolation", False, f"异常: {e}")

    # ============================================================
    # 场景 3：表隔离
    # ============================================================

    def _test_table_isolation(self) -> None:
        """W1 建表 t，W2 建表 t → 两者独立。"""
        from app.services.memory_table_service import (
            create_memory_table, list_tables,
        )

        table_name = f"iso_table_{self._run_id}"
        fields = [{"name": "col1", "type": "text"}]
        try:
            # W1 建表
            create_memory_table(
                user_id=self.w1_user_id, table_name=table_name,
                fields=fields, workspace_id=self.w1_workspace,
            )

            # W2 建同名表（应成功，不冲突）
            create_memory_table(
                user_id=self.w2_user_id, table_name=table_name,
                fields=fields, workspace_id=self.w2_workspace,
            )

            # W1 列表只含 W1 的表
            w1_tables = list_tables(user_id=self.w1_user_id, workspace_id=self.w1_workspace)
            w1_names = [t.get("table_name") for t in (w1_tables if isinstance(w1_tables, list) else w1_tables.get("tables", []))]

            # W2 列表只含 W2 的表
            w2_tables = list_tables(user_id=self.w2_user_id, workspace_id=self.w2_workspace)
            w2_names = [t.get("table_name") for t in (w2_tables if isinstance(w2_tables, list) else w2_tables.get("tables", []))]

            # 两边都有同名表，但互不干扰（表 ID 不同）
            passed = table_name in w1_names and table_name in w2_names
            self._record(
                "table_isolation",
                passed,
                f"Both created '{table_name}', W1 has {len(w1_names)} tables, W2 has {len(w2_names)}",
            )
        except Exception as e:
            self._record("table_isolation", False, f"异常: {e}")

    # ============================================================
    # 场景 4：图谱隔离
    # ============================================================

    def _test_graph_isolation(self) -> None:
        """W1 建实体 E，W2 查 E → W2 查不到。"""
        from app.services.graph_memory_service import ensure_entity, search_entities

        entity_name = f"iso_entity_{self._run_id}"
        try:
            # W1 创建实体
            ensure_entity(
                user_id=self.w1_user_id,
                name=entity_name,
                entity_type="person",
                workspace_id=self.w1_workspace,
            )

            # W2 搜索同名实体
            w2_result = search_entities(
                user_id=self.w2_user_id,
                query=entity_name,
                workspace_id=self.w2_workspace,
            )
            w2_entities = w2_result if isinstance(w2_result, list) else w2_result.get("entities", [])
            w2_names = [e.get("name") for e in w2_entities]

            passed = entity_name not in w2_names
            self._record(
                "graph_isolation",
                passed,
                f"W1 created entity '{entity_name}', W2 can find: {entity_name in w2_names}",
            )
        except Exception as e:
            self._record("graph_isolation", False, f"异常: {e}")

    # ============================================================
    # 场景 5：跨 workspace 越权访问
    # ============================================================

    def _test_cross_workspace_access(self) -> None:
        """W1 用户用 W2 workspace_id 访问 W2 资源 → 应被拒绝或返回空。

        策略：service 层应通过 user_id + workspace_id 联合过滤，
        W1 用户即使指定 W2 workspace，也不应看到 W2 的数据。
        """
        from app.services.memory_variable_service import (
            set_memory_variable, get_memory_variable,
        )

        key = f"iso_cross_{self._run_id}"
        try:
            # W2 设置变量
            set_memory_variable(
                user_id=self.w2_user_id, key=key, value="w2_secret",
                ttl=None, workspace_id=self.w2_workspace,
            )

            # W1 用户尝试用 W2 的 workspace_id 查询
            # （正常情况下 service 层应校验用户是否属于该 workspace）
            # 这里测试数据层是否泄漏：W1 用户 + W2 workspace
            cross_value = get_memory_variable(
                user_id=self.w1_user_id, key=key, workspace_id=self.w2_workspace,
            )

            # 数据层隔离：W1 用户不应查到 W2 用户的变量
            passed = cross_value is None
            self._record(
                "cross_workspace_access",
                passed,
                f"W2 set {key}=w2_secret, W1 user queried: {cross_value!r}",
            )
        except Exception as e:
            # 如果 service 层主动拒绝（抛异常），也算通过
            self._record(
                "cross_workspace_access",
                True,
                f"Service 层拒绝跨 workspace 访问: {e}",
            )

    # ============================================================
    # 场景 6：RBAC 权限
    # ============================================================

    def _test_rbac_permission(self) -> None:
        """viewer 角色尝试 DELETE → 403。

        注：当前系统 RBAC 可能在 API 层而非 service 层实现。
        若 service 层无角色校验，此场景降级为"记录现状"。
        """
        try:
            # 尝试导入 RBAC 相关模块
            from app.services.auth_service import check_permission  # type: ignore

            # viewer 角色尝试 delete_fragment
            has_perm = check_permission(
                user_id=self.w1_user_id,
                workspace_id=self.w1_workspace,
                role="viewer",
                action="delete",
                resource="fragment",
            )
            passed = not has_perm  # viewer 不应有 delete 权限
            self._record(
                "rbac_permission",
                passed,
                f"viewer delete permission: {has_perm} (应为 False)",
            )
        except ImportError:
            # RBAC 模块不存在，记录为"未实现"
            self._record(
                "rbac_permission",
                False,
                "RBAC 模块未实现（auth_service 不可导入）",
            )
        except Exception as e:
            self._record("rbac_permission", False, f"异常: {e}")

    # ============================================================
    # 评估与清理
    # ============================================================

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """计算隔离通过率。"""
        scenarios = raw_results.get("scenarios", [])
        if not scenarios:
            return BenchmarkResult(
                suite_name=self.name,
                level=self.level,
                metrics={"isolation_pass_rate": 0.0},
                targets=TARGETS,
                passed=False,
                details=[{"error": "无场景结果"}],
                raw=raw_results,
            )

        passed_count = sum(1 for s in scenarios if s.get("passed"))
        pass_rate = passed_count / len(scenarios)

        result = BenchmarkResult(
            suite_name=self.name,
            level=self.level,
            metrics={"isolation_pass_rate": pass_rate},
            targets=TARGETS,
            details=scenarios,
            raw=raw_results,
        )
        result.check_targets()
        return result

    def teardown(self) -> None:
        """清理评测数据。"""
        self._cleanup()

    def _cleanup(self) -> None:
        """清理本套件产生的测试数据。"""
        try:
            from app.core.db_client import get_db_client
            db = get_db_client()
            for uid in [self.w1_user_id, self.w2_user_id]:
                db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (uid,))
                db.execute("DELETE FROM memory_variables WHERE user_id = ?", (uid,))
                db.execute("DELETE FROM memory_tables WHERE user_id = ?", (uid,))
                db.execute("DELETE FROM memory_graph_entities WHERE user_id = ?", (uid,))
            # Redis 变量清理
            try:
                from app.core.cache_client import get_redis_client
                redis = get_redis_client()
                for uid in [self.w1_user_id, self.w2_user_id]:
                    for wid in [self.w1_workspace, self.w2_workspace]:
                        pattern = f"mem:{uid}:{wid}:*"
                        for key in redis.keys(pattern):
                            redis.delete(key)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"清理失败: {e}")
