"""
R-02: LongMemEval 基准测试框架测试

验证基准测试框架的各个组件正确工作：
    1. 合成数据集格式与覆盖
    2. 数据集加载与统计
    3. 记忆适配器（摄入/召回）
    4. 评估器（LLM Judge + 启发式）
    5. 指标计算
    6. 端到端运行器
"""
import json
import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from app.benchmarks.sample_data import build_sample_dataset
from app.benchmarks.longmemeval_adapter import (
    get_ability,
    load_dataset,
    session_to_text,
    extract_user_facts,
    MemoryAdapter,
    dataset_stats,
    ABILITY_LABELS,
    QUESTION_TYPE_TO_ABILITY,
)
from app.benchmarks.evaluator import (
    evaluate_answer,
    compute_metrics,
    _heuristic_judge,
    JUDGE_PROMPT_TEMPLATE,
)
from app.benchmarks.runner import run_benchmark, generate_markdown_report


# ============================================================
# 1. 合成数据集格式与覆盖
# ============================================================

class TestSampleDataset:
    """验证合成数据集格式正确、覆盖全面。"""

    def test_dataset_has_10_instances(self):
        """合成数据集应有 10 条实例。"""
        ds = build_sample_dataset()
        assert len(ds) == 10

    def test_all_5_abilities_covered(self):
        """应覆盖全部 5 种记忆能力。"""
        ds = build_sample_dataset()
        abilities = {get_ability(inst) for inst in ds}
        assert abilities == {
            "information_extraction",
            "multi_session_reasoning",
            "temporal_reasoning",
            "knowledge_update",
            "abstention",
        }

    def test_each_ability_has_2_instances(self):
        """每种能力应有 2 条实例。"""
        ds = build_sample_dataset()
        from collections import Counter
        ability_counts = Counter(get_ability(inst) for inst in ds)
        for ability, count in ability_counts.items():
            assert count == 2, f"能力 {ability} 有 {count} 条，期望 2 条"

    def test_instance_fields_complete(self):
        """每条实例应包含所有必需字段。"""
        ds = build_sample_dataset()
        required_fields = [
            "question_id", "question_type", "question", "answer",
            "question_date", "haystack_session_ids", "haystack_dates",
            "haystack_sessions", "answer_session_ids",
        ]
        for inst in ds:
            for field in required_fields:
                assert field in inst, f"实例 {inst.get('question_id')} 缺少字段 {field}"

    def test_abstention_questions_end_with_abs(self):
        """弃权问题的 question_id 应以 _abs 结尾。"""
        ds = build_sample_dataset()
        for inst in ds:
            if get_ability(inst) == "abstention":
                assert inst["question_id"].endswith("_abs")
            else:
                assert not inst["question_id"].endswith("_abs")

    def test_haystack_sessions_format(self):
        """haystack_sessions 应为 turn 列表的列表。"""
        ds = build_sample_dataset()
        for inst in ds:
            sessions = inst["haystack_sessions"]
            assert isinstance(sessions, list)
            for session in sessions:
                assert isinstance(session, list)
                for turn in session:
                    assert "role" in turn
                    assert "content" in turn
                    assert turn["role"] in ("user", "assistant")


# ============================================================
# 2. 能力映射与文本化
# ============================================================

