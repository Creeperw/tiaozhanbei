"""队列级到期复习派发的行为约束。

这些测试固定的是三类线上缺陷的反面：只取队首候选会把队列钉死、静默吞异常
让失败不可见、以及无节制的模型调用。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.services.review_dispatch import DueReviewDispatcher


class _StubResult:
    def __init__(self, status: str, kp_id: str) -> None:
        self.status = status
        self._kp_id = kp_id

    def model_dump(self, mode: str | None = None) -> dict[str, Any]:
        return {"status": self.status, "kp_id": self._kp_id}


class _StubReviewCardUseCase:
    """Records which knowledge points dispatch actually attempted."""

    def __init__(
        self,
        *,
        raising: set[str] | None = None,
        failing: set[str] | None = None,
        interrupting: set[str] | None = None,
    ) -> None:
        self.raising = raising or set()
        self.failing = failing or set()
        self.interrupting = interrupting or set()
        self.attempted: list[str] = []

    async def execute(self, request: Any) -> _StubResult:
        kp_id = str(request.user_knowledge_state[0]["kp_id"])
        self.attempted.append(kp_id)
        if kp_id in self.raising:
            raise RuntimeError(f"generation exploded for {kp_id}")
        if kp_id in self.failing:
            return _StubResult("failed", kp_id)
        if kp_id in self.interrupting:
            return _StubResult("interrupted", kp_id)
        return _StubResult("success", kp_id)


def _container(tmp_path: Path) -> ApplicationContainer:
    return ApplicationContainer.build(
        Settings(mode="stub"),
        snapshot_root=tmp_path,
        include_backend_handoff=False,
    )


def _seed_due_knowledge_points(
    container: ApplicationContainer,
    learner_id: str,
    kp_ids: list[str],
    *,
    answered_at: str = "2026-07-18T12:00:00Z",
) -> None:
    for index, kp_id in enumerate(kp_ids):
        container.review_service.ingest_knowledge_states(
            learner_id=learner_id,
            prompt_abstract=f"知识点{kp_id}",
            states=[
                {
                    "user_id": learner_id,
                    "kp_id": kp_id,
                    "knowledge_mastery": 0.5,
                    "answer_accuracy": 0.5,
                    "forgetting_coefficient": 0.08,
                    "kp_review_status": "到期",
                    "calculated_at": answered_at,
                }
            ],
        )
        container.review_service.ingest_question_attempts(
            learner_id=learner_id,
            attempts=[
                {
                    "attempt_id": f"DISPATCH_TEST_ATTEMPT_{index}",
                    "kp_ids": [kp_id],
                    "is_correct": False,
                    "score": 0,
                    "answered_at": answered_at,
                }
            ],
        )


def _dispatcher(
    container: ApplicationContainer,
    use_case: _StubReviewCardUseCase,
    **overrides: Any,
) -> DueReviewDispatcher:
    kwargs: dict[str, Any] = {
        "review_service": container.review_service,
        "review_card_use_case": use_case,
        "max_materialize_per_run": 3,
        "min_run_interval": timedelta(0),
        "failure_cooldown": timedelta(minutes=30),
    }
    kwargs.update(overrides)
    return DueReviewDispatcher(**kwargs)


@pytest.mark.asyncio
async def test_dispatch_skips_failing_candidate_and_reaches_the_next(
    tmp_path: Path,
) -> None:
    """A failing knowledge point must not pin the queue.

    The previous implementation took ``next(...)`` — the head of the queue — so
    one failing generation starved every entry behind it forever.
    """

    container = _container(tmp_path)
    learner_id = "DISPATCH_SKIP_1"
    _seed_due_knowledge_points(container, learner_id, ["KP_A", "KP_B", "KP_C"])

    use_case = _StubReviewCardUseCase(raising={"KP_A"})
    dispatcher = _dispatcher(container, use_case)
    outcome = await dispatcher.run(learner_id)

    assert use_case.attempted[:3] == ["KP_A", "KP_B", "KP_C"], use_case.attempted
    assert [item["kp_id"] for item in outcome["materialized"]] == ["KP_B", "KP_C"]
    assert outcome["status"] == "materialized"


@pytest.mark.asyncio
async def test_business_failure_is_treated_as_failure_not_success(
    tmp_path: Path,
) -> None:
    """``status="failed"`` must not be reported as a published resource."""

    container = _container(tmp_path)
    learner_id = "DISPATCH_FAILED_STATUS_1"
    _seed_due_knowledge_points(container, learner_id, ["KP_A", "KP_B"])

    use_case = _StubReviewCardUseCase(failing={"KP_A"})
    dispatcher = _dispatcher(container, use_case)
    outcome = await dispatcher.run(learner_id)

    assert [item["kp_id"] for item in outcome["materialized"]] == ["KP_B"]
    assert outcome["skipped"] == [{"kp_id": "KP_A", "reason": "generation_failed"}]


@pytest.mark.asyncio
async def test_failed_candidate_is_cooled_down_on_the_next_run(
    tmp_path: Path,
) -> None:
    """Repeat queue reads must not re-burn a model call on a known-bad entry."""

    container = _container(tmp_path)
    learner_id = "DISPATCH_COOLDOWN_1"
    _seed_due_knowledge_points(container, learner_id, ["KP_A"])

    use_case = _StubReviewCardUseCase(raising={"KP_A"})
    dispatcher = _dispatcher(container, use_case)

    first = await dispatcher.run(learner_id)
    assert first["skipped"][0]["reason"] == "generation_error"

    second = await dispatcher.run(learner_id)
    assert second["skipped"][0]["reason"] == "failure_cooldown"
    assert second["skipped"][0]["retry_after_seconds"] > 0
    # 只尝试过一次：第二次运行在冷却期内直接跳过，没有再次调用用例。
    assert use_case.attempted == ["KP_A"]


@pytest.mark.asyncio
async def test_cooldown_expires_and_the_candidate_is_retried(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    learner_id = "DISPATCH_COOLDOWN_EXPIRY_1"
    _seed_due_knowledge_points(container, learner_id, ["KP_A"])

    now = [datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)]
    use_case = _StubReviewCardUseCase(raising={"KP_A"})
    dispatcher = _dispatcher(
        container,
        use_case,
        failure_cooldown=timedelta(minutes=30),
        clock=lambda: now[0],
    )

    await dispatcher.run(learner_id)
    now[0] += timedelta(minutes=31)
    use_case.raising.clear()
    outcome = await dispatcher.run(learner_id)

    assert [item["kp_id"] for item in outcome["materialized"]] == ["KP_A"]
    assert use_case.attempted == ["KP_A", "KP_A"]


@pytest.mark.asyncio
async def test_materialization_is_bounded_per_run(tmp_path: Path) -> None:
    container = _container(tmp_path)
    learner_id = "DISPATCH_BUDGET_1"
    _seed_due_knowledge_points(
        container, learner_id, ["KP_A", "KP_B", "KP_C", "KP_D", "KP_E"]
    )

    use_case = _StubReviewCardUseCase()
    dispatcher = _dispatcher(container, use_case, max_materialize_per_run=2)
    outcome = await dispatcher.run(learner_id)

    assert len(outcome["materialized"]) == 2
    assert len(use_case.attempted) == 2


@pytest.mark.asyncio
async def test_unresolvable_topic_is_skipped_and_cooled_down(
    tmp_path: Path,
) -> None:
    """An entry whose name cannot be resolved must not block the rest."""

    container = _container(tmp_path)
    learner_id = "DISPATCH_TOPIC_1"
    # ``prompt_abstract`` falls back to the raw kp_id when blank; use a
    # non-Chinese kp_id so the final fallback cannot resolve a topic either.
    container.review_service.ingest_question_attempts(
        learner_id=learner_id,
        attempts=[
            {
                "attempt_id": "DISPATCH_TOPIC_ATTEMPT_1",
                "kp_ids": ["kp-no-name-1"],
                "is_correct": False,
                "score": 0,
                "answered_at": "2026-07-18T12:00:00Z",
            }
        ],
    )
    _seed_due_knowledge_points(container, learner_id, ["KP_OK"])

    use_case = _StubReviewCardUseCase()
    dispatcher = _dispatcher(container, use_case)
    outcome = await dispatcher.run(learner_id)

    reasons = {item["reason"] for item in outcome["skipped"]}
    assert "unresolved_topic" in reasons
    assert [item["kp_id"] for item in outcome["materialized"]] == ["KP_OK"]


@pytest.mark.asyncio
async def test_idle_when_no_due_candidate(tmp_path: Path) -> None:
    container = _container(tmp_path)
    use_case = _StubReviewCardUseCase()
    dispatcher = _dispatcher(container, use_case)

    outcome = await dispatcher.run("DISPATCH_IDLE_1")

    assert outcome["status"] == "idle"
    assert outcome["reason"] == "no_due_candidate"
    assert use_case.attempted == []


@pytest.mark.asyncio
async def test_request_is_throttled_by_min_run_interval(tmp_path: Path) -> None:
    container = _container(tmp_path)
    learner_id = "DISPATCH_THROTTLE_1"
    _seed_due_knowledge_points(container, learner_id, ["KP_A"])

    use_case = _StubReviewCardUseCase()
    dispatcher = _dispatcher(
        container, use_case, min_run_interval=timedelta(seconds=120)
    )

    assert dispatcher.request(learner_id) is True
    # 同一学习者在上一次运行的时间窗内不会被再次排队。
    assert dispatcher.request(learner_id) is False


@pytest.mark.asyncio
async def test_interrupted_generation_is_reported_for_the_endpoint(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    learner_id = "DISPATCH_INTERRUPTED_1"
    _seed_due_knowledge_points(container, learner_id, ["KP_A", "KP_B"])

    use_case = _StubReviewCardUseCase(interrupting={"KP_A"})
    dispatcher = _dispatcher(container, use_case)
    outcome = await dispatcher.run(learner_id)

    assert outcome["interrupted"] is True
    assert [item["kp_id"] for item in outcome["materialized"]] == ["KP_B"]


@pytest.mark.asyncio
async def test_dispatch_candidates_returns_every_incomplete_due_entry(
    tmp_path: Path,
) -> None:
    container = _container(tmp_path)
    learner_id = "DISPATCH_CANDIDATES_1"
    _seed_due_knowledge_points(container, learner_id, ["KP_A", "KP_B", "KP_C"])

    candidates = container.review_service.dispatch_candidates(learner_id)

    assert sorted(item.memory_unit.kp_id for item in candidates) == [
        "KP_A",
        "KP_B",
        "KP_C",
    ]
    assert all(item.is_due for item in candidates)
    assert all(item.task is None and item.resource is None for item in candidates)


# ── 触发侧：HTTP 队列读取驱动后台派发 ─────────────────────────────────────
#
# 这一组固定的是本次改造的对外效果：只有界面在用的学习者（从不调用智能体
# 工具）也必须拿到到期复习资源。旧实现只在 ``get_review_status`` 工具被调用
# 时才生成，纯界面使用者一条资源都拿不到。


def _seed_due_learner(
    container: ApplicationContainer,
    learner_id: str,
    *,
    prompt_abstract: str = "四君子汤",
    attempt_id: str = "TRIGGER_SOURCE_ATTEMPT_1",
) -> None:
    container.review_service.ingest_knowledge_states(
        learner_id=learner_id,
        prompt_abstract=prompt_abstract,
        states=[
            {
                "user_id": learner_id,
                "kp_id": "KP_FJ_001",
                "knowledge_mastery": 0.5,
                "answer_accuracy": 0.5,
                "forgetting_coefficient": 0.08,
                "kp_review_status": "到期",
                "calculated_at": "2026-07-18T12:00:00Z",
            }
        ],
    )
    container.review_service.ingest_question_attempts(
        learner_id=learner_id,
        attempts=[
            {
                "attempt_id": attempt_id,
                "kp_ids": ["KP_FJ_001"],
                "is_correct": False,
                "score": 0,
                "answered_at": "2026-07-18T12:00:00Z",
            }
        ],
    )


def _wait_for_dispatch(dispatcher: DueReviewDispatcher, learner_id: str) -> dict:
    """Block until the background run leaves ``running`` (bounded wait)."""

    import time

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = dispatcher.state(learner_id)
        if state.get("status") not in (None, "running"):
            return state
        time.sleep(0.05)
    raise AssertionError("background dispatch never finished")


def test_reading_the_review_queue_materializes_the_due_resource(
    tmp_path: Path,
) -> None:
    """界面只读队列，也必须触发一次后台派发并真的产出资源。"""

    from fastapi.testclient import TestClient

    from competition_app.api.app import create_app

    container = _container(tmp_path)
    learner_id = "TRIGGER_QUEUE_READ_1"
    _seed_due_learner(container, learner_id)
    app = create_app(container, auth_required=False)

    with TestClient(app) as client:
        before = client.get(f"/api/v1/learners/{learner_id}/review-queue").json()
        state = _wait_for_dispatch(container.due_review_dispatcher, learner_id)
        after = client.get(f"/api/v1/learners/{learner_id}/review-queue").json()

    assert before["awaiting_resource_count"] == 1
    assert state["status"] == "materialized"
    assert state["summary"]["materialized_count"] == 1
    assert after["awaiting_resource_count"] == 0
    assert after["active_task_count"] == 1


def test_queue_read_does_not_dispatch_when_nothing_is_awaiting(
    tmp_path: Path,
) -> None:
    """没有待物化条目时不得触发模型调用。"""

    from fastapi.testclient import TestClient

    from competition_app.api.app import create_app

    container = _container(tmp_path)
    learner_id = "TRIGGER_QUEUE_READ_EMPTY_1"
    app = create_app(container, auth_required=False)

    with TestClient(app) as client:
        queue = client.get(f"/api/v1/learners/{learner_id}/review-queue").json()

    assert queue["awaiting_resource_count"] == 0
    assert container.due_review_dispatcher.state(learner_id) == {}


def test_explicit_dispatch_endpoint_returns_the_review_card_payload(
    tmp_path: Path,
) -> None:
    """显式派发端点返回复习卡本体，且与队列级触发共用同一实现。"""

    from fastapi.testclient import TestClient

    from competition_app.api.app import create_app

    container = _container(tmp_path)
    learner_id = "TRIGGER_EXPLICIT_1"
    _seed_due_learner(container, learner_id, attempt_id="TRIGGER_EXPLICIT_ATTEMPT")
    app = create_app(container, auth_required=False)

    with TestClient(app) as client:
        dispatched = client.post(
            f"/api/v1/learners/{learner_id}/review-queue/dispatch",
            json={"available_minutes": 10},
        )
        queue = client.get(f"/api/v1/learners/{learner_id}/review-queue").json()

    assert dispatched.status_code == 200
    assert dispatched.json()["resource_version"]["status"] == "published"
    assert queue["awaiting_resource_count"] == 0
    assert queue["active_task_count"] == 1
