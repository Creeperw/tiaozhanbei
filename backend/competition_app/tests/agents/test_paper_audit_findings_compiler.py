import pytest

from competition_app.agents.paper_audit_findings_compiler import (
    PaperAuditFindingsCompilerAgent,
)


_CONTEXT = {
    "trace_id": "TRACE_COMPILER",
    "request_id": "REQ_COMPILER",
    "step_id": "audit",
    "learner_id": "LEARNER_COMPILER",
}


class AnchoredCompilerModel:
    async def complete_json(self, role, payload, on_delta=None):
        assert role == "paper_audit_findings_compiler"
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "issues": [
                {
                    "issue_type": "content_quality",
                    "message": "题1与题2重复，必须替换题2。",
                    "blocking": True,
                    "source_anchors": [
                        {
                            "source_field": "audit_report",
                            "source_quote": "题1与题2重复，必须替换题2。",
                        }
                    ],
                }
            ],
        }


class InventingCompilerModel:
    async def complete_json(self, role, payload, on_delta=None):
        return {
            "status": "compiled",
            "contract_version": "1.0",
            "issues": [
                {
                    "issue_type": "paper_blueprint_mismatch",
                    "message": "缺少五道填空题。",
                    "blocking": True,
                    "source_anchors": [
                        {
                            "source_field": "audit_report",
                            "source_quote": "缺少五道填空题。",
                        }
                    ],
                }
            ],
        }


@pytest.mark.asyncio
async def test_paper_audit_compiler_accepts_verbatim_anchored_issue() -> None:
    envelope = await PaperAuditFindingsCompilerAgent(AnchoredCompilerModel()).compile(
        _CONTEXT,
        audit_report="审核发现：题1与题2重复，必须替换题2。",
        findings=[],
    )

    assert envelope.result.status == "compiled"
    assert envelope.result.issues[0].blocking is True
    assert len(envelope.source_digest) == 64


@pytest.mark.asyncio
async def test_paper_audit_compiler_rejects_invented_issue_and_anchor() -> None:
    envelope = await PaperAuditFindingsCompilerAgent(InventingCompilerModel()).compile(
        _CONTEXT,
        audit_report="试卷整体可用。",
        findings=[],
    )

    assert envelope.result.status == "needs_revision"
    assert any(
        issue.code == "source_anchor_invalid" for issue in envelope.result.issues
    )
