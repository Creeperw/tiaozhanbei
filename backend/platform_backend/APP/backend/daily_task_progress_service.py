import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from APP.backend.database import (
    DailyTaskInstanceRecord,
    DailyTaskItemRecord,
    DailyTaskQuestionSnapshotRecord,
    DailyTaskVideoEvidenceRecord,
    KnowledgePoint,
    LearningKnowledgePoint,
    LearningQuestion,
    LearningQuestionAttempt,
    LearningUserProfile,
    QuestionAttempt,
    QuestionBankItem,
    QuestionKPLinkRecord,
    QuestionVersionRecord,
)

TERMINAL_AUDIT_DECISIONS = {"pass", "revise", "reject"}
NON_TERMINAL_AUDIT_DECISIONS = {"pending", "needs_human_review", "human_review"}
FORMAL_QUESTION_SOURCE_PREFIX = "formal-content:"
KNOWLEDGE_ATLAS_SOURCE = "formal-content:knowledge-atlas-2026-07-18"
SYSTEM_AUDITED_SOURCE_KINDS = {"agent_audited_paper"}
_QUESTION_TYPES = {
    "单项选择题": "single_choice",
    "单选题": "single_choice",
    "多项选择题": "multiple_choice",
    "多选题": "multiple_choice",
    "判断题": "true_false",
    "填空题": "fill_blank",
    "名词解释": "term_explanation",
    "简答题": "short_answer",
    "案例分析/实验报告": "case_quiz",
    "临床病例问答": "case_quiz",
}

# --- 每日测验（Daily Quiz） ---
# 每日任务在固定知识点练习之外追加一小段“今日测验”：按用户当前学习
# 情况（答题准确率）与用户画像选择 10-15 道跨知识点题目，并按真实标注
# 难度（standard_difficulty 1-5）分层。难度数据缺失时保持无标注（不推断）。
QUIZ_DEFAULT_TARGET_COUNT = 12
QUIZ_MIN_TARGET_COUNT = 10
QUIZ_MAX_TARGET_COUNT = 15

# 难度画像：difficulty -> 期望题数。总和为目标题数。
# advanced   准确率高（>=0.8）：难题为主，检验拔高。
# balanced   中等：各难度均衡。
# foundation 基础/无证据：基础为主，先建立信心。
QUIZ_DIFFICULTY_PROFILES: dict[str, dict[int, int]] = {
    "advanced": {1: 1, 2: 2, 3: 3, 4: 4, 5: 2},
    "balanced": {1: 2, 2: 3, 3: 4, 4: 2, 5: 1},
    "foundation": {1: 3, 2: 4, 3: 3, 4: 1, 5: 1},
}

# 非测验类知识点配套练习固定使用 3 题难度模板。这里仅消费题库中已经
# 审核落库的 standard_difficulty，不推断、补写或改造题目难度。
KNOWLEDGE_PRACTICE_DIFFICULTY_TARGETS: dict[str, tuple[int, ...]] = {
    "foundation": (1, 1, 2),
    "balanced": (2, 3, 4),
    "advanced": (3, 4, 5),
}

# 目标难度题不足时，先从该画像的其他已标注题中稳定补齐；无标签题仅是
# 最后的执行兜底。顺序是确定性的，确保相同题库状态得到相同结果。
KNOWLEDGE_PRACTICE_DIFFICULTY_FALLBACKS: dict[str, tuple[int, ...]] = {
    "foundation": (1, 2, 3, 4, 5),
    "balanced": (3, 2, 4, 1, 5),
    "advanced": (5, 4, 3, 2, 1),
}

# 用户画像（user_profile.user_group_json / constitution）中出现的分组关键词
# 到基础难度倾向的映射，仅在没有任何答题记录时兜底使用。
_QUIZ_GROUP_PROFILE_HINTS: dict[str, str] = {
    "大众兴趣": "foundation",
    "入门": "foundation",
    "科普": "foundation",
    "跨专业": "balanced",
    "学历教育": "balanced",
    "进阶": "advanced",
    "应试": "advanced",
}


def _trusted_formal_source(value: Any) -> bool:
    source = str(value or "").strip()
    return (
        source.startswith(FORMAL_QUESTION_SOURCE_PREFIX)
        or source == "formal_question_bank"
        or source.startswith("formal-vector-question-bank:")
        or source in SYSTEM_AUDITED_SOURCE_KINDS
    )


class DailyTaskProgressError(RuntimeError):
    def __init__(self, message: str, code: int = 400):
        super().__init__(message)
        self.code = code