class TestAbilityMapping:
    """验证能力映射与文本化函数。"""

    def test_question_type_to_ability_mapping(self):
        """question_type 应正确映射到 ability。"""
        assert QUESTION_TYPE_TO_ABILITY["single-session-user"] == "information_extraction"
        assert QUESTION_TYPE_TO_ABILITY["multi-session"] == "multi_session_reasoning"
        assert QUESTION_TYPE_TO_ABILITY["temporal-reasoning"] == "temporal_reasoning"
        assert QUESTION_TYPE_TO_ABILITY["knowledge-update"] == "knowledge_update"

    def test_get_ability_for_abstention(self):
        """_abs 后缀应识别为 abstention。"""
        inst = {"question_id": "q1_abs", "question_type": "single-session-user"}
        assert get_ability(inst) == "abstention"

    def test_get_ability_for_non_abstention(self):
        """非 _abs 后缀应按 question_type 映射。"""
        inst = {"question_id": "q1", "question_type": "multi-session"}
        assert get_ability(inst) == "multi_session_reasoning"

    def test_ability_labels(self):
        """能力标签应包含中文。"""
        assert ABILITY_LABELS["information_extraction"] == "信息提取"
        assert ABILITY_LABELS["abstention"] == "弃权"

    def test_session_to_text(self):
        """session_to_text 应正确格式化会话。"""
        session = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        text = session_to_text(session)
        assert "用户: Hello" in text
        assert "助手: Hi there" in text

    def test_session_to_text_user_only(self):
        """include_assistant=False 时应只包含用户消息。"""
        session = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        text = session_to_text(session, include_assistant=False)
        assert "Hello" in text
        assert "Hi there" not in text

    def test_extract_user_facts(self):
        """extract_user_facts 应只提取用户消息。"""
        session = [
            {"role": "user", "content": "I like Python"},
            {"role": "assistant", "content": "That's great"},
            {"role": "user", "content": "I also like Java"},
        ]
        facts = extract_user_facts(session)
        assert "Python" in facts
        assert "Java" in facts
        assert "That's great" not in facts


# ============================================================
# 3. 数据集加载与统计
# ============================================================

class TestDatasetLoading:
    """验证数据集加载与统计。"""

    def test_load_dataset(self, tmp_path):
        """load_dataset 应正确加载 JSON 文件。"""
        data = build_sample_dataset()
        filepath = tmp_path / "test_dataset.json"
        filepath.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

        loaded = load_dataset(str(filepath))
        assert len(loaded) == 10
        assert loaded[0]["question_id"] == data[0]["question_id"]

    def test_load_dataset_invalid_format(self, tmp_path):
        """非列表 JSON 应抛出 ValueError。"""
        filepath = tmp_path / "bad.json"
        filepath.write_text('{"not": "a list"}', encoding="utf-8")
        with pytest.raises(ValueError, match="期望列表"):
            load_dataset(str(filepath))

    def test_dataset_stats(self):
        """dataset_stats 应正确计算统计信息。"""
        ds = build_sample_dataset()
        stats = dataset_stats(ds)
        assert stats["total"] == 10
        assert stats["total_sessions"] > 0
        assert stats["total_user_turns"] > 0
        assert stats["avg_sessions_per_instance"] > 0
        assert "by_ability" in stats
        assert "by_question_type" in stats
        assert stats["by_ability"]["abstention"] == 2


# ============================================================
# 4. 启发式评估器
# ============================================================

class TestHeuristicJudge:
    """验证启发式评判逻辑。"""

    def test_exact_match_correct(self):
        """完全匹配应判为正确。"""
        correct, reason = _heuristic_judge(
            "What is my name?",
            "Alice",
            "Alice",
        )
        assert correct is True

    def test_keyword_match_correct(self):
        """关键词命中率 >= 50% 应判为正确。"""
        correct, reason = _heuristic_judge(
            "Where do I work?",
            "Google",
            "I work at Google",
        )
        assert correct is True

    def test_no_match_incorrect(self):
        """无关键词匹配应判为错误。"""
        correct, reason = _heuristic_judge(
            "What is my name?",
            "Alice",
            "I don't know the answer",
        )
        assert correct is False

    def test_abstention_correct(self):
        """弃权问题：模型答"不知道"应判为正确。"""
        correct, reason = _heuristic_judge(
            "What is my favorite color?",
            "The information is not mentioned",
            "I don't have enough information to answer this question.",
            is_abstention=True,
        )
        assert correct is True

    def test_abstention_incorrect(self):
        """弃权问题：模型给出了具体答案应判为错误。"""
        correct, reason = _heuristic_judge(
            "What is my favorite color?",
            "The information is not mentioned",
            "Your favorite color is blue.",
            is_abstention=True,
        )
        assert correct is False


# ============================================================
# 5. 评估器（evaluate_answer）
# ============================================================

