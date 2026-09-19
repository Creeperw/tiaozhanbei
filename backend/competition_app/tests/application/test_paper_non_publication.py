"""发布门槛只卡「审核有没有形成语义结论」，问题由卷面说明承担。

旧的发布闸门要求审核结论必须是 pass：非 pass 时既不发布，也把问题清单当作
「没出卷的原因」讲给学习者。候选不足是常态，组卷会在缺口上按降级策略补题
（掺入其他知识点的题、现场生成补充题），审核因此长期带着非阻塞问题，一律
扣下会让学习者拿不到任何东西。

现在的分工：

* ``_paper_non_publication_reason`` 只在两种情况阻止发布——本卷没有内容，
  或审核没有形成语义结论（审核器输出不符合协议，系统拿不到任何内容判断）；
* ``_paper_audit_notice`` 负责把审核指出的问题写进卷面，试卷照发、问题照说。
"""

import pytest

from competition_app.application.personalized_review_card import (
    PersonalizedReviewCardUseCase,
)
from competition_app.contracts.resource import AuditResult


def _audit(
    *,
    decision: str,
    structured_findings: list[dict] | None = None,
    findings: list[str] | None = None,
    semantic_verdict_available: bool = True,
) -> AuditResult:
    from competition_app.contracts.local_repair import RepairIssue

    return AuditResult(
        audit_result_id="AUDIT_1",
        decision=decision,
        audit_report="审核报告正文。",
        findings=list(findings or []),
        structured_findings=[
            RepairIssue(**item) for item in (structured_findings or [])
        ],
        semantic_verdict_available=semantic_verdict_available,
    )


def reason_for(**kwargs) -> str:
    return PersonalizedReviewCardUseCase._paper_non_publication_reason(
        audit=_audit(**kwargs), empty_reason=""
    )


def notice_for(paper=None, **kwargs) -> str:
    return PersonalizedReviewCardUseCase._paper_audit_notice(_audit(**kwargs), paper=paper)


def test_approved_paper_has_no_non_publication_reason() -> None:
    assert reason_for(decision="pass") == ""


def test_empty_state_keeps_its_existing_message() -> None:
    reason = PersonalizedReviewCardUseCase._paper_non_publication_reason(
        audit=_audit(decision="revise"), empty_reason="暂未找到与当前学习范围匹配的题目"
    )
    assert "暂未找到与当前学习范围匹配的题目" in reason
    assert "本次没有生成试卷内容" in reason


def test_paper_is_published_when_audit_formed_a_semantic_verdict() -> None:
    """审核有语义结论时，即使结论是 revise 也要发布。"""

    assert (
        reason_for(
            decision="revise",
            structured_findings=[
                {
                    "issue_id": "PAPER_NATIVE_ISSUE_1",
                    "issue_type": "paper_blueprint_mismatch",
                    "message": "整卷核心范围与本单元不符。",
                    "blocking": True,
                }
            ],
        )
        == ""
    )


def test_missing_semantic_verdict_is_described_as_an_incomplete_audit() -> None:
    """审核器失效与“内容不合格”对学习者意义不同，必须分开说明。"""

    reason = reason_for(
        decision="revise",
        findings=["组卷审核模型输出格式不符合约定，系统改用确定性硬门禁判定。"],
        semantic_verdict_available=False,
    )

    assert "内容审核没能完成" in reason
    assert "本次没有发布试卷" in reason
    assert "组卷审核模型输出格式不符合约定" not in reason


def test_approved_paper_has_no_audit_notice() -> None:
    assert notice_for(decision="pass") == ""


def test_blocking_findings_are_listed_in_the_audit_notice() -> None:
    notice = notice_for(
        decision="revise",
        structured_findings=[
            {
                "issue_id": "PAPER_NATIVE_ISSUE_1",
                "issue_type": "paper_blueprint_mismatch",
                "message": "整卷核心范围与本单元不符。",
                "blocking": True,
            },
            {
                "issue_id": "PAPER_NATIVE_ISSUE_2",
                "issue_type": "paper_item_invalid",
                "message": "第4题是选择题句式但没有选项。",
                "blocking": True,
            },
        ],
    )

    assert "内容审核对本次试卷提出了以下问题" in notice
    assert "试卷已按当前可用题目发布" in notice
    assert "整卷核心范围与本单元不符。" in notice
    assert "第4题是选择题句式但没有选项。" in notice


def test_non_blocking_findings_are_not_presented_as_audit_problems() -> None:
    """非阻断建议不是审核提出的问题，不能列进卷面说明。"""

    notice = notice_for(
        decision="revise",
        structured_findings=[
            {
                "issue_id": "PAPER_NATIVE_ISSUE_1",
                "issue_type": "content_quality",
                "message": "解析可以更完整。",
                "blocking": False,
            }
        ],
    )

    assert "解析可以更完整。" not in notice
    assert notice == ""


