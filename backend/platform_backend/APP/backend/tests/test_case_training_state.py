import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone

from APP.backend.case_training_state import (
    CaseTrainingState,
    DEFAULT_EXPIRATION,
    MAX_ROUNDS,
    transition,
)


class CaseTrainingStateTests(unittest.TestCase):
    NOW = datetime(2026, 7, 13, tzinfo=timezone.utc)

    def state(self, **changes):
        return CaseTrainingState(created_at=self.NOW, **changes)

    def transition(self, state, event):
        return transition(state, event, now=self.NOW)

    def test_direct_construction_rejects_help_available_before_ten_messages(self):
        with self.assertRaises(ValueError):
            self.state(status="help_available", learner_messages=9)

    def test_state_construction_and_normal_transitions_require_explicit_time(self):
        with self.assertRaises(ValueError):
            CaseTrainingState()
        with self.assertRaises(ValueError):
            transition(self.state(status="active"), "learner_message")

    def test_created_session_advances_through_standard_completion(self):
        state = self.state()

        state = self.transition(state, "activate")
        for _ in range(10):
            state = self.transition(state, "learner_message")
        state = self.transition(state, "submit")
        state = self.transition(state, "start_grading")
        state = self.transition(state, "complete")

        self.assertEqual(state.status, "completed")

    def test_active_and_help_available_sessions_can_be_abandoned(self):
        for status, messages in (("active", 0), ("help_available", 10)):
            with self.subTest(status=status):
                state = self.transition(self.state(status=status, learner_messages=messages), "abandon")
                self.assertEqual(state.status, "abandoned")

    def test_grading_can_require_human_review_before_completion(self):
        state = self.state(status="grading")

        state = self.transition(state, "request_human_review")
        state = self.transition(state, "complete")

        self.assertEqual(state.status, "completed")

    def test_any_nonterminal_state_can_expire(self):
        for status, messages in (
            ("created", 0),
            ("active", 0),
            ("help_available", 10),
            ("submitted", 0),
            ("grading", 0),
            ("needs_human_review", 0),
        ):
            with self.subTest(status=status):
                state = transition(self.state(status=status, learner_messages=messages), "expire")
                self.assertEqual(state.status, "expired")

    def test_first_nine_learner_messages_keep_session_active(self):
        state = self.state(status="active")

        for expected_messages in range(1, 10):
            state = self.transition(state, "learner_message")
            self.assertEqual(state.status, "active")
            self.assertEqual(state.learner_messages, expected_messages)

    def test_help_cannot_be_made_available_before_ten_learner_messages(self):
        state = self.state(status="active", learner_messages=9)

        with self.assertRaises(ValueError):
            self.transition(state, "make_help_available")

    def test_tenth_learner_message_makes_help_available(self):
        state = self.state(status="active", learner_messages=9)

        state = self.transition(state, "learner_message")

        self.assertEqual(state.status, "help_available")
        self.assertEqual(state.learner_messages, 10)

    def test_learner_messages_cannot_exceed_thirty_rounds(self):
        state = self.state(status="active", learner_messages=MAX_ROUNDS - 1)

        state = self.transition(state, "learner_message")

        self.assertEqual(state.learner_messages, MAX_ROUNDS)
        with self.assertRaises(ValueError):
            self.transition(state, "learner_message")

    def test_answering_help_cancels_scoring_and_consumes_only_help(self):
        state = self.state(status="help_available", learner_messages=10, scoring_enabled=True)

        answered = self.transition(state, "answer_help")

        self.assertEqual(answered.status, "active")
        self.assertFalse(answered.scoring_enabled)
        self.assertTrue(answered.help_used)
        with self.assertRaises(ValueError):
            self.transition(answered, "make_help_available")

    def test_submitted_session_forbids_learner_messages(self):
        state = self.state(status="submitted")

        with self.assertRaises(ValueError):
            self.transition(state, "learner_message")

    def test_default_expiration_is_twenty_four_hours(self):
        created_at = datetime(2026, 7, 13, tzinfo=timezone.utc)
        state = CaseTrainingState(created_at=created_at)

        self.assertEqual(state.expires_at, created_at + DEFAULT_EXPIRATION)
        self.assertEqual(DEFAULT_EXPIRATION, timedelta(hours=24))

    def test_expired_nonterminal_state_becomes_expired_before_normal_action(self):
        now = datetime(2026, 7, 13, tzinfo=timezone.utc)
        state = CaseTrainingState(created_at=now, status="active", expires_at=now - timedelta(seconds=1))

        expired = transition(state, "learner_message", now=now)

        self.assertEqual(expired.status, "expired")
        self.assertEqual(expired.learner_messages, 0)
        with self.assertRaises(ValueError):
            transition(expired, "learner_message", now=now)

    def test_expire_is_idempotent_for_terminal_states_but_other_events_are_rejected(self):
        for status in ("completed", "abandoned", "expired"):
            with self.subTest(status=status):
                state = self.state(status=status)

                self.assertIs(transition(state, "expire"), state)
                with self.assertRaises(ValueError):
                    self.transition(state, "activate")

    def test_transition_returns_a_new_frozen_state(self):
        state = self.state()

        updated = self.transition(state, "activate")

        self.assertIsNot(state, updated)
        self.assertEqual(state.status, "created")
        self.assertEqual(updated.status, "active")
        with self.assertRaises(FrozenInstanceError):
            updated.status = "abandoned"


if __name__ == "__main__":
    unittest.main()
