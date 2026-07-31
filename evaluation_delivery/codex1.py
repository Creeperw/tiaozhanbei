#!/usr/bin/env python3
"""Professional factual-error evaluator for multi-agent learning outputs.

Production method: trusted-KB retrieval + multilingual NLI.  The strict
hallucination rate treats both contradiction and unsupported factual claims as
errors.  No per-output human labels are required.

Input JSON/JSONL record:
  {"case_id":"c1", "topic":"四君子汤", "response":"...",
   "evidence":[{"source_id":"book:1", "text":"..."}]}

When ``evidence`` is omitted, ``--kb`` is searched by ``topic``.  KB files may
be JSON/JSONL and should contain ``text`` (or ``content``/``description``),
``source_id`` and optional ``topic`` fields.

Install for production:
  pip install torch transformers sentence-transformers
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_EMBED_MODEL = os.getenv(
    "EVAL_EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
)
DEFAULT_NLI_MODEL = os.getenv(
    "EVAL_NLI_MODEL", "MoritzLaurer/mDeBERTa-v3-base-mnli-xnli"
)

# A small, auditable vertical-domain fixture. Formal acceptance should point
# --kb to the approved 方剂学教材/exam-outline corpus mounted by the system.
VERTICAL_KB = [
    {
        "source_id": "DEMO_FJ:00001",
        "topic": "四君子汤",
        "text": "四君子汤由人参、白术、茯苓、炙甘草组成，功用为益气健脾，主治脾胃气虚证。",
    },
    {
        "source_id": "DEMO_FJ:00002",
        "topic": "理中丸",
        "text": "理中丸由人参、干姜、白术、炙甘草组成，功用为温中祛寒、补气健脾，用于中焦虚寒证辨析。",
    },
    {
        "source_id": "FJ_FIXTURE:003",
        "topic": "四君子汤配伍",
        "text": "四君子汤中人参为君，白术为臣，茯苓为佐，炙甘草益气和中并调和诸药。",
    },
    {
        "source_id": "FJ_FIXTURE:004",
        "topic": "四君子汤证候",
        "text": "脾胃气虚证常见面色萎白、语声低微、气短乏力、食少便溏，舌淡苔白，脉虚弱。",
    },
    {
        "source_id": "FJ_FIXTURE:005",
        "topic": "理中丸证候",
        "text": "理中丸所治中焦虚寒证可见脘腹绵绵作痛、呕吐、食少、便溏，口不渴，舌淡苔白，脉沉细。",
    },
]

DEMO_RECORDS = [
    {
        "case_id": "FJ_FACT_001",
        "topic": "四君子汤",
        "response": "四君子汤由人参、白术、茯苓、炙甘草组成。其功用是益气健脾，主治脾胃气虚证。",
    },
    {
        "case_id": "FJ_FACT_002",
        "topic": "理中丸",
        "response": "理中丸由人参、干姜、白术、炙甘草组成，具有温中祛寒、补气健脾之功。",
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
        raise ValueError(f"{path} must contain a JSON list or JSONL records")
    return [item for item in value if isinstance(item, dict)]


def evidence_text(item: dict[str, Any]) -> str:
    return str(
        item.get("text")
        or item.get("content")
        or item.get("description")
        or item.get("content_summary")
        or ""
    ).strip()


def split_claims(text: str) -> list[str]:
    """Split output into short factual claims and discard UI/meta language."""
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    pieces = re.split(r"[。！？!?；;\n]+", text)
    claims: list[str] = []
    non_factual = re.compile(r"^(建议|请|可以|接下来|学习目标|小结|注意|练习|问题|参考|来源)[:：]?")
    for piece in pieces:
        claim = re.sub(r"^\s*(?:[-*•]|\d+[.)、])\s*", "", piece).strip(" ：:")
        if len(claim) < 6 or non_factual.match(claim):
            continue
        claims.append(claim)
    return claims


def wilson_interval(errors: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 1.0
    p = errors / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


@dataclass
class PairScore:
    entailment: float
    contradiction: float
    neutral: float


class NliEngine:
    def __init__(self, embed_model: str, nli_model: str) -> None:
        try:
            import torch
            from sentence_transformers import SentenceTransformer
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:
            raise RuntimeError(
                "NLI engine requires torch, transformers and sentence-transformers"
            ) from exc
        self.torch = torch
        self.embedder = SentenceTransformer(embed_model)
        self.tokenizer = AutoTokenizer.from_pretrained(nli_model)
        self.model = AutoModelForSequenceClassification.from_pretrained(nli_model)
        self.model.eval()
        self.labels = {
            int(key): str(value).lower()
            for key, value in self.model.config.id2label.items()
        }

    def similarities(self, query: str, texts: list[str]) -> list[float]:
        vectors = self.embedder.encode(
            [query, *texts], normalize_embeddings=True, convert_to_numpy=True
        )
        return [float(vectors[0] @ vector) for vector in vectors[1:]]

    def nli(self, premise: str, hypothesis: str) -> PairScore:
        encoded = self.tokenizer(
            premise, hypothesis, return_tensors="pt", truncation=True, max_length=512
        )
        with self.torch.inference_mode():
            probabilities = self.torch.softmax(self.model(**encoded).logits[0], dim=-1)
        mapped = {"entailment": 0.0, "contradiction": 0.0, "neutral": 0.0}
        for index, probability in enumerate(probabilities.tolist()):
            label = self.labels.get(index, f"label_{index}")
            for canonical in mapped:
                if canonical in label:
                    mapped[canonical] = float(probability)
        if not any(mapped.values()):
            raise RuntimeError(f"NLI model exposes unsupported labels: {self.labels}")
        return PairScore(**mapped)


class LexicalSmokeEngine:
    """Deterministic offline smoke test; deliberately not acceptance-grade."""

    @staticmethod
    def similarities(query: str, texts: list[str]) -> list[float]:
        terms = set(re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z0-9]+", query))
        return [
            len(terms & set(re.findall(r"[\u4e00-\u9fff]{2,6}|[A-Za-z0-9]+", text)))
            / max(1, len(terms))
            for text in texts
        ]

    @staticmethod
    def nli(premise: str, hypothesis: str) -> PairScore:
        tokens = set(re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z0-9]+", hypothesis))
        overlap = sum(token in premise for token in tokens) / max(1, len(tokens))
        return PairScore(entailment=overlap, contradiction=0.0, neutral=1.0 - overlap)


def select_evidence(record: dict[str, Any], kb: list[dict[str, Any]]) -> list[dict[str, Any]]:
    supplied = record.get("evidence")
    if isinstance(supplied, list) and supplied:
        return [item for item in supplied if isinstance(item, dict) and evidence_text(item)]
    topic = str(record.get("topic") or record.get("query") or "").strip()
    if not topic:
        return kb
    exact = [item for item in kb if topic in str(item.get("topic") or evidence_text(item))]
    return exact or kb


def evaluate(
    records: list[dict[str, Any]],
    kb: list[dict[str, Any]],
    engine: Any,
    *,
    top_k: int,
    retrieval_threshold: float,
    entailment_threshold: float,
    contradiction_threshold: float,
    target: float,
    min_claims: int,
    certifiable: bool,
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    counts = {"supported": 0, "contradicted": 0, "unsupported": 0}
    for row_number, record in enumerate(records, 1):
        case_id = str(record.get("case_id") or f"case-{row_number}")
        response = str(record.get("response") or record.get("output") or "").strip()
        if not response:
            details.append({"case_id": case_id, "status": "invalid", "reason": "empty_response"})
            continue
        evidence = select_evidence(record, kb)
        texts = [evidence_text(item) for item in evidence]
        if not texts:
            details.append({"case_id": case_id, "status": "invalid", "reason": "no_evidence"})
            continue
        for claim in split_claims(response):
            similarities = engine.similarities(claim, texts)
            ranked = sorted(range(len(texts)), key=lambda i: similarities[i], reverse=True)[:top_k]
            candidates = [i for i in ranked if similarities[i] >= retrieval_threshold]
            best_entail = (0.0, None, None)
            best_contra = (0.0, None, None)
            for index in candidates:
                score = engine.nli(texts[index], claim)
                if score.entailment > best_entail[0]:
                    best_entail = (score.entailment, index, score)
                if score.contradiction > best_contra[0]:
                    best_contra = (score.contradiction, index, score)
            if best_entail[0] >= entailment_threshold:
                verdict = "supported"
                chosen = best_entail
            elif best_contra[0] >= contradiction_threshold and best_contra[0] > best_entail[0]:
                verdict = "contradicted"
                chosen = best_contra
            else:
                verdict = "unsupported"
                chosen = best_entail
            counts[verdict] += 1
            chosen_index = chosen[1]
            details.append(
                {
                    "case_id": case_id,
                    "claim": claim,
                    "verdict": verdict,
                    "retrieval_score": round(max(similarities, default=0.0), 4),
                    "entailment": round(best_entail[0], 4),
                    "contradiction": round(best_contra[0], 4),
                    "source_id": (
                        evidence[chosen_index].get("source_id") if chosen_index is not None else None
                    ),
                }
            )
    total = sum(counts.values())
    strict_errors = counts["contradicted"] + counts["unsupported"]
    strict_rate = strict_errors / total if total else 1.0
    lower, upper = wilson_interval(strict_errors, total)
    point_pass = strict_rate < target
    confidence_pass = upper < target
    eligible = certifiable and total >= min_claims
    return {
        "metric": "professional_knowledge_hallucination_rate",
        "definition": "(contradicted + unsupported factual claims) / factual claims",
        "domain": "中医执业医师-方剂学",
        "engine": "nli" if certifiable else "lexical_smoke_only",
        "summary": {
            "claims": total,
            **counts,
            "strict_hallucination_rate": round(strict_rate, 6),
            "contradiction_rate": round(counts["contradicted"] / total, 6) if total else 1.0,
            "wilson_95_interval": [round(lower, 6), round(upper, 6)],
            "target": target,
            "minimum_claims": min_claims,
            "point_estimate_pass": point_pass,
            "confidence_pass": confidence_pass,
            "certifiable": eligible,
            "passed": bool(eligible and confidence_pass),
        },
        "invalid_cases": [item for item in details if item.get("status") == "invalid"],
        "claims_detail": [item for item in details if "claim" in item],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Automated factual-error evaluator")
    parser.add_argument("--input", type=Path, help="Agent output JSON/JSONL")
    parser.add_argument("--kb", type=Path, help="Trusted KB JSON/JSONL")
    parser.add_argument("--output", type=Path, default=Path("codex1-report.json"))
    parser.add_argument("--engine", choices=("nli", "lexical"), default="nli")
    parser.add_argument("--embed-model", default=DEFAULT_EMBED_MODEL)
    parser.add_argument("--nli-model", default=DEFAULT_NLI_MODEL)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--retrieval-threshold", type=float, default=0.35)
    parser.add_argument("--entailment-threshold", type=float, default=0.70)
    parser.add_argument("--contradiction-threshold", type=float, default=0.70)
    parser.add_argument("--target", type=float, default=0.05)
    parser.add_argument("--min-claims", type=int, default=100)
    parser.add_argument("--demo", action="store_true", help="Use built-in vertical fixture")
    args = parser.parse_args()

    if not args.input and not args.demo:
        parser.error("provide --input or --demo")
    records = DEMO_RECORDS if args.demo else read_records(args.input)
    kb = read_records(args.kb) if args.kb else VERTICAL_KB
    if not records:
        raise SystemExit("no evaluation records")
    if not kb:
        raise SystemExit("trusted knowledge base is empty")
    if args.engine == "nli":
        engine = NliEngine(args.embed_model, args.nli_model)
        certifiable = True
    else:
        engine = LexicalSmokeEngine()
        certifiable = False
    report = evaluate(
        records,
        kb,
        engine,
        top_k=max(1, args.top_k),
        retrieval_threshold=args.retrieval_threshold,
        entailment_threshold=args.entailment_threshold,
        contradiction_threshold=args.contradiction_threshold,
        target=args.target,
        min_claims=args.min_claims,
        certifiable=certifiable,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"report: {args.output.resolve()}")
    return 0 if report["summary"]["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
