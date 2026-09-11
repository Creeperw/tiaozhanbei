from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from competition_app.api.app import create_app
from competition_app.application.container import ApplicationContainer
from competition_app.config import Settings, SettingsError
from competition_app.evaluation.d1_ab100_v2_runner import D1AB100V2Runner
from competition_app.evaluation.accountability_faults import FaultInjectingAgentProxy
from competition_app.evaluation.d1_v2_batch import D1V2BatchService, DEFAULT_EXECUTION_PROTOCOL_VERSION
from competition_app.evaluation.d1_v2_registry import D1V2DatasetRegistryService


TOKEN = "accountability-test-token-2026"
STARTUP_REVIEW_ATTESTATION = {
    "attempt_number": 1,
    "human_reviewer": True,
    "independent_review": True,
    "gold_labels_not_seen": True,
    "prior_review_decisions_not_seen": True,
}


def _registered_client(
    container: ApplicationContainer,
    *,
    username: str = "accountability-eval-user",
) -> tuple[TestClient, str]:
    client = TestClient(create_app(container, auth_required=True))
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "display_name": "追责评测用户",
            "password": "correct-horse-2026",
        },
    )
    assert response.status_code == 201
    return client, response.json()["user"]["user_id"]


def _valid_v2_pair_result(runner: D1AB100V2Runner, case_id: str) -> dict:
    case = next(item for item in runner.cases if item.case_id == case_id)
    exposure = bool(case.frozen_evidence.expected_rule_exposure)
    return {
        "case_id": case_id,
        "pair_order": case.pair_order,
        "formal_environment_write_allowed": False,
        "retrieval_pack_digest": "a" * 64,
        "validation": {"valid": True, "repair_contract_valid": True},
        "receipt": {
            "arms": [
                {
                    "arm": "A",
                    "input_digest": "b" * 64,
                    "rule_exposed": False,
                    "repair_count": 0,
                    "repair_attempt_count": 0,
                    "repair_exhausted": False,
                    "max_repair_attempts": 2,
                },
                {
                    "arm": "B",
                    "input_digest": "b" * 64,
                    "rule_exposed": exposure,
                    "repair_count": 0,
                    "repair_attempt_count": 0,
                    "repair_exhausted": False,
                    "max_repair_attempts": 2,
                },
            ]
        },
    }


async def _persist_completed_v2_formal_run(
    *,
    state_root: Path,
    registry_service: D1V2DatasetRegistryService,
    runner_id: str,
) -> str:
    registry, cases = registry_service.load()
    runner = D1AB100V2Runner(cases=cases)

    async def execute_pair(case_id: str, _learner_id: str) -> dict:
        return _valid_v2_pair_result(runner, case_id)

    service = D1V2BatchService(
        registry=registry,
        registry_service=registry_service,
        runner=runner,
        execute_pair=execute_pair,
        runtime_mode="live",
        state_root=state_root,
        execution_protocol_version=DEFAULT_EXECUTION_PROTOCOL_VERSION,
    )
    single = service.start(
        requested_by=runner_id,
        learner_id=runner_id,
        case_ids=["EVO-D1-V2-001"],
        purpose="single_trace",
    )
    await service._tasks[single["run_id"]]
    precheck = service.start(
        requested_by=runner_id,
        learner_id=runner_id,
        case_ids=list(registry.human_review_sample_case_ids),
        purpose="precheck",
        prerequisite_run_id=single["run_id"],
    )
    await service._tasks[precheck["run_id"]]
    service.confirm_precheck(
        precheck["run_id"],
        requested_by=runner_id,
        decision="go",
        note="已人工核对十条紧凑回执和技术执行合同。",
    )
    formal = service.start(
        requested_by=runner_id,
        learner_id=runner_id,
        case_ids=[case.case_id for case in runner.cases],
        purpose="formal",
        prerequisite_run_id=precheck["run_id"],
    )
    await service._tasks[formal["run_id"]]
    return str(formal["run_id"])


def test_internal_fault_routes_are_absent_when_feature_is_disabled(tmp_path):
    container = ApplicationContainer.build(
        Settings(mode="stub", runtime_root=tmp_path / "runtime"),
        snapshot_root=tmp_path,
    )
    paths = create_app(container, auth_required=False).openapi()["paths"]

    assert "/api/v1/internal-eval/accountability/cases" not in paths
    assert container.accountability_evaluation_service is None


