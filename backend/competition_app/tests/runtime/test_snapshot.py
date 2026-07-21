import json
from pathlib import Path

from competition_app.runtime.snapshot import SnapshotExporter


def test_snapshot_redacts_sensitive_values_inside_regular_fields(tmp_path: Path) -> None:
    path = SnapshotExporter(tmp_path).export(
        "CASE_1",
        "EXE_1",
        {
            "note": "Authorization: Bearer secret-token",
            "connection": "mysql+pymysql://root:secret-password@localhost/competition_app",
            "nested": {"api_key": "another-secret"},
            "model_output": "token=dash-redaction-test-token-value",
            "cookie_note": "Cookie: sessionid=very-sensitive-session-value",
            "profile_text": "手机号 13812345678，身份证 110101199001011234",
        },
    )

    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert "secret-token" not in text
    assert "secret-password" not in text
    assert "another-secret" not in text
    assert "dash-redaction-test-token-value" not in text
    assert "very-sensitive-session-value" not in text
    assert "13812345678" not in text
    assert "110101199001011234" not in text
    assert "[REDACTED]" in payload["note"]
    assert "[REDACTED]" in payload["connection"]
