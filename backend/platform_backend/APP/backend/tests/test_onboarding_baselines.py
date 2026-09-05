import unittest

from APP.backend.onboarding_template_service import apply_onboarding_defaults, get_group_templates


class OnboardingBaselineTests(unittest.TestCase):
    def test_three_explicit_readiness_profiles_and_legacy_read_compatibility(self):
        groups = get_group_templates()["groups"]
        self.assertEqual([group["key"] for group in groups], ["low", "medium", "high"])
        for group in groups:
            with self.subTest(group=group["key"]):
                payload = apply_onboarding_defaults({
                    "learner_group": group["key"],
                    "background": {"major_or_role": "旧专业", "tcm_foundation": "旧基础"},
                    "preferences": {"resource_preference": []},
                })
                self.assertEqual(payload["background"]["major_or_role"], group["description"])
                self.assertEqual(payload["background"]["tcm_foundation"], group["readiness"])
                self.assertEqual(payload["preferences"]["resource_preference"], [])
        self.assertEqual(apply_onboarding_defaults({"learner_group": "academic"})["learner_group"], "academic")

    def test_explicit_empty_preference_does_not_restore_group_default(self):
        payload = apply_onboarding_defaults({"learner_group": "academic", "preferences": {"resource_preference": []}})
        self.assertEqual(payload["preferences"]["resource_preference"], [])