def test_enabled_evaluation_requires_a_nontrivial_secret(tmp_path):
    with pytest.raises(SettingsError, match="at least 16 characters"):
        ApplicationContainer.build(
            Settings(
                mode="stub",
                runtime_root=tmp_path / "runtime",
                accountability_evaluation_enabled=True,
                accountability_evaluation_token="short",
            ),
            snapshot_root=tmp_path,
        )


def test_internal_evaluation_is_authenticated_and_executes_isolated_repair(tmp_path):
    container = ApplicationContainer.build(
        Settings(
            mode="stub",
            runtime_root=tmp_path / "runtime",
            accountability_evaluation_enabled=True,
            accountability_evaluation_token=TOKEN,
        ),
        snapshot_root=tmp_path,
    )
    client, learner_id = _registered_client(container)
    headers = {"X-Accountability-Evaluation-Token": TOKEN}

    assert not isinstance(
        container.review_card_use_case.orchestrator.agent_registry.get(
            "knowledge_base_agent"
        ),
        FaultInjectingAgentProxy,
    )
    assert isinstance(
        container.accountability_evaluation_service.use_case.orchestrator.agent_registry.get(
            "knowledge_base_agent"
        ),
        FaultInjectingAgentProxy,
    )

    assert client.get("/api/v1/internal-eval/accountability/cases").status_code == 403
    cases = client.get(
        "/api/v1/internal-eval/accountability/cases", headers=headers
    )
    assert cases.status_code == 200
    assert len(cases.json()["items"]) == 100
    evolution_cases = [
        item
        for item in cases.json()["items"]
        if item["case_id"].startswith("EVO_AB50_")
    ]
    assert len(evolution_cases) == 50
    added = [
        item
        for item in cases.json()["items"]
        if item["case_id"].startswith("ONL_FAULT_NEW30_")
    ]
    assert len(added) == 30
    assert len({item["topic"] for item in added}) == 30
    assert len({item["prompt"] for item in added}) == 30

    unknown_pair_rule = client.post(
        "/api/v1/internal-eval/evolution-effect/EVO_AB50_001/pair",
        params={"rule_id": "ERULE_UNKNOWN"},
        headers=headers,
    )
    assert unknown_pair_rule.status_code == 404

    with client.stream(
        "POST",
        "/api/v1/internal-eval/accountability/ONL_FAULT_FAKE_SIJUNZI/stream",
        headers=headers,
    ) as response:
        assert response.status_code == 200
        events = [
            json.loads(line[6:])
            for line in response.iter_lines()
            if line.startswith("data: ")
        ]

    started = next(item for item in events if item["event"] == "run_started")
    terminal = events[-1]
    assert terminal["event"] == "run_completed"
    assert any(item["event"] == "evaluation_fault_injected" for item in events)
    record = client.get(
        f"/api/v1/internal-eval/accountability/runs/{started['thread_id']}",
        headers=headers,
    )
    assert record.status_code == 200
    body = record.json()
    assert body["fault_detected"] is True
    assert body["strict_owner_correct"] is True
    assert body["rerun_chain_correct"] is True
    assert body["repair_success"] is True
    assert body["predicted_owner_step_ids"] == ["expert"]
    assert body["actual_rerun_step_ids"] == ["expert", "audit"]

    # The evaluation UseCase has private in-memory repositories and no
    # writeback executor; the formal conversation repository remains empty.
    formal_messages = container.review_card_use_case.conversation_repository.get_messages(
        started["thread_id"],
        learner_id,
    )
    assert formal_messages == []


