"""
P0 高级召回优化测试

验证三项 P0 改进的正确性：
    P0-1: 多会话信息聚合召回
    P0-2: 时间感知召回
    P0-3: 知识更新检测
"""
import pytest
from datetime import datetime

from app.services.advanced_recall import (
    # P0-1: 多会话聚合
    is_multi_session_question,
    generate_sub_queries,
    _extract_key_nouns,
    _format_multi_session_context,
    # P0-2: 时间感知
    extract_time_signals,
    is_temporal_question,
    _parse_memory_date,
    _format_temporal_context,
    # P0-3: 知识更新
    detect_knowledge_update,
    _extract_updatable_entity,
    _text_similarity,
    filter_superseded_memories,
    # 统一入口
    advanced_recall,
    # P1-1: 会话分解索引
    decompose_session,
    _is_noise_sentence,
    # P1-2: 多键索引
    _extract_fact_keywords,
    _extract_semantic_concepts,
    _extract_time_keywords,
    search_by_multi_keys,
    # P1-3: 时间感知查询扩展
    expand_query_with_time,
    # P1 集成入口
    advanced_recall_v2,
)


# ============================================================
# P0-1: 多会话信息聚合召回测试
# ============================================================

class TestMultiSessionDetection:
    """验证多会话问题检测。"""

    def test_detect_how_many(self):
        """'how many' 应识别为多会话问题。"""
        assert is_multi_session_question("How many years of experience do I have?") is True

    def test_detect_total(self):
        """'total' 应识别为多会话问题。"""
        assert is_multi_session_question("What is the total number of languages I speak?") is True

    def test_detect_all(self):
        """'all' 应识别为多会话问题。"""
        assert is_multi_session_question("List all companies I worked at") is True

    def test_detect_chinese_total(self):
        """中文'总共'应识别为多会话问题。"""
        assert is_multi_session_question("我总共工作过几年？") is True

    def test_not_multi_session(self):
        """简单问题不应识别为多会话问题。"""
        assert is_multi_session_question("What is my name?") is False

    def test_not_multi_session_simple(self):
        """单会话事实查询不应识别为多会话问题。"""
        assert is_multi_session_question("Where do I work?") is False


class TestSubQueryGeneration:
    """验证子查询生成。"""

    def test_generate_includes_original(self):
        """子查询列表应包含原始问题。"""
        subs = generate_sub_queries("How many years of experience do I have?")
        assert "How many years of experience do I have?" in subs

    def test_generate_splits_and(self):
        """按 'and' 拆分子查询。"""
        subs = generate_sub_queries("What is my name and where do I work?")
        assert len(subs) >= 2
        assert any("name" in s.lower() for s in subs)
        assert any("work" in s.lower() for s in subs)

    def test_generate_splits_comma(self):
        """按逗号拆分子查询。"""
        subs = generate_sub_queries("What is my name, where do I work")
        assert len(subs) >= 2

    def test_generate_deduplicates(self):
        """子查询应去重。"""
        subs = generate_sub_queries("What is my name and what is my name?")
        # 去重后不应有重复
        lower_subs = [s.lower() for s in subs]
        assert len(lower_subs) == len(set(lower_subs))

    def test_generate_returns_at_least_one(self):
        """任何问题都应返回至少一个子查询。"""
        subs = generate_sub_queries("simple question")
        assert len(subs) >= 1

    def test_extract_key_nouns(self):
        """关键名词提取应过滤停用词。"""
        nouns = _extract_key_nouns("How many years of programming experience?")
        assert "years" in nouns
        assert "programming" in nouns
        assert "experience" in nouns
        assert "how" not in nouns  # 停用词应被过滤

    def test_format_multi_session_context(self):
        """多会话上下文格式化。"""
        memories = [
            {"content": "I know Python", "_sub_query": "python"},
            {"content": "I know Java", "_sub_query": "java"},
        ]
        ctx = _format_multi_session_context(memories, ["python", "java"])
        assert "多会话聚合记忆" in ctx
        assert "Python" in ctx
        assert "Java" in ctx

    def test_format_empty_memories(self):
        """空记忆列表应返回空字符串。"""
        ctx = _format_multi_session_context([], [])
        assert ctx == ""


# ============================================================
# P0-2: 时间感知召回测试
# ============================================================

