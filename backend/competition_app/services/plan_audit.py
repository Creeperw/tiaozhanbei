from __future__ import annotations

import hashlib
import json
from typing import Any


def plan_audit_subject_digest(
    *,
    plan_scope: str,
    proposal: Any,
    compiled_plan_contract: Any,
    parent_plan_constraints: dict[str, Any] | None = None,
    prerequisite_assessment: dict[str, Any] | None = None,
    planning_request_scope: dict[str, Any] | None = None,
    planning_focus_assessment: dict[str, Any] | None = None,
) -> str:
    """Bind an audit approval to the exact semantic proposal and parent context."""

    payload = {
        "plan_scope": plan_scope,
        "proposal": (
            proposal.model_dump(mode="json")
            if hasattr(proposal, "model_dump")
            else proposal
        ),
        "compiled_contract": (
            compiled_plan_contract.model_dump(mode="json")
            if hasattr(compiled_plan_contract, "model_dump")
            else compiled_plan_contract
        ),
        "parent_plan_constraints": parent_plan_constraints or {},
    }
    if prerequisite_assessment is not None:
        payload["prerequisite_assessment"] = prerequisite_assessment
    if planning_request_scope is not None:
        payload["planning_request_scope"] = planning_request_scope
    if planning_focus_assessment is not None:
        payload["planning_focus_assessment"] = planning_focus_assessment
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
