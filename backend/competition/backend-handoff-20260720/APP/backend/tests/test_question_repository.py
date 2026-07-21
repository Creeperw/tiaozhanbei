import json
import unittest
from dataclasses import FrozenInstanceError

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from APP.backend.database import (
    Base,
    QuestionBankItem,
    QuestionKPLinkRecord,
    QuestionVersionRecord,
    VariationQuestionVersionRecord,
)
from APP.backend.question_repository import (
    QuestionRepository,
    QuestionSelectionCriteria,
    QuestionShortage,
    QuestionVersionView,
)


class QuestionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def add_question(
        self,
        question_id,
        *,
        kp_ids=("kp-1",),
        question_type="single_choice",
        difficulty=2,
        status="active",
        stem=None,
    ):
        session = self.Session()
        try:
            session.add(QuestionBankItem(
                question_id=question_id,
                stem=stem or f"stem-{question_id}",
                answer=f"answer-{question_id}",
                analysis=f"analysis-{question_id}",
                kp_ids_json=json.dumps(kp_ids),
                question_type=question_type,
                difficulty=difficulty,
                source="seed",
                status=status,
            ))
            session.commit()
        finally:
            session.close()

    def add_authoritative_version(
        self,
        question_version_id,
        question_id,
        *,
        version=1,
        kp_ids=("kp-1",),
        question_type="single_choice",
        difficulty=2,
        status="active",
        link_status="active",
    ):
        session = self.Session()
        try:
            session.add(QuestionVersionRecord(
                question_version_id=question_version_id,
                question_id=question_id,
                version=version,
                question_type=question_type,
                stem=f"stem-{question_version_id}",
                answer=f"answer-{question_version_id}",
                analysis=f"analysis-{question_version_id}",
                standard_difficulty=difficulty,
                source_kind="authoritative",
                status=status,
            ))
            for kp_id in kp_ids:
                session.add(QuestionKPLinkRecord(
                    question_version_id=question_version_id,
                    kp_id=kp_id,
                    status=link_status,
                ))
            session.commit()
        finally:
            session.close()

    def test_selects_authoritative_active_version_without_legacy_row(self):
        self.add_authoritative_version("QV_REAL_2", "question-real", version=2, kp_ids=("kp-1", "kp-2"))
        repository = QuestionRepository(self.Session)

        selected = repository.select(QuestionSelectionCriteria(
            kp_ids=("kp-1", "kp-2"),
            type_difficulty_counts=(("single_choice", 2, 1),),
        ))

        self.assertEqual(selected[0].question_version_id, "QV_REAL_2")
        self.assertEqual(selected[0].question_id, "question-real")
        self.assertEqual(selected[0].kp_ids, ("kp-1", "kp-2"))

    def test_selects_lowest_stable_id_among_active_versions_of_same_question(self):
        self.add_authoritative_version("QV_Z", "question-multi", version=1)
        self.add_authoritative_version("QV_A", "question-multi", version=2)
        repository = QuestionRepository(self.Session)

        selected = repository.select(QuestionSelectionCriteria(
            kp_ids=("kp-1",),
            type_difficulty_counts=(("single_choice", 2, 1),),
        ))

        self.assertEqual(selected[0].question_version_id, "QV_A")

    def test_select_canonicalizes_authoritative_versions_before_applying_quota(self):
        self.add_authoritative_version("QV_Z", "question-1", version=2)
        self.add_authoritative_version("QV_A", "question-1", version=1)
        self.add_authoritative_version("QV_Q2", "question-2", version=1)
        repository = QuestionRepository(self.Session)

        selected = repository.select(QuestionSelectionCriteria(
            kp_ids=("kp-1",),
            type_difficulty_counts=(("single_choice", 2, 2),),
        ))

        self.assertEqual(
            [(item.question_id, item.question_version_id) for item in selected],
            [("question-1", "QV_A"), ("question-2", "QV_Q2")],
        )

    def test_canonicalizes_authoritative_version_before_applying_difficulty_quota(self):
        self.add_authoritative_version("QV_A", "question-multi", version=1, difficulty=3)
        self.add_authoritative_version("QV_Z", "question-multi", version=2, difficulty=2)
        repository = QuestionRepository(self.Session)

        selected = repository.select(QuestionSelectionCriteria(
            kp_ids=("kp-1",),
            type_difficulty_counts=(("single_choice", 2, 1),),
        ))

        self.assertIsInstance(selected, QuestionShortage)
        self.assertEqual(selected.available_count, 0)

    def test_private_variation_does_not_block_public_legacy_question_fallback(self):
        self.add_question("Q1")
        self.add_authoritative_version("private-Q1", "Q1")
        session = self.Session()
        try:
            session.add(VariationQuestionVersionRecord(
                variation_set_id="variation-set-Q1",
                question_version_id="private-Q1",
                owner_user_id=1,
                scope="user",
            ))
            session.commit()
        finally:
            session.close()

        selected = QuestionRepository(self.Session).select(QuestionSelectionCriteria(
            kp_ids=("kp-1",),
            type_difficulty_counts=(("single_choice", 2, 1),),
        ))

        self.assertNotIsInstance(selected, QuestionShortage)
        self.assertEqual([item.question_id for item in selected], ["Q1"])
        self.assertEqual([item.question_version_id for item in selected], ["Q1:v1"])
        self.assertNotIn("private-Q1", [item.question_version_id for item in selected])

    def test_authoritative_presence_blocks_legacy_fallback_even_when_version_or_link_inactive(self):
        for question_id, version_status, link_status in (
            ("inactive-version", "inactive", "active"),
            ("inactive-link", "active", "inactive"),
        ):
            self.add_question(question_id)
            self.add_authoritative_version(
                f"QV_{question_id}", question_id,
                status=version_status, link_status=link_status,
            )
        repository = QuestionRepository(self.Session)

        selected = repository.select(QuestionSelectionCriteria(
            kp_ids=("kp-1",),
            type_difficulty_counts=(("single_choice", 2, 1),),
        ))

        self.assertIsInstance(selected, QuestionShortage)
        self.assertEqual(selected.available_count, 0)

    def test_selection_criteria_is_immutable(self):
        criteria = QuestionSelectionCriteria(
            kp_ids=("kp-1",),
            type_difficulty_counts=(("single_choice", 2, 1),),
        )

        with self.assertRaises(FrozenInstanceError):
            criteria.kp_ids = ("kp-2",)

    def test_criteria_and_version_view_snapshot_list_inputs(self):
        kp_ids = ["kp-1"]
        type_difficulty_counts = [["single_choice", 2, 1]]
        exclude_question_ids = ["question-1"]
        criteria = QuestionSelectionCriteria(
            kp_ids=kp_ids,
            type_difficulty_counts=type_difficulty_counts,
            exclude_question_ids=exclude_question_ids,
        )
        version_kp_ids = ["kp-2"]
        version_view = QuestionVersionView(
            question_version_id="question-2:v1",
            question_id="question-2",
            question_type="single_choice",
            stem="stem",
            answer="answer",
            analysis="analysis",
            kp_ids=version_kp_ids,
            standard_difficulty=2,
            source_kind="seed",
        )

        kp_ids.append("kp-3")
        type_difficulty_counts.append(["short_answer", 2, 1])
        type_difficulty_counts[0][2] = 2
        exclude_question_ids.append("question-3")
        version_kp_ids.append("kp-4")

        self.assertEqual(criteria.kp_ids, ("kp-1",))
        self.assertEqual(criteria.type_difficulty_counts, (("single_choice", 2, 1),))
        self.assertEqual(criteria.exclude_question_ids, ("question-1",))
        self.assertEqual(version_view.kp_ids, ("kp-2",))

    def test_select_returns_active_strict_matches_before_primary_only_matches(self):
        self.add_question("z-strict", kp_ids=("kp-1", "kp-2"), difficulty=2)
        self.add_question("a-strict", kp_ids=("kp-1", "kp-2"), difficulty=2)
        self.add_question("primary", kp_ids=("kp-1",), difficulty=2)
        self.add_question("inactive", kp_ids=("kp-1", "kp-2"), difficulty=2, status="inactive")
        repository = QuestionRepository(self.Session)

        selected = repository.select(QuestionSelectionCriteria(
            kp_ids=("kp-1", "kp-2"),
            type_difficulty_counts=(("single_choice", 2, 3),),
        ))

        self.assertEqual([item.question_id for item in selected], ["a-strict", "z-strict", "primary"])
        self.assertEqual(
            [item.question_version_id for item in selected],
            ["a-strict:v1", "z-strict:v1", "primary:v1"],
        )
        self.assertEqual(selected[0].kp_ids, ("kp-1", "kp-2"))
        self.assertEqual(selected[0].standard_difficulty, 2)
        self.assertEqual(selected[0].source_kind, "seed")

    def test_select_deduplicates_excludes_and_returns_shortage_without_relaxing_type_or_difficulty(self):
        self.add_question("duplicate", difficulty=2)
        self.add_question("excluded", difficulty=2)
        self.add_question("wrong-type", question_type="short_answer", difficulty=2)
        self.add_question("wrong-difficulty", difficulty=3)
        repository = QuestionRepository(self.Session)

        selected = repository.select(QuestionSelectionCriteria(
            kp_ids=("kp-1",),
            type_difficulty_counts=(("single_choice", 2, 3),),
            exclude_question_ids=("duplicate", "excluded"),
        ))

        self.assertIsInstance(selected, QuestionShortage)
        self.assertEqual(selected.requested_count, 3)
        self.assertEqual(selected.available_count, 0)
        self.assertEqual(
            selected.criteria.type_difficulty_counts,
            (("single_choice", 2, 3),),
        )

    def test_legacy_question_without_kp_ids_is_not_selected_for_personalized_training(self):
        self.add_question("unlinked", kp_ids=())
        repository = QuestionRepository(self.Session)

        selected = repository.select(QuestionSelectionCriteria(
            kp_ids=("kp-1",),
            type_difficulty_counts=(("single_choice", 2, 1),),
        ))

        self.assertIsInstance(selected, QuestionShortage)
        self.assertEqual(selected.available_count, 0)

    def test_learner_snapshot_omits_answers(self):
        self.add_question("learner-view")
        repository = QuestionRepository(self.Session)

        snapshot = repository.learner_snapshot(repository.select(QuestionSelectionCriteria(
            kp_ids=("kp-1",),
            type_difficulty_counts=(("single_choice", 2, 1),),
        )))

        self.assertEqual(snapshot, ({
            "question_version_id": "learner-view:v1",
            "question_id": "learner-view",
            "question_type": "single_choice",
            "stem": "stem-learner-view",
            "kp_ids": ("kp-1",),
            "standard_difficulty": 2,
            "source_kind": "seed",
        },))
        self.assertNotIn("answer", snapshot[0])
        self.assertNotIn("analysis", snapshot[0])


if __name__ == "__main__":
    unittest.main()
