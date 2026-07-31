#!/usr/bin/env python3
"""Core-knowledge-point coverage evaluator for generated learning resources.

Coverage is measured against a frozen, weighted vertical-domain blueprint.  A
point is covered by an explicit kp_id or by semantic similarity between its
canonical statement and a generated text segment.  This requires no manual
labeling of generated outputs.

Input JSON/JSONL record:
  {"case_id":"c1", "required_kp_ids":["FJ_SJZT_01", "FJ_SJZT_02"],
   "response":"...", "covered_kp_ids":[]}

Install for semantic mode:
  pip install sentence-transformers
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any


DEFAULT_EMBED_MODEL = os.getenv(
    "EVAL_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
)

# Frozen vertical blueprint: 中医执业医师-方剂学 (四君子汤/理中丸).
# In formal testing, replace/extend it with the versioned official outline and
# approved textbook mapping via --blueprint; do not derive the target list from
# the generated content itself.
BLUEPRINT = [
    {"kp_id": "FJ_SJZT_01", "topic": "四君子汤", "name": "组成", "statement": "四君子汤由人参、白术、茯苓、炙甘草组成", "aliases": ["四味药组成"], "weight": 3},
    {"kp_id": "FJ_SJZT_02", "topic": "四君子汤", "name": "功用", "statement": "四君子汤的功用是益气健脾", "aliases": ["益气健脾"], "weight": 3},
    {"kp_id": "FJ_SJZT_03", "topic": "四君子汤", "name": "主治", "statement": "四君子汤主治脾胃气虚证", "aliases": ["脾胃气虚"], "weight": 3},
    {"kp_id": "FJ_SJZT_04", "topic": "四君子汤", "name": "配伍结构", "statement": "四君子汤以人参为君、白术为臣、茯苓为佐、炙甘草调和诸药", "aliases": ["君臣佐使", "配伍"], "weight": 2},
    {"kp_id": "FJ_SJZT_05", "topic": "四君子汤", "name": "证候表现", "statement": "脾胃气虚证可见气短乏力、食少便溏、面色萎白、舌淡苔白、脉虚弱", "aliases": ["气短乏力", "食少便溏"], "weight": 2},
    {"kp_id": "FJ_SJZT_06", "topic": "四君子汤", "name": "辨析", "statement": "四君子汤辨证核心是脾胃气虚，不以中焦虚寒为主要证机", "aliases": ["方证辨析"], "weight": 2},
    {"kp_id": "FJ_LZW_01", "topic": "理中丸", "name": "组成", "statement": "理中丸由人参、干姜、白术、炙甘草组成", "aliases": ["理中丸四味药"], "weight": 3},
    {"kp_id": "FJ_LZW_02", "topic": "理中丸", "name": "功用", "statement": "理中丸的功用是温中祛寒、补气健脾", "aliases": ["温中祛寒", "补气健脾"], "weight": 3},
    {"kp_id": "FJ_LZW_03", "topic": "理中丸", "name": "主治", "statement": "理中丸用于中焦虚寒证的辨析", "aliases": ["中焦虚寒"], "weight": 3},
    {"kp_id": "FJ_LZW_04", "topic": "理中丸", "name": "核心药物", "statement": "理中丸以干姜温中祛寒为核心配伍之一", "aliases": ["干姜", "温中"], "weight": 2},
    {"kp_id": "FJ_LZW_05", "topic": "理中丸", "name": "证候表现", "statement": "中焦虚寒可见脘腹绵绵作痛、呕吐、食少、便溏、口不渴", "aliases": ["脘腹绵绵作痛", "便溏"], "weight": 2},
    {"kp_id": "FJ_LZW_06", "topic": "理中丸", "name": "辨析", "statement": "理中丸证偏于中焦虚寒，四君子汤证偏于脾胃气虚", "aliases": ["四君子汤与理中丸辨析"], "weight": 2},
]

DEMO_RECORDS = [
    {
        "case_id": "FJ_COVERAGE_SJZT",
        "required_kp_ids": [f"FJ_SJZT_0{i}" for i in range(1, 7)],
        "response": "四君子汤由人参、白术、茯苓、炙甘草组成，益气健脾，主治脾胃气虚证。配伍上人参为君、白术为臣、茯苓为佐，炙甘草调和诸药。常见气短乏力、食少便溏、面色萎白、舌淡苔白、脉虚弱。辨证核心是脾胃气虚，应与中焦虚寒相区别。",
    },
    {
        "case_id": "FJ_COVERAGE_LZW",
        "required_kp_ids": [f"FJ_LZW_0{i}" for i in range(1, 7)],
        "response": "理中丸由人参、干姜、白术、炙甘草组成，功用为温中祛寒、补气健脾，用于中焦虚寒。干姜是温中的核心药物。可见脘腹绵绵作痛、呕吐、食少、便溏、口不渴。理中丸偏中焦虚寒，四君子汤偏脾胃气虚。",
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


def read_blueprint(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return BLUEPRINT
    return read_records(path)


def split_segments(text: str) -> list[str]:
    pieces = re.split(r"[。！？!?；;\n]+", text)
    return [piece.strip() for piece in pieces if len(piece.strip()) >= 4]


class SemanticEngine:
    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("semantic mode requires sentence-transformers") from exc
        self.model = SentenceTransformer(model_name)

    def max_similarity(self, statement: str, segments: list[str]) -> tuple[float, str | None]:
        if not segments:
            return 0.0, None
        vectors = self.model.encode(
            [statement, *segments], normalize_embeddings=True, convert_to_numpy=True
        )
        scores = [float(vectors[0] @ vector) for vector in vectors[1:]]
        index = max(range(len(scores)), key=scores.__getitem__)
        return scores[index], segments[index]


class LexicalSmokeEngine:
    @staticmethod
    def max_similarity(statement: str, segments: list[str]) -> tuple[float, str | None]:
        # Character bigrams are deterministic and work for smoke tests only.
        def grams(value: str) -> set[str]:
            compact = re.sub(r"\s+", "", value)
            return {compact[i : i + 2] for i in range(max(0, len(compact) - 1))}
        expected = grams(statement)
        scores = [len(expected & grams(segment)) / max(1, len(expected)) for segment in segments]
        if not scores:
            return 0.0, None
        index = max(range(len(scores)), key=scores.__getitem__)
        return scores[index], segments[index]


def validate_blueprint(blueprint: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for item in blueprint:
        kp_id = str(item.get("kp_id") or "").strip()
        if not kp_id or kp_id in index:
            raise ValueError(f"blueprint kp_id missing or duplicated: {kp_id!r}")
        if not str(item.get("statement") or "").strip():
            raise ValueError(f"blueprint statement missing for {kp_id}")
        weight = float(item.get("weight", 1.0))
        if weight <= 0:
            raise ValueError(f"blueprint weight must be positive for {kp_id}")
        index[kp_id] = {**item, "weight": weight}
    return index


def evaluate(
    records: list[dict[str, Any]],
    blueprint: list[dict[str, Any]],
    engine: Any,
    *,
    semantic_threshold: float,
    target: float,
    min_required_points: int,
    certifiable: bool,
) -> dict[str, Any]:
    bp = validate_blueprint(blueprint)
    detail: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    total_weight = 0.0
    covered_weight = 0.0
    required_unique: set[str] = set()
    covered_unique: set[str] = set()
    by_case: dict[str, dict[str, float]] = {}
    for number, record in enumerate(records, 1):
        case_id = str(record.get("case_id") or f"case-{number}")
        response = str(record.get("response") or record.get("output") or "").strip()
        required = [str(value) for value in record.get("required_kp_ids") or []]
        explicit = {str(value) for value in record.get("covered_kp_ids") or []}
        unknown = [kp_id for kp_id in required if kp_id not in bp]
        if not response and not explicit:
            invalid.append({"case_id": case_id, "reason": "empty_response"})
            continue
        if not required:
            invalid.append({"case_id": case_id, "reason": "required_kp_ids_empty"})
            continue
        if unknown:
            invalid.append({"case_id": case_id, "reason": "unknown_required_kp_ids", "kp_ids": unknown})
            continue
        segments = split_segments(response)
        case_total_weight = 0.0
        case_covered_weight = 0.0
        for kp_id in required:
            item = bp[kp_id]
            weight = float(item["weight"])
            required_unique.add(kp_id)
            total_weight += weight
            case_total_weight += weight
            if kp_id in explicit:
                score, matched, method = 1.0, None, "explicit_kp_id"
                covered = True
            else:
                statement = str(item["statement"])
                score, matched = engine.max_similarity(statement, segments)
                aliases = [str(value) for value in item.get("aliases") or []]
                alias_hit = next((alias for alias in aliases if alias and alias in response), None)
                # An alias alone is evidence of topical presence; require a modest
                # semantic score too so a heading is not counted as full coverage.
                covered = score >= semantic_threshold or bool(alias_hit and score >= semantic_threshold - 0.12)
                method = "semantic" if score >= semantic_threshold else ("alias_plus_semantic" if covered else "not_covered")
            if covered:
                covered_weight += weight
                case_covered_weight += weight
                covered_unique.add(kp_id)
            detail.append(
                {
                    "case_id": case_id,
                    "kp_id": kp_id,
                    "topic": item.get("topic"),
                    "name": item.get("name"),
                    "weight": weight,
                    "covered": covered,
                    "method": method,
                    "similarity": round(score, 4),
                    "matched_segment": matched,
                }
            )
        by_case[case_id] = {
            "required_weight": round(case_total_weight, 4),
            "covered_weight": round(case_covered_weight, 4),
            "coverage_rate": round(
                case_covered_weight / case_total_weight if case_total_weight else 0.0,
                6,
            ),
        }
    coverage = covered_weight / total_weight if total_weight else 0.0
    eligible = certifiable and len(required_unique) >= min_required_points and not invalid
    all_cases_pass = bool(by_case) and all(
        item["coverage_rate"] >= target for item in by_case.values()
    )
    return {
        "metric": "weighted_core_knowledge_point_coverage_rate",
        "definition": "sum(weights of covered required KPs) / sum(weights of all required KPs)",
        "domain": "中医执业医师-方剂学",
        "engine": "semantic" if certifiable else "lexical_smoke_only",
        "summary": {
            "required_unique_points": len(required_unique),
            "covered_unique_points": len(covered_unique),
            "required_weight": round(total_weight, 4),
            "covered_weight": round(covered_weight, 4),
            "coverage_rate": round(coverage, 6),
            "target": target,
            "minimum_required_points": min_required_points,
            "point_estimate_pass": coverage >= target,
            "all_cases_pass": all_cases_pass,
            "certifiable": eligible,
            "passed": bool(eligible and coverage >= target and all_cases_pass),
        },
        "by_case": by_case,
        "invalid_cases": invalid,
        "gaps": [item for item in detail if not item["covered"]],
        "point_detail": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated core-KP coverage evaluator")
    parser.add_argument("--input", type=Path, help="Generated output JSON/JSONL")
    parser.add_argument("--blueprint", type=Path, help="Versioned core-KP blueprint")
    parser.add_argument("--output", type=Path, default=Path("codex3-report.json"))
    parser.add_argument("--engine", choices=("semantic", "lexical"), default="semantic")
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    parser.add_argument("--semantic-threshold", type=float, default=0.72)
    parser.add_argument("--target", type=float, default=0.90)
    parser.add_argument("--min-required-points", type=int, default=30)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if not args.input and not args.demo:
        parser.error("provide --input or --demo")
    records = DEMO_RECORDS if args.demo else read_records(args.input)
    blueprint = read_blueprint(args.blueprint)
    if args.engine == "semantic":
        engine = SemanticEngine(args.embed_model)
        certifiable = True
    else:
        engine = LexicalSmokeEngine()
        certifiable = False
    report = evaluate(
        records,
        blueprint,
        engine,
        semantic_threshold=args.semantic_threshold,
        target=args.target,
        min_required_points=args.min_required_points,
        certifiable=certifiable,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report: {args.output.resolve()}")
    return 0 if report["summary"]["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