def test_d1_real_execution_route_is_token_protected_and_isolated(tmp_path):
    container = ApplicationContainer.build(
        Settings(
            mode="stub",
            runtime_root=tmp_path / "runtime",
            accountability_evaluation_enabled=True,
            accountability_evaluation_token=TOKEN,
        ),
        snapshot_root=tmp_path,
    )
    client, _learner_id = _registered_client(container)
    panel = client.get("/internal-eval/d1-precheck")
    assert panel.status_code == 200
    assert "D1 真实模型在线预检" in panel.text
    assert "Execute" in panel.text
    assert "/api/v1/internal-eval/d1-precheck/{evaluation_case_id}/execute" in create_app(
        container, auth_required=False
    ).openapi()["paths"]

    denied = client.post(
        "/api/v1/internal-eval/d1-precheck/EVO-D1-PRE-001/execute"
    )
    assert denied.status_code == 403

    ab100_panel = client.get("/internal-eval/d1-ab100")
    assert ab100_panel.status_code == 200
    assert "D1 AB100 真实模型完整轨迹" in ab100_panel.text
    assert "Execute" in ab100_panel.text
    assert (
        "/api/v1/internal-eval/d1-ab100/{evaluation_case_id}/execute-trace"
        in create_app(container, auth_required=False).openapi()["paths"]
    )
    denied_ab100 = client.post(
        "/api/v1/internal-eval/d1-ab100/EVO-D1-AB50-001/execute-trace"
    )
    assert denied_ab100.status_code == 403


def test_d1_precheck_routes_are_absent_when_feature_is_disabled(tmp_path):
    container = ApplicationContainer.build(
        Settings(mode="stub", runtime_root=tmp_path / "runtime"),
        snapshot_root=tmp_path,
    )
    paths = create_app(container, auth_required=False).openapi()["paths"]
    assert "/api/v1/internal-eval/d1-precheck/cases" not in paths
    assert "/api/v1/internal-eval/d1-ab100/cases" not in paths


def test_d1_precheck_prepare_and_validate_are_token_protected(tmp_path):
    container = ApplicationContainer.build(
        Settings(
            mode="stub",
            runtime_root=tmp_path / "runtime",
            accountability_evaluation_enabled=True,
            accountability_evaluation_token=TOKEN,
        ),
        snapshot_root=tmp_path,
    )
    client, _ = _registered_client(container)
    assert client.get("/api/v1/internal-eval/d1-precheck/cases").status_code == 403
    headers = {"X-Accountability-Evaluation-Token": TOKEN}
    cases = client.get("/api/v1/internal-eval/d1-precheck/cases", headers=headers)
    assert cases.status_code == 200
    assert len(cases.json()["items"]) == 5
    prepared = client.post(
        "/api/v1/internal-eval/d1-precheck/EVO-D1-PRE-001/prepare",
        headers=headers,
    )
    assert prepared.status_code == 200
    body = prepared.json()
    assert body["formal_environment_write_allowed"] is False
    assert body["arms"][0]["input_digest"] == body["arms"][1]["input_digest"]
    assert body["arms"][0]["rule_enabled"] is False
    assert body["arms"][1]["rule_enabled"] is True
    validation = client.post(
        "/api/v1/internal-eval/d1-precheck/EVO-D1-PRE-001/validate",
        headers=headers,
        json={
            "case_id": "EVO-D1-PRE-001",
            "arms": [
                {"arm": "A", "input_digest": body["arms"][0]["input_digest"], "rule_exposed": False, "user_output": "回答", "target_failure": True, "closure_allowed": False},
                {"arm": "B", "input_digest": body["arms"][1]["input_digest"], "rule_exposed": True, "exposed_target_agent": "expert_agent", "user_output": "回答", "target_failure": False, "closure_allowed": True},
            ],
        },
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is True

    ab100_cases = client.get(
        "/api/v1/internal-eval/d1-ab100/cases",
        headers=headers,
    )
    assert ab100_cases.status_code == 200
    assert len(ab100_cases.json()["items"]) == 100


def test_d1_v2_admin_overview_is_read_only_and_redacted(tmp_path):
    container = ApplicationContainer.build(
        Settings(
            mode="stub",
            runtime_root=tmp_path / "runtime",
            accountability_evaluation_enabled=True,
            accountability_evaluation_token=TOKEN,
            admin_default_password="admin-password-2026",
        ),
        snapshot_root=tmp_path,
    )
    client = TestClient(create_app(container, auth_required=True))
    assert client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin-password-2026"},
    ).status_code == 200

    overview = client.get("/api/v1/evolution/evaluation/d1-v2/overview")

    assert overview.status_code == 200
    body = overview.json()
    assert body["evaluation_does_not_approve_or_activate_rule"] is True
    assert body["runs"] == []
    manifest = body["manifest"]
    assert manifest["human_review_required_count"] == 10
    assert manifest["human_review_completed_count"] == 0
    assert manifest["human_review_passed"] is False
    assert manifest["runtime_mode"] == "stub"
    assert manifest["execution_block_reason"] == "live_mode_required"
    assert "dataset_path" not in manifest
    assert not str(manifest["dataset_artifact"]).startswith("/")


