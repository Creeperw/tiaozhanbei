from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from datetime import datetime

import pytest
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings
from competition_app.exam_scope import current_exam_workspace
from competition_app.integrations import backend_handoff
from competition_app.integrations.backend_handoff import (
    BackendHandoffRuntime,
    _explicit_profile_updates,
    _model_environment,
    _normalize_profile_memory_value,
)
from competition_app.services.profile_readiness import ProfileReadinessService


def test_memory_agent_normalizes_legacy_instruction_shaped_learning_goal():
    assert _normalize_profile_memory_value(
        "learning_goal",
        "请结合我的学习状态，重新给我制定一份长期规划。我要考取中医执业医师资格证。",
    ) == "中医执业医师资格考试"


def test_load_active_exam_scope_maps_the_persisted_learning_target():
    calls = []

    class FakeDB:
        def close(self):
            calls.append("closed")

    db = FakeDB()
    target = SimpleNamespace(id=11)
    serialized = {
        "scope_id": "EXS_11",
        "exam_track_id": "EXAM_2025_TCM_PHYSICIAN",
        "exam_name": "中医执业医师资格考试",
    }
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.learning_target_service": SimpleNamespace(
            get_active_learning_target=lambda current_db, user_id: (
                calls.append(("load", current_db, user_id)) or target
            ),
            serialize_learning_target=lambda value: (
                calls.append(("serialize", value)) or serialized
            ),
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: (
        calls.append(("user", current_db, external_id))
        or SimpleNamespace(id=7)
    )

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.load_active_exam_scope("external-1")

    assert result == serialized
    assert calls == [
        ("user", db, "external-1"),
        ("load", db, 7),
        ("serialize", target),
        "closed",
    ]


