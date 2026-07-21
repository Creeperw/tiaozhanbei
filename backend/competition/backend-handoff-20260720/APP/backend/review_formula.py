from dataclasses import dataclass
import math


FORMULA_VERSION = "ebbinghaus_classic_hybrid_v1_1"
ALPHA = 0.35
TARGET_RETENTION = 0.9
RECOVERY_INTERVAL_SECONDS = 300
STAGE_INTERVALS = (0, 1200, 3600, 32400, 86400, 172800, 518400, 2678400)


@dataclass(frozen=True)
class ReviewTransition:
    stage: int
    interval_seconds: int
    requires_remediation: bool = False


def clip(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def mastery_after_attempt(
    *,
    previous_score: float | None,
    q_t: float,
    lambda_value: float,
    delta_days: float,
) -> float:
    if not math.isfinite(q_t) or not math.isfinite(delta_days) or not 0 <= q_t <= 1 or delta_days < 0:
        raise ValueError("invalid mastery input")
    previous = q_t if previous_score is None else previous_score / 100
    current = (1 - ALPHA) * previous * math.exp(-lambda_value * delta_days) + ALPHA * q_t
    return 100 * clip(current, 0, 1)


def lambda_per_day(recent_five_wrong_count: int, consecutive_correct: int) -> float:
    return clip(
        0.08 + 0.04 * recent_five_wrong_count - 0.015 * min(consecutive_correct, 5),
        0.03,
        0.20,
    )


def effective_interval_seconds(value: float) -> int:
    return max(RECOVERY_INTERVAL_SECONDS, int(round(value)))


def stability_for_interval(interval_seconds: int) -> float:
    return -effective_interval_seconds(interval_seconds) / math.log(TARGET_RETENTION)


def review_transition(stage: int, outcome: str) -> ReviewTransition:
    if not 0 <= stage < len(STAGE_INTERVALS):
        raise ValueError("invalid review stage")

    if outcome == "independent_correct":
        next_stage = min(stage + 1, len(STAGE_INTERVALS) - 1)
        interval_seconds = STAGE_INTERVALS[next_stage]
    elif outcome == "hinted_correct":
        next_stage = stage
        interval_seconds = STAGE_INTERVALS[next_stage] * 0.75
    elif outcome == "skipped":
        next_stage = max(stage - 1, 1)
        interval_seconds = STAGE_INTERVALS[next_stage] * 0.5
    elif outcome == "wrong":
        next_stage = stage
        interval_seconds = RECOVERY_INTERVAL_SECONDS
    else:
        raise ValueError("invalid review outcome")

    return ReviewTransition(
        stage=next_stage,
        interval_seconds=effective_interval_seconds(interval_seconds),
    )
