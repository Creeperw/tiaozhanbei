"""字段级校验证据与修复说明：只用服务端合同元数据，绝不回填模型文本。"""

import json

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from competition_app.llm.validation_diagnostics import (
    _RULE_DESCRIPTIONS,
    _RULES,
    describe_validation_issues,
    validation_issues,
)


SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "stem": {"type": "string", "maxLength": 10},
                    "analysis": {"type": "string", "maxLength": 20},
                },
                "required": ["stem"],
            },
        },
    },
    "required": ["items"],
}


class _StrictItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stem: str = Field(min_length=1, max_length=10)


def issues_for(value: object) -> list[dict[str, object]]:
    with pytest.raises(JsonSchemaValidationError) as caught:
        Draft202012Validator(SCHEMA).validate(value)
    return validation_issues(caught.value, SCHEMA)


def test_array_position_is_kept_so_the_model_can_locate_the_item() -> None:
    """数组下标是 schema 结构位置，不是模型文本，必须保留。

    抹成 ``*`` 会让修复指令退化成“某一项有问题”，模型无法定位到具体哪一项，
    于是第二次尝试只能从零重写。
    """

    assert issues_for({"items": [{"stem": "x" * 99}]}) == [
        {"field_path": "/items/0/stem", "rule": "maxLength", "limit": 10}
    ]


def test_unknown_property_names_are_still_masked() -> None:
    """非 schema 名仍然打码，模型写的内容不得进入路径。

    取而代之的是服务端声明的允许字段清单：多余字段的名字是模型文本，不能
    回填；但只报“对象里有契约不允许的字段”又让模型无从下手，第二次尝试必然
    原样重犯。
    """

    with pytest.raises(ValidationError) as caught:
        _StrictItem.model_validate({"stem": "ok", "private_key": "secret"})
    issues = validation_issues(caught.value, _StrictItem.model_json_schema())
    assert issues == [
        {"field_path": "/*", "rule": "additionalProperties", "allowed": ["stem"]}
    ]
    assert "private_key" not in json.dumps(issues)


def test_additional_properties_reports_the_schema_owned_allow_list() -> None:
    """``additionalProperties`` 必须给出可执行的允许字段清单。

    线上补充检索决策模型反复在顶层多写一个字段，schema 校验是全或无，整条
    输出被拒；修复反馈只有“输出对象：包含契约不允许的字段”，模型不知道要删
    哪个字段，第二次尝试重犯同一个违规（DB 里 15 条记录全是
    request_attempt_count=2，全部止步于此）。
    """

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "decision": {"type": "string"},
            "reason": {"type": "string"},
        },
        "required": ["decision", "reason"],
    }
    with pytest.raises(JsonSchemaValidationError) as caught:
        Draft202012Validator(schema).validate(
            {"decision": "continue", "reason": "候选不足", "unit_id": "UNIT_03"}
        )
    issues = validation_issues(caught.value, schema)
    assert issues == [
        {
            "field_path": "/",
            "rule": "additionalProperties",
            "allowed": ["decision", "reason"],
        }
    ]
    assert describe_validation_issues(issues).endswith(
        "- 输出对象：包含契约不允许的字段（只允许：decision、reason）"
    )
    assert "unit_id" not in json.dumps(issues)


def test_description_names_the_field_and_the_schema_threshold() -> None:
    assert describe_validation_issues(
        issues_for({"items": [{"stem": "x" * 99}]})
    ) == (
        "上一次返回的 JSON 有 1 处不符合契约，请只修正下列字段，其余内容保持原样：\n"
        "- items[0].stem：内容长度超过上限（10）"
    )


def test_required_rule_has_no_threshold() -> None:
    issues = issues_for({"items": [{}]})
    assert issues == [{"field_path": "/items/0/stem", "rule": "required"}]
    line = describe_validation_issues(issues).split("\n")[1]
    assert line == "- items[0].stem：缺少必填字段"


def test_rendering_keeps_positions_and_masks_unknown_slots() -> None:
    issues = [
        {"field_path": "/items/2/analysis", "rule": "maxLength", "limit": 20},
        {"field_path": "/items/*/stem", "rule": "maxLength", "limit": 10},
        {"field_path": "/*", "rule": "additionalProperties"},
    ]
    lines = describe_validation_issues(issues).split("\n")
    assert lines[1].startswith("- items[2].analysis：")
    assert lines[2].startswith("- items[*].stem：")
    assert lines[3].startswith("- 输出对象（未知字段）：")


def test_only_the_first_few_violations_are_described() -> None:
    """说明太长会稀释真正需要改的那个字段。"""

    issues = [
        {"field_path": f"/items/{index}/stem", "rule": "maxLength", "limit": 10}
        for index in range(10)
    ]
    lines = describe_validation_issues(issues).split("\n")
    assert len(lines) == 7
    assert "有 6 处不符合契约" in lines[0]