def test_learning_insights_passes_current_automation_trace_to_status_builder():
    captured = {}

    class FakeDB:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class FakeGovernance:
        @staticmethod
        def build_governance_agent_decider():
            return None

        @staticmethod
        def build_learning_insights(*args, **kwargs):
            return {"overview": {"stage_id": "T5"}, "data_quality": {}}

        @staticmethod
        def run_automation_cycle(*args, **kwargs):
            return {
                "insights": {"overview": {"stage_id": "T5"}, "data_quality": {}},
                "intervention": {"intervention_id": 21},
                "intervention_trace": {
                    "requested": True,
                    "executed": True,
                    "outcome": "created",
                },
                "plan_review": None,
            }

        @staticmethod
        def build_intervention_status(*args, **kwargs):
            captured.update(kwargs)
            return {"gate": "delivered"}

    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: FakeDB()),
        "APP.backend.learning_governance_service": FakeGovernance,
        "APP.backend.expert_agent_service": SimpleNamespace(
            decide_learning_intervention=lambda snapshot: None,
            decide_plan_review=lambda snapshot: None,
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.load_learning_insights("external-1", run_automation=True)

    assert captured["automation_requested"] is True
    assert captured["automation_trace"]["outcome"] == "created"
    assert result["intervention_status"]["gate"] == "delivered"


def test_stable_paper_submission_injects_question_explanation_agent():
    calls = []

    class FakeDB:
        def close(self):
            calls.append("closed")

    db = FakeDB()
    explanation_runner = object()
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.paper_submission_service": SimpleNamespace(
            submit_paper=lambda *args, **kwargs: (
                calls.append((args, kwargs)) or {"status": "completed"}
            )
        ),
        "APP.backend.expert_agent_service": SimpleNamespace(
            generate_question_explanation=explanation_runner
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.submit_paper("external-1", "PAPER_1", "request-1")

    assert result == {"status": "completed"}
    assert calls[0][0][1:] == (7, "PAPER_1", "request-1")
    assert calls[0][1]["explanation_runner"] is explanation_runner
    assert calls[-1] == "closed"


def test_daily_task_refreshed_notification_is_deduplicated_per_day():
    calls = []

    class FakeDB:
        def commit(self):
            calls.append("committed")

        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    db = FakeDB()

    class FakeGovernance:
        @staticmethod
        def utc_now():
            from datetime import datetime, timezone

            return datetime(2026, 7, 24, 8, 30, tzinfo=timezone.utc)

        @staticmethod
        def create_notification(*args, **kwargs):
            calls.append(("create_notification", args, kwargs))
            return {"notification_id": "NOTIF_1"}

        @staticmethod
        def serialize_notification(row):
            return {**row, "serialized": True}

    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.learning_governance_service": FakeGovernance,
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.create_daily_task_refreshed_notification(
            "external-1", task_summary="今日围绕《方剂学》补气剂学习。"
        )

    assert result == {"notification_id": "NOTIF_1", "serialized": True}
    create_args = calls[0][1]
    kwargs = calls[0][2]
    assert create_args[1:] == (7,)
    assert kwargs["category"] == "daily_task"
    assert kwargs["title"] == "今日学习任务已更新"
    assert kwargs["dedupe_key"] == "daily-task-refreshed:2026-07-24"
    assert "今日围绕《方剂学》补气剂学习。" in kwargs["message"]
    assert kwargs["action"] == {"type": "navigate", "page": "learning_path"}
    assert calls[-1] == "closed"


def test_daily_task_refreshed_notification_skips_when_preferences_disable_it():
    calls = []

    class FakeDB:
        def commit(self):
            calls.append("committed")

        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    db = FakeDB()

    class FakeGovernance:
        @staticmethod
        def utc_now():
            from datetime import datetime, timezone

            return datetime(2026, 7, 24, 8, 30, tzinfo=timezone.utc)

        @staticmethod
        def create_notification(*args, **kwargs):
            # in_app_enabled=False: governance returns None and writes nothing.
            calls.append(("create_notification", args, kwargs))
            return None

        @staticmethod
        def serialize_notification(row):
            return None

    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.learning_governance_service": FakeGovernance,
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.create_daily_task_refreshed_notification("external-1")

    assert result is None
    assert calls[0][0] == "create_notification"
    assert calls[1:] == ["committed", "closed"]


def test_plan_review_lifecycle_notification_updates_one_record():
    calls = []

    class FakeDB:
        def flush(self):
            calls.append("flushed")

        def commit(self):
            calls.append("committed")

        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    row = SimpleNamespace(
        notification_id="NOTIF_PLAN_1",
        title="",
        message="",
        severity="",
        action_json="{}",
        status="unread",
        read_at=None,
        delivered_at=None,
    )

    class FakeGovernance:
        @staticmethod
        def utc_now():
            from datetime import datetime, timezone

            return datetime(2026, 8, 27, 8, 30, tzinfo=timezone.utc)

        @staticmethod
        def create_notification(*args, **kwargs):
            calls.append(("create_notification", kwargs))
            return row

        @staticmethod
        def serialize_notification(current):
            return {
                "notification_id": current.notification_id,
                "title": current.title,
                "message": current.message,
                "severity": current.severity,
            }

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.learning_governance_service": FakeGovernance,
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        runtime.update_plan_review_lifecycle_notification(
            "external-1", review_id="REVIEW_1", status="queued"
        )
        failed = runtime.update_plan_review_lifecycle_notification(
            "external-1",
            review_id="REVIEW_1",
            status="failed",
            summary="模型服务超时",
        )

    notification_calls = [item for item in calls if isinstance(item, tuple)]
    assert len(notification_calls) == 2
    assert {
        item[1]["dedupe_key"] for item in notification_calls
    } == {"plan-review-lifecycle:REVIEW_1"}
    assert failed["title"] == "规划调整执行失败"
    assert "模型服务超时" in failed["message"]
    assert "已完成" not in failed["message"]
    assert failed["severity"] == "warning"



def test_daily_task_handoff_maps_user_and_rolls_back_failed_upsert():
    calls = []

    class FakeDB:
        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.daily_task_progress_service": SimpleNamespace(
            upsert_daily_task_snapshot=lambda *_: (_ for _ in ()).throw(
                RuntimeError("storage unavailable")
            )
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        with pytest.raises(RuntimeError, match="storage unavailable"):
            runtime.upsert_daily_task_execution("external-1", {"task_id": "TASK_1"})

    assert calls == ["rolled_back", "closed"]


def test_memory_governance_persists_candidates_and_confirmed_replacement_atomically():
    calls = []

    class FakeDB:
        def add(self, item):
            calls.append(("add", item.event_type, item.user_id))

        def commit(self):
            calls.append("committed")

        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    class FakeAgentEvent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(
            SessionLocal=lambda: db,
            AgentEvent=FakeAgentEvent,
        ),
        "APP.backend.health_memory": SimpleNamespace(
            save_extracted_memories=lambda *args, **kwargs: (
                calls.append(("candidates", args[1], args[2], kwargs))
                or {"non_important_candidates": args[2]["candidates"]}
            ),
            apply_confirmed_memory_replacements=lambda *args, **kwargs: (
                calls.append(("replace", args[1], args[2]))
                or {"replaced": [{"memory_id": 7, "successor_id": 8}]}
            ),
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: (
        calls.append(("user", current_db, external_id))
        or SimpleNamespace(id=23)
    )

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.persist_memory_governance(
            "external-23",
            execution_id="EXE_1",
            candidates=[{"summary": "用户长期偏好对比表式资源。"}],
            resolution="replace_existing",
            conflicts=[{"memory_id": 7, "proposed_memory": "每天学习一小时。"}],
        )

    assert calls[0] == ("user", db, "external-23")
    assert calls[1][0:2] == ("candidates", 23)
    assert calls[1][3]["commit"] is False
    assert calls[2] == (
        "replace",
        23,
        [{"memory_id": 7, "proposed_memory": "每天学习一小时。"}],
    )
    assert calls[-2:] == ["committed", "closed"]
    assert result["replaced"] == [{"memory_id": 7, "successor_id": 8}]


def test_memory_governance_writes_auto_confirm_candidates_directly():
    """确定性记忆（auto_confirm_candidates）必须经 important_short_term 直接沉淀，
    而不是进入待确认候选池。"""
    calls = []

    class FakeDB:
        def add(self, item):
            calls.append(("add", getattr(item, "event_type", None)))

        def commit(self):
            calls.append("committed")

        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    class FakeAgentEvent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(
            SessionLocal=lambda: db,
            AgentEvent=FakeAgentEvent,
        ),
        "APP.backend.health_memory": SimpleNamespace(
            save_extracted_memories=lambda *args, **kwargs: (
                calls.append(("saved", args[1], args[2], kwargs))
                or {"non_important_candidates": [], "auto_confirmed": args[2].get("important_short_term", [])}
            ),
            apply_confirmed_memory_replacements=lambda *args, **kwargs: (
                calls.append(("replace", args[1], args[2])) or {"replaced": []}
            ),
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: (
        calls.append(("user", current_db, external_id)) or SimpleNamespace(id=23)
    )

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.persist_memory_governance(
            "external-23",
            execution_id="EXE_2",
            candidates=[{"summary": "用户偏好对比表式资源。"}],
            auto_confirm_candidates=[{"summary": "用户明确每天学习45分钟。"}],
            resolution="none",
            conflicts=[],
        )

    assert calls[0] == ("user", db, "external-23")
    saved_call = next(call for call in calls if call[0] == "saved")
    extracted = saved_call[2]
    assert extracted["candidates"] == [
        {
            "content": "用户偏好对比表式资源。",
            "title": "",
            "importance": "normal",
            "reason": "Memory Agent 提取，等待用户在学习记忆设置中确认。",
            "confidence": 0.8,
            "category": "long_term",
        }
    ]
    assert extracted["important_short_term"] == [
        {
            "content": "用户明确每天学习45分钟。",
            "title": "",
            "importance": "normal",
            "reason": "记忆管理智能体识别为确定性信息，直接沉淀。",
            "confidence": 0.9,
            "requires_confirmation": False,
            "category": "long_term",
        }
    ]
    # 普通候选走 pending 确认流程，auto-confirm 走直接沉淀
    assert result["auto_confirmed"] == extracted["important_short_term"]
    assert result["candidates"] == []
    assert calls[-2:] == ["committed", "closed"]


def test_memory_governance_rolls_back_if_replacement_fails():
    calls = []

    class FakeDB:
        def add(self, item):
            calls.append("added")

        def commit(self):
            calls.append("committed")

        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(
            SessionLocal=lambda: db,
            AgentEvent=lambda **kwargs: kwargs,
        ),
        "APP.backend.health_memory": SimpleNamespace(
            save_extracted_memories=lambda *args, **kwargs: {},
            apply_confirmed_memory_replacements=lambda *args, **kwargs: (
                (_ for _ in ()).throw(ValueError("foreign memory"))
            ),
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=23)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        with pytest.raises(ValueError, match="foreign memory"):
            runtime.persist_memory_governance(
                "external-23",
                execution_id="EXE_1",
                candidates=[],
                resolution="replace_existing",
                conflicts=[{"memory_id": 99, "proposed_memory": "新值"}],
            )

    assert calls == ["rolled_back", "closed"]


def test_legacy_simulated_patient_activity_is_idempotent_and_invalidates_cache():
    calls = []
    records = []

    class FakeQuery:
        def filter_by(self, **kwargs):
            calls.append(("filter", kwargs))
            self.criteria = kwargs
            return self

        def one_or_none(self):
            for record in records:
                if all(getattr(record, key) == value for key, value in self.criteria.items()):
                    return record
            return None

    class FakeDB:
        def query(self, model):
            return FakeQuery()

        def add(self, record):
            records.append(record)

        def commit(self):
            calls.append("committed")

        def rollback(self):
            calls.append("rolled_back")

        def close(self):
            calls.append("closed")

    class FakeActivity:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(
            SessionLocal=lambda: db,
            LearningActivityRecord=FakeActivity,
        ),
        "APP.backend.system_data_service": SimpleNamespace(
            rebuild_system_data=lambda current_db, **kwargs: calls.append(
                ("rebuild", current_db, kwargs)
            )
        ),
        "APP.backend.time_utils": SimpleNamespace(utc_now=lambda: "NOW"),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)
    runtime.invalidate_learning_context = lambda external_id: calls.append(
        ("invalidate", external_id)
    )
    result = {
        "history_id": "HIST_STABLE_1",
        "session_id": "SESSION_1",
        "practice_scope": "acupuncture",
        "data": {"grading_report": {"score": 88, "diagnosis_correct": True}},
    }

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        first = runtime.record_legacy_simulated_patient_activity("external-1", result)
        replay = runtime.record_legacy_simulated_patient_activity("external-1", result)

    assert first == {"projected": True, "history_id": "HIST_STABLE_1"}
    assert replay == {"projected": False, "history_id": "HIST_STABLE_1"}
    assert len(records) == 1
    assert records[0].activity_type == "case_training"
    assert records[0].resource_type == "simulated_patient_session"
    assert records[0].resource_id == "HIST_STABLE_1"
    assert records[0].score == 0.88
    assert sum(call[0] == "rebuild" for call in calls if isinstance(call, tuple)) == 1
    assert calls.count(("invalidate", "external-1")) == 2


def test_formal_knowledge_point_resolver_delegates_to_handoff_repository():
    calls = []

    class FakeDB:
        def close(self):
            calls.append("closed")

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.daily_task_progress_service": SimpleNamespace(
            resolve_executable_knowledge_point=lambda current_db, name, **kwargs: (
                calls.append((current_db, name, kwargs)) or "KP_FORMAL_1"
            )
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.resolve_executable_knowledge_point("四君子汤")

    assert result == "KP_FORMAL_1"
    assert calls[0] == (db, "四君子汤", {"required_question_count": 3})
    assert calls[-1] == "closed"


def test_publish_agent_paper_forwards_optional_daily_task_item_id():
    calls = []

    class FakeDB:
        def close(self):
            calls.append("closed")

    db = FakeDB()
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.learning_workshop_service": SimpleNamespace(
            publish_agent_paper=lambda *args, **kwargs: (
                calls.append((args, kwargs)) or {"paper_id": "PAPER_1", "status": "published"}
            )
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        result = runtime.publish_agent_paper(
            "external-1",
            execution_id="EXEC_1",
            paper={},
            blueprint={},
            evidence_pack={},
            daily_task_item_id="ITEM_BOUND",
        )

    assert result == {"paper_id": "PAPER_1", "status": "published"}
    assert calls[0][1]["user_id"] == 7
    assert calls[0][1]["daily_task_item_id"] == "ITEM_BOUND"
    assert calls[-1] == "closed"


def test_knowledge_card_handoff_maps_external_user_for_list_get_and_save():
    calls = []

    class FakeDB:
        def close(self):
            calls.append("closed")

    db = FakeDB()
    service = SimpleNamespace(
        list_knowledge_cards=lambda *args, **kwargs: (
            calls.append(("list", args, kwargs)) or {"items": []}
        ),
        get_knowledge_card=lambda *args, **kwargs: (
            calls.append(("get", args, kwargs)) or {"card_id": "CARD_1"}
        ),
        upsert_knowledge_card=lambda *args, **kwargs: (
            calls.append(("save", args, kwargs)) or {"card_id": "CARD_1"}
        ),
    )
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.learning_workshop_service": service,
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: SimpleNamespace(id=7)

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        assert runtime.list_knowledge_cards("external-1", offset=3, limit=5) == {
            "items": []
        }
        assert runtime.get_knowledge_card("external-1", "CARD_1") == {
            "card_id": "CARD_1"
        }
        assert runtime.save_knowledge_card(
            "external-1",
            kp_id="KP_1",
            title="阴阳学说",
            resource_bundle={"summary": "核心概念"},
            source_execution_id="EXEC_1",
        ) == {"card_id": "CARD_1"}

    assert calls[0] == ("list", (db,), {"user_id": 7, "offset": 3, "limit": 5})
    assert calls[2] == ("get", (db,), {"user_id": 7, "card_id": "CARD_1"})
    assert calls[4] == (
        "save",
        (db,),
        {
            "user_id": 7,
            "kp_id": "KP_1",
            "title": "阴阳学说",
            "resource_bundle": {"summary": "核心概念"},
            "source_execution_id": "EXEC_1",
        },
    )
    assert calls.count("closed") == 3


def test_memory_agent_drops_generic_planning_instruction_as_goal():
    assert _normalize_profile_memory_value(
        "learning_goal",
        "请结合我的学习状态，为我制定一份学习计划。",
    ) == ""


def test_explicit_profile_fallback_captures_current_turn_without_inference():
    assert _explicit_profile_updates(
        "请制定长期规划。我想考中医执业医师资格考试，目前零基础，"
        "我是计算机专业，每周可以学习4天、每天4小时。"
    ) == {
        "learning_goal": "中医执业医师资格考试",
        "learning_background": "零基础，计算机专业",
        "time_constraints": "每周可以学习4天，每天4小时",
    }


def test_explicit_profile_fallback_accepts_bare_exam_answer():
    assert _explicit_profile_updates("中医执业医师考试") == {
        "learning_goal": "中医执业医师资格考试"
    }


def test_explicit_profile_fallback_keeps_major_after_zero_basis_clause():
    updates = _explicit_profile_updates(
        "请制定长期计划。我是零基础，计算机专业，每周学习4天，每天2小时。"
    )

    assert updates["learning_background"] == "零基础，计算机专业"
    assert updates["time_constraints"] == "每周学习4天，每天2小时"


def test_explicit_profile_fallback_does_not_invent_missing_facts():
    assert _explicit_profile_updates("请结合我的学习状态制定长期规划") == {}


def test_registration_survey_becomes_agent_ready_profile_context():
    assert hasattr(backend_handoff, "_onboarding_profile_context")
    onboarding = {
        "status": "onboarding_completed",
        "survey_answers": {
            "learner_group": "academic",
            "learner_group_title": "学历教育群体",
            "major_or_role": "非医学专业",
            "tcm_foundation": "零基础",
            "learned_courses": [],
            "long_term_goal": "职业技能认证",
            "target_exam_or_course": "中医执业医师",
            "textbook_route_id": "textbook_tcm_physician",
            "textbook_route_version": 1,
            "daily_available_minutes": 45,
            "preferred_time_slot": "晚间",
            "resource_preference": ["知识卡片", "分阶测试题"],
            "custom_requirements": "希望侧重方剂背诵，每天只学 30 分钟",
        },
        "l0_baseline": {
            "stage_id": "L0",
            "major_or_role": "非医学专业",
            "target_exam_or_course": "中医执业医师",
            "textbook_route_id": "textbook_tcm_physician",
            "textbook_route_version": 1,
            "daily_available_minutes": 45,
        },
    }

    profile = backend_handoff._onboarding_profile_context(onboarding)

    assert profile["learning_goal"] == "中医执业医师"
    assert profile["learning_background"] == "零基础；非医学专业"
    assert profile["daily_available_minutes"] == 45
    assert profile["user_major_or_profession"] == "非医学专业"
    assert profile["custom_requirements"] == "希望侧重方剂背诵，每天只学 30 分钟"
    assert profile["user_preference"]["custom_requirements"] == "希望侧重方剂背诵，每天只学 30 分钟"
    assert profile["goals"] == {
        "goal_type": "credential",
        "goal_name": "中医执业医师",
        "long_term_goal": "职业技能认证",
        "short_term_goal": "",
        "textbook_route_id": "textbook_tcm_physician",
        "textbook_route_version": 1,
    }
    readiness = ProfileReadinessService().evaluate(
        {"user_profile": profile},
        "long_term",
    )
    assert readiness.can_proceed is True
    assert readiness.questions == []


def test_multiscale_loaders_map_external_user_and_share_the_read_transaction():
    calls = []

    class FakeDB:
        def close(self):
            calls.append(("close", self))

    db = FakeDB()
    state = {
        "schema_version": "1.0",
        "learner_id": "7",
        "state_digest": "a" * 24,
    }
    candidates = {
        "schema_version": "1.0",
        "learner_id": "7",
        "scope": "daily_task",
        "items": [],
    }
    modules = {
        "APP.backend.database": SimpleNamespace(SessionLocal=lambda: db),
        "APP.backend.multiscale_learning_service": SimpleNamespace(
            verify_host_plan_context=lambda context, **kwargs: {
                **context,
                "verified_for": kwargs["external_user_id"],
            },
            build_multiscale_state=lambda current_db, user_id, **kwargs: (
                calls.append(("state", current_db, user_id, kwargs)) or state
            ),
            build_path_candidates=lambda current_db, user_id, **kwargs: (
                calls.append(("candidates", current_db, user_id, kwargs))
                or candidates
            ),
        ),
    }
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._workshop_user = lambda current_db, external_id: (
        calls.append(("user", current_db, external_id))
        or SimpleNamespace(id=7)
    )

    with patch.object(
        backend_handoff.importlib,
        "import_module",
        side_effect=lambda name: modules[name],
    ):
        loaded_state = runtime.load_multiscale_learning_state(
            "external-1",
            plan_context={"long_term_plan": {"plan_id": "LONG_1"}},
            window_days=30,
        )
        loaded_candidates = runtime.load_path_candidates(
            "external-1",
            plan_context={"short_term_plan": {"plan_id": "SHORT_1"}},
            scope="daily_task",
            limit=5,
            include_blocked=False,
        )

    assert loaded_state is state
    assert loaded_candidates is candidates
    assert calls[0] == ("user", db, "external-1")
    assert calls[1] == (
        "state",
        db,
        7,
        {
            "plan_context": {
                "long_term_plan": {"plan_id": "LONG_1"},
                "verified_for": "external-1",
            },
            "window_days": 30,
        },
    )
    assert calls[3] == ("user", db, "external-1")
    assert calls[4] == (
        "candidates",
        db,
        7,
        {
            "plan_context": {
                "short_term_plan": {"plan_id": "SHORT_1"},
                "verified_for": "external-1",
            },
            "scope": "daily_task",
            "limit": 5,
            "include_blocked": False,
        },
    )
    assert calls[2] == ("close", db)
    assert calls[5] == ("close", db)


class FakeBackendHandoffRuntime:
    def __init__(self, question_attempts: list[dict] | None = None) -> None:
        self.app = FastAPI()
        self._started = False
        self.active_exam_scope = {}
        self.active_exam_scope_calls = []
        self.invalidated_user_ids = []

        @self.app.get("/handoff-ping")
        async def ping():
            return {"source": "handoff"}

        @self.app.get("/handoff-identity")
        async def identity(request: Request):
            user = getattr(request.state, "current_user", None)
            return {
                "user_id": user.user_id if user is not None else None,
                "username": user.username if user is not None else None,
            }

        @self.app.put("/personalization/learning-target")
        async def update_target_for_test(request: Request, response: Response):
            payload = await request.json()
            if payload.get("exam_track_id") == "FAIL_TARGET":
                raise HTTPException(status_code=422, detail="invalid target")
            self.active_exam_scope = {
                "exam_track_id": payload["exam_track_id"],
            }
            response.headers["X-Competition-Learning-Target-Changed"] = "1"
            return {"target": dict(self.active_exam_scope)}

        @self.app.put("/personalization/learning-target/current")
        async def switch_target_for_test(request: Request, response: Response):
            return await update_target_for_test(request, response)

        @self.app.post("/personalization/learning-targets")
        async def enroll_targets_for_test(request: Request, response: Response):
            payload = await request.json()
            self.active_exam_scope = {
                "exam_track_id": payload["current_exam_track_id"],
            }
            response.headers["X-Competition-Learning-Target-Changed"] = "1"
            return {"target": dict(self.active_exam_scope)}

        @self.app.post("/training/onboarding/survey")
        async def submit_onboarding_for_test(request: Request, response: Response):
            payload = await request.json()
            exam_track_id = str(payload.get("exam_track_id") or "").strip()
            if exam_track_id:
                self.active_exam_scope = {"exam_track_id": exam_track_id}
                response.headers["X-Competition-Learning-Target-Changed"] = "1"
            return {
                "status": "onboarding_completed",
                "learning_target": (
                    dict(self.active_exam_scope) if exam_track_id else None
                ),
            }

        @self.app.get("/handoff-exam-scope")
        async def bound_exam_scope(request: Request):
            user = getattr(request.state, "current_user", None)
            workspace = current_exam_workspace(
                user.user_id if user is not None else None
            )
            return {
                "exam_track_id": (
                    workspace.exam_track_id if workspace is not None else None
                )
            }

        self.loaded_user_ids = []
        self.question_attempts = question_attempts or []
        self.profile_updates = []

    async def startup(self) -> None:
        self._started = True

    async def shutdown(self) -> None:
        self._started = False

    def status(self) -> dict[str, object]:
        return {
            "enabled": True,
            "mounted": True,
            "route_count": len(self.app.routes),
            "started": self._started,
        }

    def load_learning_context(self, external_user_id: str) -> dict:
        self.loaded_user_ids.append(external_user_id)
        return {
            "source": "frontend_backend",
            "calculated_at": "2026-07-21T08:00:00+08:00",
            "learning_profile": {"weak_kp_ids": ["KP_WEAK_1"]},
            "system_data": {"task_completion_rate": {"value": 0.5}},
            "learning_trends": {"days": 7, "series": []},
            "question_attempt": self.question_attempts,
        }

    def load_active_exam_scope(self, external_user_id: str) -> dict:
        self.active_exam_scope_calls.append(external_user_id)
        return dict(self.active_exam_scope)

    def invalidate_learning_context(self, external_user_id: str) -> None:
        self.invalidated_user_ids.append(external_user_id)

    def update_learning_profile(self, external_user_id: str, updates: dict, execution_id=None) -> dict:
        self.profile_updates.append((external_user_id, updates, execution_id))
        return dict(updates)

    def load_review_dashboard(self, external_user_id: str, *, history_limit: int = 100) -> dict:
        return {
            "schema_version": "1.0",
            "learner_id": external_user_id,
            "mastery": [{
                "kp_id": "KP_FJ_001", "kp_name": "四君子汤",
                "mastery_score": 82.0, "attempt_count": 2,
            }],
            "mastery_history": [{
                "history_id": "H_1", "kp_id": "KP_FJ_001",
                "kp_name": "四君子汤", "mastery_score": 82.0,
            }],
            "review_states": [],
            "review_tasks": [],
        }


def test_settings_parse_backend_handoff_configuration() -> None:
    settings = Settings.from_env(
        {
            "BACKEND_HANDOFF_ENABLED": "true",
            "BACKEND_HANDOFF_ROOT": "/tmp/backend-handoff",
            "BACKEND_HANDOFF_RUNTIME_ROOT": "/tmp/backend-runtime",
            "BACKEND_HANDOFF_MYSQL_DATABASE": "frontend_domain",
            "BACKEND_HANDOFF_SECRET_KEY": "test-secret",
        }
    )

    assert settings.backend_handoff_enabled is True
    assert settings.backend_handoff_root == Path("/tmp/backend-handoff")
    assert settings.backend_handoff_runtime_root == Path("/tmp/backend-runtime")
    assert settings.backend_handoff_mysql_database == "frontend_domain"
    assert settings.backend_handoff_secret_key == "test-secret"


def test_handoff_uses_main_model_stack_enables_embedding_and_disables_voice() -> None:
    settings = Settings(
        chat_base_url="https://main-model.test/v1",
        chat_model="deepseek-v4-flash",
        embedding_model="Qwen/Qwen3-Embedding-4B",
        llm_api_key="secret-for-test",
        embedding_api_key="embedding-secret-for-test",
    )

    environment = _model_environment(settings)

    assert environment["LLM_API_BASE_URL"] == settings.chat_base_url
    assert environment["LLM_API_MODEL"] == settings.chat_model
    assert environment["PLANNER_EXECUTOR_MODEL"] == settings.chat_model
    assert environment["MANAGER_REVIEWER_MODEL"] == settings.chat_model
    assert environment["EMBEDDING_MODEL_ID"] == settings.embedding_model
    assert environment["EMBEDDING_MODE"] == "enabled"
    assert environment["EMBEDDING_API_BASE_URL"] == settings.embedding_base_url
    assert environment["EMBEDDING_API_KEY"] == settings.embedding_api_key
    assert environment["VOICE_MODE"] == "disabled"


def test_delivered_routes_require_the_main_cookie_boundary() -> None:
    container = ApplicationContainer.build(Settings())
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime
    app = create_app(container, auth_required=True)

    with TestClient(app) as client:
        handoff_response = client.get("/handoff-ping")
        protected_status = client.get("/api/v1/platform/status")

    assert handoff_response.status_code == 401
    assert protected_status.status_code == 401
    assert runtime._started is False


def test_platform_status_exposes_mounted_contract_when_auth_is_disabled() -> None:
    container = ApplicationContainer.build(Settings())
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/api/v1/platform/status")
        assert response.status_code == 200
        assert response.json()["mounted"] is True
        assert response.json()["started"] is True


def test_production_api_prefix_reaches_delivered_business_routes() -> None:
    container = ApplicationContainer.build(Settings())
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/api/handoff-ping")

    assert response.status_code == 200
    assert response.json() == {"source": "handoff"}


def test_formal_frontend_assistant_character_assets_are_mounted(tmp_path) -> None:
    asset_root = tmp_path / "assistant-character"
    asset_root.mkdir(parents=True)
    (asset_root / "avatar.png").write_bytes(b"frontend-avatar")
    container = ApplicationContainer.build(
        Settings(frontend_dist_root=tmp_path),
        snapshot_root=tmp_path / "snapshots",
    )

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/assistant-character/avatar.png")

    assert response.status_code == 200
    assert response.content == b"frontend-avatar"


def test_formal_frontend_learning_stage_assets_are_mounted(tmp_path) -> None:
    asset_root = tmp_path / "learning-stage"
    asset_root.mkdir(parents=True)
    (asset_root / "foundation.png").write_bytes(b"learning-stage-artwork")
    container = ApplicationContainer.build(
        Settings(frontend_dist_root=tmp_path),
        snapshot_root=tmp_path / "snapshots",
    )

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/learning-stage/foundation.png")

    assert response.status_code == 200
    assert response.content == b"learning-stage-artwork"


def test_formal_frontend_acupuncture_models_are_mounted(tmp_path) -> None:
    (tmp_path / "blender.yibiaozhu.glb").write_bytes(b"acupuncture-model")
    container = ApplicationContainer.build(
        Settings(frontend_dist_root=tmp_path),
        snapshot_root=tmp_path / "snapshots",
    )

    with TestClient(create_app(container, auth_required=True)) as client:
        response = client.get("/acupuncture-models/blender.yibiaozhu.glb")

    assert response.status_code == 200
    assert response.content == b"acupuncture-model"


def test_formal_frontend_textbook_cover_assets_are_mounted(tmp_path) -> None:
    asset_root = tmp_path / "textbook-covers"
    asset_root.mkdir(parents=True)
    (asset_root / "方剂学.jpg").write_bytes(b"textbook-cover")
    container = ApplicationContainer.build(
        Settings(frontend_dist_root=tmp_path),
        snapshot_root=tmp_path / "snapshots",
    )

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/textbook-covers/%E6%96%B9%E5%89%82%E5%AD%A6.jpg")

    assert response.status_code == 200
    assert response.content == b"textbook-cover"


def test_main_cookie_identity_reaches_mounted_business_routes(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "mounted-owner",
                "display_name": "集成同学",
                "password": "correct-horse-2026",
            },
        ).json()["user"]
        response = client.get("/handoff-identity")

    assert response.status_code == 200
    assert response.json() == {
        "user_id": registered["user_id"],
        "username": "mounted-owner",
    }


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "PUT",
            "/api/personalization/learning-target",
            {
                "target_type": "certification",
                "exam_track_id": "EXAM_2025_TCM_PHYSICIAN",
            },
        ),
        (
            "PUT",
            "/api/personalization/learning-target/current",
            {
                "target_type": "certification",
                "exam_track_id": "EXAM_2025_TCM_PHYSICIAN",
            },
        ),
        (
            "POST",
            "/api/personalization/learning-targets",
            {
                "exam_track_ids": ["EXAM_2025_TCM_PHYSICIAN"],
                "current_exam_track_id": "EXAM_2025_TCM_PHYSICIAN",
            },
        ),
        (
            "POST",
            "/api/training/onboarding/survey",
            {
                "learner_group": "academic",
                "target_type": "certification",
                "exam_track_id": "EXAM_2025_TCM_PHYSICIAN",
            },
        ),
    ],
)
def test_learning_target_mutations_invalidate_exam_and_learning_context_caches(
    tmp_path,
    method: str,
    path: str,
    payload: dict,
) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "exam-scope-owner",
                "display_name": "考试空间同学",
                "password": "correct-horse-2026",
            },
        ).json()["user"]
        user_id = registered["user_id"]

        # The registration request populates the empty exam-scope cache.
        runtime.active_exam_scope_calls.clear()
        updated = client.request(method, path, json=payload)
        scope = client.get("/handoff-exam-scope")

    assert updated.status_code == 200
    assert scope.status_code == 200
    assert runtime.invalidated_user_ids == [user_id]
    assert runtime.active_exam_scope_calls == [user_id, user_id]
    assert scope.json() == {"exam_track_id": "EXAM_2025_TCM_PHYSICIAN"}


