#!/usr/bin/env python3
from __future__ import annotations

"""Drive the isolated D1 V5 evolution experiment through backend HTTP APIs."""

import argparse
import json
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any
from datetime import datetime, timezone


EXPECTED_DATASET_ID = "d1_three_stage_v5_advantage"
EXPECTED_DATASET_VERSION = "5.1.0"
TERMINAL_STATES = {
    "completed",
    "qualification_passed",
    "qualification_failed",
    "final_failed",
    "failed",
    "interrupted",
    "qualification_interrupted",
    "final_interrupted",
    "stale",
    "rejected",
    "cleaned",
}


class ApiError(RuntimeError):
    pass


class Client:
    def __init__(self, base_url: str, token: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        headers = {
            "Accept": "application/json",
            "X-Accountability-Evaluation-Token": self.token,
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ApiError(f"HTTP {exc.code} {method} {path}: {detail[:1000]}") from exc
        except urllib.error.URLError as exc:
            raise ApiError(f"backend request failed: {exc.reason}") from exc
        parsed = json.loads(body or "{}")
        if not isinstance(parsed, dict):
            raise ApiError(f"backend returned a non-object for {method} {path}")
        return parsed


class Runner:
    def __init__(self, args: argparse.Namespace, client: Client) -> None:
        self.args = args
        self.client = client
        self.run_id = str(args.run_id or "")
        self.discovery_run_id = str(args.discovery_run_id or "")
        self.discovery_cleanup_needed = False
        self.cleanup_needed = bool(self.run_id)
        self.interrupted = False
        self.resume_attempts = 0

    def install_signal_handlers(self) -> None:
        def stop(signum: int, _frame: Any) -> None:
            self.interrupted = True
            raise KeyboardInterrupt(f"received signal {signum}")

        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

    def execute(self) -> dict[str, Any]:
        if self.args.cleanup:
            if not self.run_id:
                raise ValueError("--cleanup requires --run-id")
            return self.cleanup()

        if not self.run_id:
            if not self.discovery_run_id:
                discovery = self.client.request(
                    "POST",
                    "/api/v1/internal-eval/d1-v5-discovery/batches",
                )
                self.discovery_run_id = str(discovery["run_id"])
                self.discovery_cleanup_needed = True
                discovery = self._wait_for_discovery()
                if discovery.get("status") != "completed":
                    raise ApiError(
                        "V5.1 discovery did not complete: "
                        + json.dumps(discovery, ensure_ascii=False)[:2000]
                    )
                receipts = self.client.request(
                    "GET",
                    "/api/v1/internal-eval/d1-v5-discovery/batches/"
                    f"{self.discovery_run_id}/receipts",
                )
                failed = [
                    row.get("case_id")
                    for row in receipts.get("receipts") or []
                    if (row.get("semantic_verdict") or {}).get("acceptable") is False
                ]
                if len(failed) < 2:
                    raise ApiError(
                        "discovery found fewer than two semantic failures; "
                        "candidate generation is not justified"
                    )
                print(
                    "discovery source candidates: " + ", ".join(map(str, failed)),
                    flush=True,
                )
            packet = self.client.request(
                "POST",
                "/api/v1/internal-eval/d1-v5/evolution/runs",
                {"discovery_run_id": self.discovery_run_id},
            )
            self.run_id = str(packet["run_id"])
            self.cleanup_needed = True
            self._print_review_packet(packet)
        else:
            packet = self.client.request(
                "GET",
                f"/api/v1/internal-eval/d1-v5/evolution/runs/{self.run_id}/review-packet",
            )

        if packet.get("status") == "awaiting_review":
            candidate = packet.get("candidate") or {}
            if candidate.get("source_case_ids") is None:
                raise ApiError("candidate source boundary is missing")

        if self.args.extract_only:
            self.cleanup_needed = False
            return {
                "run_id": self.run_id,
                "status": "awaiting_review",
                "candidate_digest": packet.get("candidate_digest"),
            }

        status = self.client.request(
            "GET",
            f"/api/v1/internal-eval/d1-v5/evolution/runs/{self.run_id}",
        )
        if status["status"] == "awaiting_review":
            self._verify_candidate(packet)
            status = self.client.request(
                "POST",
                f"/api/v1/internal-eval/d1-v5/evolution/runs/{self.run_id}/review",
                {
                    "decision": "approve",
                    "reviewer_type": "github_copilot",
                    "independent_human_review": False,
                    "note": (
                        "GitHub Copilot 非独立、非人工审核：候选仅来自本次冻结 discovery "
                        "中最先出现的两条机制匹配语义失败，作用域限定 "
                        "expert/resource.content，"
                        "不注册、不激活、不写入生产治理存储。"
                    ),
                    "candidate_digest": packet["candidate_digest"],
                },
            )
        if status["status"] == "reviewed":
            self.client.request(
                "POST",
                f"/api/v1/internal-eval/d1-v5/evolution/runs/{self.run_id}/qualification",
            )
            status = self._wait_for({"qualification_passed", "qualification_failed", "failed", "interrupted"})
        if status["status"] != "qualification_passed":
            evidence = self._evidence()
            raise ApiError(
                "qualification did not pass: "
                + json.dumps(
                    evidence.get("qualification") or status.get("qualification_summary"),
                    ensure_ascii=False,
                )[:2000]
            )
        self._print_qualification(status)

        self.client.request(
            "POST",
            f"/api/v1/internal-eval/d1-v5/evolution/runs/{self.run_id}/final",
        )
        status = self._wait_for({"completed", "final_failed", "failed", "interrupted"})
        evidence = self._evidence()
        if status["status"] != "completed":
            raise ApiError(f"final evaluation ended as {status['status']}")
        self._write_summary(evidence)
        return evidence

    def _wait_for_discovery(self) -> dict[str, Any]:
        last_case = object()
        while True:
            status = self.client.request(
                "GET",
                "/api/v1/internal-eval/d1-v5-discovery/batches/"
                f"{self.discovery_run_id}",
            )
            if status.get("dataset_id") != EXPECTED_DATASET_ID or status.get(
                "dataset_version"
            ) != EXPECTED_DATASET_VERSION:
                raise ApiError("backend is not serving the frozen V5.1 advantage dataset")
            current_case = status.get("active_case_id")
            if current_case != last_case:
                print(
                    "[discovery] "
                    f"{status.get('completed_case_count', 0)}/"
                    f"{status.get('total_case_count', 0)} {current_case or ''}",
                    flush=True,
                )
                last_case = current_case
            if status.get("status") in {"completed", "failed", "interrupted"}:
                return status
            time.sleep(self.args.poll_seconds)

    def cleanup(self) -> dict[str, Any]:
        query = urllib.parse.urlencode(
            {"purge_artifacts": "true" if self.args.purge_artifacts else "false"}
        )
        result = self.client.request(
            "DELETE",
            f"/api/v1/internal-eval/d1-v5/evolution/runs/{self.run_id}?{query}",
        )
        self.cleanup_needed = False
        return result

    def cleanup_discovery(self) -> dict[str, Any] | None:
        if not self.discovery_run_id or not self.discovery_cleanup_needed:
            return None
        result = self.client.request(
            "DELETE",
            "/api/v1/internal-eval/d1-v5-discovery/batches/"
            f"{self.discovery_run_id}?purge_artifact=true",
        )
        self.discovery_cleanup_needed = False
        return result

    def _wait_for(self, terminal_states: set[str]) -> dict[str, Any]:
        last_case = object()
        while True:
            status = self.client.request(
                "GET",
                f"/api/v1/internal-eval/d1-v5/evolution/runs/{self.run_id}",
            )
            current_case = status.get("active_case_id")
            if current_case != last_case:
                completed = int(status.get("completed_case_count") or 0)
                total = int(status.get("total_case_count") or 0)
                print(
                    f"[{status.get('active_stage') or status['status']}] "
                    f"{completed}/{total} {current_case or ''}",
                    flush=True,
                )
                last_case = current_case
            if status["status"] in {
                "interrupted",
                "qualification_interrupted",
                "final_interrupted",
                "stale",
            }:
                status = self._resume(status)
                last_case = object()
                continue
            if status["status"] in terminal_states:
                return status
            heartbeat_age = self._heartbeat_age(status.get("heartbeat_at"))
            if (
                status["status"] in {"qualification_running", "final_running"}
                and heartbeat_age is not None
                and heartbeat_age > self.args.stale_seconds
            ):
                # The status endpoint marks an orphaned server task stale. A
                # still-live task is left alone because its own hard deadlines
                # remain authoritative.
                refreshed = self.client.request(
                    "GET",
                    f"/api/v1/internal-eval/d1-v5/evolution/runs/{self.run_id}",
                )
                if refreshed["status"] == "stale":
                    status = self._resume(refreshed)
                    last_case = object()
                    continue
            time.sleep(self.args.poll_seconds)

    def _resume(self, status: dict[str, Any]) -> dict[str, Any]:
        if self.resume_attempts >= self.args.max_resumes:
            raise ApiError(
                f"evaluation exceeded {self.args.max_resumes} resume attempts; "
                f"last state={status.get('status')}"
            )
        self.resume_attempts += 1
        print(
            f"resuming {status.get('resume_stage') or 'unknown stage'} from arm checkpoint "
            f"(attempt {self.resume_attempts}/{self.args.max_resumes})",
            flush=True,
        )
        return self.client.request(
            "POST",
            f"/api/v1/internal-eval/d1-v5/evolution/runs/{self.run_id}/resume",
        )

    @staticmethod
    def _heartbeat_age(value: Any) -> float | None:
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())

    def _evidence(self) -> dict[str, Any]:
        return self.client.request(
            "GET",
            f"/api/v1/internal-eval/d1-v5/evolution/runs/{self.run_id}/evidence",
        )

    @staticmethod
    def _verify_candidate(packet: dict[str, Any]) -> None:
        candidate = packet.get("candidate") or {}
        signature = packet.get("signature") or {}
        constraints = packet.get("review_constraints") or {}
        failures: list[str] = []
        source_case_ids = candidate.get("source_case_ids") or []
        if len(source_case_ids) != 2 or any(
            not str(case_id).startswith("D1V5-DISCOVERY-T")
            for case_id in source_case_ids
        ):
            failures.append("candidate source boundary is not two discovery cases")
        if candidate.get("template_id") != "d1_v5_conflict_authority_boundary":
            failures.append("candidate template is not the isolated V5 template")
        if candidate.get("target_agent") != "expert_agent":
            failures.append("candidate target agent is not expert_agent")
        if candidate.get("target_step_id") != "expert":
            failures.append("candidate target step is not expert")
        if candidate.get("field_path") != "resource.content":
            failures.append("candidate field scope is not resource.content")
        if candidate.get("production_registered") is not False:
            failures.append("candidate claims production registration")
        if candidate.get("production_active") is not False:
            failures.append("candidate claims production activation")
        if candidate.get("candidate_authorship") != "experimental_model_authored_candidate":
            failures.append("candidate strategy is not model-authored")
        if candidate.get("hardcoded_strategy_fallback_used") is not False:
            failures.append("candidate used a hard-coded strategy fallback")
        proposal = candidate.get("model_authored_proposal") or {}
        if proposal.get("strategy_text") != candidate.get("strategy_text"):
            failures.append("candidate strategy differs from the model proposal")
        if not candidate.get("candidate_proposal_digest"):
            failures.append("candidate proposal digest is missing")
        if signature.get("case_count") != 2 or signature.get("candidate_ready") is not True:
            failures.append("experimental signature threshold is invalid")
        if constraints.get("independent_human_review") is not False:
            failures.append("review packet misstates review independence")
        if not (packet.get("safety_replay") or {}).get("passed"):
            failures.append("safety replay did not pass")
        if failures:
            raise ValueError("candidate review rejected: " + "; ".join(failures))

    def _print_review_packet(self, packet: dict[str, Any]) -> None:
        candidate = packet.get("candidate") or {}
        print(f"sandbox run: {packet['run_id']}")
        print(f"candidate digest: {packet['candidate_digest']}")
        print(f"candidate template: {candidate.get('template_id')}")
        print(f"source cases: {', '.join(candidate.get('source_case_ids') or [])}")
        print(f"analysis: {candidate.get('natural_language_analysis', '')}")

    @staticmethod
    def _print_qualification(status: dict[str, Any]) -> None:
        summary = status.get("qualification_summary") or {}
        print(
            "qualification passed: "
            f"{summary.get('paired_improvement_count', 0)} improvements, "
            f"{summary.get('paired_regression_count', 0)} regressions",
            flush=True,
        )

    def _write_summary(self, evidence: dict[str, Any]) -> None:
        final = evidence.get("final") or {}
        score = {
            key: value
            for key, value in final.items()
            if key not in {"receipts", "technical_errors"}
        }
        output = Path(self.args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        evidence_output = self._write_evidence(evidence)
        output.write_text(
            json.dumps(
                {
                    "run_id": self.run_id,
                    "candidate_digest": evidence.get("candidate_digest"),
                    "review": evidence.get("review"),
                    "qualification": {
                        key: value
                        for key, value in (evidence.get("qualification") or {}).items()
                        if key not in {"receipts", "technical_errors"}
                    },
                    "final_score": score,
                    "production_snapshot_before": evidence.get("production_snapshot_before"),
                    "operator_full_blindness": evidence.get("operator_full_blindness"),
                    "operator_blindness_note": evidence.get("operator_blindness_note"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(json.dumps(score.get("core_metrics") or {}, ensure_ascii=False, indent=2))
        print(f"summary written: {output}")
        print(f"evidence written: {evidence_output}")

    def _write_evidence(
        self,
        evidence: dict[str, Any],
        *,
        failure: str | None = None,
    ) -> Path:
        output = Path(self.args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        evidence_output = output.with_name(output.stem + ".evidence.json")
        payload = dict(evidence)
        if failure:
            payload["driver_failure"] = failure[:2000]
        temporary = evidence_output.with_suffix(evidence_output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, evidence_output)
        return evidence_output

    def record_cleanup(self, cleanup: dict[str, Any]) -> None:
        output = Path(self.args.output).expanduser().resolve()
        evidence_output = output.with_name(output.stem + ".evidence.json")
        for path in (output, evidence_output):
            if not path.is_file():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                continue
            payload["cleanup_receipt"] = cleanup
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run isolated D1 V5 rule extraction, review, qualification and final A/B."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:7860")
    parser.add_argument(
        "--discovery-run-id",
        default="",
        help="reuse a completed V5.1 discovery run; blank starts a fresh run",
    )
    parser.add_argument("--run-id", default="", help="resume an in-memory sandbox run")
    parser.add_argument("--extract-only", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument("--purge-artifacts", action="store_true")
    parser.add_argument("--keep-sandbox", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument("--request-timeout", type=float, default=120.0)
    parser.add_argument("--stale-seconds", type=float, default=1_500.0)
    parser.add_argument("--max-resumes", type=int, default=2)
    parser.add_argument(
        "--output",
        default="runtime/evaluation/d1-v5-evolution/latest-summary.json",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = os.environ.get("ACCOUNTABILITY_EVALUATION_TOKEN", "").strip()
    if len(token) < 16:
        print(
            "ACCOUNTABILITY_EVALUATION_TOKEN must be set to the backend evaluation token",
            file=sys.stderr,
        )
        return 2
    runner = Runner(args, Client(args.base_url, token, args.request_timeout))
    runner.install_signal_handlers()
    failure: str | None = None
    try:
        result = runner.execute()
        print(
            json.dumps(
                {
                    "run_id": runner.run_id,
                    "status": result.get("status") or "completed",
                },
                ensure_ascii=False,
            )
        )
        return 0
    except KeyboardInterrupt:
        failure = "evaluation interrupted"
        print("evaluation interrupted; cleaning sandbox", file=sys.stderr)
        return 130
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if (
            runner.cleanup_needed
            and not args.extract_only
            and not args.keep_sandbox
            and not args.cleanup
        ):
            try:
                if failure:
                    try:
                        runner._write_evidence(runner._evidence(), failure=failure)
                    except Exception as evidence_exc:
                        print(
                            f"failure evidence capture failed: {evidence_exc}",
                            file=sys.stderr,
                        )
                cleanup = runner.cleanup()
                runner.record_cleanup(cleanup)
                print("cleanup: " + json.dumps(cleanup, ensure_ascii=False))
            except Exception as exc:
                print(f"cleanup failed: {exc}", file=sys.stderr)
        if runner.discovery_cleanup_needed and not args.keep_sandbox:
            try:
                discovery_cleanup = runner.cleanup_discovery()
                print(
                    "discovery cleanup: "
                    + json.dumps(discovery_cleanup, ensure_ascii=False)
                )
            except Exception as exc:
                print(f"discovery cleanup failed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