class TestTemporalDetection:
    """验证时间问题检测。"""

    def test_detect_recently(self):
        """'recently' 应识别为时间问题。"""
        signals = extract_time_signals("Which company did I work at most recently?")
        assert signals["has_time_signal"] is True
        assert signals["time_preference"] == "recent"

    def test_detect_first(self):
        """'first' 应识别为时间问题。"""
        signals = extract_time_signals("What was my first job?")
        assert signals["has_time_signal"] is True
        assert signals["time_preference"] == "earliest"

    def test_detect_year(self):
        """具体年份应识别为时间问题。"""
        signals = extract_time_signals("What did I do in 2023?")
        assert signals["has_time_signal"] is True
        assert "2023" in str(signals["time_preference"])
        assert signals["year"] == 2023

    def test_detect_before_year(self):
        """'before 2023' 应识别为时间问题。"""
        signals = extract_time_signals("Where did I work before 2023?")
        assert signals["has_time_signal"] is True
        assert signals["time_preference"].startswith("before")

    def test_detect_chinese_recent(self):
        """中文'最近'应识别为时间问题。"""
        signals = extract_time_signals("我最近在哪家公司工作？")
        assert signals["has_time_signal"] is True

    def test_not_temporal(self):
        """非时间问题不应识别。"""
        signals = extract_time_signals("What is my name?")
        assert signals["has_time_signal"] is False
        assert signals["time_preference"] is None

    def test_is_temporal_question_helper(self):
        """is_temporal_question 辅助函数。"""
        assert is_temporal_question("When did I start learning Python?") is True
        assert is_temporal_question("What is my name?") is False


class TestParseMemoryDate:
    """验证记忆时间戳解析。"""

    def test_parse_longmemeval_timestamp(self):
        """LongMemEval 格式 [2024/05/15] 时间戳解析。"""
        mem = {"content": "[2024/05/15] I started learning Python"}
        date = _parse_memory_date(mem)
        assert date.year == 2024
        assert date.month == 5
        assert date.day == 15

    def test_parse_iso_timestamp(self):
        """ISO 格式时间戳解析。"""
        mem = {"content": "test", "created_at": "2024-03-20T10:00:00"}
        date = _parse_memory_date(mem)
        assert date.year == 2024
        assert date.month == 3

    def test_parse_no_date_fallback(self):
        """无时间戳时回退到默认日期。"""
        mem = {"content": "no date here"}
        date = _parse_memory_date(mem)
        assert date.year == 2000  # 回退值

    def test_format_temporal_context(self):
        """时间感知上下文格式化。"""
        memories = [
            {"content": "[2024/01/15] Event A", "_parsed_date": datetime(2024, 1, 15)},
            {"content": "[2024/05/20] Event B", "_parsed_date": datetime(2024, 5, 20)},
        ]
        signals = {"time_preference": "recent"}
        ctx = _format_temporal_context(memories, signals)
        assert "时间感知记忆" in ctx
        assert "Event A" in ctx
        assert "2024/01/15" in ctx


# ============================================================
# P0-3: 知识更新检测测试
# ============================================================

class TestUpdatableEntityExtraction:
    """验证可更新实体提取。"""

    def test_extract_location_english(self):
        """英文住址提取。"""
        entity = _extract_updatable_entity("I just moved to San Francisco")
        assert entity is not None
        assert entity[0] == "location"
        assert "san francisco" in entity[1].lower()

    def test_extract_organization_english(self):
        """英文公司提取。"""
        entity = _extract_updatable_entity("I got a job at Google")
        assert entity is not None
        assert entity[0] == "organization"

    def test_extract_location_chinese(self):
        """中文住址提取。"""
        entity = _extract_updatable_entity("我搬到北京了")
        assert entity is not None
        assert entity[0] == "location"

    def test_extract_no_entity(self):
        """无可更新实体时返回 None。"""
        entity = _extract_updatable_entity("I like Python programming")
        assert entity is None

    def test_extract_title(self):
        """职位提取。"""
        entity = _extract_updatable_entity("I got promoted to Senior Software Engineer")
        assert entity is not None
        assert entity[0] == "title"