@pytest.mark.parametrize("status_code", [200, 422])
def test_profile_mutation_invalidates_only_learning_context_on_success(tmp_path, status_code) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    @runtime.app.put("/personalization/learner-profile")
    async def update_profile_for_test(response: Response):
        response.status_code = status_code
        response.headers["X-Competition-Learner-Profile-Changed"] = "1"
        return {"resource_preference": ["案例训练"]}

    with TestClient(create_app(container, auth_required=True)) as client:
        user = client.post("/api/v1/auth/register", json={
            "username": "profile-cache-owner", "display_name": "偏好测试",
            "password": "correct-horse-2026",
        }).json()["user"]
        client.get("/handoff-exam-scope")
        runtime.active_exam_scope_calls.clear()
        updated = client.put("/api/personalization/learner-profile", json={"resource_preference": ["案例训练"]})
        client.get("/handoff-exam-scope")

    assert updated.status_code == status_code
    assert "X-Competition-Learner-Profile-Changed" not in updated.headers
    assert runtime.invalidated_user_ids == ([user["user_id"]] if status_code == 200 else [])
    assert runtime.active_exam_scope_calls == []


def test_failed_learning_target_mutation_keeps_existing_caches(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "failed-scope-owner",
                "display_name": "失败目标同学",
                "password": "correct-horse-2026",
            },
        )
        client.get("/handoff-exam-scope")
        runtime.active_exam_scope_calls.clear()

        failed = client.put(
            "/api/personalization/learning-target",
            json={
                "target_type": "certification",
                "exam_track_id": "FAIL_TARGET",
            },
        )
        scope = client.get("/handoff-exam-scope")

    assert failed.status_code == 422
    assert runtime.invalidated_user_ids == []
    assert runtime.active_exam_scope_calls == []
    assert scope.json() == {"exam_track_id": ""}


