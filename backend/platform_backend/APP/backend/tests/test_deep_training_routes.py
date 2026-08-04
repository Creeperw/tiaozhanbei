import unittest


class DeepTrainingRoutesOpenApiTests(unittest.TestCase):
    def test_phase5_deep_training_routes_are_registered_in_openapi(self):
        from APP.backend.main import app

        paths = app.openapi()["paths"]

        self.assertIn("/deep-training/knowledge/align", paths)
        self.assertIn("/deep-training/questions/select", paths)
        self.assertIn("/deep-training/diagnosis", paths)
        self.assertIn("/deep-training/cross-validate", paths)
        self.assertIn("/deep-training/demo", paths)


if __name__ == "__main__":
    unittest.main()