def test_d1_v5_evolution_namespace_is_token_only_without_weakening_others(tmp_path):
    container = ApplicationContainer.build(
        Settings(
            mode="stub",
            runtime_root=tmp_path / "runtime",
            accountability_evaluation_enabled=True,
            accountability_evaluation_token=TOKEN,
        ),
        snapshot_root=tmp_path,
    )
    client = TestClient(create_app(container, auth_required=True))
    headers = {"X-Accountability-Evaluation-Token": TOKEN}

    # The exact V5 namespace reaches its sandbox without creating an auth session.
    v5 = client.post(
        "/api/v1/internal-eval/d1-v5/evolution/runs",
        headers=headers,
        json={"discovery_run_id": "D1V5DISC_" + "a" * 32},
    )
    assert v5.status_code == 409
    assert "COMPETITION_APP_MODE=live" in v5.json()["detail"]
    assert container.authentication_service.repository.get_session("unknown") is None

    invalid = client.post(
        "/api/v1/internal-eval/d1-v5/evolution/runs",
        headers={"X-Accountability-Evaluation-Token": "wrong-token-value"},
        json={"discovery_run_id": "D1V5DISC_" + "a" * 32},
    )
    assert invalid.status_code == 401

    # Existing internal-eval routes retain the cookie + token contract.
    old_namespace = client.get(
        "/api/v1/internal-eval/d1-v5-discovery/manifest",
        headers=headers,
    )
    assert old_namespace.status_code == 401


def test_d1_v2_console_and_independent_review_are_protected_and_blinded(tmp_path):
    container = ApplicationContainer.build(
        Settings(
            mode="stub",
            runtime_root=tmp_path / "runtime",
            accountability_evaluation_enabled=True,
            accountability_evaluation_token=TOKEN,
        ),
        snapshot_root=tmp_path,
    )
    client, _ = _registered_client(container)
    headers = {"X-Accountability-Evaluation-Token": TOKEN}

    panel = client.get("/internal-eval/d1-v2")
    assert panel.status_code == 200
    assert panel.headers["cache-control"].startswith("no-store")
    assert "D1 AB100-V2 评测运行台" in panel.text
    assert 'type="password"' in panel.text
    assert 'id="precheckReceipts"' in panel.text
    assert 'id="blindRunId"' in panel.text
    assert TOKEN not in panel.text

    assert client.get("/api/v1/internal-eval/d1-v2/manifest").status_code == 403
    assert (
        client.get("/api/v1/internal-eval/d1-v2/independent-review").status_code
        == 403
    )
    package = client.get(
        "/api/v1/internal-eval/d1-v2/independent-review",
        headers=headers,
    )
    assert package.status_code == 200
    assert package.headers["cache-control"].startswith("no-store")
    assert package.headers["pragma"] == "no-cache"
    body = package.json()
    assert len(body["items"]) == 10
    assert body["gold_labels_included"] is False
    assert body["gold_rationales_included"] is False
    assert body["prior_review_decisions_included"] is False
    assert body["attempt_number"] == 1
    assert "同一目标声明" in body["relation_definitions"]["compatible"]
    forbidden = {
        "gold_relation",
        "relation_dimension",
        "gold_rationale",
        "expected_rule_exposure",
        "synthetic_fault",
        "reviewer_decision",
    }
    assert all(not forbidden.intersection(item) for item in body["items"])

    reviews = [
        {
            "case_id": item["case_id"],
            "reviewer_relation": "not_applicable",
            "reviewer_name": "独立复核员",
            "review_notes": "已独立核对两条证据。",
        }
        for item in body["items"]
    ]
    submitted = client.post(
        "/api/v1/internal-eval/d1-v2/independent-review",
        headers=headers,
        json={**STARTUP_REVIEW_ATTESTATION, "items": reviews},
    )
    assert submitted.status_code == 200
    assert submitted.json()["human_review_complete"] is True
    assert submitted.json()["human_review_submitter_present"] is True
    assert "human_review_submitted_by" not in submitted.json()
    duplicate = client.post(
        "/api/v1/internal-eval/d1-v2/independent-review",
        headers=headers,
        json={**STARTUP_REVIEW_ATTESTATION, "items": reviews},
    )
    assert duplicate.status_code == 409
    assert "different authenticated account" in duplicate.json()["detail"]