def test_onboarding_without_learning_target_keeps_existing_caches(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        client.post(
            "/api/v1/auth/register",
            json={
                "username": "profile-only-onboarding",
                "display_name": "仅画像同学",
                "password": "correct-horse-2026",
            },
        )
        client.get("/handoff-exam-scope")
        runtime.active_exam_scope_calls.clear()

        submitted = client.post(
            "/api/training/onboarding/survey",
            json={"learner_group": "academic"},
        )
        scope = client.get("/handoff-exam-scope")

    assert submitted.status_code == 200
    assert runtime.invalidated_user_ids == []
    assert runtime.active_exam_scope_calls == []
    assert scope.json() == {"exam_track_id": ""}


def test_learning_target_cache_invalidation_is_isolated_per_user(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as app_client:
        alice = TestClient(app_client.app)
        bob = TestClient(app_client.app)
        try:
            alice_user = alice.post(
                "/api/v1/auth/register",
                json={
                    "username": "target-alice",
                    "display_name": "甲同学",
                    "password": "correct-horse-2026",
                },
            ).json()["user"]
            bob_user = bob.post(
                "/api/v1/auth/register",
                json={
                    "username": "target-bob",
                    "display_name": "乙同学",
                    "password": "correct-horse-2026",
                },
            ).json()["user"]
            alice.get("/handoff-exam-scope")
            bob.get("/handoff-exam-scope")
            runtime.active_exam_scope_calls.clear()

            updated = alice.put(
                "/api/personalization/learning-target",
                json={
                    "target_type": "certification",
                    "exam_track_id": "EXAM_2025_TCM_PHYSICIAN",
                },
            )
            bob_scope = bob.get("/handoff-exam-scope")
            alice_scope = alice.get("/handoff-exam-scope")
        finally:
            alice.close()
            bob.close()

    assert updated.status_code == 200
    assert runtime.invalidated_user_ids == [alice_user["user_id"]]
    assert runtime.active_exam_scope_calls == [alice_user["user_id"]]
    assert bob_user["user_id"] != alice_user["user_id"]
    assert bob_scope.json() == {"exam_track_id": ""}
    assert alice_scope.json() == {"exam_track_id": "EXAM_2025_TCM_PHYSICIAN"}


def test_runtime_learning_context_invalidation_clears_only_selected_user() -> None:
    runtime = object.__new__(BackendHandoffRuntime)
    runtime._learning_context_cache = {
        ("alice", 7): (1.0, {"days": 7}),
        ("alice", 30): (1.0, {"days": 30}),
        ("bob", 30): (1.0, {"days": 30}),
    }
    runtime._learning_context_lock = __import__("threading").RLock()

    runtime.invalidate_learning_context("alice")

    assert runtime._learning_context_cache == {
        ("bob", 30): (1.0, {"days": 30})
    }


def test_spa_page_keeps_precedence_over_legacy_root_mount(tmp_path) -> None:
    frontend_root = tmp_path / "frontend"
    frontend_root.mkdir()
    (frontend_root / "index.html").write_text(
        '<div id="spa-route-owner"></div>', encoding="utf-8"
    )
    container = ApplicationContainer.build(
        Settings(mode="stub", frontend_dist_root=frontend_root),
        snapshot_root=tmp_path / "snapshots",
    )
    container.backend_handoff_runtime = FakeBackendHandoffRuntime()

    with TestClient(create_app(container, auth_required=False)) as client:
        response = client.get("/assistant")

    assert response.status_code == 200
    assert '<div id="spa-route-owner"></div>' in response.text


def test_learning_context_uses_authenticated_host_identity(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "behavior-owner",
                "display_name": "行为同学",
                "password": "correct-horse-2026",
            },
        )
        user_id = registered.json()["user"]["user_id"]
        response = client.get("/api/v1/learning-context")

    assert response.status_code == 200
    assert response.json()["learner_id"] == user_id
    assert response.json()["learning_profile"]["weak_kp_ids"] == ["KP_WEAK_1"]
    assert runtime.loaded_user_ids == [user_id]


def test_learning_context_projects_completed_questions_into_review_queue_once(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime([{
        "attempt_id": "SERVER_QUESTION_ATTEMPT_1",
        "kp_ids": ["KP_FJ_001"],
        "knowledge_point_name": "四君子汤",
        "is_correct": True,
        "score": 100,
        "answered_at": "2026-07-21T08:00:00Z",
    }])
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "review-owner",
                "display_name": "复习同学",
                "password": "correct-horse-2026",
            },
        )
        user_id = registered.json()["user"]["user_id"]
        first = client.get("/api/v1/learning-context").json()
        version = first["review_queue"]["entries"][0]["memory_unit"]["version"]
        replay = client.get("/api/v1/learning-context").json()

    assert first["review_queue"]["entries"][0]["memory_unit"]["source_attempt_id"] == "SERVER_QUESTION_ATTEMPT_1"
    assert replay["review_queue"]["entries"][0]["memory_unit"]["version"] == version


