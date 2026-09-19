"""输出契约必须与校验一致地声明字段封闭性。

背景：业务契约基类 ``StrictModelOutput`` 声明 ``extra="forbid"``，校验器对任何
多余字段一律拒绝；但发给模型的契约文本（``_compact_output_contract``）只列字段
名，从不说明“不得增加其他字段”。模型看到的契约因此严格弱于被校验的契约。

2026-09-17 更正：本条说明**不是**组卷检索决策线上失败的原因。抓取模型原始输出
后确认，模型每次都给出完全合法的四字段 JSON；多出来的 ``retrieval_reason`` 是
``_normalize_common_output`` 的别名补齐自己加的（见
``test_output_alias_contract_closure.py``）。这条说明作为提示词完整性改进保留，
但它无法、也不曾修复那次失败。
"""
from __future__ import annotations

import json

from competition_app.llm.openai_compatible import _compact_output_contract
from competition_app.llm.schemas import (
    KnowledgeSupplementDecisionModelOutput,
    PaperGapGenerationModelOutput,
)

CLOSURE_NOTE = "只能包含以上字段，不得增加任何其他字段"


def test_closed_business_contract_states_that_extra_fields_are_forbidden():
    schema = KnowledgeSupplementDecisionModelOutput.model_json_schema()
    assert schema["additionalProperties"] is False

    for strict in (False, True):
        text = _compact_output_contract(schema, strict_json=strict)
        assert CLOSURE_NOTE in text, strict
        # 声明必须排在字段清单之后，模型才会把它当作对上述字段的收尾约束。
        assert text.index("missing_requirements") < text.index(CLOSURE_NOTE)


def test_every_closed_business_contract_carries_the_note():
    for model in (
        KnowledgeSupplementDecisionModelOutput,
        PaperGapGenerationModelOutput,
    ):
        text = _compact_output_contract(model.model_json_schema(), strict_json=False)
        assert CLOSURE_NOTE in text, model.__name__


def test_open_contract_is_not_given_a_false_closure_note():
    """schema 未声明 additionalProperties=false 时不得凭空添加该说明。"""

    open_schema = {
        "type": "object",
        "title": "OpenEnvelope",
        "properties": {
            "note": {"type": "string", "description": "说明"},
            "extra": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": ["note"],
    }
    text = _compact_output_contract(open_schema, strict_json=False)
    assert "note" in text
    assert CLOSURE_NOTE not in text


def test_nested_closed_object_is_annotated_at_its_own_level():
    """嵌套对象的封闭性要在它自己的缩进层级声明，不能只标根对象。"""

    schema = {
        "type": "object",
        "title": "Outer",
        "properties": {
            "decision": {"type": "string"},
            "detail": {
                "type": "object",
                "title": "Detail",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
        "required": ["decision"],
    }
    text = _compact_output_contract(schema, strict_json=False)
    assert text.count(CLOSURE_NOTE) == 1
    note_line = next(
        line for line in text.splitlines() if CLOSURE_NOTE in line
    )
    # 嵌套层缩进一格（两个空格），根对象没有声明封闭性因此不加说明。
    assert note_line == f"  （{CLOSURE_NOTE}）"


def test_union_branches_keep_their_own_closure_note():
    """互斥分支各自的封闭性说明不能丢，也不得重复成多份。"""

    branch = {
        "type": "object",
        "properties": {"reason": {"type": "string"}},
        "required": ["reason"],
        "additionalProperties": False,
    }
    schema = {
        "type": "object",
        "oneOf": [
            {**branch, "title": "BranchA"},
            {**branch, "title": "BranchB"},
        ],
        "discriminator": {"propertyName": "kind"},
    }
    text = _compact_output_contract(schema, strict_json=False)
    assert "所选分支的字段直接放在根对象" in text
    assert text.count(CLOSURE_NOTE) == 2


def test_note_reaches_the_provider_prompt_verbatim():
    """说明必须真正进入 system 提示词，而不只是留在中间结果里。"""

    from competition_app.llm.openai_compatible import OpenAICompatibleChatModel

    model = OpenAICompatibleChatModel.__new__(OpenAICompatibleChatModel)
    messages = OpenAICompatibleChatModel._build_messages(
        model,
        "knowledge_base_agent",
        {
            "task_instructions": "判断本单元候选题是否足够。",
            "permission_note": "只判断证据与候选是否足够。",
            "payload": {
                "phase": "paper_retrieval_decision",
                "output_schema": KnowledgeSupplementDecisionModelOutput.model_json_schema(),
            },
        },
        strict_json=False,
        business_json=True,
    )
    system_prompt = messages[0]["content"]
    assert CLOSURE_NOTE in system_prompt
    # 契约不得把模型写下的字段名回填进提示词。
    assert json.dumps(system_prompt, ensure_ascii=False).count("unit_id") == 0
