"""计划层 ↔ 执行层每日任务发布契约。

回归背景：计划层历史上允许模型标签 ``recall`` / ``reading`` 直接成为原子项，
而执行层只为 ``knowledge_practice`` / ``video_section`` 提供完成入口。执行层
按整版原子校验，于是**一个不可完成的项会让同版本全部可执行项一起被拒收**，
用户看到的是「今天所有任务都点不开」。本文件用真实的计划层载荷驱动真实的
执行层服务，把这条跨服务契约固定在测试里。
"""

import json
import unittest
from datetime import datetime, timezone
from typing import get_args

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from APP.backend import database
from APP.backend.daily_task_progress_service import (
    SUPPORTED_ITEM_KINDS,
    DailyTaskProgressError,
    upsert_daily_task_snapshot,
)
from competition_app.contracts.learning_plan import (
    EXECUTABLE_ITEM_TYPES,
    DailyTaskItemSpec,
    LearningTask,
    is_executable_item,
)
from competition_app.services.daily_task_execution import daily_task_progress_request


DECLARED_ITEM_TYPES = frozenset(
    get_args(DailyTaskItemSpec.model_fields["item_type"].annotation)
)


def _practice_item(ordinal: int, *, task_item_id: str = "ITM_PRACTICE") -> DailyTaskItemSpec:
    return DailyTaskItemSpec(
        task_item_id=task_item_id,
        ordinal=ordinal,
        item_type="knowledge_practice",
        title="练习：四君子汤",
        estimated_minutes=2.0,
        knowledge_point_name="四君子汤",
        kp_id="KP_1",
        required_question_count=1,
        completion_policy={"policy": "frozen_question_set"},
    )


def _video_item(ordinal: int) -> DailyTaskItemSpec:
    return DailyTaskItemSpec(
        task_item_id="ITM_VIDEO",
        ordinal=ordinal,
        item_type="video_section",
        title="视频：补气剂",
        estimated_minutes=3.0,
        resource_ref={
            "source": "textbook",
            "start_seconds": 0,
            "end_seconds": 120,
        },
        completion_policy={"policy": "html5_coverage"},
    )


def _legacy_recall_item(ordinal: int) -> DailyTaskItemSpec:
    """模型曾在正文里写 ``recall``，该标签没有完成路径。"""

    return DailyTaskItemSpec(
        task_item_id="ITM_RECALL",
        ordinal=ordinal,
        item_type="recall",
        title="回顾：君臣佐使",
        estimated_minutes=1.0,
    )


def _learning_task(items: list[DailyTaskItemSpec]) -> LearningTask:
    now = datetime(2026, 7, 16, 10, 0, tzinfo=timezone.utc)
    return LearningTask(
        task_id="TASK_CONTRACT",
        learner_id="learner-contract",
        short_term_plan_id="STP_CONTRACT",
        task_type="daily",
        task_content="学习四君子汤",
        learning_chapter="《方剂学》补益剂",
        estimated_minutes=max(1.0, sum(item.estimated_minutes for item in items)),
        expected_output="完成练习",
        completion_criteria="完成全部任务项",
        version=1,
        status="active",
        created_at=now,
        updated_at=now,
        items=items,
    )


class DailyTaskPublishContractTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        database.Base.metadata.create_all(bind=self.engine)
        database.ensure_runtime_schema_for(self.engine)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False
        )
        with self.session_factory() as db:
            db.add(
                database.UserModel(
                    id=1, username="learner", email="learner@example.com", hashed_password="x"
                )
            )
            db.add(
                database.KnowledgePoint(
                    kp_id="KP_1",
                    name="四君子汤",
                    aliases_json=json.dumps([]),
                    source="formal-content:test",
                    status="active",
                )
            )
            db.add(
                database.LearningQuestion(
                    question_id="Q_1",
                    question_type="single_choice",
                    question_content="题干1",
                    options_json=json.dumps(["A", "B"]),
                    answer_json=json.dumps(["A"]),
                    explanation="解释",
                    difficulty=1.0,
                    kp_ids_json=json.dumps(["KP_1"]),
                    key_points="key",
                    scoring_rubric="rubric",
                )
            )
            db.add(
                database.QuestionVersionRecord(
                    question_version_id="QV_1",
                    question_id="Q_1",
                    version=1,
                    question_type="single_choice",
                    stem="题干1",
                    answer="A",
                    analysis="解释",
                    source_kind="formal-content:test",
                    status="active",
                )
            )
            db.add(
                database.QuestionKPLinkRecord(
                    question_version_id="QV_1", kp_id="KP_1", is_primary=True, status="active"
                )
            )
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def _publish(self, payload):
        with self.session_factory() as db:
            return upsert_daily_task_snapshot(db, user_id=1, payload=payload)

    # --- 静态契约 ---------------------------------------------------------

    def test_execution_side_supports_every_executable_item_type(self):
        missing = EXECUTABLE_ITEM_TYPES - SUPPORTED_ITEM_KINDS
        self.assertEqual(
            missing,
            set(),
            "计划层认为可执行的项类型必须被执行层支持，否则整版发布会被拒收",
        )

    def test_item_types_without_a_completion_path_are_not_executable(self):
        """``recall`` / ``reading`` 必须两边都不被当成可执行项。

        任一侧新增对它们的支持都会让本用例失败，迫使两边同步更新，
        从而避免「计划层发布、执行层不认识」的漂移再次发生。
        """

        non_executable = DECLARED_ITEM_TYPES - EXECUTABLE_ITEM_TYPES
        self.assertEqual(non_executable, {"reading", "recall"})
        for item_type in sorted(non_executable):
            with self.subTest(item_type=item_type):
                self.assertNotIn(item_type, SUPPORTED_ITEM_KINDS)
                self.assertFalse(is_executable_item({"item_type": item_type}))
                self.assertFalse(is_executable_item({"item_type": item_type, "kp_id": None}))

    def test_declared_item_types_are_the_contract_surface(self):
        self.assertEqual(
            DECLARED_ITEM_TYPES,
            {"video_section", "knowledge_practice", "reading", "recall"},
        )

    # --- 端到端：真实计划层载荷 → 真实执行层服务 --------------------------

    def test_planned_task_payload_is_fully_accepted_by_execution_side(self):
        task = _learning_task([_practice_item(1), _video_item(2)])

        payload = daily_task_progress_request(task)

        self.assertEqual(
            [item["task_item_id"] for item in payload["items"]],
            ["ITM_PRACTICE", "ITM_VIDEO"],
        )
        snapshot = self._publish(payload)
        self.assertEqual(
            [item["task_item_id"] for item in snapshot["items"]],
            ["ITM_PRACTICE", "ITM_VIDEO"],
        )

    def test_legacy_label_in_plan_never_breaks_the_whole_version(self):
        """带 ``recall`` 的任务发布后，同版本的练习项必须仍然可完成。"""

        task = _learning_task(
            [_practice_item(1, task_item_id="ITM_PRACTICE"), _legacy_recall_item(2)]
        )

        payload = daily_task_progress_request(task)

        self.assertEqual(
            [item["task_item_id"] for item in payload["items"]], ["ITM_PRACTICE"]
        )
        snapshot = self._publish(payload)
        self.assertEqual(
            [item["task_item_id"] for item in snapshot["items"]], ["ITM_PRACTICE"]
        )

    def test_legacy_payload_from_an_old_producer_only_drops_the_bad_item(self):
        """修复前的生产者直接发布原始载荷时，执行层也必须只丢坏项。"""

        snapshot = self._publish(
            {
                "host_task_id": "TASK_LEGACY",
                "host_task_version": 1,
                "items": [
                    {"task_item_id": "ITM_RECALL", "item_type": "recall"},
                    {
                        "task_item_id": "ITM_PRACTICE",
                        "item_type": "knowledge_practice",
                        "kp_id": "KP_1",
                        "required_question_count": 1,
                        "completion_policy": {"policy": "frozen_question_set"},
                    },
                ],
            }
        )

        self.assertEqual(
            [item["task_item_id"] for item in snapshot["items"]], ["ITM_PRACTICE"]
        )

    def test_version_without_any_completable_item_is_rejected_atomically(self):
        with self.assertRaises(DailyTaskProgressError) as captured:
            self._publish(
                {
                    "host_task_id": "TASK_ALL_BAD",
                    "host_task_version": 1,
                    "items": [
                        {"task_item_id": "ITM_RECALL", "item_type": "recall"},
                        {"task_item_id": "ITM_READING", "item_type": "reading"},
                    ],
                }
            )

        self.assertEqual(captured.exception.code, 409)
        with self.session_factory() as db:
            self.assertEqual(db.query(database.DailyTaskItemRecord).count(), 0)
            self.assertEqual(db.query(database.DailyTaskInstanceRecord).count(), 0)


if __name__ == "__main__":
    unittest.main()