class TestTextSimilarity:
    """验证文本相似度计算。"""

    def test_identical_text(self):
        """完全相同的文本相似度为 1.0。"""
        score = _text_similarity("I live in New York", "I live in New York")
        assert score == 1.0

    def test_no_overlap(self):
        """无重叠词的文本相似度为 0.0。"""
        score = _text_similarity("apple banana", "cat dog")
        assert score == 0.0

    def test_partial_overlap(self):
        """部分重叠应在 0-1 之间。"""
        score = _text_similarity("I live in New York", "I moved to New York")
        assert 0 < score < 1


class TestFilterSuperseded:
    """验证已过时记忆过滤。"""

    def test_filter_superseded(self):
        """应过滤 lifecycle_status=superseded 的记忆。"""
        memories = [
            {"id": 1, "content": "old", "lifecycle_status": "superseded"},
            {"id": 2, "content": "new", "lifecycle_status": "active"},
            {"id": 3, "content": "normal"},  # 无 status 默认 active
        ]
        filtered = filter_superseded_memories(memories)
        assert len(filtered) == 2
        assert all(m.get("lifecycle_status", "active") != "superseded" for m in filtered)

    def test_filter_all_active(self):
        """全为 active 时不过滤。"""
        memories = [
            {"id": 1, "lifecycle_status": "active"},
            {"id": 2, "lifecycle_status": "active"},
        ]
        filtered = filter_superseded_memories(memories)
        assert len(filtered) == 2

    def test_filter_all_superseded(self):
        """全为 superseded 时返回空列表。"""
        memories = [
            {"id": 1, "lifecycle_status": "superseded"},
        ]
        filtered = filter_superseded_memories(memories)
        assert len(filtered) == 0


class TestKnowledgeUpdateDetection:
    """验证知识更新检测（需要数据库）。"""

    def test_detect_no_update_for_non_updatable(self):
        """非可更新内容不应触发更新检测。"""
        result = detect_knowledge_update(
            user_id=999,
            new_content="I like Python programming",
        )
        assert result["success"] is True
        assert result["updated"] is False
        assert result["update_type"] == "none"


# ============================================================
# 统一入口测试
# ============================================================

class TestAdvancedRecallRouting:
    """验证 advanced_recall 的策略路由。"""

    def test_routes_temporal_to_time_aware(self):
        """时间问题应路由到时间感知策略。"""
        # 使用 mock 验证路由逻辑
        from unittest.mock import patch, MagicMock

        with patch("app.services.advanced_recall.time_aware_recall") as mock_time:
            mock_time.return_value = {"success": True, "source": "time_aware", "memories": [], "context": ""}
            result = advanced_recall(999, "Which company did I work at most recently?")
            mock_time.assert_called_once()
            assert result["source"] == "time_aware"

    def test_routes_multi_session_to_aggregation(self):
        """多会话问题应路由到聚合策略。"""
        from unittest.mock import patch

        with patch("app.services.advanced_recall.multi_session_recall") as mock_multi:
            mock_multi.return_value = {"success": True, "source": "multi_session", "memories": [], "context": ""}
            result = advanced_recall(999, "How many languages do I speak?")
            mock_multi.assert_called_once()
            assert result["source"] == "multi_session"

    def test_routes_standard_for_simple_questions(self):
        """简单问题应路由到标准策略。"""
        from unittest.mock import patch

        # search_relevant_memories 在函数内部导入，需 patch 源模块
        with patch("app.services.auto_recall_service.search_relevant_memories") as mock_search:
            mock_search.return_value = {
                "success": True,
                "memories": [{"id": 1, "content": "test"}],
            }
            result = advanced_recall(999, "What is my name?")
            mock_search.assert_called_once()
            assert result["source"] == "standard_filtered"

    def test_temporal_takes_priority_over_multi_session(self):
        """时间问题优先于多会话问题。"""
        from unittest.mock import patch

        with patch("app.services.advanced_recall.time_aware_recall") as mock_time:
            mock_time.return_value = {"success": True, "source": "time_aware", "memories": [], "context": ""}
            # 这个问题既是时间问题也是多会话问题
            result = advanced_recall(999, "How many companies have I worked at most recently?")
            mock_time.assert_called_once()
            assert result["source"] == "time_aware"


# ============================================================
# P1-1: 会话分解索引测试
# ============================================================

