"""
存储后端一致性评测套件

验证存储后端切换后行为一致：
    - 关系：SQLite ↔ PostgreSQL
    - 向量：ChromaDB ↔ Milvus
    - 缓存：Redis ↔ FakeRedis

方法：
    通过环境变量参数化后端，对同一操作序列运行两遍，
    收集最终状态快照，diff 比对。

一致性判定：
    - 搜索结果集相等（顺序可不同）
    - 向量相似度容差 1e-4
    - LLM 固定为 mock 模式

注：完整对比需 PG/Milvus 环境，CI 中 nightly 运行。
本地降级验证可用 SQLite+Chroma vs SQLite+FakeRedis。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Set

from app.benchmarks.base import BenchmarkResult, BenchmarkSuite, register_suite

logger = logging.getLogger(__name__)

# 固件路径
_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
_OPERATIONS_PATH = os.path.join(_FIXTURES_DIR, "backend_operations.json")

# 目标：一致性通过率 100%
TARGETS = {
    "consistency_pass_rate": 1.0,
}

# 向量相似度容差
_SIMILARITY_TOLERANCE = 1e-4


@register_suite
class BackendConsistencySuite(BenchmarkSuite):
    """L1 存储后端一致性评测套件。

    对同一操作序列，在两个后端配置下分别执行，比对最终状态快照。

    用法：
        # 完整对比（需 PG 环境）
        DATABASE_URL=postgresql://... python -m app.benchmarks.runner run --suite backend_consistency

        # 本地降级验证（SQLite vs SQLite，仅验证缓存层）
        python -m app.benchmarks.runner run --suite backend_consistency
    """

    name = "backend_consistency"
    level = "L1"
    requires_llm = False
    requires_external = True  # 完整对比需 PG/Milvus
    description = "存储后端一致性（SQLite↔PG / ChromaDB↔Milvus / Redis↔FakeRedis）"

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self._run_id = uuid.uuid4().hex[:8]
        self.user_id: int = 9101
        self.operations: List[Dict[str, Any]] = []
        # 后端 A / B 配置（默认 SQLite+Chroma+FakeRedis vs SQLite+Chroma+Redis）
        self.backend_a_config = self.config.get("backend_a", {
            "database_url": os.environ.get("DATABASE_URL", "sqlite:///agent_memory.db"),
            "vector_backend": os.environ.get("VECTOR_BACKEND", "chroma"),
            "cache_backend": "fakeredis",
        })
        self.backend_b_config = self.config.get("backend_b", {
            "database_url": os.environ.get("DATABASE_URL_B", "sqlite:///agent_memory_b.db"),
            "vector_backend": os.environ.get("VECTOR_BACKEND_B", "chroma"),
            "cache_backend": os.environ.get("CACHE_BACKEND_B", "redis"),
        })

    def setup(self) -> None:
        """加载操作序列固件。"""
        if os.path.exists(_OPERATIONS_PATH):
            with open(_OPERATIONS_PATH, "r", encoding="utf-8") as f:
                self.operations = json.load(f)
            logger.info(f"加载 {len(self.operations)} 个后端一致性操作")
        else:
            logger.warning(f"操作固件不存在: {_OPERATIONS_PATH}，使用默认操作")
            self.operations = _default_operations()

    def run(self) -> Dict[str, Any]:
        """在后端 A 和后端 B 上分别执行操作序列，收集状态快照。"""
        logger.info(f"=== 后端 A: {self.backend_a_config} ===")
        snapshot_a = self._run_on_backend(self.backend_a_config)

        logger.info(f"=== 后端 B: {self.backend_b_config} ===")
        snapshot_b = self._run_on_backend(self.backend_b_config)

        return {
            "snapshot_a": snapshot_a,
            "snapshot_b": snapshot_b,
            "backend_a_config": self.backend_a_config,
            "backend_b_config": self.backend_b_config,
        }

    def _run_on_backend(self, backend_config: Dict[str, Any]) -> Dict[str, Any]:
        """在指定后端配置下执行操作序列，返回最终状态快照。

        策略：设置环境变量后重新初始化存储后端，执行操作，收集快照。
        为避免单进程中后端切换的复杂性，采用子进程方式运行。
        """
        import subprocess
        import sys

        # 构造子进程执行的 Python 代码
        script = f"""
