from competition_app.llm.openai_compatible import OpenAICompatibleChatModel, _format_user_data


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
    assert "【外部信息】" in rendered
    assert "教材切片：感冒的病因病机" in rendered
    assert "【用户画像】" in rendered
    assert rendered.index("【用户画像】") < rendered.index("【压缩历史对话】")
    assert rendered.index("【压缩历史对话】") < rendered.index("【近期历史对话】")
    assert rendered.index("【近期历史对话】") < rendered.index("【外部信息】")
    history_section = rendered.split("【近期历史对话】", 1)[1].split(
        "【外部信息】", 1
    )[0]
    assert "教材切片" not in history_section


def test_provider_prompt_uses_only_the_fixed_four_context_headings() -> None:
    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret",
        model="test-model",
    )
    messages = client._build_messages(
        "expert_agent",
        {
            "payload": {
                "shared_context": {
                    "user_profile": {"学习背景": "零基础"},
                    "compressed_conversation": "user：此前学习过基础理论。",
                    "recent_conversation": [
                        {"role": "user", "content": "请详细讲解当前知识点"}
                    ],
                    "external_information": [],
                },
                "topic": "阴阳学说",
            }
        },
        strict_json=False,
    )
    prompt = messages[1]["content"]
    headings = [
        "【用户画像】",
        "【压缩历史对话】",
        "【近期历史对话】",
        "【外部信息】",
    ]
    assert all(prompt.count(item) == 1 for item in headings)
    assert [prompt.index(item) for item in headings] == sorted(
        prompt.index(item) for item in headings
    )
    assert "请详细讲解当前知识点" in prompt
