from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Mapping


MEDICAL_TRAINING_DISCLAIMER = "本模拟仅用于教学训练，不能替代专业诊断。"
SAFE_FAILURE_REPLY = "暂时无法生成安全的模拟患者回答，请稍后重试。"
SAFE_HUMAN_REVIEW_REPLY = "该模拟回答需要人工复核，暂时无法继续本轮问诊。"
EMERGENCY_REPLY = "这可能是现实中的紧急情况，请立即联系急救服务或尽快前往急诊，并停止本次角色扮演。"

_REAL_WORLD_MARKERS = ("不是演练", "不是模拟", "现实中", "真实情况", "我现在")
_EMERGENCY_MARKERS = ("胸痛", "呼吸困难", "喘不上气", "晕倒", "昏迷", "大出血", "自杀", "服毒")
_HUMAN_REVIEW_DECISIONS = {"human_review", "needs_human_review"}
_ALLOWED_AUDIT_DECISIONS = {"pass", "revise", "reject", *_HUMAN_REVIEW_DECISIONS}
_HIDDEN_CONTEXT_KEYS = (
    "syndrome",
    "prescription",
    "gold_answer",
    "golden_standard",
    "rubric",
    "treatment",
    "formula",
)


@dataclass(frozen=True)
class CasePatientOrchestrationRequest:
    session_id: str
    learner_message: str
    conversation: tuple[dict[str, str], ...]
    patient_context: dict[str, Any]


@dataclass(frozen=True)
class CasePatientOrchestrationResult:
    status: str
    reply: str
    persistable: bool
    disclaimer: str
    error_code: str | None
    trace: tuple[dict[str, Any], ...]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reply": self.reply,
            "persistable": self.persistable,
            "disclaimer": self.disclaimer,
            "error_code": self.error_code,
            "trace": [dict(item) for item in self.trace],
        }


def generate_simulated_patient_reply(
    *,
    learner_message: str,
    conversation: tuple[dict[str, str], ...],
    patient_context: Mapping[str, Any],
    revision_instruction: str | None = None,
) -> dict[str, str]:
    symptoms = patient_context.get("reported_symptoms")
    if not isinstance(symptoms, (list, tuple)):
        symptoms = ()
    safe_symptoms = [str(value).strip() for value in symptoms if str(value).strip()]
    if safe_symptoms:
        reply = f"我主要感觉{'、'.join(safe_symptoms[:3])}。"
    else:
        reply = "我只能描述自己目前感受到的不适，您可以继续询问具体症状。"
    return {"reply": reply}


def audit_simulated_patient_reply(
    *,
    draft: Mapping[str, Any],
    learner_message: str,
    conversation: tuple[dict[str, str], ...],
    patient_context: Mapping[str, Any],
) -> dict[str, str]:
    reply = _extract_reply(draft)
    if not reply or _contains_hidden_value(reply, patient_context):
        return {"decision": "reject", "reason": "patient_reply_not_publishable"}
    return {"decision": "pass"}


