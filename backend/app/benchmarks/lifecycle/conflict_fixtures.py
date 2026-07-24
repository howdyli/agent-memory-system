"""
冲突检测固件加载

固件文件: fixtures/conflict_pairs.json
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List

_FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "fixtures")
_FIXTURE_PATH = os.path.join(_FIXTURES_DIR, "conflict_pairs.json")


def get_fixture_path() -> str:
    return _FIXTURE_PATH


def load_conflict_pairs() -> List[Dict[str, Any]]:
    if not os.path.exists(_FIXTURE_PATH):
        return []
    with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
