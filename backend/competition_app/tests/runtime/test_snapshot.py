import json
from pathlib import Path

from competition_app.runtime.snapshot import INTERNAL_HANDLE, SnapshotExporter


def test_internal_handle_definition_covers_the_shapes_that_reach_the_browser() -> None:
    """内部标识符定义必须覆盖生产环境实际透出的句柄形态。

    这份定义是 prose 与 JSON 两条浏览器边界共用的唯一来源；漏掉一种形态就会
    让过程面板重新出现机器句柄。取值全部来自生产渲染出的过程面板。
    """

    for handle in (
        "USER_5303c3e61f954a6297b97202cd3b11b6",
        "EP_0ce994058df141159a0c2dee6d04a48e",
        "EP_SCOPE_EXE_1",
        "DRAFT_bc1548f2ff1a44e09320a8ada3e5af8a",
        "AUDIT_037ad5770b254aa5a4ac674573dace9d",
        "C_d255a09b56cc4f8e8e3863cd776cfbe8",
        "KP_9f2c8d1e",
        "THREAD_abc123",
        "EVID_SECRET_1",
        "E_CHUNK_中医学基础_clean:00011",
        "E_VECTOR_3",
        "E_EXA_KNOWLEDGE_1",
    ):
        assert INTERNAL_HANDLE.fullmatch(handle), handle

    for ordinary in (
        "整体观念与辨证论治",
        "E_1",
        "知识讲解",
        "仅用于中医药教学训练，不构成诊疗建议。",
    ):
        assert not INTERNAL_HANDLE.fullmatch(ordinary), ordinary


def test_snapshot_redacts_sensitive_values_inside_regular_fields(tmp_path: Path) -> None:
    path = SnapshotExporter(tmp_path).export(
        "CASE_1",
        "EXE_1",
        {
            "note": "Authorization: Bearer secret-token",
            "connection": "mysql+pymysql://root:secret-password@localhost/competition_app",
            "nested": {"api_key": "another-secret"},
            "camelCaseSecrets": {
                "accessToken": "access-token-secret",
                "jwtSecretKey": "jwt-secret-value",
                "accountability_evaluation_token": "evaluation-token-secret",
            },
            "model_output": "token=dash-redaction-test-token-value",
            "cookie_note": "Cookie: sessionid=very-sensitive-session-value",
            "jwt_note": "eyJabcdefghijk.abcdefghijkl.abcdefghijklmnop",
            "password_note": "password=plain-text-secret",
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
    assert "access-token-secret" not in text
    assert "jwt-secret-value" not in text
    assert "evaluation-token-secret" not in text
    assert "plain-text-secret" not in text
    assert "eyJabcdefghijk" not in text
    assert "13812345678" not in text
    assert "110101199001011234" not in text
    assert "[REDACTED]" in payload["note"]
    assert "[REDACTED]" in payload["connection"]
