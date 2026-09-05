import unittest


class DocumentIngestionRoutesTests(unittest.TestCase):
    def test_document_ingestion_route_is_registered_in_openapi(self):
        from APP.backend.main import app

        paths = app.openapi()["paths"]

        self.assertIn("/knowledge/ingest", paths)

    def test_ingest_request_rejects_blank_file_path(self):
        from APP.backend.routers.knowledge_routes import DocumentIngestRequest
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            DocumentIngestRequest(
                file_path="   ",
                original_filename="outline.pdf",
                scope="public",
                document_kind="exam_outline",
            )


if __name__ == "__main__":
    unittest.main()
