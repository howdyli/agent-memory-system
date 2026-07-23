"""
LongMemEval 合成样本数据集

由于真实 LongMemEval 数据集需从 HuggingFace 下载（~115K tokens/实例），
本模块提供一个符合 LongMemEval 格式的小型合成数据集，用于：
    1. 离线验证基准测试框架的正确性
    2. CI 流水线中的冒烟测试
    3. 无 LLM 环境下的回退评估

合成数据集覆盖全部 5 种记忆能力，共 10 条实例。
数据格式严格遵循 LongMemEval 官方 JSON Schema。
"""
from __future__ import annotations

from typing import Any, Dict, List


def build_sample_dataset() -> List[Dict[str, Any]]:
    """构建合成 LongMemEval 样本数据集（10 条实例）。

    覆盖 5 种记忆能力：
        - 信息提取（2 条）
        - 多会话推理（2 条）
        - 时间推理（2 条）
        - 知识更新（2 条）
        - 弃权（2 条）
    """
    return [
        # ============================================================
        # 1. 信息提取 — single-session-user
        # ============================================================
        {
            "question_id": "sample_001",
            "question_type": "single-session-user",
            "question": "What is my favorite programming language?",
            "answer": "Python",
            "question_date": "2024/06/01 (Sat) 10:00",
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2024/05/15", "2024/05/20"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I have been coding in Python for 5 years now."},
                    {"role": "assistant", "content": "That's great! Python is a versatile language."},
                ],
                [
                    {"role": "user", "content": "I love using Python for data analysis."},
                    {"role": "assistant", "content": "Python has excellent libraries like pandas and numpy."},
                ],
            ],
            "answer_session_ids": ["s1"],
        },

        # ============================================================
        # 2. 信息提取 — single-session-user
        # ============================================================
        {
            "question_id": "sample_002",
            "question_type": "single-session-user",
            "question": "Where do I work?",
            "answer": "Google",
            "question_date": "2024/06/02 (Sun) 14:00",
            "haystack_session_ids": ["s1"],
            "haystack_dates": ["2024/05/10"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I just got a job at Google as a software engineer."},
                    {"role": "assistant", "content": "Congratulations on your new role at Google!"},
                ],
            ],
            "answer_session_ids": ["s1"],
        },

        # ============================================================
        # 3. 多会话推理 — multi-session
        # ============================================================
        {
            "question_id": "sample_003",
            "question_type": "multi-session",
            "question": "How many years of programming experience do I have in total?",
            "answer": "8 years",
            "question_date": "2024/06/03 (Mon) 09:00",
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2024/05/01", "2024/05/15"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I started learning Java 3 years ago."},
                    {"role": "assistant", "content": "Java is a great language for enterprise applications."},
                ],
                [
                    {"role": "user", "content": "I have been coding in Python for 5 years now."},
                    {"role": "assistant", "content": "That's impressive! 5 years with Python."},
                ],
            ],
            "answer_session_ids": ["s1", "s2"],
        },

        # ============================================================
        # 4. 多会话推理 — multi-session
        # ============================================================
        {
            "question_id": "sample_004",
            "question_type": "multi-session",
            "question": "What languages do I speak?",
            "answer": "English, Chinese, and Japanese",
            "question_date": "2024/06/04 (Tue) 11:00",
            "haystack_session_ids": ["s1", "s2", "s3"],
            "haystack_dates": ["2024/04/01", "2024/04/15", "2024/05/01"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I am a native English speaker."},
                    {"role": "assistant", "content": "English is widely used globally."},
                ],
                [
                    {"role": "user", "content": "I learned Chinese when I was a child."},
                    {"role": "assistant", "content": "Chinese is a fascinating language."},
                ],
                [
                    {"role": "user", "content": "I have been studying Japanese for 2 years."},
                    {"role": "assistant", "content": "Japanese has three writing systems."},
                ],
            ],
            "answer_session_ids": ["s1", "s2", "s3"],
        },

        # ============================================================
        # 5. 时间推理 — temporal-reasoning
        # ============================================================
        {
            "question_id": "sample_005",
            "question_type": "temporal-reasoning",
            "question": "Which company did I work at most recently before Google?",
            "answer": "Microsoft",
            "question_date": "2024/06/05 (Wed) 16:00",
            "haystack_session_ids": ["s1", "s2", "s3"],
            "haystack_dates": ["2024/01/15", "2024/03/20", "2024/05/10"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I used to work at Amazon in 2022."},
                    {"role": "assistant", "content": "Amazon is a major tech company."},
                ],
                [
                    {"role": "user", "content": "I joined Microsoft in early 2023."},
                    {"role": "assistant", "content": "Microsoft has a great engineering culture."},
                ],
                [
                    {"role": "user", "content": "I just got a job at Google as a software engineer."},
                    {"role": "assistant", "content": "Congratulations on your new role at Google!"},
                ],
            ],
            "answer_session_ids": ["s2", "s3"],
        },

        # ============================================================
        # 6. 时间推理 — temporal-reasoning
        # ============================================================
        {
            "question_id": "sample_006",
            "question_type": "temporal-reasoning",
            "question": "When did I start learning Python?",
            "answer": "2019",
            "question_date": "2024/06/06 (Thu) 10:00",
            "haystack_session_ids": ["s1"],
            "haystack_dates": ["2024/05/15"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I have been coding in Python for 5 years now since 2019."},
                    {"role": "assistant", "content": "5 years of Python experience is valuable."},
                ],
            ],
            "answer_session_ids": ["s1"],
        },

        # ============================================================
        # 7. 知识更新 — knowledge-update
        # ============================================================
        {
            "question_id": "sample_007",
            "question_type": "knowledge-update",
            "question": "What city do I currently live in?",
            "answer": "San Francisco",
            "question_date": "2024/06/07 (Fri) 12:00",
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2024/04/01", "2024/05/15"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I live in New York City."},
                    {"role": "assistant", "content": "New York is a vibrant city!"},
                ],
                [
                    {"role": "user", "content": "I just moved to San Francisco last week."},
                    {"role": "assistant", "content": "San Francisco is a great place for tech professionals."},
                ],
            ],
            "answer_session_ids": ["s2"],
        },

        # ============================================================
        # 8. 知识更新 — knowledge-update
        # ============================================================
        {
            "question_id": "sample_008",
            "question_type": "knowledge-update",
            "question": "What is my current job title?",
            "answer": "Senior Software Engineer",
            "question_date": "2024/06/08 (Sat) 15:00",
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2024/03/01", "2024/05/20"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I am a Junior Software Engineer at a startup."},
                    {"role": "assistant", "content": "Being a junior engineer is a great starting point."},
                ],
                [
                    {"role": "user", "content": "I just got promoted to Senior Software Engineer!"},
                    {"role": "assistant", "content": "Congratulations on your promotion!"},
                ],
            ],
            "answer_session_ids": ["s2"],
        },

        # ============================================================
        # 9. 弃权 — abstention (question_id 以 _abs 结尾)
        # ============================================================
        {
            "question_id": "sample_009_abs",
            "question_type": "abstention",
            "question": "What is my favorite color?",
            "answer": "The information is not mentioned in the conversation history.",
            "question_date": "2024/06/09 (Sun) 09:00",
            "haystack_session_ids": ["s1", "s2"],
            "haystack_dates": ["2024/05/01", "2024/05/15"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I love programming in Python."},
                    {"role": "assistant", "content": "Python is a great choice!"},
                ],
                [
                    {"role": "user", "content": "I work at Google as a software engineer."},
                    {"role": "assistant", "content": "That's a wonderful job!"},
                ],
            ],
            "answer_session_ids": [],
        },

        # ============================================================
        # 10. 弃权 — abstention
        # ============================================================
        {
            "question_id": "sample_010_abs",
            "question_type": "abstention",
            "question": "What is my mother's name?",
            "answer": "The information is not mentioned in the conversation history.",
            "question_date": "2024/06/10 (Mon) 14:00",
            "haystack_session_ids": ["s1"],
            "haystack_dates": ["2024/05/20"],
            "haystack_sessions": [
                [
                    {"role": "user", "content": "I have been studying Japanese for 2 years."},
                    {"role": "assistant", "content": "Japanese is an interesting language to learn."},
                ],
            ],
            "answer_session_ids": [],
        },
    ]
