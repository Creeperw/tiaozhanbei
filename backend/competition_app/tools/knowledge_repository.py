from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from competition_app.contracts.knowledge import QuestionBridge
from competition_app.tools.knowledge_assets import KnowledgeAssetPaths, KnowledgeAssetRepository


@dataclass(frozen=True)
class KnowledgeRepositoryPaths(KnowledgeAssetPaths):
    questions: Path
    question_kp_matches: Path

    @classmethod
    def from_delivery_root(cls, root: Path) -> "KnowledgeRepositoryPaths":
        base = KnowledgeAssetPaths.from_delivery_root(root)
        formatted_questions = root / "01_question_bank" / "formatted_questions.json"
        legacy_questions = root / "01_question_bank" / "final_questions.json"
        return cls(
            knowledge_points=base.knowledge_points,
            kp_chunk_links=base.kp_chunk_links,
            source_chunks=base.source_chunks,
            questions=(formatted_questions if formatted_questions.is_file() else legacy_questions),
            question_kp_matches=root / "05_bridge" / "question_kp_all_matches.jsonl",
        )


class KnowledgeRepository(KnowledgeAssetRepository):
    def __init__(self, paths: KnowledgeRepositoryPaths) -> None:
        super().__init__(paths)
        self.paths = paths
        self._questions_by_id: dict[str, dict[str, object]] | None = None
        self._bridges_by_question: dict[str, list[QuestionBridge]] | None = None
        self._bridges_by_kp: dict[str, list[QuestionBridge]] | None = None
        self._question_ids_by_kp: dict[str, list[str]] | None = None
        self.invalid_bridge_count = 0

    def get_question(self, question_id: str) -> dict[str, object]:
        try:
            return self._load_questions()[question_id]
        except KeyError as exc:
            raise KeyError(f"unknown question: {question_id}") from exc

    def bridges_for_kp(self, kp_id: str) -> list[QuestionBridge]:
        self._load_question_bridges()
        assert self._bridges_by_kp is not None
        return list(self._bridges_by_kp.get(kp_id, []))

    def bridges_for_question(self, question_id: str) -> list[QuestionBridge]:
        self._load_question_bridges()
        assert self._bridges_by_question is not None
        return list(self._bridges_by_question.get(question_id, []))

    def question_ids_for_kp(self, kp_id: str) -> list[str]:
        self._load_question_bridges()
        assert self._question_ids_by_kp is not None
        return list(self._question_ids_by_kp.get(kp_id, []))

    def _load_questions(self) -> dict[str, dict[str, object]]:
        if self._questions_by_id is None:
            with self.paths.questions.open("r", encoding="utf-8") as handle:
                rows = json.load(handle)
            if not isinstance(rows, list):
                raise ValueError("question file must contain a JSON array")
            self._questions_by_id = {
                str(row.get("question_id") or row.get("题目id")): row for row in rows
            }
        return self._questions_by_id

    def _load_question_bridges(self) -> None:
        if self._bridges_by_question is not None:
            return
        questions = self._load_questions()
        known_kps = {str(item["kp_id"]) for item in self._load_knowledge_points()}
        known_chunks = set(self._load_chunks())
        by_question: dict[str, list[QuestionBridge]] = defaultdict(list)
        by_kp: dict[str, list[QuestionBridge]] = defaultdict(list)
        question_ids_by_kp: dict[str, set[str]] = defaultdict(set)
        invalid = 0
        if self.paths.question_kp_matches.is_file():
            rows = self._iter_jsonl(self.paths.question_kp_matches)
            for row in rows:
                question_id = str(row.get("题目id", ""))
                kp_id = str(row.get("kp_id", ""))
                chunk_uid = str(row.get("evidence_chunk_uid", ""))
                if question_id not in questions or kp_id not in known_kps or chunk_uid not in known_chunks:
                    invalid += 1
                    continue
                bridge = QuestionBridge(
                    kp_id=kp_id,
                    bridge_layer=str(row["bridge_layer"]),
                    relation=str(row["relation"]),
                    confidence=float(row["confidence"]),
                    rank=int(row["rank"]),
                    evidence_chunk_uid=chunk_uid,
                    match_method=str(row["match_method"]),
                )
                by_question[question_id].append(bridge)
                by_kp[kp_id].append(bridge)
                question_ids_by_kp[kp_id].add(question_id)
        else:
            links_by_kp = self._load_links()
            for question_id, question in questions.items():
                for rank, raw_kp_id in enumerate(question.get("kp_ids", []), start=1):
                    kp_id = str(raw_kp_id)
                    links = links_by_kp.get(kp_id, [])
                    chunk_uid = str(links[0].get("chunk_uid", "")) if links else ""
                    if kp_id not in known_kps or chunk_uid not in known_chunks:
                        invalid += 1
                        continue
                    bridge = QuestionBridge(
                        kp_id=kp_id,
                        bridge_layer="strict",
                        relation="primary",
                        confidence=1.0,
                        rank=rank,
                        evidence_chunk_uid=chunk_uid,
                        match_method="question_kp_ids",
                    )
                    by_question[question_id].append(bridge)
                    by_kp[kp_id].append(bridge)
                    question_ids_by_kp[kp_id].add(question_id)
        self.invalid_bridge_count = invalid
        self._bridges_by_question = dict(by_question)
        self._bridges_by_kp = dict(by_kp)
        self._question_ids_by_kp = {
            kp_id: sorted(question_ids) for kp_id, question_ids in question_ids_by_kp.items()
        }