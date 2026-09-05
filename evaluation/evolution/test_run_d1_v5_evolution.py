from __future__ import annotations

import argparse
import json

from run_d1_v5_evolution import Runner


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.statuses = iter(
            [
                {"status": "awaiting_review"},
                {"status": "qualification_passed", "active_case_id": None},
                {"status": "completed", "active_case_id": None},
            ]
        )

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "POST" and path.endswith("/runs"):
            return _packet()
        if method == "GET" and path.endswith("/evidence"):
            return {
                "status": "completed",
                "candidate_digest": "a" * 64,
                "review": {"reviewer_type": "github_copilot", "human_reviewer": False},
                "qualification": {"passed": True, "receipts": []},
                "final": {"core_metrics": {"same_class_net_improvement": 0.2}, "receipts": []},
                "operator_full_blindness": False,
                "operator_blindness_note": "模型未见后续集；操作者非完全盲态。",
            }
        if method == "GET":
            return next(self.statuses)
        if path.endswith("/review"):
            return {"status": "reviewed"}
        if path.endswith("/qualification"):
            return {"status": "qualification_running"}
        if path.endswith("/final"):
            return {"status": "final_running"}
        if method == "DELETE":
            return {"cleanup_completed": True}
        raise AssertionError((method, path, payload))


def _packet():
    return {
        "run_id": "D1V5EVO_" + "b" * 32,
        "status": "awaiting_review",
        "candidate_digest": "a" * 64,
        "candidate": {
            "source_case_ids": ["D1V5-DISCOVERY-T003", "D1V5-DISCOVERY-T006"],
            "target_agent": "expert_agent",
            "target_step_id": "expert",
            "field_path": "resource.content",
            "template_id": "d1_v5_conflict_authority_boundary",
            "production_registered": False,
            "production_active": False,
        },
        "signature": {"case_count": 2, "candidate_ready": True},
        "safety_replay": {"passed": True},
        "review_constraints": {"independent_human_review": False},
    }


def test_runner_drives_full_http_flow_and_writes_evidence(tmp_path):
    output = tmp_path / "summary.json"
    args = argparse.Namespace(
        run_id="",
        cleanup=False,
        discovery_run_id="D1V5DISC_" + "a" * 32,
        extract_only=False,
        purge_artifacts=False,
        keep_sandbox=False,
        poll_seconds=0,
        output=str(output),
    )
    client = FakeClient()
    runner = Runner(args, client)

    result = runner.execute()

    assert result["status"] == "completed"
    review = next(payload for method, path, payload in client.calls if path.endswith("/review"))
    assert review["reviewer_type"] == "github_copilot"
    assert review["independent_human_review"] is False
    assert output.is_file()
    evidence = output.with_name("summary.evidence.json")
    assert evidence.is_file()
    assert json.loads(evidence.read_text(encoding="utf-8"))["status"] == "completed"

    cleanup = runner.cleanup()
    assert cleanup["cleanup_completed"] is True
    runner.record_cleanup(cleanup)
    assert json.loads(output.read_text(encoding="utf-8"))["cleanup_receipt"] == cleanup
    assert json.loads(evidence.read_text(encoding="utf-8"))["cleanup_receipt"] == cleanup
    assert any(method == "DELETE" for method, _path, _payload in client.calls)