def test_plain_findings_are_used_when_no_structured_finding_is_blocking() -> None:
    """只有整卷级文字结论时，也要把结论带给学习者。"""

    notice = notice_for(
        decision="revise",
        findings=["整卷核心范围与本单元不符，非定点替换可修复。"],
    )

    assert "整卷核心范围与本单元不符，非定点替换可修复。" in notice


def test_long_finding_lists_are_bounded() -> None:
    notice = notice_for(
        decision="revise",
        structured_findings=[
            {
                "issue_id": f"PAPER_NATIVE_ISSUE_{index}",
                "issue_type": "paper_item_invalid",
                "message": f"第{index}题无法作答。",
                "blocking": True,
            }
            for index in range(1, 10)
        ],
    )

    assert "第1题无法作答。" in notice
    assert "第5题无法作答。" in notice
    assert "第6题无法作答。" not in notice
    assert "另有 4 条问题未在此列出" in notice


@pytest.mark.parametrize("decision", ["revise", "reject"])
def test_audit_notice_is_emitted_for_every_non_pass_verdict(decision: str) -> None:
    notice = notice_for(decision=decision, findings=["题目来源标注不完整。"])
    assert "题目来源标注不完整。" in notice


def _paper_with_internal_ids():
    """一份带系统内部标识符的试卷载荷（ID 长度均超过清理阈值）。"""

    from types import SimpleNamespace

    return SimpleNamespace(
        items=[
            SimpleNamespace(
                sequence=1,
                unit_id="UNIT_BLUEPRINT_01",
                paper_item_id="PAPER_ITEM_7f3a91",
                question_version_id="QVERSION_9c21",
                question=SimpleNamespace(
                    question_id="generated_临床案例问答__6180aa5bb742"
                ),
            )
        ]
    )


def test_audit_notice_names_the_problem_by_question_number_not_internal_id() -> None:
    """位置用系统位置标签（题号）标出，正文里的内部 ID 不带到卷面上。

    线上实际表现：卷面说明印着「第1题（generated_临床案例问答__6180aa5bb742）
    题干缺失…」。这个 ID 是系统给审核模型的位置键，学习者在卷面上没有办法把它
    对应回任何一道题，只能当噪声读过去。
    """

    notice = notice_for(
        paper=_paper_with_internal_ids(),
        decision="revise",
        structured_findings=[
            {
                "issue_id": "PAPER_NATIVE_ISSUE_1",
                "issue_type": "paper_item_invalid",
                "message": (
                    "第1题（generated_临床案例问答__6180aa5bb742）题干缺失“以下方证”"
                    "的备选列表，考生无法作答。"
                ),
                "blocking": True,
                "locations": [
                    {
                        "location_key": "paper:question:generated_临床案例问答__6180aa5bb742",
                        "subject_type": "exam_paper",
                        "location_type": "question",
                        "display_label": "第1题",
                    }
                ],
            }
        ],
    )

    assert "generated_临床案例问答__6180aa5bb742" not in notice
    assert "· 第1题" in notice
    assert "题干缺失“以下方证”的备选列表，考生无法作答。" in notice


def test_audit_notice_keeps_whole_paper_findings_on_one_line() -> None:
    """只有整卷级位置时不加位置行：「当前试卷全文」不提供额外信息。"""

    notice = notice_for(
        paper=_paper_with_internal_ids(),
        decision="revise",
        structured_findings=[
            {
                "issue_id": "PAPER_NATIVE_ISSUE_1",
                "issue_type": "paper_blueprint_mismatch",
                "message": "整卷核心范围与本单元不符。",
                "blocking": True,
                "locations": [
                    {
                        "location_key": "paper:whole",
                        "subject_type": "exam_paper",
                        "location_type": "whole_subject",
                        "display_label": "当前试卷全文",
                    }
                ],
            }
        ],
    )

    assert "· 整卷核心范围与本单元不符。" in notice
    assert "当前试卷全文" not in notice


def test_audit_notice_leaves_text_alone_when_no_paper_payload_is_available() -> None:
    """拿不到试卷载荷时不做清理：没有系统自己的 ID 清单，就不能猜哪个串是 ID。"""

    notice = notice_for(
        decision="revise",
        structured_findings=[
            {
                "issue_id": "PAPER_NATIVE_ISSUE_1",
                "issue_type": "paper_item_invalid",
                "message": "题目 generated_临床案例问答__6180aa5bb742 无法作答。",
                "blocking": True,
            }
        ],
    )

    assert "generated_临床案例问答__6180aa5bb742" in notice
