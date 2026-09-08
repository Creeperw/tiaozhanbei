"""Process-local attestations issued only after explicit semantic plan review.

No model output, user payload or persisted old audit can manufacture an approval.
Restart invalidates pending approvals; callers must re-audit rather than fall back.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Any


_SIGNING_KEY = secrets.token_bytes(32)
_POLICY = "medical-education-semantic-v1"
_TTL_SECONDS = 3600


def _digest(proposal: Any) -> str:
    value = proposal.model_dump(mode="json")
    return hashlib.sha256(_canonical(value)).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def issue_plan_safety_approval(*, proposal: Any, learner_id: str, scope: str,
                               audit_id: str) -> dict[str, Any]:
    if not learner_id or not audit_id or scope not in {"long_term", "short_term"}:
        raise ValueError("invalid medical safety approval identity")
    issued = time.time()
    body = {
        "policy": _POLICY, "learner_id": learner_id, "scope": scope,
        "proposal_digest": _digest(proposal), "audit_id": audit_id,
        "issued_at": issued, "expires_at": issued + _TTL_SECONDS,
    }
    return {**body, "signature": hmac.new(_SIGNING_KEY, _canonical(body), hashlib.sha256).hexdigest()}


def verify_plan_safety_approval(approval: Any, *, proposal: Any,
                                learner_id: str, scope: str) -> None:
    message = "medical safety approval is missing, stale or does not match current proposal"
    if not isinstance(approval, dict):
        raise ValueError(message)
    body = {key: value for key, value in approval.items() if key != "signature"}
    signature = approval.get("signature")
    try:
        expected = hmac.new(_SIGNING_KEY, _canonical(body), hashlib.sha256).hexdigest()
        now = time.time()
        valid = (
            isinstance(signature, str) and hmac.compare_digest(signature, expected)
            and body["policy"] == _POLICY and body["learner_id"] == learner_id
            and body["scope"] == scope and scope in {"long_term", "short_term"}
            and body["proposal_digest"] == _digest(proposal)
            and body["issued_at"] <= now < body["expires_at"]
            and body["expires_at"] - body["issued_at"] == _TTL_SECONDS
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError(message)