def test_d1_v2_failed_startup_review_retry_requires_new_authenticated_account(
    tmp_path,
):
    runtime_root = tmp_path / "runtime"
    container = ApplicationContainer.build(
        Settings(
            mode="stub",
            runtime_root=runtime_root,
            accountability_evaluation_enabled=True,
            accountability_evaluation_token=TOKEN,
        ),
        snapshot_root=tmp_path,
    )
    first_client, _first_id = _registered_client(
        container,
        username="first-startup-reviewer",
    )
    second_client, _second_id = _registered_client(
        container,
        username="second-startup-reviewer",
    )
    headers = {"X-Accountability-Evaluation-Token": TOKEN}
    state_root = runtime_root / "evaluation" / "d1-v2"
    registry_service = D1V2DatasetRegistryService(
        human_review_path=state_root / "independent_review.csv"
    )
    _registry, cases = registry_service.load()
    gold_by_id = {
        case.case_id: str(case.frozen_evidence.gold_relation)
        for case in cases
    }

    first_package = first_client.get(
        "/api/v1/internal-eval/d1-v2/independent-review",
        headers=headers,
    ).json()
    first_items = [
        {
            "case_id": item["case_id"],
            "reviewer_relation": gold_by_id[item["case_id"]],
            "reviewer_name": "首次真人复核员",
            "review_notes": "按统一规则范围和目标声明独立判断。",
        }
        for item in first_package["items"]
    ]
    first_items[0]["reviewer_relation"] = "compatible"
    first_response = first_client.post(
        "/api/v1/internal-eval/d1-v2/independent-review",
        headers=headers,
        json={**STARTUP_REVIEW_ATTESTATION, "items": first_items},
    )
    assert first_response.status_code == 200
    assert first_response.json()["human_review_retry_allowed"] is True
    first_path = state_root / "independent_review.csv"
    first_bytes = first_path.read_bytes()

    second_package = second_client.get(
        "/api/v1/internal-eval/d1-v2/independent-review",
        headers=headers,
    ).json()
    assert second_package["attempt_number"] == 2
    assert second_package["prior_review_decisions_included"] is False
    second_items = [
        {
            "case_id": item["case_id"],
            "reviewer_relation": gold_by_id[item["case_id"]],
            "reviewer_name": "第二次真人复核员",
            "review_notes": "按统一规则范围和目标声明独立判断。",
        }
        for item in second_package["items"]
    ]
    second_response = second_client.post(
        "/api/v1/internal-eval/d1-v2/independent-review",
        headers=headers,
        json={
            **STARTUP_REVIEW_ATTESTATION,
            "attempt_number": 2,
            "items": second_items,
        },
    )

    assert second_response.status_code == 200
    assert first_path.read_bytes() == first_bytes
    assert second_response.json()["human_review_passed"] is True
    assert second_response.json()["human_review_attempt_count"] == 2
    assert (
        state_root / "independent_review.attempt-2.csv"
    ).is_file()
    third_response = second_client.post(
        "/api/v1/internal-eval/d1-v2/independent-review",
        headers=headers,
        json={
            **STARTUP_REVIEW_ATTESTATION,
            "attempt_number": 2,
            "items": second_items,
        },
    )
    assert third_response.status_code == 409
    assert "already been recorded" in third_response.json()["detail"]