class TestSessionDecomposition:
    """验证会话分解索引。"""

    def test_decompose_single_fact(self):
        """单条事实消息应正确分解。"""
        session = [{"role": "user", "content": "I have been coding in Python for 5 years now."}]
        facts = decompose_session(session)
        assert len(facts) == 1
        assert "Python" in facts[0]

    def test_decompose_multiple_sentences(self):
        """多句消息应分解为多个原子事实。"""
        session = [{"role": "user", "content": "I live in New York. I work at Google. I like Python."}]
        facts = decompose_session(session)
        assert len(facts) == 3

    def test_decompose_filters_short_sentences(self):
        """短句应被过滤。"""
        session = [{"role": "user", "content": "Hi. I work at Google as a software engineer."}]
        facts = decompose_session(session)
        # "Hi" 太短被过滤
        assert all(len(f) >= 10 for f in facts)
        assert len(facts) == 1

    def test_decompose_filters_greetings(self):
        """问候语应被过滤。"""
        session = [{"role": "user", "content": "Hello there! I have been studying Japanese for 2 years."}]
        facts = decompose_session(session)
        assert all("Hello" not in f for f in facts)
        assert any("Japanese" in f for f in facts)

    def test_decompose_ignores_assistant_messages(self):
        """助手消息应被忽略。"""
        session = [
            {"role": "user", "content": "I work at Google as a software engineer."},
            {"role": "assistant", "content": "That is a great job at Google!"},
        ]
        facts = decompose_session(session)
        assert len(facts) == 1
        assert "Google" in facts[0]

    def test_decompose_handles_newlines(self):
        """换行分隔的句子应正确分解。"""
        session = [{"role": "user", "content": "I live in Tokyo\nI work at Sony\nI speak Japanese"}]
        facts = decompose_session(session)
        assert len(facts) == 3

    def test_decompose_empty_session(self):
        """空会话应返回空列表。"""
        assert decompose_session([]) == []

    def test_decompose_no_user_messages(self):
        """只有助手消息的会话应返回空列表。"""
        session = [{"role": "assistant", "content": "How can I help you today?"}]
        assert decompose_session(session) == []

    def test_is_noise_sentence_hi(self):
        """'hi' 应被识别为噪声。"""
        assert _is_noise_sentence("hi there") is True

    def test_is_noise_sentence_fact(self):
        """事实句子不应被识别为噪声。"""
        assert _is_noise_sentence("I have been coding in Python for 5 years") is False


# ============================================================
# P1-2: 多键索引测试
# ============================================================

class TestFactKeywordExtraction:
    """验证事实关键词提取。"""

    def test_extract_proper_nouns(self):
        """应提取专有名词。"""
        keys = _extract_fact_keywords("I work at Google as a software engineer")
        assert "Google" in keys

    def test_extract_numbers_with_units(self):
        """应提取数字+单位。"""
        keys = _extract_fact_keywords("I have been coding for 5 years now")
        assert any("5" in k for k in keys)

    def test_extract_chinese_nouns(self):
        """应提取中文专有名词。"""
        keys = _extract_fact_keywords("我在北京工作，是一名软件工程师")
        assert any("北京" in k for k in keys or "软件" in k for k in keys)

    def test_extract_deduplication(self):
        """重复关键词应去重。"""
        keys = _extract_fact_keywords("Google Google Google Python Python")
        # 去重后不应有重复
        lower_keys = [k.lower() for k in keys]
        assert len(lower_keys) == len(set(lower_keys))

    def test_extract_empty_content(self):
        """空内容应返回空列表。"""
        assert _extract_fact_keywords("") == []


class TestSemanticConceptExtraction:
    """验证语义概念提取。"""

    def test_extract_verb_noun_phrase(self):
        """应提取动词+名词短语。"""
        keys = _extract_semantic_concepts("I live in San Francisco and work at Google")
        assert len(keys) > 0

    def test_extract_have_been_phrase(self):
        """应提取 have been + 动名词短语。"""
        keys = _extract_semantic_concepts("I have been coding in Python for years")
        assert any("coding" in k for k in keys)

    def test_extract_empty_content(self):
        """空内容应返回空列表。"""
        assert _extract_semantic_concepts("") == []


