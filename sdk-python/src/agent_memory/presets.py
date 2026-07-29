"""Preset configurations — opinionated defaults for common scenarios."""

from typing import Any, Dict

# 预设配置模板（W3-F3.2）
#
# | Preset    | recall_top_k | semantic_threshold | preference_half_life | plan_half_life |
# |-----------|--------------|--------------------|----------------------|----------------|
# | chatbot   | 5            | 0.4                | 1 天                 | 30 天          |
# | knowledge | 10           | 0.2                | 30 天                | 180 天         |
# | assistant | 5            | 0.3                | 1 天                 | 90 天          |
PRESETS: Dict[str, Dict[str, Any]] = {
    # 对话机器人：偏好权重高、短半衰期，召回更精准
    "chatbot": {
        "recall_top_k": 5,
        "semantic_threshold": 0.4,
        "preference_half_life_days": 1,
        "plan_half_life_days": 30,
    },
    # 知识库：info 权重高、长半衰期，召回更全面
    "knowledge": {
        "recall_top_k": 10,
        "semantic_threshold": 0.2,
        "preference_half_life_days": 30,
        "plan_half_life_days": 180,
    },
    # 通用助手：均衡配置（默认）
    "assistant": {
        "recall_top_k": 5,
        "semantic_threshold": 0.3,
        "preference_half_life_days": 1,
        "plan_half_life_days": 90,
    },
}

DEFAULT_PRESET = "assistant"


def get_preset(name: str) -> Dict[str, Any]:
    """按名称获取预设配置的副本。

    Args:
        name: 预设名称（chatbot / knowledge / assistant）

    Raises:
        ValueError: 预设名称不存在
    """
    if name not in PRESETS:
        raise ValueError(
            f"未知预设: {name!r}，可用预设: {', '.join(sorted(PRESETS))}"
        )
    return dict(PRESETS[name])