def test_unsafe_entries_never_reach_the_description() -> None:
    """路径里的模型文本被清洗挡掉，说明退化为空而不是泄露。"""

    issues = [{"field_path": "/items/0/私钥", "rule": "maxLength", "limit": 10}]
    assert describe_validation_issues(issues) == ""


def test_no_issues_means_no_description() -> None:
    assert describe_validation_issues([]) == ""
    assert describe_validation_issues(None) == ""
    assert describe_validation_issues([{"field_path": "/x", "rule": "unknown"}]) == ""


def test_every_known_rule_has_a_description() -> None:
    """规则词表与说明表必须同步，否则那一条违规会被静默丢掉。"""

    assert set(_RULE_DESCRIPTIONS) == set(_RULES)


# 与线上审核契约同形：``list[X] | None`` 生成的是 anyOf（数组分支 | null），
# 没有 discriminator。曾经只报 ``anyOf``——“不符合契约允许的任一分支”，模型
# 无法据此修正，第二轮只能靠猜，两轮重试全部失败后系统静默降级成 unresolved。
UNION_SCHEMA = {
    "type": "object",
    "properties": {
        "structured_findings": {
            "anyOf": [
                {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "issue_type": {"enum": ["paper_item_invalid"]},
                            "message": {"type": "string"},
                            "location_keys": {"type": "array", "minItems": 1},
                        },
                        "required": ["issue_type", "message", "location_keys"],
                        "additionalProperties": False,
                    },
                },
                {"type": "null"},
            ]
        },
    },
    "required": ["structured_findings"],
}


def union_issues_for(value: object) -> list[dict[str, object]]:
    with pytest.raises(JsonSchemaValidationError) as caught:
        Draft202012Validator(UNION_SCHEMA).validate(value)
    return validation_issues(caught.value, UNION_SCHEMA)


def test_union_without_discriminator_reports_the_failing_item() -> None:
    """无 discriminator 的 anyOf 必须下钻到失败分支，报出具体条目。"""

    issues = union_issues_for(
        {
            "structured_findings": [
                {
                    "issue_type": "paper_item_invalid",
                    "message": "第4题是选择题句式但没有选项。",
                    "location_keys": ["paper:question:Q1"],
                    "severity": "high",
                }
            ]
        }
    )
    assert issues == [
        {
            "field_path": "/structured_findings/0",
            "rule": "additionalProperties",
            "allowed": ["issue_type", "message", "location_keys"],
        }
    ]


def test_union_without_discriminator_reports_missing_item_field() -> None:
    issues = union_issues_for(
        {"structured_findings": [{"issue_type": "paper_item_invalid", "message": "缺定位"}]}
    )
    assert issues == [
        {"field_path": "/structured_findings/0/location_keys", "rule": "required"}
    ]


def test_union_without_discriminator_reports_wrong_container_type() -> None:
    issues = union_issues_for({"structured_findings": {"0": {}}})
    assert issues == [{"field_path": "/structured_findings", "rule": "type"}]


def test_union_without_discriminator_reports_enum_violation() -> None:
    issues = union_issues_for(
        {
            "structured_findings": [
                {
                    "issue_type": "typo_issue",
                    "message": "问题",
                    "location_keys": ["paper:question:Q1"],
                }
            ]
        }
    )
    assert issues == [
        {"field_path": "/structured_findings/0/issue_type", "rule": "enum"}
    ]


def test_union_diagnosis_is_actionable_rather_than_opaque() -> None:
    """修复说明必须落到具体字段，而不是“不符合任一分支”。"""

    issues = union_issues_for(
        {"structured_findings": [{"issue_type": "paper_item_invalid", "message": "缺定位"}]}
    )
    described = describe_validation_issues(issues)
    assert "- structured_findings[0].location_keys：缺少必填字段" in described
    assert "不符合契约允许的任一分支" not in described


def test_audit_contract_union_reports_a_locatable_field() -> None:
    """锁住线上真实形状：审核契约的 structured_findings 必须可定位。"""

    from competition_app.llm.schemas import AuditModelOutput

    schema = AuditModelOutput.model_json_schema()
    value = {
        "decision": "revise",
        "findings": ["第4题无法作答。"],
        "audit_report": "审核报告。",
        "structured_findings": [
            {
                "issue_type": "paper_item_invalid",
                "message": "第4题是选择题句式但没有选项。",
                "blocking": True,
                "location_keys": ["paper:question:Q1"],
                "severity": "high",
            }
        ],
    }
    with pytest.raises(JsonSchemaValidationError) as caught:
        Draft202012Validator(schema).validate(value)
    issues = validation_issues(caught.value, schema)
    assert issues[0]["field_path"].startswith("/structured_findings/0")
    assert issues[0]["rule"] != "anyOf"
    assert issues[0].get("allowed") == [
        "issue_type",
        "message",
        "blocking",
        "location_keys",
    ]
