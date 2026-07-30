#!/usr/bin/env python3
"""Learner-profile/resource-difficulty adaptation evaluator.

This test does not ask a human or an LLM to label recommendations.  Each
profile defines an auditable target difficulty band, prerequisite mastery and
time budget; each top-K resource is checked against those constraints.

Input JSON/JSONL record:
  {"case_id":"c1", "profile_id":"novice", "topic":"四君子汤",
   "recommendations":[{"resource_id":"r1", "difficulty":2,
     "difficulty_source":"question_bank", "estimated_minutes":15,
     "prerequisite_kp_ids":[]}]}
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any


# At least three materially different backgrounds, as required by the test.
PROFILES: dict[str, dict[str, Any]] = {
    "novice": {
        "name": "跨专业零基础学习者",
        "background": "非医学专业，尚未系统学习中医基础理论",
        "target_difficulty": [1, 2],
        "available_minutes": 25,
        "mastery": 0.20,
        "knowledge_mastery": {},
    },
    "intermediate": {
        "name": "西医相关专业转学者",
        "background": "具备医学基础，已完成中医基础和中药学入门",
        "target_difficulty": [2, 3],
        "available_minutes": 45,
        "mastery": 0.55,
        "knowledge_mastery": {"TCM_BASIC": 0.80, "CHINESE_MATERIA_MEDICA": 0.70},
    },
    "advanced": {
        "name": "中医专业实习/备考学习者",
        "background": "中医药专业背景，目标为执业医师方证综合应用",
        "target_difficulty": [4, 5],
        "available_minutes": 75,
        "mastery": 0.85,
        "knowledge_mastery": {
            "TCM_BASIC": 0.92,
            "CHINESE_MATERIA_MEDICA": 0.90,
            "FORMULA_BASICS": 0.82,
        },
    },
}

DEMO_RECORDS = [
    {
        "case_id": "FJ_ADAPT_NOVICE",
        "profile_id": "novice",
        "topic": "四君子汤",
        "recommendations": [
            {"resource_id": "CARD_SJZT_BASIC", "difficulty": "D1", "difficulty_source": "expert_blueprint_v1", "estimated_minutes": 15, "prerequisite_kp_ids": []},
            {"resource_id": "Q_SJZT_RECALL", "difficulty": 2, "difficulty_source": "question_bank", "estimated_minutes": 10, "prerequisite_kp_ids": []},
        ],
    },
    {
        "case_id": "FJ_ADAPT_INTERMEDIATE",
        "profile_id": "intermediate",
        "topic": "四君子汤",
        "recommendations": [
            {"resource_id": "LESSON_SJZT_COMPAT", "difficulty": 3, "difficulty_source": "expert_blueprint_v1", "estimated_minutes": 30, "prerequisite_kp_ids": ["TCM_BASIC"]},
            {"resource_id": "Q_SJZT_COMPARE", "difficulty": "D3", "difficulty_source": "question_bank", "estimated_minutes": 15, "prerequisite_kp_ids": ["CHINESE_MATERIA_MEDICA"]},
        ],
    },
    {
        "case_id": "FJ_ADAPT_ADVANCED",
        "profile_id": "advanced",
        "topic": "四君子汤",
        "recommendations": [
            {"resource_id": "CASE_SJZT_CLINICAL", "difficulty": 4, "difficulty_source": "expert_blueprint_v1", "estimated_minutes": 45, "prerequisite_kp_ids": ["FORMULA_BASICS"]},
            {"resource_id": "Q_SJZT_TRANSFER", "difficulty": "D5", "difficulty_source": "question_bank", "estimated_minutes": 25, "prerequisite_kp_ids": ["TCM_BASIC", "CHINESE_MATERIA_MEDICA"]},
        ],
    },
]


def read_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig").strip()
    if not text:
        return []
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    value = json.loads(text)
    if isinstance(value, dict):
        value = value.get("records", value.get("items", [value]))
    if not isinstance(value, list):
        raise ValueError("input must be a JSON list or JSONL records")
    return [item for item in value if isinstance(item, dict)]


def read_profiles(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return PROFILES
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(value, list):
        value = {str(item["profile_id"]): item for item in value}
    if not isinstance(value, dict):
        raise ValueError("profiles must be a JSON object or list")
    return {str(key): item for key, item in value.items() if isinstance(item, dict)}


def parse_difficulty(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and float(value).is_integer():
        level = int(value)
        return level if 1 <= level <= 5 else None
    match = re.fullmatch(r"\s*(?:D|L)?\s*([1-5])\s*", str(value or ""), re.I)
    return int(match.group(1)) if match else None


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def evaluate_resource(resource: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    difficulty = parse_difficulty(resource.get("difficulty"))
    source = str(resource.get("difficulty_source") or "").strip()
    low, high = [int(value) for value in profile["target_difficulty"]]
    difficulty_fit = difficulty is not None and low <= difficulty <= high
    source_valid = bool(source)

    required = [str(value) for value in resource.get("prerequisite_kp_ids") or []]
    mastery = profile.get("knowledge_mastery") or {}
    threshold = float(profile.get("prerequisite_mastery_threshold", 0.60))
    unmet = [kp_id for kp_id in required if float(mastery.get(kp_id, 0.0)) < threshold]
    prerequisites_fit = not unmet

    minutes = resource.get("estimated_minutes")
    time_known = isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and minutes > 0
    time_fit = bool(time_known and float(minutes) <= float(profile["available_minutes"]))

    # Missing difficulty/source is a failure, never removed from the denominator.
    difficulty_adaptation_correct = bool(difficulty_fit and source_valid and prerequisites_fit)
    operational_fit = bool(difficulty_adaptation_correct and time_fit)
    reasons: list[str] = []
    if difficulty is None:
        reasons.append("difficulty_missing_or_invalid")
    elif not difficulty_fit:
        reasons.append("difficulty_outside_profile_band")
    if not source_valid:
        reasons.append("difficulty_source_missing")
    if unmet:
        reasons.append("unmet_prerequisites")
    if not time_known:
        reasons.append("estimated_minutes_missing_or_invalid")
    elif not time_fit:
        reasons.append("exceeds_time_budget")
    return {
        "resource_id": str(resource.get("resource_id") or resource.get("id") or "unknown"),
        "difficulty": difficulty,
        "target_band": [low, high],
        "difficulty_source": source or None,
        "difficulty_fit": difficulty_fit,
        "prerequisites_fit": prerequisites_fit,
        "unmet_prerequisites": unmet,
        "time_fit": time_fit,
        "difficulty_adaptation_correct": difficulty_adaptation_correct,
        "operational_fit": operational_fit,
        "reasons": reasons,
    }


def evaluate(
    records: list[dict[str, Any]],
    profiles: dict[str, dict[str, Any]],
    *,
    top_k: int,
    target: float,
    min_recommendations: int,
) -> dict[str, Any]:
    detail: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    by_profile: dict[str, dict[str, int]] = {
        profile_id: {"total": 0, "correct": 0, "operational_fit": 0}
        for profile_id in profiles
    }
    for number, record in enumerate(records, 1):
        case_id = str(record.get("case_id") or f"case-{number}")
        profile_id = str(record.get("profile_id") or "")
        if profile_id not in profiles:
            invalid.append({"case_id": case_id, "reason": "unknown_profile", "profile_id": profile_id})
            continue
        recommendations = record.get("recommendations")
        if not isinstance(recommendations, list) or not recommendations:
            invalid.append({"case_id": case_id, "reason": "recommendations_empty"})
            continue
        for rank, resource in enumerate(recommendations[:top_k], 1):
            if not isinstance(resource, dict):
                invalid.append({"case_id": case_id, "rank": rank, "reason": "resource_not_object"})
                continue
            result = evaluate_resource(resource, profiles[profile_id])
            result.update({"case_id": case_id, "profile_id": profile_id, "rank": rank})
            detail.append(result)
            stat = by_profile[profile_id]
            stat["total"] += 1
            stat["correct"] += int(result["difficulty_adaptation_correct"])
            stat["operational_fit"] += int(result["operational_fit"])

    total = len(detail)
    correct = sum(item["difficulty_adaptation_correct"] for item in detail)
    operational = sum(item["operational_fit"] for item in detail)
    missing_difficulty = sum(item["difficulty"] is None for item in detail)
    lower, upper = wilson_interval(correct, total)
    profile_reports: dict[str, Any] = {}
    all_profiles_present = True
    all_profiles_confidence_pass = True
    for profile_id, profile in profiles.items():
        stat = by_profile[profile_id]
        all_profiles_present &= stat["total"] > 0
        profile_lower, profile_upper = wilson_interval(stat["correct"], stat["total"])
        profile_confidence_pass = bool(stat["total"] and profile_lower >= target)
        all_profiles_confidence_pass &= profile_confidence_pass
        profile_reports[profile_id] = {
            "name": profile.get("name"),
            "target_band": profile.get("target_difficulty"),
            **stat,
            "accuracy": round(stat["correct"] / stat["total"], 6) if stat["total"] else None,
            "wilson_95_interval": [round(profile_lower, 6), round(profile_upper, 6)],
            "confidence_pass": profile_confidence_pass,
        }
    eligible = total >= min_recommendations and all_profiles_present and not invalid
    return {
        "metric": "learner_profile_resource_difficulty_adaptation_accuracy",
        "definition": "top-K resources satisfying profile difficulty band, difficulty provenance and prerequisites / all top-K resources",
        "domain": "中医执业医师-方剂学",
        "summary": {
            "recommendations": total,
            "correct": correct,
            "accuracy": round(correct / total, 6) if total else 0.0,
            "wilson_95_interval": [round(lower, 6), round(upper, 6)],
            "operational_fit_rate": round(operational / total, 6) if total else 0.0,
            "missing_difficulty_count": missing_difficulty,
            "metadata_completeness": round((total - missing_difficulty) / total, 6) if total else 0.0,
            "target": target,
            "minimum_recommendations": min_recommendations,
            "all_profiles_present": all_profiles_present,
            "all_profiles_confidence_pass": all_profiles_confidence_pass,
            "eligible": eligible,
            "point_estimate_pass": bool(total and correct / total >= target),
            "confidence_pass": lower >= target,
            "passed": bool(eligible and lower >= target and all_profiles_confidence_pass),
        },
        "by_profile": profile_reports,
        "invalid_cases": invalid,
        "recommendation_detail": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated profile/difficulty adaptation evaluator")
    parser.add_argument("--input", type=Path, help="Recommendation JSON/JSONL")
    parser.add_argument("--profiles", type=Path, help="Optional profile JSON")
    parser.add_argument("--output", type=Path, default=Path("codex2-report.json"))
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--target", type=float, default=0.85)
    parser.add_argument("--min-recommendations", type=int, default=180)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if not args.input and not args.demo:
        parser.error("provide --input or --demo")
    records = DEMO_RECORDS if args.demo else read_records(args.input)
    profiles = read_profiles(args.profiles)
    if len(profiles) < 3:
        raise SystemExit("at least three learner profiles are required")
    report = evaluate(
        records,
        profiles,
        top_k=max(1, args.top_k),
        target=args.target,
        min_recommendations=args.min_recommendations,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report: {args.output.resolve()}")
    return 0 if report["summary"]["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
