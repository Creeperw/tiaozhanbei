import math
import unittest

from APP.backend.review_formula import (
    FORMULA_VERSION,
    effective_interval_seconds,
    lambda_per_day,
    mastery_after_attempt,
    review_transition,
    stability_for_interval,
)


class ReviewFormulaTests(unittest.TestCase):
    def test_first_attempt_uses_score_as_initial_mastery(self):
        result = mastery_after_attempt(
            previous_score=None,
            q_t=0.68,
            lambda_value=0.08,
            delta_days=0,
        )
        self.assertAlmostEqual(result, 68.0)

    def test_historical_attempt_applies_decay_and_alpha(self):
        result = mastery_after_attempt(
            previous_score=80.0,
            q_t=0.0,
            lambda_value=0.08,
            delta_days=1.0,
        )
        expected = 100 * (0.65 * 0.8 * math.exp(-0.08))
        self.assertAlmostEqual(result, expected)

    def test_lambda_is_clipped_and_caps_correct_streak(self):
        self.assertEqual(lambda_per_day(5, 0), 0.20)
        self.assertEqual(lambda_per_day(0, 99), 0.03)

    def test_stage_zero_hinted_correct_uses_recovery_floor(self):
        transition = review_transition(0, "hinted_correct")
        self.assertEqual(transition.stage, 0)
        self.assertEqual(transition.interval_seconds, 300)
        self.assertFalse(transition.requires_remediation)

    def test_independent_correct_advances_stage_and_uses_new_interval(self):
        transition = review_transition(2, "independent_correct")
        self.assertEqual(transition.stage, 3)
        self.assertEqual(transition.interval_seconds, 32400)

    def test_independent_correct_caps_at_highest_stage(self):
        transition = review_transition(7, "independent_correct")
        self.assertEqual(transition.stage, 7)
        self.assertEqual(transition.interval_seconds, 2678400)

    def test_hinted_correct_scales_current_stage_interval(self):
        transition = review_transition(2, "hinted_correct")
        self.assertEqual(transition.stage, 2)
        self.assertEqual(transition.interval_seconds, 2700)

    def test_skipped_retreats_stage_and_scales_new_interval(self):
        transition = review_transition(3, "skipped")
        self.assertEqual(transition.stage, 2)
        self.assertEqual(transition.interval_seconds, 1800)

    def test_skipped_does_not_retreat_below_stage_one(self):
        transition = review_transition(0, "skipped")
        self.assertEqual(transition.stage, 1)
        self.assertEqual(transition.interval_seconds, 600)

    def test_wrong_uses_recovery_interval(self):
        transition = review_transition(4, "wrong")
        self.assertEqual(transition.stage, 4)
        self.assertEqual(transition.interval_seconds, 300)

    def test_transition_rejects_unknown_outcome_and_invalid_stage(self):
        with self.assertRaises(ValueError):
            review_transition(0, "unknown")
        with self.assertRaises(ValueError):
            review_transition(-1, "wrong")
        with self.assertRaises(ValueError):
            review_transition(8, "wrong")

    def test_effective_interval_uses_recovery_floor(self):
        self.assertEqual(effective_interval_seconds(299.5), 300)

    def test_mastery_rejects_invalid_score_or_negative_time(self):
        for q_t in (-0.1, 1.1, math.nan, math.inf):
            with self.subTest(q_t=q_t), self.assertRaises(ValueError):
                mastery_after_attempt(
                    previous_score=None,
                    q_t=q_t,
                    lambda_value=0.08,
                    delta_days=0,
                )
        for delta_days in (-1, math.nan, math.inf, -math.inf):
            with self.subTest(delta_days=delta_days), self.assertRaises(ValueError):
                mastery_after_attempt(
                    previous_score=None,
                    q_t=0.5,
                    lambda_value=0.08,
                    delta_days=delta_days,
                )

    def test_stability_targets_ninety_percent_retention(self):
        stability = stability_for_interval(1200)
        self.assertAlmostEqual(math.exp(-1200 / stability), 0.9)
        self.assertEqual(FORMULA_VERSION, "ebbinghaus_classic_hybrid_v1_1")


if __name__ == "__main__":
    unittest.main()
