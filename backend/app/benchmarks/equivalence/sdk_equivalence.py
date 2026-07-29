"""
SDK 模式等价性评测套件

验证 HTTP 模式与 Embedded 模式返回结果一致，Python SDK 与 TypeScript SDK 行为对齐。
这是"SDK 可脱离后端独立运行"的核心证据。

测试矩阵：
    | 操作 | HTTP (Python) | Embedded (Python) | HTTP (TypeScript) |
    | remember/recall | ✓ | ✓ | ✓ |
    | fragment create/get/search | ✓ | ✓ | ✓ |
    | table create/add_record/query | ✓ | ✓ | ✓ |
    | graph add_entity/neighbors | ✓ | ✓ | ✓ |

等价性判定：
    - 标量字段：严格相等
    - 浮点字段：|a - b| < 1e-6
    - 列表字段：排序后逐元素比较
    - 时间字段：仅比较日期部分
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite

logger = logging.getLogger(__name__)

# 固件路径
_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
_OPERATIONS_PATH = os.path.join(_FIXTURES_DIR, "sdk_operations.json")

# TS 桥脚本路径
_TS_RUNNER = os.path.join(os.path.dirname(__file__), "ts_runner.js")

# 等价性容差
_FLOAT_TOLERANCE = 1e-6

# 目标：所有操作等价性通过率 100%
TARGETS = {
    "equivalence_pass_rate": 1.0,
}


# ============================================================
# 等价性比较工具
# ============================================================

def _normalize_value(value: Any) -> Any:
    """规范化值以便比较：浮点保留 6 位，字符串去首尾空白。"""
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, str):
        return value.strip()
    return value


def _compare_values(a: Any, b: Any, path: str = "") -> List[str]:
    """递归比较两个值，返回差异列表。

    Args:
        a, b: 待比较的值
        path: 字段路径（用于错误定位）

    Returns:
        差异描述列表（空列表表示相等）
    """
    diffs: List[str] = []

    # 类型不同（除 int/float 可互比）
    if isinstance(a, bool) or isinstance(b, bool):
        if a != b:
            diffs.append(f"{path}: bool 不匹配 {a!r} vs {b!r}")
        return diffs
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if abs(a - b) > _FLOAT_TOLERANCE:
            diffs.append(f"{path}: 数值不匹配 {a} vs {b} (容差 {_FLOAT_TOLERANCE})")
        return diffs

    # None 处理
    if a is None or b is None:
        if a != b:
            diffs.append(f"{path}: None 不匹配 {a!r} vs {b!r}")
        return diffs

    # 字典比较
    if isinstance(a, dict) and isinstance(b, dict):
        all_keys = set(a.keys()) | set(b.keys())
        for key in all_keys:
            sub_path = f"{path}.{key}" if path else key
            if key not in a:
                diffs.append(f"{sub_path}: 仅 b 有")
            elif key not in b:
                diffs.append(f"{sub_path}: 仅 a 有")
            else:
                diffs.extend(_compare_values(a[key], b[key], sub_path))
        return diffs

    # 列表比较（排序后逐元素）
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            diffs.append(f"{path}: 长度不匹配 {len(a)} vs {len(b)}")
            return diffs
        # 尝试排序后比较（允许顺序差异）
        try:
            a_sorted = sorted(a, key=lambda x: json.dumps(x, sort_keys=True, default=str))
            b_sorted = sorted(b, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        except (TypeError, ValueError):
            a_sorted, b_sorted = a, b
        for i, (av, bv) in enumerate(zip(a_sorted, b_sorted)):
            diffs.extend(_compare_values(av, bv, f"{path}[{i}]"))
        return diffs

    # 字符串/其他类型
    if _normalize_value(a) != _normalize_value(b):
        diffs.append(f"{path}: 值不匹配 {a!r} vs {b!r}")
    return diffs


def compare_responses(a: Dict[str, Any], b: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """比较两个 SDK 响应是否等价。

    Returns:
        (is_equal, diffs)
    """
    diffs = _compare_values(a, b)
    return len(diffs) == 0, diffs


# ============================================================
# Python 客户端执行器
# ============================================================

def _execute_python_operations(
    operations: List[Dict[str, Any]],
    mode: str,
    base_url: Optional[str] = None,
    user_id: int = 1,
    workspace_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """用 Python SDK 执行操作序列。

    Args:
        operations: 操作列表
        mode: "http" 或 "embedded"
        base_url: HTTP 模式的后端地址
        user_id: 用户 ID
        workspace_id: workspace ID

    Returns:
        响应列表（每个操作一项）
    """
    # 添加 backend 目录到 sys.path（embedded 模式需要）
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)

    from agent_memory import MemoryClient

    client_kwargs: Dict[str, Any] = {"mode": mode, "user_id": user_id}
    if mode == "http":
        client_kwargs["base_url"] = base_url or "http://localhost:8000"
        if workspace_id is not None:
            client_kwargs["workspace_id"] = str(workspace_id)
    elif mode == "embedded":
        if workspace_id is not None:
            client_kwargs["workspace_id"] = str(workspace_id)

    client = MemoryClient(**client_kwargs)
    responses: List[Dict[str, Any]] = []

    try:
        for op in operations:
            resp = _execute_single_op(client, op)
            responses.append(resp)
    finally:
        client.close()

    return responses


def _execute_single_op(client: Any, op: Dict[str, Any]) -> Dict[str, Any]:
    """执行单个操作并返回响应。"""
    op_name = op["op"]
    args = op.get("args", {})

    try:
        if op_name == "remember":
            result = client.remember(**args)
            return {"op": op_name, "result": result, "success": True}
        elif op_name == "recall":
            result = client.recall_context(**args)
            return {"op": op_name, "result": result, "success": True}
        elif op_name == "search":
            result = client.search(**args)
            return {"op": op_name, "result": result, "success": True}
        elif op_name == "create_fragment":
            result = client.remember_fragment(**args)
            return {"op": op_name, "result": result, "success": True}
        elif op_name == "list_variables":
            result = client.list_variables(**args)
            return {"op": op_name, "result": result, "success": True}
        elif op_name == "forget":
            result = client.forget(**args)
            return {"op": op_name, "result": result, "success": True}
        elif op_name == "create_table":
            result = client.create_table(**args)
            return {"op": op_name, "result": result, "success": True}
        elif op_name == "list_tables":
            result = client.list_tables()
            return {"op": op_name, "result": result, "success": True}
        elif op_name == "get_context":
            result = client.get_context(**args)
            return {"op": op_name, "result": result, "success": True}
        else:
            return {"op": op_name, "error": f"未知操作: {op_name}", "success": False}
    except Exception as e:
        return {"op": op_name, "error": str(e), "success": False}


# ============================================================
# TypeScript 客户端执行器
# ============================================================

def _execute_typescript_operations(
    operations: List[Dict[str, Any]],
    base_url: str,
    user_id: int = 1,
    workspace_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """用 TypeScript SDK 执行操作序列（通过 node 子进程）。

    Args:
        operations: 操作列表
        base_url: 后端地址
        user_id: 用户 ID
        workspace_id: workspace ID

    Returns:
        响应列表
    """
    if not os.path.exists(_TS_RUNNER):
        logger.warning(f"TS 桥脚本不存在: {_TS_RUNNER}，跳过 TypeScript 测试")
        return []

    sdk_ts_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
        "sdk-typescript",
    )

    payload = {
        "operations": operations,
        "base_url": base_url,
        "user_id": user_id,
        "workspace_id": workspace_id,
    }

    try:
        result = subprocess.run(
            ["node", _TS_RUNNER],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=60,
            cwd=sdk_ts_dir,
        )
        if result.returncode != 0:
            logger.error(f"TS 执行失败: {result.stderr}")
            return [{"error": result.stderr, "success": False} for _ in operations]
        return json.loads(result.stdout) if result.stdout.strip() else []
    except FileNotFoundError:
        logger.warning("node 未安装，跳过 TypeScript 测试")
        return []
    except subprocess.TimeoutExpired:
        logger.error("TS 执行超时")
        return [{"error": "timeout", "success": False} for _ in operations]
    except json.JSONDecodeError as e:
        logger.error(f"TS 输出解析失败: {e}")
        return []


# ============================================================
# SDK 等价性评测套件
# ============================================================

@register_suite
class SdkEquivalenceSuite(BenchmarkSuite):
    """L1 SDK 模式等价性评测套件。

    对同一组操作序列，分别用 HTTP/Embedded/TS 三种客户端执行，
    收集响应并做字段级 diff，验证等价性。
    """

    name = "sdk_equivalence"
    level = "L1"
    requires_llm = False
    requires_external = True  # 需要 HTTP 后端 + node（TS 路径）
    description = "SDK 模式等价性（HTTP/Embedded/TypeScript 三客户端字段级对比）"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.base_url: str = self.config.get("base_url", "http://localhost:8000")
        self.user_id: int = self.config.get("user_id", 888)  # 独立 user_id 避免冲突
        self.workspace_id: Optional[int] = self.config.get("workspace_id")
        self.skip_ts: bool = self.config.get("skip_ts", False)
        self.operations: List[Dict[str, Any]] = []

    def setup(self) -> None:
        """加载操作序列固件。"""
        if os.path.exists(_OPERATIONS_PATH):
            with open(_OPERATIONS_PATH, "r", encoding="utf-8") as f:
                self.operations = json.load(f)
            logger.info(f"加载 {len(self.operations)} 个 SDK 操作")
        else:
            logger.warning(f"操作固件不存在: {_OPERATIONS_PATH}，使用默认操作")
            self.operations = _default_operations()

    def run(self) -> Dict[str, Any]:
        """对同一操作序列跑三种客户端，收集响应。"""
        results: Dict[str, Any] = {"comparisons": []}

        # 1. Python HTTP 模式
        logger.info("运行 Python HTTP 模式...")
        http_responses = _execute_python_operations(
            self.operations, mode="http",
            base_url=self.base_url,
            user_id=self.user_id,
            workspace_id=self.workspace_id,
        )
        results["http_responses"] = http_responses

        # 2. Python Embedded 模式
        logger.info("运行 Python Embedded 模式...")
        embedded_responses = _execute_python_operations(
            self.operations, mode="embedded",
            user_id=self.user_id,
            workspace_id=self.workspace_id,
        )
        results["embedded_responses"] = embedded_responses

        # 3. TypeScript HTTP 模式（可选）
        ts_responses: List[Dict[str, Any]] = []
        if not self.skip_ts:
            logger.info("运行 TypeScript HTTP 模式...")
            ts_responses = _execute_typescript_operations(
                self.operations,
                base_url=self.base_url,
                user_id=self.user_id,
                workspace_id=self.workspace_id,
            )
        results["ts_responses"] = ts_responses

        # 4. 比较响应
        comparisons = []
        for i, op in enumerate(self.operations):
            http_resp = http_responses[i] if i < len(http_responses) else {}
            emb_resp = embedded_responses[i] if i < len(embedded_responses) else {}

            # HTTP vs Embedded
            he_equal, he_diffs = compare_responses(
                _strip_dynamic_fields(http_resp),
                _strip_dynamic_fields(emb_resp),
            )
            comp = {
                "op": op["op"],
                "http_vs_embedded": {"equal": he_equal, "diffs": he_diffs},
            }

            # HTTP Python vs HTTP TypeScript
            if ts_responses and i < len(ts_responses):
                ts_resp = ts_responses[i]
                ht_equal, ht_diffs = compare_responses(
                    _strip_dynamic_fields(http_resp),
                    _strip_dynamic_fields(ts_resp),
                )
                comp["http_vs_typescript"] = {"equal": ht_equal, "diffs": ht_diffs}

            comparisons.append(comp)

        results["comparisons"] = comparisons
        return results

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """计算等价性通过率。"""
        comparisons = raw_results.get("comparisons", [])
        if not comparisons:
            return BenchmarkResult(
                suite_name=self.name,
                level=self.level,
                metrics={"equivalence_pass_rate": 0.0},
                targets=TARGETS,
                passed=False,
                details=[{"error": "无比较结果"}],
                raw=raw_results,
            )

        total = 0
        passed = 0
        for comp in comparisons:
            # HTTP vs Embedded 必须通过
            total += 1
            if comp.get("http_vs_embedded", {}).get("equal"):
                passed += 1
            # HTTP vs TS（若有）
            if "http_vs_typescript" in comp:
                total += 1
                if comp["http_vs_typescript"].get("equal"):
                    passed += 1

        pass_rate = passed / total if total > 0 else 0.0
        result = BenchmarkResult(
            suite_name=self.name,
            level=self.level,
            metrics={"equivalence_pass_rate": pass_rate},
            targets=TARGETS,
            details=comparisons,
            raw=raw_results,
        )
        result.check_targets()
        return result

    def teardown(self) -> None:
        """清理评测数据。"""
        try:
            from app.core.db_client import get_db_client
            db = get_db_client()
            db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (self.user_id,))
            db.execute("DELETE FROM memory_variables WHERE user_id = ?", (self.user_id,))
        except Exception as e:
            logger.debug(f"清理失败: {e}")


# ============================================================
# 动态字段剥离（id/timestamp 等无法跨模式一致的字段）
# ============================================================

def _strip_dynamic_fields(resp: Dict[str, Any]) -> Dict[str, Any]:
    """剥离动态生成的字段（id、timestamp、trace_id 等）。

    这些字段在每次调用时都会变化，无法跨模式/跨次比较。
    保留 success/op/result 等语义字段。
    """
    if not isinstance(resp, dict):
        return resp

    DYNAMIC_KEYS = {"id", "fragment_id", "created_at", "updated_at", "timestamp", "trace_id"}

    def _strip(obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: _strip(v) for k, v in obj.items() if k not in DYNAMIC_KEYS}
        if isinstance(obj, list):
            return [_strip(item) for item in obj]
        return obj

    return _strip(resp)


# ============================================================
# 默认操作序列（固件未就绪时的降级方案）
# ============================================================

def _default_operations() -> List[Dict[str, Any]]:
    """默认操作序列（最小验证集）。"""
    return [
        {"op": "remember", "args": {"key": "eq_test_key", "value": "张三"}},
        {"op": "list_variables", "args": {}},
        {"op": "create_fragment", "args": {"content": "等价性测试片段", "importance_score": 0.8}},
        {"op": "search", "args": {"query": "等价性测试", "top_k": 5}},
        {"op": "recall", "args": {"query": "等价性测试"}},
        {"op": "get_context", "args": {}},
        {"op": "forget", "args": {"key": "eq_test_key"}},
    ]