import sys, os, json
sys.path.insert(0, {os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))!r})

# 设置后端环境变量
os.environ['DATABASE_URL'] = {backend_config['database_url']!r}
os.environ['VECTOR_BACKEND'] = {backend_config['vector_backend']!r}
if {backend_config['cache_backend']!r} == 'fakeredis':
    os.environ['REDIS_URL'] = 'fakeredis://'
else:
    os.environ.setdefault('REDIS_URL', 'redis://localhost:6379/0')

# 重新加载 settings
from app.core import config as config_module
config_module._settings = None  # 清除单例

# 执行操作序列
operations = json.loads({json.dumps(self.operations)!r})
user_id = {self.user_id!r}
run_id = {self._run_id!r}

from app.core.db_client import get_db_client
db = get_db_client()

# 清理旧数据
db.execute("DELETE FROM memory_fragments WHERE user_id = ?", (user_id,))
db.execute("DELETE FROM memory_variables WHERE user_id = ?", (user_id,))

from app.services.memory_variable_service import set_memory_variable, get_memory_variable, list_variables
from app.services.memory_fragment_service import create_fragment, search_fragments_by_semantic, list_fragments

results = {{}}
for op in operations:
    op_name = op['op']
    args = op.get('args', {{}})
    # 注入 run_id 到字符串值中避免冲突
    if 'key' in args:
        args['key'] = f"{{args['key']}}_{{run_id}}"
    if 'content' in args:
        args['content'] = f"{{args['content']}} [{{run_id}}]"
    if 'query' in args:
        args['query'] = f"{{args['query']}} [{{run_id}}]"

    try:
        if op_name == 'set_variable':
            set_memory_variable(user_id=user_id, key=args['key'], value=args['value'],
                              ttl=None, workspace_id=None)
        elif op_name == 'get_variable':
            val = get_memory_variable(user_id=user_id, key=args['key'], workspace_id=None)
            results[f'get_{{args["key"]}}'] = val
        elif op_name == 'create_fragment':
            create_fragment(user_id=user_id, content=args['content'],
                          fragment_type='info', importance_score=0.5, workspace_id=None)
        elif op_name == 'search_fragments':
            search_result = search_fragments_by_semantic(
                user_id=user_id, query=args['query'], top_k=5, threshold=0.0, workspace_id=None)
            frags = search_result.get('fragments', []) if isinstance(search_result, dict) else []
            results[f'search_{{args["query"][:20]}}'] = {{
                'count': len(frags),
                'contents': [f.get('content', '')[:50] for f in frags],
                'scores': [round(f.get('similarity_score', 0), 4) for f in frags],
            }}
        elif op_name == 'list_variables':
            vars_result = list_variables(user_id=user_id, workspace_id=None)
            vars_list = vars_result if isinstance(vars_result, list) else vars_result.get('variables', [])
            results['list_variables'] = {{
                'count': len(vars_list),
                'keys': sorted([v.get('key', '') for v in vars_list]),
            }}
        elif op_name == 'list_fragments':
            frags_result = list_fragments(user_id=user_id, fragment_type=None, workspace_id=None)
            frags = frags_result.get('fragments', []) if isinstance(frags_result, dict) else frags_result
            results['list_fragments'] = {{
                'count': len(frags),
                'contents': sorted([f.get('content', '')[:50] for f in frags]),
            }}
    except Exception as e:
        results[f'error_{{op_name}}'] = str(e)