@pytest.mark.asyncio
async def test_d1_v2_formal_blind_review_is_cross_account_but_results_are_owner_only(
    tmp_path,
):
    runtime_root = tmp_path / "runtime"
    settings = Settings(
        mode="stub",
        runtime_root=runtime_root,
        accountability_evaluation_enabled=True,
        accountability_evaluation_token=TOKEN,
    )
    container = ApplicationContainer.build(settings, snapshot_root=tmp_path)
    runner_client, runner_id = _registered_client(
        container,
        username="v2-evaluation-runner",
    )
    reviewer_client, reviewer_id = _registered_client(
        container,
        username="v2-independent-reviewer",
    )
    headers = {"X-Accountability-Evaluation-Token": TOKEN}
    state_root = runtime_root / "evaluation" / "d1-v2"
    registry_service = D1V2DatasetRegistryService(
        human_review_path=state_root / "independent_review.csv"
    )
    _registry, cases = registry_service.load()
    gold_by_id = {
        case.case_id: str(case.frozen_evidence.gold_relation)
        for case in cases
    }
    startup_package = reviewer_client.get(
        "/api/v1/internal-eval/d1-v2/independent-review",
        headers=headers,
    ).json()
    startup_review = reviewer_client.post(
        "/api/v1/internal-eval/d1-v2/independent-review",
        headers=headers,
        json={
            **STARTUP_REVIEW_ATTESTATION,
            "items": [
                {
                    "case_id": item["case_id"],
                    "reviewer_relation": gold_by_id[item["case_id"]],
                    "reviewer_name": "启动复核员",
                    "review_notes": "已独立核对主张和两条冻结证据。",
                }
                for item in startup_package["items"]
            ]
        },
    )
    assert startup_review.status_code == 200
    assert startup_review.json()["human_review_passed"] is True
    assert reviewer_id != runner_id

    formal_run_id = await _persist_completed_v2_formal_run(
        state_root=state_root,
        registry_service=registry_service,
        runner_id=runner_id,
    )
    api_app = create_app(container, auth_required=True)
    runner_api = TestClient(api_app)
    reviewer_api = TestClient(api_app)
    assert runner_api.post(
        "/api/v1/auth/login",
        json={
            "username": "v2-evaluation-runner",
            "password": "correct-horse-2026",
        },
    ).status_code == 200
    assert reviewer_api.post(
        "/api/v1/auth/login",
        json={
            "username": "v2-independent-reviewer",
            "password": "correct-horse-2026",
        },
    ).status_code == 200

    status_path = f"/api/v1/internal-eval/d1-v2/batches/{formal_run_id}"
    assert runner_api.get(status_path, headers=headers).status_code == 200
    assert runner_api.get(f"{status_path}/receipts", headers=headers).status_code == 200
    assert runner_api.get(f"{status_path}/score", headers=headers).status_code == 200
    assert reviewer_api.get(status_path, headers=headers).status_code == 403
    assert reviewer_api.get(f"{status_path}/receipts", headers=headers).status_code == 403
    assert reviewer_api.get(f"{status_path}/score", headers=headers).status_code == 403

    blind_package = reviewer_api.get(
        f"{status_path}/blind-review",
        headers=headers,
    )
    assert blind_package.status_code == 200
    blind_body = blind_package.json()
    assert blind_body["gold_labels_included"] is False
    assert blind_body["gold_rationales_included"] is False
    assert blind_body["model_outcomes_included"] is False
    assert len(blind_body["items"]) == 20
    assert all(
        not {
            "gold_relation",
            "gold_rationale",
            "model_output",
            "receipt",
            "score",
        }.intersection(item)
        for item in blind_body["items"]
    )
    formal_reviews = {
        "items": [
            {
                "case_id": item["case_id"],
                "reviewer_relation": gold_by_id[item["case_id"]],
                "reviewer_name": "正式盲审员",
                "review_notes": "已独立核对主张和两条冻结证据。",
            }
            for item in blind_body["items"]
        ]
    }
    self_review = runner_api.post(
        f"{status_path}/blind-review",
        headers=headers,
        json=formal_reviews,
    )
    assert self_review.status_code == 409
    assert "independent" in self_review.json()["detail"]
    submitted = reviewer_api.post(
        f"{status_path}/blind-review",
        headers=headers,
        json=formal_reviews,
    )
    assert submitted.status_code == 200
    assert submitted.json()["blind_review"]["minimum_met"] is True

    paths = api_app.openapi()["paths"]
    assert (
        "/api/v1/internal-eval/d1-v2/batches/{run_id}/review-evidence"
        not in paths
    )
    retired_response = reviewer_api.post(
        f"{status_path}/review-evidence",
        headers=headers,
        json={"repeat_receipts": [], "blind_reviews": []},
    )
    assert retired_response.status_code in {404, 405}
