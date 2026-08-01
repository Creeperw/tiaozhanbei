from competition_app.services.conversation_history import (
    format_dialogue_history,
    sanitize_conversation_content,
    sanitize_conversation_messages,
)


def test_conversation_history_keeps_only_formal_user_assistant_content() -> None:
    trace = '<<EV:{"rawModelInput":"' + ("x" * 210_000) + '"}>>'
    messages = sanitize_conversation_messages([
        {"message_id": "M1", "role": "user", "content": "给我讲讲感冒"},
        {
            "message_id": "M2",
            "role": "assistant",
            "content": trace
            + "<think>内部推理</think>感冒可从风寒、风热等证型辨治。"
            + '<<REFS:[{"source":"教材"}]>>',
            "model_input": {"secret": "not history"},
        },
        {"message_id": "T1", "role": "tool", "content": "外部检索结果"},
    ])

    assert messages == [
        {"message_id": "M1", "role": "user", "content": "给我讲讲感冒"},
        {
            "message_id": "M2",
            "role": "assistant",
            "content": "感冒可从风寒、风热等证型辨治。",
        },
    ]
    assert format_dialogue_history(messages) == (
        "user：给我讲讲感冒\nassistant：感冒可从风寒、风热等证型辨治。"
    )


def test_conversation_history_uses_only_post_revision_answer() -> None:
    content = "旧回答<<ROLLBACK:审核未通过>><think>修订过程</think>新回答"
    assert sanitize_conversation_content(content) == "新回答"


def test_conversation_history_keeps_navigation_metadata_but_drops_trace_metadata() -> None:
    messages = sanitize_conversation_messages([
        {
            "message_id": "M1",
            "role": "assistant",
            "content": "试卷已经生成。",
            "actions": [{"label": "开始答题", "destination": "workshop.paper"}],
            "execution_trace": {"raw_model_input": "不要持久化"},
            "external_evidence": ["教材片段"],
        }
    ])

    assert messages == [{
        "message_id": "M1",
        "role": "assistant",
        "content": "试卷已经生成。",
        "actions": [{"label": "开始答题", "destination": "workshop.paper"}],
    }]
