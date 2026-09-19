"""别名补齐只能补当前契约真正声明的字段。

背景（线上实测 r2d / r2e）：``_normalize_common_output`` 为
``knowledge_base_agent`` 配了一条 ``reason -> retrieval_reason`` 的兼容别名，
按角色无条件生效。组卷检索决策契约 ``KnowledgeSupplementDecisionModelOutput``
有一个必填字段正好叫 ``reason``，且没有 ``retrieval_reason``；于是模型每次给出
完全合法的四字段输出，程序都会自己再补一个 ``retrieval_reason``，被
``additionalProperties: false`` 判为 ``business_schema_invalid``（``field_path``
为 ``/``）。线上 21 次决策调用 100% 落到代码兜底文本，模型从未真正参与它负责的
决策，而诊断里 ``allowed`` 只列契约字段、不回填多余字段名，所以看上去像“模型多
写了字段”。

本文件锁定修复后的行为：别名只在契约声明了目标字段时才补。
"""
from __future__ import annotations

import inspect
import json

import pytest

from competition_app.llm import schemas as schemas_module
from competition_app.llm.openai_compatible import (
    _normalize_common_output,
    _validate_local_output_schema,
)
from competition_app.llm.schemas import (
    KnowledgeRetrievalPlanModelOutput,
    KnowledgeSupplementDecisionModelOutput,
)

# 模型在 r2e 里真实给出的输出：四个字段全部在契约内，格式完全合法。
LIVE_DECISION_OUTPUT = {
    "decision": "continue",
    "reason": (
        "当前检索轮次未获得任何正式题库候选（candidate_count=0），仅凭复习状态中的"
        "10个已解析知识点和空检索结果，无法完成本蓝图单元要求的10道简答题。"
    ),
    "supplemental_queries": [
        "伤寒论 太阳病提纲证 病机 简答题",
        "太阳中风 伤寒 温病 表证鉴别 简答题",
    ],
    "missing_requirements": ["正式题库中缺少太阳病篇总纲类简答题候选，当前候选数为0。"],
}

ALL_ALIAS_SOURCES = (
    "agents",
    "explanation",
    "content",
    "knowledge_query",
    "question_search",
    "findings",
    "reason",
)


def _strict_contracts() -> list[tuple[str, type]]:
    result = []
    for name, obj in vars(schemas_module).items():
        if not inspect.isclass(obj) or obj.__module__ != schemas_module.__name__:
            continue
        if not hasattr(obj, "model_json_schema"):
            continue
        try:
            schema = obj.model_json_schema()
        except Exception:  # noqa: BLE001 - 契约构造失败与本测试无关
            continue
        if isinstance(schema, dict) and schema.get("additionalProperties") is False:
            result.append((name, obj))
    return result


def test_live_decision_output_survives_normalization_and_validation():
    """线上那次失败必须被修好：合法输出不得被程序自己改坏。"""

    schema = KnowledgeSupplementDecisionModelOutput.model_json_schema()
    assert schema["additionalProperties"] is False
    assert "retrieval_reason" not in schema["properties"]

    normalized = _normalize_common_output(
        LIVE_DECISION_OUTPUT, "knowledge_base_agent", schema
    )

    assert "retrieval_reason" not in normalized
    assert set(normalized) == set(LIVE_DECISION_OUTPUT)
    # 修复前这里抛 ValueError（additionalProperties @ /），线上即 business_schema_invalid。
    assert _validate_local_output_schema(normalized, schema) is normalized
    parsed = KnowledgeSupplementDecisionModelOutput.model_validate(normalized)
    assert parsed.decision == "continue"
    assert len(parsed.supplemental_queries) == 2


def test_alias_still_fills_when_the_contract_declares_the_target():
    """兼容别名的原本用途必须保留：契约声明了目标字段就照补。"""

    schema = KnowledgeRetrievalPlanModelOutput.model_json_schema()
    assert "retrieval_reason" in schema["properties"]

    normalized = _normalize_common_output(
        {"reason": "检索依据", "kp_query": None}, "knowledge_base_agent", schema
    )

    assert normalized["retrieval_reason"] == "检索依据"
    assert normalized["reason"] == "检索依据"


def test_alias_target_is_skipped_when_the_contract_does_not_declare_it():
    """契约没有该字段时不得补，否则必然撞 additionalProperties。"""

    schema = {
        "type": "object",
        "properties": {"decision": {"type": "string"}},
        "required": ["decision"],
        "additionalProperties": False,
    }
    normalized = _normalize_common_output(
        {"decision": "enough", "reason": "已足够"}, "knowledge_base_agent", schema
    )

    assert "retrieval_reason" not in normalized


def test_loose_mode_without_a_schema_keeps_the_previous_behaviour():
    """无契约可判时不改变既有行为：宽松模式没有封闭性可违反。"""

    normalized = _normalize_common_output(
        {"reason": "检索依据"}, "knowledge_base_agent"
    )

    assert normalized["retrieval_reason"] == "检索依据"


@pytest.mark.parametrize("source", ALL_ALIAS_SOURCES)
def test_no_closed_contract_can_receive_an_undeclared_alias_field(source: str):
    """不变量：任何封闭契约都不可能被别名补出契约外字段。

    修复前 ``KnowledgeSupplementDecisionModelOutput`` 就违反这条不变量，
    但当时没有任何测试覆盖“程序自己加字段”这条路径。
    """

    offenders = []
    for name, model in _strict_contracts():
        schema = model.model_json_schema()
        payload = {source: "x"}
        normalized = _normalize_common_output(
            payload, "knowledge_base_agent", schema
        )
        # 只关心程序**新增**的字段；模型自己多写的字段不属于本函数职责。
        added = set(normalized) - set(payload)
        undeclared = added - set(schema["properties"])
        if undeclared:
            offenders.append((name, sorted(undeclared)))
    assert offenders == [], (source, offenders)


def test_normalization_never_reaches_the_model_as_written_fields():
    """别名补齐结果不得把模型文本字段名回填进提示词契约。"""

    schema = KnowledgeSupplementDecisionModelOutput.model_json_schema()
    text = json.dumps(schema, ensure_ascii=False)
    assert "retrieval_reason" not in text
    assert "selected_agents" not in text
