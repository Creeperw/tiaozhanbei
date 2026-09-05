from __future__ import annotations

"""Independent learner-visible prose judge for isolated D1 evaluation.

The judge receives only the question, two anonymous source passages, and one
arm's final learner-visible answer.  Treatment assignment,
rule identifiers, evidence identifiers, system bindings, Audit output, repair
metadata, case groups, claims, and gold labels are deliberately excluded.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from competition_app.llm.base import ChatModel


JUDGE_VERSION = "d1-learner-visible-semantic-judge-v2"


class D1SemanticJudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["judged"]
    acceptable: bool
    request_fulfilled: bool
    material_a_handled_correctly: bool
    material_b_handled_correctly: bool
    relationship_handled_correctly: bool
    no_unsupported_resolution: bool
    rationale_code: Literal[
        "acceptable",
        "request_not_fulfilled",
        "material_a_mishandled",
        "material_b_mishandled",
        "relationship_mishandled",
        "unsupported_resolution",
    ]


_OUTPUT_SCHEMA = D1SemanticJudgeVerdict.model_json_schema()
_ALLOWED_RATIONALE_BY_FAILED_FIELD = (
    ("request_fulfilled", "request_not_fulfilled"),
    ("material_a_handled_correctly", "material_a_mishandled"),
    ("material_b_handled_correctly", "material_b_mishandled"),
    ("relationship_handled_correctly", "relationship_mishandled"),
    ("no_unsupported_resolution", "unsupported_resolution"),
)


class D1LearnerVisibleSemanticJudge:
    """Judge each arm absolutely; never compare A with B in one model call."""

    def __init__(self, chat_model: ChatModel) -> None:
        self.chat_model = chat_model

    async def judge(
        self,
        *,
        question: str,
        material_a: str,
        material_b: str,
        answer: str,
    ) -> dict[str, Any]:
        def validate_result(raw: dict[str, Any]) -> dict[str, Any]:
            verdict = D1SemanticJudgeVerdict.model_validate(raw)
            self._validate_consistency(verdict)
            return verdict.model_dump(mode="json")

        try:
            raw = await self.chat_model.complete_json(
                "learner_visible_semantic_judge",
                {
                    "strict_json": True,
                    "task_instructions": (
                        "你是独立的学习者可见正文盲审员。只根据问题、材料A、材料B和"
                        "候选正文做绝对评价。先逐条理解两份材料，再判断正文是否完成问题、"
                        "是否准确处理每份材料及二者关系、是否凭空裁决。不得猜测实验分组、"
                        "系统规则、证据绑定、审核或修复过程。相互矛盾的材料，正文必须明确"
                        "指出不能同时成立且不得无依据消解；相互补充的材料不得误报成冲突；"
                        "普通学习支持材料应按问题正常使用。只有五项布尔条件全部为真时"
                        "acceptable 才能为 true。rationale_code 必须对应第一项失败条件。"
                    ),
                    "permission_note": (
                        "只评价当前匿名正文；不得请求或推断模型身份、A/B分组、案例类别、"
                        "预设主张、金标准、内部编号、系统绑定、Audit结论、repair信息或"
                        "closure状态。"
                    ),
                    "_result_validator": validate_result,
                    "payload": {
                        "question": str(question),
                        "material_a": str(material_a),
                        "material_b": str(material_b),
                        "candidate_answer": str(answer),
                        "output_schema": _OUTPUT_SCHEMA,
                    },
                },
            )
            verdict = D1SemanticJudgeVerdict.model_validate(raw)
            self._validate_consistency(verdict)
        except (RuntimeError, TypeError, ValueError) as exc:
            return self.unavailable_verdict(exc)
        return {
            "schema_version": "d1-semantic-verdict-1.0",
            "judge_version": JUDGE_VERSION,
            **verdict.model_dump(mode="json"),
        }

    @staticmethod
    def _validate_consistency(verdict: D1SemanticJudgeVerdict) -> None:
        checks = [
            verdict.request_fulfilled,
            verdict.material_a_handled_correctly,
            verdict.material_b_handled_correctly,
            verdict.relationship_handled_correctly,
            verdict.no_unsupported_resolution,
        ]
        expected_acceptable = all(checks)
        if verdict.acceptable is not expected_acceptable:
            raise ValueError("semantic judge acceptable flag is inconsistent")
        expected_code = "acceptable"
        for field_name, code in _ALLOWED_RATIONALE_BY_FAILED_FIELD:
            if not bool(getattr(verdict, field_name)):
                expected_code = code
                break
        if verdict.rationale_code != expected_code:
            raise ValueError("semantic judge rationale_code is inconsistent")

    @staticmethod
    def unavailable_verdict(exc: Exception) -> dict[str, Any]:
        error_type = type(exc).__name__[:128]
        # No provider text is persisted.  Unavailability is explicitly unknown,
        # never converted into a passing semantic judgement.
        return {
            "schema_version": "d1-semantic-verdict-1.0",
            "judge_version": JUDGE_VERSION,
            "status": "unavailable",
            "acceptable": None,
            "request_fulfilled": None,
            "material_a_handled_correctly": None,
            "material_b_handled_correctly": None,
            "relationship_handled_correctly": None,
            "no_unsupported_resolution": None,
            "rationale_code": "judge_unavailable",
            "error_type": error_type,
        }