def orchestrate_case_patient_reply(
    request: CasePatientOrchestrationRequest,
    *,
    patient_runner: Callable[..., Any],
    auditor: Callable[..., Any],
    max_revisions: int = 1,
) -> CasePatientOrchestrationResult:
    if max_revisions < 0 or max_revisions > 2:
        raise ValueError("max_revisions must be between 0 and 2")
    if _is_real_world_emergency(request.learner_message):
        return _result(
            status="safety_stopped",
            reply=EMERGENCY_REPLY,
            persistable=False,
            error_code="real_world_emergency",
            trace=({"stage": "safety_gate", "status": "stopped", "attempt": 0},),
        )

    trace: list[dict[str, Any]] = []
    protected_patient_context = deepcopy(request.patient_context)
    protected_conversation = deepcopy(request.conversation)
    revision_instruction = None
    for attempt in range(max_revisions + 1):
        try:
            draft = patient_runner(
                learner_message=request.learner_message,
                conversation=deepcopy(protected_conversation),
                patient_context=deepcopy(protected_patient_context),
                revision_instruction=revision_instruction,
            )
        except Exception:
            trace.append({"stage": "patient_runner", "status": "failed", "attempt": attempt + 1})
            return _result(
                status="failed",
                reply=SAFE_FAILURE_REPLY,
                persistable=False,
                error_code="patient_runner_failed",
                trace=tuple(trace),
            )

        reply = _extract_reply(draft)
        trace.append({"stage": "patient_runner", "status": "completed", "attempt": attempt + 1})
        if not reply or _contains_hidden_value(reply, protected_patient_context):
            return _result(
                status="failed",
                reply=SAFE_FAILURE_REPLY,
                persistable=False,
                error_code="patient_reply_not_publishable",
                trace=tuple(trace),
            )

        try:
            audit = auditor(
                draft={"reply": reply},
                learner_message=request.learner_message,
                conversation=deepcopy(protected_conversation),
                patient_context=deepcopy(protected_patient_context),
            )
        except Exception:
            trace.append({"stage": "patient_audit", "status": "failed", "attempt": attempt + 1})
            return _result(
                status="failed",
                reply=SAFE_FAILURE_REPLY,
                persistable=False,
                error_code="patient_audit_failed",
                trace=tuple(trace),
            )

        decision = _audit_value(audit, "decision").strip().lower()
        trace_decision = decision if decision in _ALLOWED_AUDIT_DECISIONS else "invalid"
        trace.append({"stage": "patient_audit", "status": trace_decision, "attempt": attempt + 1})
        if decision == "pass":
            return _result(
                status="completed",
                reply=reply,
                persistable=True,
                error_code=None,
                trace=tuple(trace),
            )
        if decision == "revise" and attempt < max_revisions:
            revision_instruction = _audit_value(audit, "reason") or "请消除答案泄漏并保持患者角色。"
            continue
        if decision in _HUMAN_REVIEW_DECISIONS:
            return _result(
                status="needs_human_review",
                reply=SAFE_HUMAN_REVIEW_REPLY,
                persistable=False,
                error_code="patient_reply_needs_human_review",
                trace=tuple(trace),
            )
        return _result(
            status="failed",
            reply=SAFE_FAILURE_REPLY,
            persistable=False,
            error_code="patient_reply_not_publishable",
            trace=tuple(trace),
        )

    raise AssertionError("unreachable patient orchestration state")


def _result(
    *,
    status: str,
    reply: str,
    persistable: bool,
    error_code: str | None,
    trace: tuple[dict[str, Any], ...],
) -> CasePatientOrchestrationResult:
    return CasePatientOrchestrationResult(
        status=status,
        reply=reply,
        persistable=persistable,
        disclaimer=MEDICAL_TRAINING_DISCLAIMER,
        error_code=error_code,
        trace=trace,
    )


def _extract_reply(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping) and isinstance(value.get("reply"), str):
        return value["reply"].strip()
    if hasattr(value, "reply") and isinstance(value.reply, str):
        return value.reply.strip()
    return ""


def _audit_value(value: Any, field: str) -> str:
    raw = value.get(field) if isinstance(value, Mapping) else getattr(value, field, "")
    return raw if isinstance(raw, str) else ""


def _contains_hidden_value(reply: str, patient_context: Mapping[str, Any]) -> bool:
    return any(secret in reply for secret in _hidden_strings(patient_context) if len(secret) >= 2)


def _hidden_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ()
    hidden: list[str] = []
    for key, nested in value.items():
        if str(key).strip().lower() in _HIDDEN_CONTEXT_KEYS:
            hidden.extend(_scalar_strings(nested))
        elif isinstance(nested, Mapping):
            hidden.extend(_hidden_strings(nested))
    return tuple(hidden)


def _scalar_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        return tuple(item for nested in value.values() for item in _scalar_strings(nested))
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(item for nested in value for item in _scalar_strings(nested))
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _is_real_world_emergency(message: str) -> bool:
    normalized = message.strip().lower()
    return (
        any(marker in normalized for marker in _REAL_WORLD_MARKERS)
        and any(marker in normalized for marker in _EMERGENCY_MARKERS)
    )
