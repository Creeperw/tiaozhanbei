from competition_app.llm.openai_compatible import _format_user_data


def test_model_prompt_separates_dialogue_external_information_and_profile() -> None:
    rendered = _format_user_data({
        "user_request": "请讲解感冒",
        "shared_context": {
            "recent_conversation": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好，请问想学习什么？"},
            ],
            "compressed_conversation": "user：此前学习过中医学基础。",
            "external_information": ["教材切片：感冒的病因病机"],
            "user_profile": {"learning_background": "零基础"},
        },
    })

    assert "【近期历史对话】\nuser：你好\nassistant：你好，请问想学习什么？" in rendered
    assert "【压缩历史对话】\nuser：此前学习过中医学基础。" in rendered
    assert "【外部信息】\n1. 教材切片：感冒的病因病机" in rendered
    assert "【用户画像】" in rendered
    history_section = rendered.split("【近期历史对话】", 1)[1].split(
        "【压缩历史对话】", 1
    )[0]
    assert "教材切片" not in history_section