# 输出快照 JSON
print(json.dumps(results, ensure_ascii=False, default=str))
"""

        try:
            result = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                logger.error(f"子进程执行失败: {result.stderr}")
                return {"error": result.stderr}
            return json.loads(result.stdout.strip()) if result.stdout.strip() else {}
        except subprocess.TimeoutExpired:
            logger.error("子进程执行超时")
            return {"error": "timeout"}
        except json.JSONDecodeError as e:
            logger.error(f"快照解析失败: {e}")
            return {"error": str(e)}

    def evaluate(self, raw_results: Dict[str, Any]) -> BenchmarkResult:
        """比对两个后端的状态快照。"""
        snapshot_a = raw_results.get("snapshot_a", {})
        snapshot_b = raw_results.get("snapshot_b", {})

        if "error" in snapshot_a or "error" in snapshot_b:
            return BenchmarkResult(
                suite_name=self.name,
                level=self.level,
                metrics={"consistency_pass_rate": 0.0},
                targets=TARGETS,
                passed=False,
                details=[{
                    "snapshot_a_error": snapshot_a.get("error"),
                    "snapshot_b_error": snapshot_b.get("error"),
                }],
                raw=raw_results,
            )

        # 逐项比对
        comparisons = []
        all_keys = set(snapshot_a.keys()) | set(snapshot_b.keys())
        passed_count = 0

        for key in all_keys:
            val_a = snapshot_a.get(key)
            val_b = snapshot_b.get(key)

            equal, diffs = _compare_snapshots(val_a, val_b)
            if equal:
                passed_count += 1
            comparisons.append({
                "key": key,
                "equal": equal,
                "diffs": diffs,
            })

        total = len(all_keys)
        pass_rate = passed_count / total if total > 0 else 0.0

        result = BenchmarkResult(
            suite_name=self.name,
            level=self.level,
            metrics={"consistency_pass_rate": pass_rate},
            targets=TARGETS,
            details=comparisons,
            raw=raw_results,
        )
        result.check_targets()
        return result

    def teardown(self) -> None:
        """清理（子进程已清理，此处仅清理主进程可能残留）。"""
        pass


# ============================================================
# 快照比对工具
# ============================================================

def _compare_snapshots(a: Any, b: Any) -> tuple:
    """比对两个快照值，返回 (is_equal, diffs)。"""
    diffs: List[str] = []

    if type(a) != type(b):
        # 允许 int/float 互比
        if not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
            return False, [f"类型不匹配: {type(a).__name__} vs {type(b).__name__}"]

    if isinstance(a, dict):
        all_keys = set(a.keys()) | set(b.keys())
        for key in all_keys:
            if key not in a:
                diffs.append(f"{key}: 仅 B 有")
            elif key not in b:
                diffs.append(f"{key}: 仅 A 有")
            else:
                sub_equal, sub_diffs = _compare_snapshots(a[key], b[key])
                if not sub_equal:
                    diffs.extend([f"{key}.{d}" for d in sub_diffs])
        return len(diffs) == 0, diffs

    if isinstance(a, list):
        if len(a) != len(b):
            return False, [f"长度不匹配: {len(a)} vs {len(b)}"]
        # 排序后比较（允许顺序差异）
        try:
            a_sorted = sorted(a, key=lambda x: json.dumps(x, sort_keys=True, default=str))
            b_sorted = sorted(b, key=lambda x: json.dumps(x, sort_keys=True, default=str))
        except (TypeError, ValueError):
            a_sorted, b_sorted = a, b
        for i, (av, bv) in enumerate(zip(a_sorted, b_sorted)):
            sub_equal, sub_diffs = _compare_snapshots(av, bv)
            if not sub_equal:
                diffs.extend([f"[{i}].{d}" for d in sub_diffs])
        return len(diffs) == 0, diffs

    if isinstance(a, (int, float)):
        # 向量相似度容差
        if abs(a - b) > _SIMILARITY_TOLERANCE:
            return False, [f"数值不匹配: {a} vs {b} (容差 {_SIMILARITY_TOLERANCE})"]
        return True, []

    if a != b:
        return False, [f"值不匹配: {a!r} vs {b!r}"]
    return True, []


# ============================================================
# 默认操作序列
# ============================================================

def _default_operations() -> List[Dict[str, Any]]:
    """默认后端一致性操作序列。"""
    return [
        {"op": "set_variable", "args": {"key": "consistency_name", "value": "张三"}},
        {"op": "set_variable", "args": {"key": "consistency_age", "value": 28}},
        {"op": "get_variable", "args": {"key": "consistency_name"}},
        {"op": "list_variables", "args": {}},
        {"op": "create_fragment", "args": {"content": "一致性测试片段：用户喜欢极简设计"}},
        {"op": "create_fragment", "args": {"content": "用户在研究记忆系统架构"}},
        {"op": "create_fragment", "args": {"content": "一致性测试：用户使用 Python 开发"}},
        {"op": "search_fragments", "args": {"query": "一致性测试"}},
        {"op": "search_fragments", "args": {"query": "用户偏好"}},
        {"op": "list_fragments", "args": {}},
    ]