class TestTimeKeywordExtraction:
    """验证时间关键词提取。"""

    def test_extract_year(self):
        """应提取年份。"""
        keys = _extract_time_keywords("I started learning Python in 2019")
        assert "2019" in keys

    def test_extract_relative_time(self):
        """应提取相对时间。"""
        keys = _extract_time_keywords("I changed jobs last year")
        assert any("last year" in k.lower() for k in keys)

    def test_extract_from_session_date(self):
        """应从会话日期提取年份。"""
        keys = _extract_time_keywords("I love Python", session_date="2024/05/15")
        assert "2024" in keys

    def test_extract_empty_content(self):
        """空内容应返回空列表。"""
        assert _extract_time_keywords("") == []


class TestMultiKeySearch:
    """验证多键索引检索。"""

    def test_search_returns_fragment_ids(self):
        """检索应返回片段 ID 列表。"""
        from unittest.mock import patch, MagicMock

        mock_db = MagicMock()
        mock_db.execute.return_value = [
            {"fragment_id": 1, "key_type": "fact"},
            {"fragment_id": 1, "key_type": "time"},
            {"fragment_id": 2, "key_type": "fact"},
        ]
        with patch("app.services.advanced_recall.get_db_client", return_value=mock_db):
            result = search_by_multi_keys(999, "Python 2019", top_k=5)
        assert result["success"] is True
        assert 1 in result["fragment_ids"]

    def test_search_no_query_keys(self):
        """查询无可提取关键词时应返回空。"""
        from unittest.mock import patch, MagicMock
        mock_db = MagicMock()
        with patch("app.services.advanced_recall.get_db_client", return_value=mock_db):
            result = search_by_multi_keys(999, "???", top_k=5)
        assert result["success"] is True
        assert result["fragment_ids"] == []

    def test_search_key_match_scoring(self):
        """多键匹配的片段应排在前面。"""
        from unittest.mock import patch, MagicMock

        mock_db = MagicMock()
        mock_db.execute.return_value = [
            {"fragment_id": 1, "key_type": "fact"},
            {"fragment_id": 1, "key_type": "time"},
            {"fragment_id": 2, "key_type": "fact"},
        ]
        with patch("app.services.advanced_recall.get_db_client", return_value=mock_db):
            result = search_by_multi_keys(999, "Python 2019", top_k=5)
        # fragment 1 匹配 2 个键，应排在 fragment 2 之前
        assert result["fragment_ids"][0] == 1


# ============================================================
# P1-3: 时间感知查询扩展测试
# ============================================================

class TestQueryExpansion:
    """验证时间感知查询扩展。"""

    def test_expand_recent_question(self):
        """recent 问题应扩展出相关查询。"""
        queries = expand_query_with_time("Which company did I work at most recently?")
        assert len(queries) > 1
        assert queries[0] == "Which company did I work at most recently?"
        # 应包含 "recently" 扩展
        assert any("recently" in q.lower() or "latest" in q.lower() for q in queries)

    def test_expand_earliest_question(self):
        """earliest 问题应扩展出相关查询。"""
        queries = expand_query_with_time("What was my first job?")
        assert len(queries) > 1
        assert any("first" in q.lower() or "initial" in q.lower() for q in queries)

    def test_expand_year_question(self):
        """特定年份问题应扩展出年份查询。"""
        queries = expand_query_with_time("What happened in 2023?")
        assert len(queries) > 1
        assert any("2023" in q for q in queries)

    def test_expand_before_question(self):
        """before 问题应扩展出相关查询。"""
        queries = expand_query_with_time("Where did I work before 2023?")
        assert len(queries) > 1
        assert any("before" in q.lower() or "prior" in q.lower() for q in queries)

    def test_expand_after_question(self):
        """after 问题应扩展出相关查询。"""
        queries = expand_query_with_time("Where did I work after 2022?")
        assert len(queries) > 1
        assert any("after" in q.lower() or "since" in q.lower() for q in queries)

    def test_expand_non_temporal_question(self):
        """非时间问题应只返回原始查询。"""
        queries = expand_query_with_time("What is my name?")
        assert len(queries) == 1
        assert queries[0] == "What is my name?"

    def test_expand_always_includes_original(self):
        """扩展查询列表始终包含原始查询。"""
        question = "When did I start learning Python?"
        queries = expand_query_with_time(question)
        assert question in queries

    def test_expand_limits_query_count(self):
        """扩展查询数量应有限制。"""
        queries = expand_query_with_time("Which company did I work at most recently?")
        assert len(queries) <= 6


