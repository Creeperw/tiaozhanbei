import unittest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from APP.backend.api_errors import install_api_error_handlers


class Payload(BaseModel):
    value: int


class ApiErrorContractTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        install_api_error_handlers(app)

        @app.get("/string-error")
        def string_error():
            raise HTTPException(status_code=404, detail="missing resource")

        @app.get("/structured-error")
        def structured_error():
            raise HTTPException(
                status_code=503,
                detail={"code": "index_unavailable", "message": "index is warming"},
            )

        @app.post("/validate")
        def validate(payload: Payload):
            return payload

        self.client = TestClient(app)

    def test_http_errors_have_one_shape_and_request_id(self):
        response = self.client.get("/string-error", headers={"X-Request-ID": "req-test"})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["X-Request-ID"], "req-test")
        self.assertEqual(response.json(), {
            "code": "not_found",
            "detail": "missing resource",
            "request_id": "req-test",
            "field_errors": [],
        })

        structured = self.client.get("/structured-error")
        self.assertEqual(structured.json()["code"], "index_unavailable")
        self.assertEqual(structured.json()["detail"], "index is warming")
        self.assertTrue(structured.json()["request_id"])

    def test_validation_errors_use_field_errors(self):
        response = self.client.post("/validate", json={"value": "not-an-int"})

        self.assertEqual(response.status_code, 422)
        payload = response.json()
        self.assertEqual(payload["code"], "validation_error")
        self.assertEqual(payload["detail"], "Request validation failed")
        self.assertEqual(payload["field_errors"][0]["field"], "body.value")


if __name__ == "__main__":
    unittest.main()