def _typed_int(value: Any, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise DailyTaskProgressError(f"{field_name} must be an integer", 400) from exc


def _json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise DailyTaskProgressError(f"{field_name} is invalid", 409) from exc
        if isinstance(decoded, dict):
            return decoded
    if value is None:
        return {}
    raise DailyTaskProgressError(f"{field_name} is invalid", 409)


def _finite_float(value: Any, *, field_name: str, code: int = 400) -> float:
    if isinstance(value, bool):
        raise DailyTaskProgressError(f"{field_name} must be a finite number", code)
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise DailyTaskProgressError(f"{field_name} must be a finite number", code) from exc
    if not math.isfinite(number):
        raise DailyTaskProgressError(f"{field_name} must be a finite number", code)
    return number


def _authoritative_video_spec(item: DailyTaskItemRecord) -> tuple[str, float, float, float]:
    if item.item_kind != "video":
        raise DailyTaskProgressError("task item is not a video", 409)

    resource_ref = _json_object(item.resource_ref, field_name="resource_ref")
    completion_policy = _json_object(item.completion_policy, field_name="completion_policy")
    policy = str(completion_policy.get("policy") or "")
    modes = {
        "html5_coverage": "html5",
        "iframe_focus_and_confirmation": "iframe",
    }
    mode = modes.get(policy)
    if mode is None:
        raise DailyTaskProgressError("video completion policy is missing or unsupported", 409)

    start = _finite_float(
        resource_ref.get("start_seconds", resource_ref.get("segment_start_seconds")),
        field_name="resource_ref.start_seconds",
        code=409,
    )
    end = _finite_float(
        resource_ref.get("end_seconds", resource_ref.get("segment_end_seconds")),
        field_name="resource_ref.end_seconds",
        code=409,
    )
    if start < 0 or end <= start:
        raise DailyTaskProgressError("published video segment is invalid", 409)

    threshold = _finite_float(
        completion_policy.get("coverage_threshold", 0.9),
        field_name="completion_policy.coverage_threshold",
        code=409,
    )
    if threshold < 0.9 or threshold > 1.0:
        raise DailyTaskProgressError("video coverage threshold must be between 0.9 and 1.0", 409)
    return mode, start, end, threshold


def _normalized_watched_intervals(
    intervals: Any,
    *,
    segment_start: float,
    segment_end: float,
    reject_out_of_range: bool,
) -> list[tuple[float, float]]:
    if not isinstance(intervals, list):
        raise DailyTaskProgressError("watched_intervals must be a list", 400)
    normalized: list[tuple[float, float]] = []
    for interval in intervals:
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            raise DailyTaskProgressError("each watched interval must contain start and end", 400)
        start = _finite_float(interval[0], field_name="watched interval start")
        end = _finite_float(interval[1], field_name="watched interval end")
        if end <= start:
            raise DailyTaskProgressError("watched interval end must be after start", 400)
        if reject_out_of_range and (start < segment_start or end > segment_end):
            raise DailyTaskProgressError("watched interval is outside the published video segment", 400)
        clipped_start = max(segment_start, start)
        clipped_end = min(segment_end, end)
        if clipped_end > clipped_start:
            normalized.append((clipped_start, clipped_end))
    return normalized


def _mk_task_item_id(host_task_id: str, host_task_version: int, ordinal: int, user_id: int) -> str:
    seed = f"{host_task_id}:{host_task_version}:{ordinal}:{user_id}"
    return f"ITEM_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:24]}"


def _snapshot_hash(question: dict[str, Any]) -> str:
    canonical = json.dumps(question, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _question_row_candidates(db: Session, kp_id: str):
    return (
        db.execute(
            select(QuestionVersionRecord)
            .join(QuestionKPLinkRecord, QuestionKPLinkRecord.question_version_id == QuestionVersionRecord.question_version_id)
            .where(
                QuestionKPLinkRecord.kp_id == kp_id,
                QuestionKPLinkRecord.status == "active",
                QuestionVersionRecord.status == "active",
                QuestionVersionRecord.source_kind.like(f"{FORMAL_QUESTION_SOURCE_PREFIX}%"),
            )
            .order_by(QuestionVersionRecord.created_at.asc(), QuestionVersionRecord.question_version_id.asc())
        )
        .scalars()
        .all()
    )


def _quiz_question_candidates(db: Session, kp_ids: list[str]) -> list[QuestionVersionRecord]:
    """Collect deduplicated formal question versions across quiz KPs."""

    seen: set[str] = set()
    collected: list[QuestionVersionRecord] = []
    for kp_id in kp_ids:
        for row in _question_row_candidates(db, kp_id):
            # 同一题目只保留一个活跃版本，避免同一道题以多个版本重复出现。
            if row.question_version_id in seen:
                continue
            seen.add(row.question_version_id)
            collected.append(row)
    return collected


def quiz_learner_profile(db: Session, user_id: int) -> str:
    """Determine the daily-quiz difficulty profile from real learner signals.

    Real answered-question accuracy is the primary evidence; the persisted
    user profile (learner group) is only a fallback when no attempt exists.
    Never infers a difficulty for individual questions - this only selects a
    target distribution for question sampling.
    """

    attempts = (
        db.query(LearningQuestionAttempt)
        .filter(LearningQuestionAttempt.user_id == user_id)
        .all()
    )
    if attempts:
        correct = sum(1 for attempt in attempts if attempt.is_correct)
        accuracy = correct / len(attempts)
        if accuracy >= 0.8:
            return "advanced"
        if accuracy >= 0.55:
            return "balanced"
        return "foundation"

    public_attempts = (
        db.query(QuestionAttempt)
        .filter(QuestionAttempt.user_id == user_id)
        .all()
    )
    if public_attempts:
        correct = sum(1 for attempt in public_attempts if attempt.is_correct)
        accuracy = correct / len(public_attempts)
        if accuracy >= 0.8:
            return "advanced"
        if accuracy >= 0.55:
            return "balanced"
        return "foundation"

    profile = db.query(LearningUserProfile).filter_by(user_id=user_id).one_or_none()
    if profile is not None:
        group_text = str(profile.user_group_json or "")
        try:
            group_payload = json.loads(group_text)
        except (TypeError, ValueError):
            group_payload = {}
        if isinstance(group_payload, dict):
            evaluation_profile = str(
                group_payload.get("evaluation_profile") or ""
            ).strip().lower()
            if evaluation_profile in KNOWLEDGE_PRACTICE_DIFFICULTY_TARGETS:
                return evaluation_profile
        for keyword, profile_name in _QUIZ_GROUP_PROFILE_HINTS.items():
            if keyword in group_text:
                return profile_name
    return "balanced"


def select_knowledge_practice_questions(
    db: Session,
    kp_id: str,
    profile: str,
    target_count: int,
) -> list[QuestionVersionRecord]:
    """Select deterministic, difficulty-aware questions for one knowledge point.

    Exact target slots are filled first. Any shortfall is filled from remaining
    annotated questions in the profile-specific order, and only then from
    unlabelled formal questions. The caller retains the existing responsibility
    to reject publication when the total formal candidate count is insufficient.
    """

    normalized_profile = (
        profile
        if profile in KNOWLEDGE_PRACTICE_DIFFICULTY_TARGETS
        else "balanced"
    )
    required = max(0, int(target_count))
    if required == 0:
        return []

    from APP.backend.question_retrieval_service import hybrid_rank_questions

    candidates = _question_row_candidates(db, kp_id)
    ranked = hybrid_rank_questions(
        [
            {
                "question_id": row.question_id,
                "stem": row.stem,
                "answer": row.answer,
                "analysis": row.analysis,
                "kp_ids": [kp_id],
                "question_type": row.question_type,
                "difficulty": row.standard_difficulty,
                "_row": row,
            }
            for row in candidates
        ],
        query=kp_id,
        kp_ids=[kp_id],
        limit=len(candidates) or 1,
    )
    candidates = [row["_row"] for row in ranked]
    by_difficulty: dict[int | None, list[QuestionVersionRecord]] = {
        1: [], 2: [], 3: [], 4: [], 5: [], None: []
    }
    for row in candidates:
        difficulty = row.standard_difficulty
        key = (
            int(difficulty)
            if difficulty is not None and int(difficulty) in {1, 2, 3, 4, 5}
            else None
        )
        by_difficulty[key].append(row)

    target_template = KNOWLEDGE_PRACTICE_DIFFICULTY_TARGETS[normalized_profile]
    target_levels = [
        target_template[index % len(target_template)]
        for index in range(required)
    ]
    selected: list[QuestionVersionRecord] = []
    selected_ids: set[str] = set()
    shortfall = 0

    for level in target_levels:
        pool = by_difficulty[level]
        row = next(
            (
                candidate
                for candidate in pool
                if candidate.question_version_id not in selected_ids
            ),
            None,
        )
        if row is None:
            shortfall += 1
            continue
        selected.append(row)
        selected_ids.add(row.question_version_id)

    for level in KNOWLEDGE_PRACTICE_DIFFICULTY_FALLBACKS[normalized_profile]:
        if shortfall <= 0:
            break
        for row in by_difficulty[level]:
            if shortfall <= 0:
                break
            if row.question_version_id in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(row.question_version_id)
            shortfall -= 1

    if shortfall > 0:
        for row in by_difficulty[None]:
            if shortfall <= 0:
                break
            if row.question_version_id in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(row.question_version_id)
            shortfall -= 1

    return selected[:required]


def select_quiz_questions(
    db: Session,
    kp_ids: list[str],
    profile: str,
    target_count: int,
) -> list[QuestionVersionRecord]:
    """Select a difficulty-stratified quiz question set across KPs.

    Only real annotated difficulties (standard_difficulty 1-5) are used for
    stratification; unlabelled questions are treated as a neutral overflow
    pool, never as a manufactured difficulty. When a difficulty tier is
    under-supplied, the shortfall is filled from the unlabelled pool first,
    then from lower tiers, then from whatever remains - the quiz never blocks
    the daily task because of a shortage.
    """

    profile = profile if profile in QUIZ_DIFFICULTY_PROFILES else "balanced"
    target = max(QUIZ_MIN_TARGET_COUNT, min(QUIZ_MAX_TARGET_COUNT, int(target_count)))
    from APP.backend.question_retrieval_service import hybrid_rank_questions

    candidates = _quiz_question_candidates(db, kp_ids)
    if not candidates:
        return []
    ranked = hybrid_rank_questions(
        [
            {
                "question_id": row.question_id,
                "stem": row.stem,
                "answer": row.answer,
                "analysis": row.analysis,
                "kp_ids": list(
                    getattr(row, "_active_kp_ids", ())
                    or [kp_id for kp_id in kp_ids]
                ),
                "question_type": row.question_type,
                "difficulty": row.standard_difficulty,
                "_row": row,
            }
            for row in candidates
        ],
        query="、".join(kp_ids),
        kp_ids=kp_ids,
        limit=len(candidates),
    )
    candidates = [row["_row"] for row in ranked]

    by_difficulty: dict[int | None, list[QuestionVersionRecord]] = {}
    for row in candidates:
        key = (
            int(row.standard_difficulty)
            if row.standard_difficulty is not None
            and int(row.standard_difficulty) in {1, 2, 3, 4, 5}
            else None
        )
        by_difficulty.setdefault(key, []).append(row)

    wanted = dict(QUIZ_DIFFICULTY_PROFILES[profile])
    selected: list[QuestionVersionRecord] = []
    selected_ids: set[str] = set()
    shortfall = 0

    for level in (1, 2, 3, 4, 5):
        pool = by_difficulty.get(level, [])
        take = min(wanted.get(level, 0), len(pool))
        for row in pool[:take]:
            selected.append(row)
            selected_ids.add(row.question_version_id)
        shortfall += wanted.get(level, 0) - take

    if shortfall > 0:
        unlabeled_pool = by_difficulty.get(None, [])
        for row in unlabeled_pool:
            if shortfall <= 0:
                break
            if row.question_version_id in selected_ids:
                continue
            selected.append(row)
            selected_ids.add(row.question_version_id)
            shortfall -= 1

    if shortfall > 0:
        for level in (1, 2, 3, 4, 5):
            if shortfall <= 0:
                break
            for row in by_difficulty.get(level, []):
                if shortfall <= 0:
                    break
                if row.question_version_id in selected_ids:
                    continue
                selected.append(row)
                selected_ids.add(row.question_version_id)
                shortfall -= 1

    # 数量仍不足目标时，不阻塞任务：有多少冻结多少。
    return selected[:target]


def _normalized_knowledge_point_label(value: Any) -> str:
    return "".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


def resolve_executable_knowledge_point(
    db: Session,
    knowledge_point_name: str,
    *,
    required_question_count: int = 3,
) -> str | None:
    """Resolve one uniquely named formal KP that can freeze the requested questions."""

    normalized_name = _normalized_knowledge_point_label(knowledge_point_name)
    if not normalized_name:
        return None
    if required_question_count <= 0:
        raise ValueError("required_question_count must be positive")

    matches: list[KnowledgePoint] = []
    rows = (
        db.query(KnowledgePoint)
        .filter(
            KnowledgePoint.status == "active",
            KnowledgePoint.source.like(f"{FORMAL_QUESTION_SOURCE_PREFIX}%"),
        )
        .all()
    )
    for row in rows:
        try:
            aliases = json.loads(row.aliases_json or "[]")
        except (TypeError, ValueError):
            aliases = []
        labels = [row.name, *(aliases if isinstance(aliases, list) else [])]
        if normalized_name in {
            _normalized_knowledge_point_label(label) for label in labels
        }:
            matches.append(row)

    if len(matches) != 1:
        return None
    kp_id = str(matches[0].kp_id or "").strip()
    if not kp_id:
        return None
    if len(_question_row_candidates(db, kp_id)) < required_question_count:
        return None
    return kp_id


def ensure_executable_knowledge_bundle(
    db: Session,
    bundle: dict[str, Any],
    *,
    required_question_count: int = 3,
) -> str:
    """Persist a small trusted-atlas bundle for one executable daily task.

    This is intentionally on-demand: it avoids copying the full public atlas
    into the personalized runtime while still freezing authoritative question
    versions for completion tracking.
    """

    if not isinstance(bundle, dict) or bundle.get("source") != "knowledge_atlas":
        raise DailyTaskProgressError("daily task bundle is not a trusted atlas bundle", 409)
    kp_payload = bundle.get("kp")
    questions = bundle.get("questions")
    if not isinstance(kp_payload, dict) or not isinstance(questions, list):
        raise DailyTaskProgressError("daily task bundle is incomplete", 409)
    kp_id = str(kp_payload.get("kp_id") or bundle.get("kp_id") or "").strip()
    kp_name = str(
        kp_payload.get("kp_lv3")
        or bundle.get("knowledge_point_name")
        or ""
    ).strip()
    if not kp_id or not kp_name:
        raise DailyTaskProgressError("daily task bundle has no canonical knowledge point", 409)
    valid_questions = [
        row
        for row in questions
        if isinstance(row, dict)
        and str(row.get("question_id") or "").strip()
        and str(row.get("question_content") or "").strip()
        and kp_id in {str(value) for value in row.get("kp_ids") or []}
    ]
    if len(valid_questions) < required_question_count:
        raise DailyTaskProgressError(
            f"trusted atlas has insufficient questions for kp {kp_id}", 409
        )

    aliases = [
        value.strip()
        for value in re.split(r"[；;、]", str(kp_payload.get("other_name") or ""))
        if value.strip()
    ]
    point = db.query(KnowledgePoint).filter_by(kp_id=kp_id).one_or_none()
    if point is None:
        point = KnowledgePoint(kp_id=kp_id)
        db.add(point)
    elif point.source and not _trusted_formal_source(point.source):
        raise DailyTaskProgressError(
            f"knowledge point {kp_id} belongs to another source", 409
        )
    point.name = kp_name
    point.aliases_json = json.dumps(aliases, ensure_ascii=False)
    point.description = " / ".join(
        filter(
            None,
            (
                str(kp_payload.get("kp_lv1") or ""),
                str(kp_payload.get("kp_lv2") or ""),
            ),
        )
    )
    point.source = KNOWLEDGE_ATLAS_SOURCE
    point.status = "active"

    mirror_kp = db.query(LearningKnowledgePoint).filter_by(kp_id=kp_id).one_or_none()
    if mirror_kp is None:
        mirror_kp = LearningKnowledgePoint(kp_id=kp_id)
        db.add(mirror_kp)
    mirror_kp.kp_lv1 = str(kp_payload.get("kp_lv1") or "")
    mirror_kp.kp_lv2 = str(kp_payload.get("kp_lv2") or "")
    mirror_kp.kp_lv3 = kp_name
    mirror_kp.raw_content = json.dumps(
        kp_payload.get("raw_content") or [], ensure_ascii=False
    )
    mirror_kp.other_name_json = json.dumps(aliases, ensure_ascii=False)
    mirror_kp.order_json = json.dumps(
        {"order_code": kp_payload.get("order")}, ensure_ascii=False
    )

    for row in valid_questions[:required_question_count]:
        question_id = str(row["question_id"]).strip()
        question_type = _QUESTION_TYPES.get(
            str(row.get("question_type") or "").strip(), "short_answer"
        )
        stem = str(row.get("question_content") or "").strip()
        answer_value = row.get("answer")
        answers = (
            [str(value) for value in answer_value]
            if isinstance(answer_value, list)
            else [str(answer_value or "")]
        )
        answer = json.dumps(answers, ensure_ascii=False)
        analysis = str(row.get("explanation") or "")
        options = row.get("options") if isinstance(row.get("options"), list) else []

        item = db.query(QuestionBankItem).filter_by(question_id=question_id).one_or_none()
        if item is None:
            item = QuestionBankItem(question_id=question_id)
            db.add(item)
        elif item.source and not _trusted_formal_source(item.source):
            raise DailyTaskProgressError(
                f"question {question_id} belongs to another source", 409
            )
        existing_kp_ids = set(json.loads(item.kp_ids_json or "[]"))
        existing_kp_ids.add(kp_id)
        item.stem = stem
        item.answer = answer
        item.analysis = analysis
        item.kp_ids_json = json.dumps(sorted(existing_kp_ids), ensure_ascii=False)
        item.question_type = question_type
        item.difficulty = None
        item.difficulty_source = None
        item.quality_score = 0.7
        item.source = KNOWLEDGE_ATLAS_SOURCE
        item.status = "active"

        mirror = db.query(LearningQuestion).filter_by(question_id=question_id).one_or_none()
        if mirror is None:
            mirror = LearningQuestion(question_id=question_id)
            db.add(mirror)
        mirror.question_type = question_type
        mirror.question_content = stem
        mirror.options_json = json.dumps(options, ensure_ascii=False)
        mirror.answer_json = answer
        mirror.explanation = analysis
        mirror.difficulty = None
        mirror.difficulty_source = None
        mirror.kp_ids_json = item.kp_ids_json
        mirror.scoring_rubric = str(row.get("scoring_rubric") or "")
        mirror.key_points = str(row.get("key_points") or "")

        version_id = (
            f"{question_id}:atlas:"
            f"{hashlib.sha1(question_id.encode('utf-8')).hexdigest()[:16]}"
        )
        version = (
            db.query(QuestionVersionRecord)
            .filter_by(question_version_id=version_id)
            .one_or_none()
        )
        if version is None:
            next_version = max(
                (
                    value
                    for value, in db.query(QuestionVersionRecord.version)
                    .filter_by(question_id=question_id)
                    .all()
                ),
                default=0,
            ) + 1
            version = QuestionVersionRecord(
                question_version_id=version_id,
                question_id=question_id,
                version=next_version,
            )
            db.add(version)
        version.question_type = question_type
        version.stem = stem
        version.answer = answer
        version.analysis = analysis
        version.standard_difficulty = None
        version.difficulty_source = None
        version.source_kind = KNOWLEDGE_ATLAS_SOURCE
        version.status = "active"
        db.flush()
        link = (
            db.query(QuestionKPLinkRecord)
            .filter_by(question_version_id=version_id, kp_id=kp_id)
            .one_or_none()
        )
        if link is None:
            db.add(
                QuestionKPLinkRecord(
                    question_version_id=version_id,
                    kp_id=kp_id,
                    is_primary=True,
                    status="active",
                )
            )
        else:
            link.status = "active"

    db.flush()
    if len(_question_row_candidates(db, kp_id)) < required_question_count:
        raise DailyTaskProgressError(
            f"failed to freeze questions for kp {kp_id}", 409
        )
    return kp_id


def _question_options(db: Session, question_id: str) -> list[Any]:
    question = db.query(LearningQuestion).filter_by(question_id=question_id).one_or_none()
    if question is None:
        return []
    return json.loads(question.options_json or "[]")


def _public_item_snapshot(item: DailyTaskItemRecord, snapshots: list[DailyTaskQuestionSnapshotRecord]) -> dict[str, Any]:
    public_questions = []
    for snapshot in sorted(snapshots, key=lambda row: (row.created_at or datetime.min, row.question_version_id)):
        public_questions.append(
            {
                "question_id": snapshot.question_id,
                "question_version_id": snapshot.question_version_id,
                "question_type": snapshot.question_type,
                "stem_snapshot": snapshot.stem_snapshot,
                "options_snapshot_json": json.loads(snapshot.options_snapshot_json or "[]"),
                "kp_snapshot_json": json.loads(snapshot.kp_snapshot_json or "[]"),
                "source_kind": snapshot.source_kind,
                "snapshot_hash": snapshot.snapshot_hash,
                "submitted_answer": snapshot.submitted_answer,
                "attempt_status": snapshot.attempt_status,
                "audit_decision": snapshot.audit_decision,
                "audit_status": snapshot.audit_status,
            }
        )
    return {
        "task_item_id": item.task_item_id,
        "kp_id": item.kp_id,
        "item_kind": item.item_kind,
        "ordinal": item.ordinal,
        "required_question_count": item.required_question_count,
        "status": item.status,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
        "questions": public_questions,
    }


def _refresh_item_completion(db: Session, item: DailyTaskItemRecord, now: datetime | None = None) -> DailyTaskItemRecord:
    if item.item_kind == "video":
        evidence = (
            db.query(DailyTaskVideoEvidenceRecord)
            .filter_by(task_item_id=item.task_item_id, user_id=item.user_id)
            .one_or_none()
        )
        if evidence is not None and evidence.status == "completed":
            item.status = "completed"
            item.completed_at = item.completed_at or now or datetime.utcnow()
        else:
            if item.status not in {"cancelled", "completed"}:
                item.status = "pending"
                item.completed_at = None
        return item

    snapshots = (
        db.query(DailyTaskQuestionSnapshotRecord)
        .filter_by(task_item_id=item.task_item_id, user_id=item.user_id)
        .all()
    )
    required = item.required_question_count or 0
    if required <= 0 or len(snapshots) <= 0:
        if item.status != "cancelled":
            item.status = "pending"
        return item

    all_terminal = True
    for snapshot in snapshots:
        if not snapshot.submitted_answer:
            all_terminal = False
            break
        if snapshot.audit_decision not in TERMINAL_AUDIT_DECISIONS:
            all_terminal = False
            break

    if all_terminal:
        item.status = "completed"
        item.completed_at = now or datetime.utcnow()
    else:
        item.status = "pending"
        item.completed_at = None
    return item


def _ensure_instance(db: Session, user_id: int, host_task_id: str, host_task_version: int) -> DailyTaskInstanceRecord:
    instance = (
        db.query(DailyTaskInstanceRecord)
        .filter_by(host_task_id=host_task_id, host_task_version=host_task_version, user_id=user_id)
        .one_or_none()
    )
    if instance is None:
        instance = DailyTaskInstanceRecord(
            host_task_id=host_task_id,
            host_task_version=host_task_version,
            user_id=user_id,
            status="active",
        )
        db.add(instance)
        db.flush()
    return instance


def _items_payload(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        raise DailyTaskProgressError("payload must include items or knowledge_practice", 400)
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("knowledge_practice")
        if isinstance(items, list):
            return items
        raise DailyTaskProgressError("payload must include a list of items", 400)
    if isinstance(payload, list):
        return payload
    raise DailyTaskProgressError("payload must include a list of items", 400)


def upsert_daily_task_snapshot(db: Session, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DailyTaskProgressError("payload must be a dictionary", 400)

    host_task_id = str(payload.get("host_task_id") or payload.get("task_id") or "")
    if not host_task_id:
        raise DailyTaskProgressError("payload must include host_task_id", 400)
    host_task_version = _typed_int(payload.get("host_task_version") or payload.get("version") or 1, field_name="host_task_version")

    items_payload = _items_payload(payload.get("items") or payload.get("knowledge_practice") or payload)
    supported_item_kinds = {"knowledge_practice", "video", "video_section"}
    unsupported_item_kinds = sorted({
        str(item_spec.get("item_kind") or item_spec.get("item_type") or "knowledge_practice")
        for item_spec in items_payload
        if str(item_spec.get("item_kind") or item_spec.get("item_type") or "knowledge_practice")
        not in supported_item_kinds
    })
    if unsupported_item_kinds:
        raise DailyTaskProgressError(
            "daily task publication contains items without a verifiable completion path: "
            + ", ".join(unsupported_item_kinds),
            409,
        )

    _ensure_instance(db, user_id, host_task_id, host_task_version)

    existing_items = {
        row.task_item_id: row
        for row in db.query(DailyTaskItemRecord).filter_by(user_id=user_id, host_task_id=host_task_id, host_task_version=host_task_version).all()
    }

    persisted_items = []
    for ordinal, item_spec in enumerate(items_payload, start=1):
        item_key = str(item_spec.get("task_item_id") or _mk_task_item_id(host_task_id, host_task_version, ordinal, user_id))
        item_kind = str(item_spec.get("item_kind") or item_spec.get("item_type") or "knowledge_practice")
        if item_kind == "video_section":
            item_kind = "video"
        kp_id = str(item_spec.get("kp_id") or item_spec.get("knowledge_point_id") or "")
        if not kp_id and item_kind == "knowledge_practice":
            raise DailyTaskProgressError("each knowledge practice item must include kp_id", 400)
        if not kp_id:
            kp_id = f"__task_item__:{item_key}"
        required_question_count = _typed_int(item_spec.get("required_question_count") or item_spec.get("required_count") or 0, field_name="required_question_count")
        resource_ref = item_spec.get("resource_ref") if isinstance(item_spec.get("resource_ref"), dict) else {}
        completion_policy = item_spec.get("completion_policy") if isinstance(item_spec.get("completion_policy"), dict) else {}
        item = existing_items.get(item_key)
        if item is None:
            item = db.query(DailyTaskItemRecord).filter_by(task_item_id=item_key, user_id=user_id).one_or_none()
        if (
            item is not None
            and item.host_task_id == host_task_id
            and item.host_task_version != host_task_version
        ):
            # A plan metadata correction may increment the parent version while
            # retaining the exact frozen atoms. Move those atoms to the new
            # version so progress does not disappear behind an empty instance.
            item.host_task_version = host_task_version
        if item is None:
            item = DailyTaskItemRecord(
                task_item_id=item_key,
                host_task_id=host_task_id,
                host_task_version=host_task_version,
                user_id=user_id,
                kp_id=kp_id,
                item_kind=item_kind,
                ordinal=ordinal,
                required_question_count=required_question_count,
                resource_ref=resource_ref,
                completion_policy=completion_policy,
                status="pending",
            )
            db.add(item)
            db.flush()
        elif item.host_task_id != host_task_id:
            raise DailyTaskProgressError(
                f"task item {item_key} is already bound to another task", 409
            )

        if item.required_question_count != required_question_count:
            item.required_question_count = required_question_count
        if not item.resource_ref and resource_ref:
            item.resource_ref = resource_ref
        if not item.completion_policy and completion_policy:
            item.completion_policy = completion_policy

        existing_snapshots = (
            db.query(DailyTaskQuestionSnapshotRecord)
            .filter_by(task_item_id=item.task_item_id, user_id=user_id)
            .all()
        )
        if existing_snapshots:
            _refresh_item_completion(db, item)
            persisted_items.append(_public_item_snapshot(item, existing_snapshots))
            continue

        if item_kind != "knowledge_practice":
            _refresh_item_completion(db, item)
            persisted_items.append(_public_item_snapshot(item, []))
            continue

        is_quiz = bool(completion_policy.get("quiz")) if isinstance(completion_policy, dict) else False
        if is_quiz:
            # 每日测验：跨同一 host task 的全部知识点选题，按用户学习情况
            # 与画像决定难度分层；候选不足时不阻塞任务，有多少冻结多少。
            quiz_kp_ids = [
                str(row.kp_id or "")
                for row in (
                    db.query(DailyTaskItemRecord)
                    .filter_by(user_id=user_id, host_task_id=host_task_id, host_task_version=host_task_version)
                    .all()
                )
                if str(row.kp_id or "") and not str(row.kp_id or "").startswith("__task_item__:")
            ]
            if not quiz_kp_ids:
                quiz_kp_ids = [kp_id]
            profile = quiz_learner_profile(db, user_id)
            quiz_target = int(
                completion_policy.get("quiz_target_count")
                or required_question_count
                or QUIZ_DEFAULT_TARGET_COUNT
            )
            selected_rows = select_quiz_questions(db, quiz_kp_ids, profile, quiz_target)
            if not selected_rows:
                _refresh_item_completion(db, item)
                persisted_items.append(_public_item_snapshot(item, []))
                continue
        else:
            profile = quiz_learner_profile(db, user_id)
            selected_rows = select_knowledge_practice_questions(
                db,
                kp_id,
                profile,
                required_question_count,
            )
            if len(selected_rows) < required_question_count:
                raise DailyTaskProgressError(
                    f"insufficient frozen question candidates for kp {kp_id}: need {required_question_count}, have {len(selected_rows)}",
                    409,
                )
        snapshot_rows: list[DailyTaskQuestionSnapshotRecord] = []
        for row in selected_rows:
            options = _question_options(db, row.question_id)
            snapshot_payload = {
                "question_id": row.question_id,
                "question_version_id": row.question_version_id,
                "question_type": row.question_type,
                "stem": row.stem,
                "options": options,
                "answer": row.answer,
                "rubric": row.analysis,
                "kp_ids": [kp_id],
                "source_kind": row.source_kind,
            }
            snapshot = DailyTaskQuestionSnapshotRecord(
                task_item_id=item.task_item_id,
                user_id=user_id,
                question_id=row.question_id,
                question_version_id=row.question_version_id,
                question_type=row.question_type,
                stem_snapshot=row.stem,
                options_snapshot_json=json.dumps(snapshot_payload["options"], ensure_ascii=False),
                answer_snapshot=row.answer,
                rubric_snapshot=row.analysis,
                kp_snapshot_json=json.dumps(snapshot_payload["kp_ids"], ensure_ascii=False),
                source_kind=row.source_kind,
                snapshot_hash=_snapshot_hash(snapshot_payload),
                submitted_answer="",
                attempt_status="pending",
                audit_decision="pending",
                audit_status="pending",
            )
            db.add(snapshot)
            db.flush()
            snapshot_rows.append(snapshot)
        _refresh_item_completion(db, item)
        persisted_items.append(_public_item_snapshot(item, snapshot_rows))

    db.commit()
    return {
        "host_task_id": host_task_id,
        "host_task_version": host_task_version,
        "status": "active",
        "items": persisted_items,
    }


def record_reviewed_question(
    db: Session,
    user_id: int,
    payload: dict[str, Any],
    *,
    commit: bool = True,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DailyTaskProgressError("payload must be a dictionary", 400)
    task_item_id = str(payload.get("task_item_id") or "")
    question_version_id = str(payload.get("question_version_id") or "")
    if not task_item_id or not question_version_id:
        raise DailyTaskProgressError("payload must include task_item_id and question_version_id", 400)

    snapshot = (
        db.query(DailyTaskQuestionSnapshotRecord)
        .filter_by(task_item_id=task_item_id, user_id=user_id, question_version_id=question_version_id)
        .one_or_none()
    )
    if snapshot is None:
        raise DailyTaskProgressError("question snapshot is not bound to this task item", 409)

    submitted_answer = payload.get("submitted_answer")
    if submitted_answer is not None:
        snapshot.submitted_answer = str(submitted_answer)
    snapshot.attempt_status = str(payload.get("attempt_status") or snapshot.attempt_status or "submitted")
    snapshot.audit_decision = str(payload.get("audit_decision") or snapshot.audit_decision or "pending")
    snapshot.audit_status = str(payload.get("audit_status") or snapshot.audit_status or "pending")

    item = db.query(DailyTaskItemRecord).filter_by(task_item_id=task_item_id, user_id=user_id).one_or_none()
    if item is None:
        raise DailyTaskProgressError("task item is missing", 404)
    _refresh_item_completion(db, item)
    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "task_item_id": task_item_id,
        "question_version_id": question_version_id,
        "status": item.status,
        "audit_decision": snapshot.audit_decision,
        "audit_status": snapshot.audit_status,
    }


def record_video_evidence(db: Session, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DailyTaskProgressError("payload must be a dictionary", 400)

    task_item_id = str(payload.get("task_item_id") or "")
    if not task_item_id:
        raise DailyTaskProgressError("payload must include task_item_id", 400)

    item = db.query(DailyTaskItemRecord).filter_by(task_item_id=task_item_id, user_id=user_id).one_or_none()
    if item is None:
        raise DailyTaskProgressError("task item is missing", 404)

    mode, segment_start, segment_end, threshold = _authoritative_video_spec(item)
    observed_mode = str(payload.get("mode") or "").strip()
    if observed_mode and observed_mode != mode:
        raise DailyTaskProgressError("video evidence mode does not match the published task item", 409)

    evidence = (
        db.query(DailyTaskVideoEvidenceRecord)
        .filter_by(task_item_id=task_item_id, user_id=user_id)
        .one_or_none()
    )
    if evidence is None:
        evidence = DailyTaskVideoEvidenceRecord(
            task_item_id=task_item_id,
            user_id=user_id,
            mode=mode,
            segment_start_seconds=segment_start,
            segment_end_seconds=segment_end,
            watched_intervals_json="[]",
            active_seconds=0.0,
            status="pending",
        )
        db.add(evidence)
        db.flush()

    evidence.mode = mode
    evidence.segment_start_seconds = segment_start
    evidence.segment_end_seconds = segment_end

    try:
        persisted_intervals = json.loads(evidence.watched_intervals_json or "[]")
    except (TypeError, ValueError):
        persisted_intervals = []
    if mode == "html5":
        merged = _normalized_watched_intervals(
            persisted_intervals,
            segment_start=segment_start,
            segment_end=segment_end,
            reject_out_of_range=False,
        )
        merged.extend(_normalized_watched_intervals(
            payload.get("watched_intervals") or [],
            segment_start=segment_start,
            segment_end=segment_end,
            reject_out_of_range=True,
        ))
        merged.sort()
        union: list[tuple[float, float]] = []
        for start, end in merged:
            if not union or start > union[-1][1]:
                union.append((start, end))
            else:
                union[-1] = (union[-1][0], max(union[-1][1], end))
        evidence.watched_intervals_json = json.dumps(union, ensure_ascii=False)
        active_seconds = sum(max(0.0, end - start) for start, end in union)
        evidence.active_seconds = active_seconds
        required = threshold * (segment_end - segment_start)
        if evidence.status != "completed":
            evidence.status = "completed" if active_seconds >= required else "pending"
    else:
        # A tracker may restart after navigation; a shorter heartbeat must not
        # reduce already persisted effective-focus evidence.
        observed_active_seconds = _finite_float(
            payload.get("active_seconds") or 0.0,
            field_name="active_seconds",
        )
        if observed_active_seconds < 0:
            raise DailyTaskProgressError("active_seconds cannot be negative", 400)
        evidence.active_seconds = max(
            float(evidence.active_seconds or 0.0),
            min(observed_active_seconds, segment_end - segment_start),
        )
        required = threshold * (segment_end - segment_start)
        if evidence.status != "completed":
            evidence.status = (
                "completed"
                if evidence.is_confirmed and evidence.active_seconds >= required
                else "pending"
            )

    _refresh_item_completion(db, item)
    db.commit()
    return {
        "task_item_id": task_item_id,
        "mode": evidence.mode,
        "active_seconds": evidence.active_seconds,
        "status": evidence.status,
        "code": 200,
    }


def confirm_iframe_video(db: Session, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DailyTaskProgressError("payload must be a dictionary", 400)

    task_item_id = str(payload.get("task_item_id") or "")
    if not task_item_id:
        raise DailyTaskProgressError("payload must include task_item_id", 400)

    item = db.query(DailyTaskItemRecord).filter_by(task_item_id=task_item_id, user_id=user_id).one_or_none()
    if item is None:
        raise DailyTaskProgressError("task item is missing", 404)
    mode, segment_start, segment_end, threshold = _authoritative_video_spec(item)
    if mode != "iframe":
        raise DailyTaskProgressError("only iframe video evidence can be confirmed", 409)

    evidence = (
        db.query(DailyTaskVideoEvidenceRecord)
        .filter_by(task_item_id=task_item_id, user_id=user_id)
        .one_or_none()
    )
    if evidence is None:
        raise DailyTaskProgressError("video evidence is missing", 404)

    evidence.mode = mode
    evidence.segment_start_seconds = segment_start
    evidence.segment_end_seconds = segment_end
    required = threshold * (segment_end - segment_start)
    confirmed = bool(payload.get("confirmed") or evidence.is_confirmed)
    if evidence.active_seconds >= required and confirmed:
        evidence.is_confirmed = True
        evidence.status = "completed"
        _refresh_item_completion(db, item)
        db.commit()
        return {"task_item_id": task_item_id, "status": "completed", "code": 200}

    return {"task_item_id": task_item_id, "status": "conflict", "code": 409}


def daily_task_progress(db: Session, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    host_task_id = str(payload.get("host_task_id") or payload.get("task_id") or "")
    host_task_version = _typed_int(payload.get("host_task_version") or payload.get("version") or 1, field_name="host_task_version")
    if not host_task_id:
        raise DailyTaskProgressError("payload must include host_task_id", 400)

    items = (
        db.query(DailyTaskItemRecord)
        .filter_by(host_task_id=host_task_id, host_task_version=host_task_version, user_id=user_id)
        .order_by(DailyTaskItemRecord.ordinal.asc())
        .all()
    )

    progress_items = []
    completed_count = 0
    for item in items:
        _refresh_item_completion(db, item)
        snapshots = (
            db.query(DailyTaskQuestionSnapshotRecord)
            .filter_by(task_item_id=item.task_item_id, user_id=user_id)
            .all()
        )
        status = item.status
        if item.item_kind == "knowledge_practice":
            status = _compute_knowledge_item_status(item, snapshots)
            reviewed_questions = sum(
                1
                for snapshot in snapshots
                if snapshot.submitted_answer
                and snapshot.audit_decision in TERMINAL_AUDIT_DECISIONS
            )
            item_progress = {
                "reviewed_questions": reviewed_questions,
                "required_questions": item.required_question_count,
            }
        else:
            evidence = (
                db.query(DailyTaskVideoEvidenceRecord)
                .filter_by(task_item_id=item.task_item_id, user_id=user_id)
                .one_or_none()
            )
            item_progress = {
                "active_seconds": float(evidence.active_seconds or 0.0) if evidence else 0.0,
                "required_seconds": (
                    max(0.0, float(evidence.segment_end_seconds - evidence.segment_start_seconds) * 0.9)
                    if evidence
                    else 0.0
                ),
            }
        if status == "completed":
            completed_count += 1
        progress_items.append(
            {
                "task_item_id": item.task_item_id,
                "kp_id": item.kp_id,
                "status": status,
                "completed": status == "completed",
                "progress": item_progress,
            }
        )

    parent_status = "completed" if items and completed_count == len(items) else "in_progress"
    return {
        "host_task_id": host_task_id,
        "host_task_version": host_task_version,
        "status": parent_status,
        "completed_items": completed_count,
        "total_items": len(items),
        "items": progress_items,
    }


def _compute_knowledge_item_status(item: DailyTaskItemRecord, snapshots: list[DailyTaskQuestionSnapshotRecord]) -> str:
    if item.required_question_count <= 0 or len(snapshots) <= 0:
        return "pending"
    if all(
        snapshot.submitted_answer and snapshot.audit_decision in TERMINAL_AUDIT_DECISIONS
        for snapshot in snapshots
    ):
        return "completed"
    return "pending"