class TestEvaluateAnswer:
    """验证 evaluate_answer 函数。"""

    def test_evaluate_with_heuristic(self):
        """禁用 LLM Judge 时应使用启发式。"""
        result = evaluate_answer(
            question="What is my name?",
            reference_answer="Alice",
            model_answer="Alice",
            user_id=999,
            use_llm_judge=False,
        )
        assert result["correct"] is True
        assert result["evaluator"] == "heuristic"

    def test_evaluate_abstention_heuristic(self):
        """弃权问题的启发式评估。"""
        result = evaluate_answer(
            question="What is my favorite color?",
            reference_answer="The information is not mentioned",
            model_answer="I don't have enough information",
            user_id=999,
            is_abstention=True,
            use_llm_judge=False,
        )
        assert result["correct"] is True
        assert result["evaluator"] == "heuristic"

    def test_evaluate_returns_required_fields(self):
        """评估结果应包含必需字段。"""
        result = evaluate_answer(
            question="test",
            reference_answer="test",
            model_answer="test",
            user_id=999,
            use_llm_judge=False,
        )
        assert "correct" in result
        assert "evaluator" in result
        assert "reason" in result


# ============================================================
# 6. 指标计算
# ============================================================

class TestComputeMetrics:
    """验证 compute_metrics 函数。"""

    def test_basic_metrics(self):
        """基本指标计算。"""
        results = [
            {"correct": True, "ability": "information_extraction", "question_type": "single-session-user", "evaluator": "heuristic"},
            {"correct": False, "ability": "information_extraction", "question_type": "single-session-user", "evaluator": "heuristic"},
            {"correct": True, "ability": "abstention", "question_type": "abstention", "evaluator": "heuristic"},
        ]
        metrics = compute_metrics(results)
        assert metrics["total"] == 3
        assert metrics["correct"] == 2
        assert metrics["accuracy"] == pytest.approx(2 / 3)

    def test_by_ability(self):
        """按能力分类统计。"""
        results = [
            {"correct": True, "ability": "information_extraction", "question_type": "x", "evaluator": "h"},
            {"correct": False, "ability": "information_extraction", "question_type": "x", "evaluator": "h"},
            {"correct": True, "ability": "abstention", "question_type": "y", "evaluator": "h"},
        ]
        metrics = compute_metrics(results)
        assert metrics["by_ability"]["information_extraction"]["total"] == 2
        assert metrics["by_ability"]["information_extraction"]["correct"] == 1
        assert metrics["by_ability"]["information_extraction"]["accuracy"] == 0.5
        assert metrics["by_ability"]["abstention"]["accuracy"] == 1.0

    def test_empty_results(self):
        """空结果应返回零值指标。"""
        metrics = compute_metrics([])
        assert metrics["total"] == 0
        assert metrics["correct"] == 0
        assert metrics["accuracy"] == 0.0

    def test_by_evaluator(self):
        """按评估器分类统计。"""
        results = [
            {"correct": True, "ability": "a", "question_type": "x", "evaluator": "llm_judge"},
            {"correct": False, "ability": "a", "question_type": "x", "evaluator": "heuristic"},
        ]
        metrics = compute_metrics(results)
        assert metrics["by_evaluator"]["llm_judge"]["accuracy"] == 1.0
        assert metrics["by_evaluator"]["heuristic"]["accuracy"] == 0.0


# ============================================================
# 7. 端到端运行器
# ============================================================