def test_review_dashboard_combines_queue_mastery_and_history_for_current_user(tmp_path) -> None:
    container = ApplicationContainer.build(Settings(mode="stub"), snapshot_root=tmp_path)
    runtime = FakeBackendHandoffRuntime()
    container.backend_handoff_runtime = runtime

    with TestClient(create_app(container, auth_required=True)) as client:
        registered = client.post(
            "/api/v1/auth/register",
            json={
                "username": "review-dashboard-owner",
                "display_name": "复习看板同学",
                "password": "correct-horse-2026",
            },
        )
        user_id = registered.json()["user"]["user_id"]
        response = client.get("/api/v1/review-dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert payload["learner_id"] == user_id
    assert payload["mastery"][0]["kp_name"] == "四君子汤"
    assert payload["summary"]["average_mastery"] == 82.0
    assert payload["summary"]["history_count"] == 1


def _review_dashboard_sqlite_runtime():
    from APP.backend import auth, database
    from APP.backend import knowledge_point_history_replay as replay
    from APP.backend import knowledge_point_identity_service as identity

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    database.Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    runtime = object.__new__(BackendHandoffRuntime)
    runtime.review_context_provider = None
    modules = {
        "APP.backend.database": SimpleNamespace(
            **{
                name: getattr(database, name)
                for name in (
                    "KnowledgeMasteryState",
                    "LearnerKPReviewState",
                    "MasteryHistoryRecord",
                    "ReviewTaskRecord",
                    "KnowledgePoint",
                    "KnowledgePointCanonicalMap",
                )
            },
            SessionLocal=Session,
        ),
        "APP.backend.auth": auth,
        "APP.backend.knowledge_point_identity_service": identity,
        "APP.backend.knowledge_point_history_replay": replay,
    }
    return runtime, engine, Session, modules, database


@pytest.mark.parametrize("mapping_status", [None, "inactive", "active"])
def test_review_dashboard_preserves_unmapped_singleton_raw_history(mapping_status) -> None:
    runtime, engine, Session, modules, database = _review_dashboard_sqlite_runtime()
    db = Session()
    try:
        db.add(database.UserModel(
            id=1,
            username="singleton-history-owner",
            email="singleton-history@example.com",
            hashed_password="x",
        ))
        db.add(database.ExternalIdentityLink(
            provider="competition_app",
            external_user_id="external-singleton",
            user_id=1,
        ))
        db.add(database.KnowledgePoint(kp_id="KP_SINGLE", name="普通知识点"))
        if mapping_status:
            db.add(database.KnowledgePointCanonicalMap(
                mapping_id="SELF_MAP", source_kp_id="KP_SINGLE",
                canonical_kp_id="KP_SINGLE", status=mapping_status,
            ))
        db.add(database.KnowledgePointCanonicalMap(
            mapping_id="NEWER_MAP", source_kp_id="KP_DUPLICATE",
            canonical_kp_id="KP_CANONICAL", status="active",
        ))
        db.add(database.MasteryHistoryRecord(
            history_id="H_NEWER_MAPPED", learner_id=1, kp_id="KP_DUPLICATE",
            mastery_score=99, calculated_at=datetime(2026, 8, 26),
        ))
        db.add(database.UserModel(id=2, username="other-history-owner", hashed_password="x"))
        db.add(database.MasteryHistoryRecord(
            history_id="H_OTHER_USER", learner_id=2, kp_id="KP_SINGLE",
            mastery_score=100, calculated_at=datetime(2026, 8, 27),
        ))
        db.add(database.MasteryHistoryRecord(
            history_id="H_SINGLE",
            learner_id=1,
            kp_id="KP_SINGLE",
            trigger_attempt_item_id="ITEM_SINGLE",
            mastery_score=63.0,
            mastery_confidence=0.72,
            calculated_at=datetime(2026, 8, 25, 8, 0, 0),
        ))
        db.commit()
    finally:
        db.close()

    try:
        with patch.object(
            backend_handoff.importlib,
            "import_module",
            side_effect=lambda name: modules[name],
        ):
            payload = runtime.load_review_dashboard("external-singleton", history_limit=1)

        if mapping_status == "active":
            assert payload["mastery_history"] == []
            return
        assert payload["mastery_history"] == [{
            "history_id": "H_SINGLE",
            "kp_id": "KP_SINGLE",
            "kp_name": "普通知识点",
            "source_kp_ids": ["KP_SINGLE"],
            "mastery_score": 63.0,
            "mastery_confidence": 0.72,
            "trigger_attempt_item_id": "ITEM_SINGLE",
            "calculated_at": "2026-08-25T08:00:00",
            "projection_source": "legacy_raw_history",
        }]
    finally:
        engine.dispose()


@pytest.mark.parametrize("kind", ["mastery", "review"])
@pytest.mark.parametrize("has_canonical", [False, True])
def test_review_dashboard_rejects_ambiguous_state_without_canonical(kind, has_canonical):
    runtime, engine, Session, modules, database = _review_dashboard_sqlite_runtime()
    try:
        with Session() as db:
            db.add(database.UserModel(id=1, username="state-owner", hashed_password="x"))
            db.add(database.ExternalIdentityLink(provider="competition_app", external_user_id="state-owner", user_id=1))
            for kp_id in ("A", "B", "C"):
                db.add(database.KnowledgePoint(kp_id=kp_id, name=kp_id))
            for kp_id in ("A", "B"):
                db.add(database.KnowledgePointCanonicalMap(mapping_id=kp_id, source_kp_id=kp_id, canonical_kp_id="C", decision="equivalent", status="active"))
            ids = ["A", "B", "C"] if has_canonical else ["A", "B"]
            for index, kp_id in enumerate(ids):
                if kind == "mastery":
                    db.add(database.KnowledgeMasteryState(mastery_state_id=kp_id, learner_id=1, kp_id=kp_id, mastery_score=20 + index * 30))
                else:
                    db.add(database.LearnerKPReviewState(review_state_id=kp_id, learner_id=1, kp_id=kp_id, review_stage=str(index)))
            db.commit()
        with patch.object(backend_handoff.importlib, "import_module", side_effect=lambda name: modules[name]):
            if not has_canonical:
                with pytest.raises(ValueError, match="canonical projection is missing for C"):
                    runtime.load_review_dashboard("state-owner")
            else:
                payload = runtime.load_review_dashboard("state-owner")
                key = "mastery" if kind == "mastery" else "review_states"
                # The selected projection must contain only the canonical identity.
                rows = payload[key]
                assert len(rows) == 1
                assert rows[0]["kp_id"] == "C"
                assert rows[0]["mastery_score" if kind == "mastery" else "review_stage"] == (80 if kind == "mastery" else "2")
    finally:
        engine.dispose()


def test_review_dashboard_never_falls_back_to_raw_history_for_mapped_lineage() -> None:
    runtime, engine, Session, modules, database = _review_dashboard_sqlite_runtime()
    db = Session()
    try:
        db.add(database.UserModel(
            id=1,
            username="mapped-history-owner",
            email="mapped-history@example.com",
            hashed_password="x",
        ))
        db.add(database.ExternalIdentityLink(
            provider="competition_app",
            external_user_id="external-mapped",
            user_id=1,
        ))
        db.add(database.KnowledgePoint(kp_id="KP_CANONICAL", name="规范知识点"))
        db.add_all([
            database.KnowledgePointCanonicalMap(
                mapping_id="MAP_A",
                source_kp_id="KP_SOURCE_A",
                canonical_kp_id="KP_CANONICAL",
                decision="equivalent",
                status="active",
            ),
            database.KnowledgePointCanonicalMap(
                mapping_id="MAP_B",
                source_kp_id="KP_SOURCE_B",
                canonical_kp_id="KP_CANONICAL",
                decision="equivalent",
                status="active",
            ),
            database.MasteryHistoryRecord(
                history_id="H_SOURCE_A",
                learner_id=1,
                kp_id="KP_SOURCE_A",
                trigger_attempt_item_id="ITEM_A",
                mastery_score=20.0,
                mastery_confidence=0.50,
                calculated_at=datetime(2026, 8, 25, 8, 0, 0),
            ),
            database.MasteryHistoryRecord(
                history_id="H_SOURCE_B",
                learner_id=1,
                kp_id="KP_SOURCE_B",
                trigger_attempt_item_id="ITEM_B",
                mastery_score=90.0,
                mastery_confidence=0.90,
                calculated_at=datetime(2026, 8, 25, 9, 0, 0),
            ),
        ])
        db.commit()
    finally:
        db.close()

    try:
        with patch.object(
            backend_handoff.importlib,
            "import_module",
            side_effect=lambda name: modules[name],
        ):
            payload = runtime.load_review_dashboard("external-mapped")

        assert payload["mastery_history"] == []
    finally:
        engine.dispose()
