import pytest

from competition_app.agents.audit_findings_compiler import AuditFindingsCompilerAgent
from competition_app.contracts.audit_compilation import AuditLocation


def _context():
    return {
        "trace_id": "TRACE_AUDIT_COMPILER",
        "request_id": "REQ_AUDIT_COMPILER",
        "learner_id": "LEARNER_1",
        "user_profile": {"learning_background": "不应发送给Compiler"},
        "messages": [{"role": "user", "content": "不应发送给Compiler"}],
    }


def _locations():
    return [
        AuditLocation(
            location_key="paper:whole",
            subject_type="exam_paper",
            location_type="whole_subject",
            display_label="当前试卷全文",
        ),
        AuditLocation(
            location_key="paper:explanation:Q1",
            subject_type="exam_paper",
            location_type="explanation",
            display_label="题目Q1的解析",
        ),
    ]


class CompilingModel:
    async def complete_json(self, role, payload, on_delta=None):
        assert role == "audit_findings_compiler"
        shared = payload["payload"]["shared_context"]
        assert shared["source_bounded_compiler"] is True
        assert shared["user_profile"] == {}
        assert shared["recent_conversation"] == []
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "issues": [
                {
                    "issue_type": "answer_or_explanation_invalid",
                    "message": "题目Q1的解析与答案不一致。",
                    "blocking": True,
                    "location_keys": ["paper:explanation:Q1"],
                    "source_anchors": [
                        {
                            "source_field": "findings",
                            "source_quote": "题目Q1的解析与答案不一致。",
                        }
                    ],
                }
            ],
        }


@pytest.mark.asyncio
async def test_compiler_keeps_verbatim_finding_and_allowed_location() -> None:
    result = await AuditFindingsCompilerAgent(CompilingModel()).compile(
        _context(),
        subject_type="exam_paper",
        audit_report="试卷需要修订。",
        findings=["题目Q1的解析与答案不一致。"],
        location_catalog=_locations(),
    )

    assert result.result.status == "compiled"
    assert result.result.issues[0].location_keys == ["paper:explanation:Q1"]


class InventingLocationModel(CompilingModel):
    async def complete_json(self, role, payload, on_delta=None):
        value = await super().complete_json(role, payload, on_delta)
        value["issues"][0]["location_keys"] = ["paper:explanation:Q999"]
        return value


@pytest.mark.asyncio
async def test_compiler_rejects_invented_location() -> None:
    result = await AuditFindingsCompilerAgent(InventingLocationModel()).compile(
        _context(),
        subject_type="exam_paper",
        audit_report="试卷需要修订。",
        findings=["题目Q1的解析与答案不一致。"],
        location_catalog=_locations(),
    )

    assert result.result.status == "needs_revision"
    assert result.result.issues[0].code == "location_not_allowed"


class DroppingFindingsModel:
    async def complete_json(self, role, payload, on_delta=None):
        assert role == "audit_findings_compiler"
        return {"status": "compiled", "issues": []}


@pytest.mark.asyncio
async def test_compiler_rejects_silently_dropped_findings() -> None:
    result = await AuditFindingsCompilerAgent(DroppingFindingsModel()).compile(
        _context(),
        subject_type="exam_paper",
        audit_report="试卷需要修订。",
        findings=["题目Q1的解析与答案不一致。"],
        location_catalog=_locations(),
    )

    assert result.result.status == "needs_revision"
    assert result.result.issues[0].code == "schema_invalid"
    assert result.result.issues[0].detail == "source_finding_not_compiled"


class RepairingModel(CompilingModel):
    def __init__(self, *, recover=True):
        self.calls = 0
        self.recover = recover

    async def complete_json(self, role, payload, on_delta=None):
        self.calls += 1
        if self.calls == 1:
            return {}
        assert payload["payload"]["compilation_feedback"]["issues"][0]["code"] == "schema_invalid"
        if not self.recover:
            return {}
        return await super().complete_json(role, payload, on_delta)


@pytest.mark.asyncio
@pytest.mark.parametrize("recover", [True, False])
async def test_compiler_repairs_once_without_prose_fallback(recover):
    model = RepairingModel(recover=recover)
    result = await AuditFindingsCompilerAgent(model).compile(
        _context(), subject_type="exam_paper", audit_report="试卷需要修订。",
        findings=["题目Q1的解析与答案不一致。"], location_catalog=_locations(),
    )
    assert model.calls == 2
    assert result.result.status == ("compiled" if recover else "needs_revision")
