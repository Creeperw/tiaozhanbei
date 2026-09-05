from __future__ import annotations

from competition_app.services.daily_task_scheduler import build_daily_task_schedule


def _candidate(
    *,
    candidate_id: str,
    name: str,
    kp_id: str,
    action: str,
    minutes: int,
    score: float = 0.8,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "eligible": True,
        "knowledge_points": [{"name": name, "kp_id": kp_id}],
        "estimated_minutes": minutes,
        "recommended_action": action,
        "score": score,
        "score_components": {
            "learning_gain": {
                "available": True,
                "value": 0.8,
                "source_refs": [f"mastery:{kp_id}"],
            },
            "retention_benefit": {
                "available": action == "review",
                "value": 0.9 if action == "review" else None,
                "source_refs": [f"review:{kp_id}"] if action == "review" else [],
            },
            "knowledge_coverage": {
                "available": True,
                "value": 1.0,
                "source_refs": [f"knowledge_point:{kp_id}"],
            },
            "difficulty_fit": {
                "available": False,
                "value": None,
                "source_refs": [],
            },
            "autonomy_support": {
                "available": True,
                "value": 0.5,
                "source_refs": ["rule:neutral"],
            },
            "repetition_penalty": {
                "available": True,
                "value": 0.0,
                "source_refs": ["state:test"],
            },
            "uncertainty_risk": {
                "available": True,
                "value": 0.1,
                "source_refs": ["state:test"],
            },
        },
        "source_refs": [
            f"review:{kp_id}" if action == "review" else f"task:{kp_id}"
        ],
        "evidence_refs": [f"knowledge_point:{kp_id}"],
    }


def _resolver(name: str, chapter: str = "") -> str | None:
    return {
        "足三里": "KP_ZUSANLI",
        "阴阳辨证": "KP_YINYANG",
        "方剂配伍": "KP_FANGJI",
    }.get(name)


def test_schedule_combines_due_review_and_new_learning_at_pool_level() -> None:
    schedule = build_daily_task_schedule(
        exam_scope_id="EXAM_TCM",
        target_minutes=60,
        learning_chapter="针灸学",
        intent_knowledge_points=["阴阳辨证"],
        knowledge_point_resolver=_resolver,
        path_candidates={
            "state_digest": "abc",
            "eligible": [
                _candidate(
                    candidate_id="REVIEW",
                    name="足三里",
                    kp_id="KP_ZUSANLI",
                    action="review",
                    minutes=10,
                ),
                _candidate(
                    candidate_id="LEARN",
                    name="阴阳辨证",
                    kp_id="KP_YINYANG",
                    action="learn",
                    minutes=15,
                ),
            ],
            "blocked": [],
        },
        task_load_policy={
            "allocation": {
                "review_minutes": 10,
                "new_learning_minutes": 50,
                "remediation_minutes": 0,
                "buffer_minutes": 0,
            }
        },
    )

    assert {item.kp_id for item in schedule.selected} == {
        "KP_ZUSANLI",
        "KP_YINYANG",
    }
    assert sum(item.estimated_minutes for item in schedule.selected) <= 60
    assert schedule.state_digest == "abc"


def test_prerequisite_required_schedule_cannot_be_replaced_by_stage_content() -> None:
    schedule = build_daily_task_schedule(
        exam_scope_id="EXAM_TCM",
        target_minutes=20,
        learning_chapter="《中医诊断学》望诊章",
        intent_knowledge_points=["阴阳辨证"],
        review_knowledge_points=["足三里"],
        knowledge_point_resolver=_resolver,
        path_candidates={
            "eligible": [
                _candidate(
                    candidate_id="STAGE_2",
                    name="方剂配伍",
                    kp_id="KP_FANGJI",
                    action="new_learning",
                    minutes=5,
                    score=1.0,
                )
            ]
        },
        task_load_policy={"allocation": {"review_minutes": 10}},
        scheduling_mode="prerequisite_required",
    )

    assert [item.knowledge_point_name for item in schedule.selected] == [
        "阴阳辨证"
    ]
    assert all(item.knowledge_point_name != "方剂配伍" for item in schedule.selected)
    assert all(item.knowledge_point_name != "足三里" for item in schedule.selected)


def test_twenty_minute_schedule_selects_one_kp_and_defers_the_rest() -> None:
    schedule = build_daily_task_schedule(
        exam_scope_id="EXAM_TCM",
        target_minutes=20,
        learning_chapter="方剂学",
        intent_knowledge_points=["方剂配伍", "阴阳辨证"],
        knowledge_point_resolver=_resolver,
        path_candidates={"eligible": [], "blocked": []},
    )

    assert len(schedule.selected) == 1
    assert len(schedule.deferred) == 1
    assert "已顺延" in schedule.explanation
    assert len(
        {
            item.candidate_id
            for item in schedule.selected + schedule.deferred
        }
    ) == 2


def test_unresolved_model_knowledge_point_is_blocked_not_invented() -> None:
    schedule = build_daily_task_schedule(
        exam_scope_id="EXAM_TCM",
        target_minutes=20,
        learning_chapter="不存在章节",
        intent_knowledge_points=["模型虚构知识点"],
        knowledge_point_resolver=_resolver,
    )

    assert schedule.selected == []
    assert schedule.deferred == []
    assert len(schedule.blocked) == 1
    assert schedule.blocked[0].kp_id is None
    assert "无法解析" in schedule.blocked[0].reason


def test_missing_difficulty_component_is_renormalized() -> None:
    schedule = build_daily_task_schedule(
        exam_scope_id="EXAM_TCM",
        target_minutes=30,
        learning_chapter="针灸学",
        intent_knowledge_points=[],
        knowledge_point_resolver=_resolver,
        path_candidates={
            "eligible": [
                _candidate(
                    candidate_id="LEARN",
                    name="足三里",
                    kp_id="KP_ZUSANLI",
                    action="learn",
                    minutes=10,
                )
            ],
            "blocked": [],
        },
    )

    trace = schedule.selected[0].score_trace
    assert "difficulty_fit" in trace.missing_positive_components
    assert trace.score > 0


def test_deferred_candidates_do_not_cross_exam_scopes() -> None:
    previous = build_daily_task_schedule(
        exam_scope_id="EXAM_TCM",
        target_minutes=20,
        learning_chapter="方剂学",
        intent_knowledge_points=["方剂配伍", "阴阳辨证"],
        knowledge_point_resolver=_resolver,
    )
    assert previous.deferred

    other_exam = build_daily_task_schedule(
        exam_scope_id="EXAM_INTEGRATED",
        target_minutes=20,
        learning_chapter="当前章节",
        intent_knowledge_points=[],
        knowledge_point_resolver=_resolver,
        previous_schedule=previous,
    )

    assert other_exam.selected == []
    assert other_exam.deferred == []
