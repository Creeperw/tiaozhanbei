import json
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from APP.backend.database import (
    QuestionBankItem,
    QuestionKPLinkRecord,
    QuestionVersionRecord,
    VariationQuestionVersionRecord,
)


@dataclass(frozen=True)
class QuestionSelectionCriteria:
    kp_ids: tuple[str, ...]
    type_difficulty_counts: tuple[tuple[str, int, int], ...]
    exclude_question_ids: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "kp_ids", tuple(self.kp_ids))
        object.__setattr__(
            self,
            "type_difficulty_counts",
            tuple(tuple(quota) for quota in self.type_difficulty_counts),
        )
        object.__setattr__(self, "exclude_question_ids", tuple(self.exclude_question_ids))


@dataclass(frozen=True)
class QuestionVersionView:
    question_version_id: str
    question_id: str
    question_type: str
    stem: str
    answer: str
    analysis: str
    kp_ids: tuple[str, ...]
    standard_difficulty: int
    source_kind: str

    def __post_init__(self):
        object.__setattr__(self, "kp_ids", tuple(self.kp_ids))


@dataclass(frozen=True)
class QuestionShortage:
    criteria: QuestionSelectionCriteria
    requested_count: int
    available_count: int


class QuestionRepository:
    def __init__(self, session_factory: Callable[[], Session]):
        self._session_factory = session_factory

    def select(self, criteria: QuestionSelectionCriteria):
        requested_count = sum(count for _, _, count in criteria.type_difficulty_counts)
        excluded_ids = set(criteria.exclude_question_ids)
        requested_kps = set(criteria.kp_ids)
        selected = []
        selected_ids = set()
        session = self._session_factory()
        try:
            question_types = tuple(dict.fromkeys(
                question_type for question_type, _, _ in criteria.type_difficulty_counts
            ))
            authoritative_question_ids = {
                question_id for question_id, in session.query(QuestionVersionRecord.question_id).filter(
                    ~QuestionVersionRecord.question_version_id.in_(
                        session.query(VariationQuestionVersionRecord.question_version_id)
                    )
                ).all()
            }
            links_by_version = {}
            for question_version_id, kp_id in session.query(
                QuestionKPLinkRecord.question_version_id,
                QuestionKPLinkRecord.kp_id,
            ).filter(QuestionKPLinkRecord.status == "active").order_by(
                QuestionKPLinkRecord.question_version_id.asc(),
                QuestionKPLinkRecord.kp_id.asc(),
            ).all():
                links_by_version.setdefault(question_version_id, []).append(kp_id)
            active_versions = session.query(QuestionVersionRecord).filter(
                QuestionVersionRecord.status == "active",
                ~QuestionVersionRecord.question_version_id.in_(
                    session.query(VariationQuestionVersionRecord.question_version_id)
                ),
            ).order_by(
                QuestionVersionRecord.question_id.asc(),
                QuestionVersionRecord.question_version_id.asc(),
            ).all()
            authoritative_items = []
            canonical_question_ids = set()
            for version in active_versions:
                if version.question_id in canonical_question_ids:
                    continue
                canonical_question_ids.add(version.question_id)
                if version.question_version_id in links_by_version:
                    version._active_kp_ids = tuple(links_by_version[version.question_version_id])
                    authoritative_items.append(version)
            legacy_items = session.query(QuestionBankItem).filter(
                QuestionBankItem.status == "active",
                QuestionBankItem.question_type.in_(question_types),
                ~QuestionBankItem.question_id.in_(authoritative_question_ids),
            ).order_by(QuestionBankItem.question_id.asc()).all()
            items = authoritative_items + legacy_items
            items.sort(key=lambda item: (item.question_id, self._version_id(item)))
            for question_type, difficulty, count in criteria.type_difficulty_counts:
                candidates = []
                candidate_ids = set()
                for item in items:
                    if (
                        item.question_type != question_type
                        or int(self._difficulty(item)) != difficulty
                        or item.question_id in excluded_ids
                        or item.question_id in selected_ids
                        or item.question_id in candidate_ids
                    ):
                        continue
                    candidates.append(item)
                    candidate_ids.add(item.question_id)
                strict = [item for item in candidates if requested_kps.issubset(self._kp_ids(item))]
                primary = [item for item in candidates if item not in strict and requested_kps & set(self._kp_ids(item))]
                for item in (strict + primary)[:count]:
                    selected.append(self._to_view(item))
                    selected_ids.add(item.question_id)
        finally:
            session.close()
        if len(selected) != requested_count:
            return QuestionShortage(criteria, requested_count, len(selected))
        return tuple(selected)

    def learner_snapshot(self, selected):
        if isinstance(selected, QuestionShortage):
            return selected
        return tuple({
            "question_version_id": item.question_version_id,
            "question_id": item.question_id,
            "question_type": item.question_type,
            "stem": item.stem,
            "kp_ids": item.kp_ids,
            "standard_difficulty": item.standard_difficulty,
            "source_kind": item.source_kind,
        } for item in selected)

    @staticmethod
    def _version_id(item):
        if isinstance(item, QuestionVersionRecord):
            return item.question_version_id
        return f"{item.question_id}:v1"

    @staticmethod
    def _difficulty(item):
        if isinstance(item, QuestionVersionRecord):
            return item.standard_difficulty
        return item.difficulty

    @staticmethod
    def _kp_ids(item):
        if isinstance(item, QuestionVersionRecord):
            return item._active_kp_ids
        try:
            value = json.loads(item.kp_ids_json or "[]")
        except (TypeError, ValueError):
            return ()
        return tuple(value) if isinstance(value, list) and all(isinstance(kp_id, str) for kp_id in value) else ()

    def _to_view(self, item):
        if isinstance(item, QuestionVersionRecord):
            return QuestionVersionView(
                question_version_id=item.question_version_id,
                question_id=item.question_id,
                question_type=item.question_type,
                stem=item.stem or "",
                answer=item.answer or "",
                analysis=item.analysis or "",
                kp_ids=self._kp_ids(item),
                standard_difficulty=int(item.standard_difficulty),
                source_kind=item.source_kind or "manual",
            )
        return QuestionVersionView(
            question_version_id=f"{item.question_id}:v1",
            question_id=item.question_id,
            question_type=item.question_type,
            stem=item.stem or "",
            answer=item.answer or "",
            analysis=item.analysis or "",
            kp_ids=self._kp_ids(item),
            standard_difficulty=int(item.difficulty),
            source_kind=item.source or "manual",
        )