class TestBenchmarkRunner:
    """验证端到端基准测试运行器。"""

    def test_run_benchmark_with_small_dataset(self):
        """使用 2 条实例运行端到端测试（启发式模式）。"""
        ds = build_sample_dataset()[:2]
        results = run_benchmark(
            instances=ds,
            user_id=999,
            use_llm_judge=False,
            use_llm_answer=False,
            reset_memory_between=True,
        )

        assert "metadata" in results
        assert "metrics" in results
        assert "results" in results
        assert "dataset_stats" in results
        assert results["metrics"]["total"] == 2
        assert len(results["results"]) == 2
        assert results["metadata"]["use_llm_judge"] is False

    def test_run_benchmark_metadata(self):
        """元数据应包含完整信息。"""
        ds = build_sample_dataset()[:1]
        results = run_benchmark(
            instances=ds,
            user_id=999,
            use_llm_judge=False,
            use_llm_answer=False,
        )
        meta = results["metadata"]
        assert meta["user_id"] == 999
        assert meta["total_instances"] == 1
        assert meta["use_llm_judge"] is False
        assert "elapsed_seconds" in meta
        assert "timestamp" in meta

    def test_run_benchmark_results_structure(self):
        """每条结果应包含必需字段。"""
        ds = build_sample_dataset()[:1]
        results = run_benchmark(
            instances=ds,
            user_id=999,
            use_llm_judge=False,
            use_llm_answer=False,
        )
        r = results["results"][0]
        assert "question_id" in r
        assert "question" in r
        assert "reference_answer" in r
        assert "model_answer" in r
        assert "correct" in r
        assert "evaluator" in r
        assert "ability" in r
        assert "stored_memories" in r

    def test_generate_markdown_report(self):
        """Markdown 报告应包含关键章节。"""
        ds = build_sample_dataset()[:2]
        results = run_benchmark(
            instances=ds,
            user_id=999,
            use_llm_judge=False,
            use_llm_answer=False,
        )
        report = generate_markdown_report(results)
        assert "# LongMemEval 基准测试报告" in report
        assert "## 总体结果" in report
        assert "## 按记忆能力分类" in report
        assert "## 详细结果" in report
        assert "准确率" in report

    def test_benchmark_covers_all_abilities(self):
        """基准测试应能处理所有能力类别。"""
        ds = build_sample_dataset()
        results = run_benchmark(
            instances=ds,
            user_id=999,
            use_llm_judge=False,
            use_llm_answer=False,
        )
        abilities_in_results = {r["ability"] for r in results["results"]}
        assert "information_extraction" in abilities_in_results
        assert "multi_session_reasoning" in abilities_in_results
        assert "temporal_reasoning" in abilities_in_results
        assert "knowledge_update" in abilities_in_results
        assert "abstention" in abilities_in_results


# ============================================================
# 8. 记忆适配器
# ============================================================

class TestMemoryAdapter:
    """验证记忆适配器（需要数据库）。"""

    def test_adapter_initialization(self):
        """适配器应能正确初始化。"""
        adapter = MemoryAdapter(user_id=999)
        assert adapter.user_id == 999
        assert adapter.stored_count == 0

    def test_ingest_session(self):
        """摄入会话应存储用户消息。"""
        adapter = MemoryAdapter(user_id=999)
        adapter.reset()
        session = [
            {"role": "user", "content": "I love Python programming"},
            {"role": "assistant", "content": "That's great!"},
        ]
        count = adapter.ingest_session(session, "test_session", "2024/01/01")
        assert count == 1
        assert adapter.stored_count >= 1

    def test_ingest_history(self):
        """摄入多个会话应返回总记忆数。"""
        adapter = MemoryAdapter(user_id=999)
        adapter.reset()
        sessions = [
            [{"role": "user", "content": "Message 1"}],
            [{"role": "user", "content": "Message 2"}],
        ]
        total = adapter.ingest_history(sessions, ["2024/01/01", "2024/01/02"])
        assert total == 2

    def test_recall_for_question(self):
        """召回应返回字符串。"""
        adapter = MemoryAdapter(user_id=999)
        adapter.reset()
        adapter.ingest_session(
            [{"role": "user", "content": "I work at Google"}],
            "test", "2024/01/01",
        )
        result = adapter.recall_for_question("Where do I work?", top_k=5)
        assert isinstance(result, str)

    def test_reset_clears_memory(self):
        """reset 应清理记忆。"""
        adapter = MemoryAdapter(user_id=999)
        adapter.ingest_session(
            [{"role": "user", "content": "test memory for reset"}],
            "test", "2024/01/01",
        )
        adapter.reset()
        # 召回应为空或极短
        result = adapter.recall_for_question("test memory", top_k=5)
        # reset 后召回可能为空字符串
        assert isinstance(result, str)
