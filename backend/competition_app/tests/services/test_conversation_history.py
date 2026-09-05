from competition_app.services.conversation_history import (
    format_dialogue_history,
    parse_compressed_dialogue_summary,
    sanitize_compressed_dialogue_summary,
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


def test_compressed_summary_keeps_only_dialogue_lines() -> None:
    summary = (
        "<think>内部推理</think>"
        "user：用户想复习感冒辨证。<think>过程</think>\n"
        "assistant：已整理风寒风热鉴别要点。"
        '<<REFS:[{"source":"教材"}]>>\n'
        "【检索证据】教材第3章片段\n"
        "system：内部注记\n"
    )
    assert sanitize_compressed_dialogue_summary(summary) == (
        "user：用户想复习感冒辨证。\n"
        "assistant：已整理风寒风热鉴别要点。"
    )


def test_compressed_summary_prose_is_never_disguised_as_dialogue() -> None:
    # A legacy/prose digest (no user：/assistant： lines) must not be wrapped
    # into a fake formal answer; it is dropped entirely.
    assert sanitize_compressed_dialogue_summary("用户偏好晚间学习。") == ""
    assert sanitize_compressed_dialogue_summary("") == ""


def test_parse_compressed_dialogue_summary_returns_pure_user_assistant_messages() -> None:
    summary = (
        "user：用户想复习感冒辨证。\n"
        "assistant：已整理风寒风热鉴别要点。\n"
        "【检索证据】教材片段\n"
        "assistant：补充了方剂配伍。\n"
    )
    assert parse_compressed_dialogue_summary(summary) == [
        {"role": "user", "content": "用户想复习感冒辨证。"},
        {"role": "assistant", "content": "已整理风寒风热鉴别要点。"},
        {"role": "assistant", "content": "补充了方剂配伍。"},
    ]
    assert parse_compressed_dialogue_summary("无对话行摘要") == []
    assert parse_compressed_dialogue_summary("") == []
