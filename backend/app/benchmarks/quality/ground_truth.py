"""
召回质量 ground truth 固件管理

提供固件加载与构建辅助函数。
固件文件: fixtures/recall_ground_truth.json
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

# 固件路径
_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
_GROUND_TRUTH_PATH = os.path.join(_FIXTURES_DIR, "recall_ground_truth.json")


def get_fixture_path() -> str:
    """返回 ground truth 固件文件路径。"""
    return _GROUND_TRUTH_PATH


def load_ground_truth() -> List[Dict[str, Any]]:
    """加载 ground truth 固件。

    若固件不存在，返回空列表（由 recall_quality.py 降级到合成数据推断）。
    """
    if not os.path.exists(_GROUND_TRUTH_PATH):
        return []
    with open(_GROUND_TRUTH_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save_ground_truth(data: List[Dict[str, Any]]) -> None:
    """保存 ground truth 固件（人工标注工具用）。"""
    os.makedirs(_FIXTURES_DIR, exist_ok=True)
    with open(_GROUND_TRUTH_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