class TestTimeExpandedRecall:
    """验证时间扩展召回路由。"""

    def test_time_expanded_recall_routes_correctly(self):
        """时间问题应路由到时间扩展召回。"""
        from unittest.mock import patch

        with patch("app.services.advanced_recall.time_expanded_recall") as mock_time:
            mock_time.return_value = {"success": True, "source": "time_expanded", "memories": [], "context": ""}
            result = advanced_recall_v2(999, "When did I start learning Python?")
            mock_time.assert_called_once()
            assert result["p1_optimized"] is True

    def test_v2_routes_temporal_to_time_expanded(self):
        """时间问题应使用 P1-3 时间扩展召回。"""
        from unittest.mock import patch

        with patch("app.services.advanced_recall.time_expanded_recall") as mock_time:
            mock_time.return_value = {
                "success": True, "source": "time_expanded",
                "memories": [{"id": 1, "content": "test"}], "context": "test",
            }
            with patch("app.services.advanced_recall.search_by_multi_keys") as mock_keys:
                mock_keys.return_value = {"success": True, "fragment_ids": [], "key_matches": {}}
                result = advanced_recall_v2(999, "most recently?")
                mock_time.assert_called_once()

    def test_v2_routes_multi_session_to_aggregation(self):
        """多会话问题应使用 P0-1 多会话聚合。"""
        from unittest.mock import patch

        with patch("app.services.advanced_recall.multi_session_recall") as mock_ms:
            mock_ms.return_value = {
                "success": True, "source": "multi_session",
                "memories": [{"id": 1, "content": "test"}], "context": "test",
            }
            with patch("app.services.advanced_recall.search_by_multi_keys") as mock_keys:
                mock_keys.return_value = {"success": True, "fragment_ids": [], "key_matches": {}}
                result = advanced_recall_v2(999, "How many languages do I speak?")
                mock_ms.assert_called_once()

    def test_v2_includes_multi_key_annotation(self):
        """所有策略都应叠加 P1-2 多键索引标注。"""
        from unittest.mock import patch

        with patch("app.services.advanced_recall.search_by_multi_keys") as mock_keys:
            mock_keys.return_value = {"success": True, "fragment_ids": [1], "key_matches": {}}
            with patch("app.services.advanced_recall.time_expanded_recall") as mock_time:
                mock_time.return_value = {
                    "success": True, "source": "time_expanded",
                    "memories": [{"id": 1, "content": "test"}], "context": "test",
                }
                result = advanced_recall_v2(999, "When did I work at Google?")
                mock_keys.assert_called_once()
                assert result.get("key_matched_count") == 1

    def test_v2_standard_delegates_to_p0(self):
        """标准问题应委托给 P0 advanced_recall，不重排。"""
        from unittest.mock import patch

        with patch("app.services.advanced_recall.advanced_recall") as mock_p0:
            mock_p0.return_value = {
                "success": True, "source": "standard_filtered",
                "memories": [{"id": 1, "content": "test"}], "context": "test",
            }
            with patch("app.services.advanced_recall.search_by_multi_keys") as mock_keys:
                mock_keys.return_value = {"success": True, "fragment_ids": [], "key_matches": {}}
                result = advanced_recall_v2(999, "What is my name?")
                mock_p0.assert_called_once()

    def test_v2_key_matched_memories_annotated_not_reordered(self):
        """多键匹配的记忆应被标注但不重排（非破坏性增强）。"""
        from unittest.mock import patch

        with patch("app.services.advanced_recall.search_by_multi_keys") as mock_keys:
            mock_keys.return_value = {"success": True, "fragment_ids": [2], "key_matches": {}}
            with patch("app.services.advanced_recall.advanced_recall") as mock_p0:
                mock_p0.return_value = {
                    "success": True,
                    "source": "standard_filtered",
                    "memories": [
                        {"id": 1, "content": "memory 1"},
                        {"id": 2, "content": "memory 2"},
                    ],
                    "context": "test",
                }
                result = advanced_recall_v2(999, "What is my name?")
                # memory 2 被 key 匹配，应被标注
                matched = [m for m in result["memories"] if m.get("_key_matched")]
                assert len(matched) == 1
                assert matched[0]["id"] == 2
                # 但不应重排（memory 1 仍在第一位）
                assert result["memories"][0]["id"] == 1
