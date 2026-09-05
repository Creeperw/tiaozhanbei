"""统一题目难度契约。

仅接受真实标注的难度：整数 1-5 或 D1-D5。
缺失或非法数据一律保持 None，绝不推断、绝不默认成“中等”。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from competition_app.contracts.base import ContractModel

# 唯一合法的难度等级集合
VALID_DIFFICULTY_LEVELS = frozenset({1, 2, 3, 4, 5})

# 组卷难度缺口时的降级策略（从高到低优先级）
DifficultyFallbackPolicy = Literal[
    "strict",          # 仅精确难度匹配，缺口不降级
    "unlabeled_official",  # 先补充难度未标注的正式题库题
    "web_reference",   # 再使用网络检索题作为参考
    "generated",       # 最后基于可信教材或网络参考生成补充题
]


def parse_difficulty(value: Any) -> int | None:
    """解析难度值，只接受整数 1-5 或 D1-D5。

    - None / 空字符串 / 布尔值 → None
    - 整数 1..5 → 原样
    - "D1".."D5" / "d1".."d5" → 对应整数
    - 浮点 1.0..5.0（整数形式）→ 对应整数
    - 非法值（6、0、-1、2.5、"D6"、"中"等）→ None
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value in VALID_DIFFICULTY_LEVELS else None
    if isinstance(value, float):
        if not value.is_integer():
            return None
        rating = int(value)
        return rating if rating in VALID_DIFFICULTY_LEVELS else None
    text = str(value).strip().upper()
    if not text:
        return None
    if text.startswith("D"):
        text = text[1:]
    if not text.isdigit():
        return None
    rating = int(text)
    return rating if rating in VALID_DIFFICULTY_LEVELS else None


def parse_difficulty_source(value: Any) -> str | None:
    """规范化难度来源字符串；空值返回 None。"""
    text = str(value or "").strip()
    return text or None


class DifficultyCoverageSummary(ContractModel):
    """题库难度能力摘要：可用等级、已标注数量、未标注数量。"""

    total_count: int = Field(default=0, ge=0)
    labeled_count: int = Field(default=0, ge=0)
    unlabeled_count: int = Field(default=0, ge=0)
    available_levels: list[int] = Field(default_factory=list)
    level_counts: dict[str, int] = Field(default_factory=dict)
    source_counts: dict[str, int] = Field(default_factory=dict)

    @property
    def difficulty_supported(self) -> bool:
        return self.labeled_count > 0


class DifficultyAbilityEvidence(ContractModel):
    """基于观测证据的“难度能力”统计（Phase 4）。

    available=False 表示没有足够的证据，下游应跳过 difficulty_fit 并重新归一化权重。
    """

    available: bool = False
    unavailable_reason: str | None = None
    attempts_by_level: dict[str, int] = Field(default_factory=dict)
    accuracy_by_level: dict[str, float] = Field(default_factory=dict)
    avg_seconds_by_level: dict[str, float] = Field(default_factory=dict)
    mastery_by_kp: dict[str, float] = Field(default_factory=dict)
    supported_stable_difficulty: int | None = Field(default=None, ge=1, le=5)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
