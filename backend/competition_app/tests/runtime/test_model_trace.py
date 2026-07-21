from competition_app.runtime.model_trace import ModelTraceRecorder


def test_model_trace_records_secret_safe_raw_input_and_output() -> None:
    recorder = ModelTraceRecorder()
    index = recorder.begin("diagnosis_agent", {"api_key": "secret", "topic": "感冒"})
    recorder.succeed(index, {"summary": "学习建议", "authorization": "Bearer secret"})

    item = recorder.items[0]
    assert item.sequence == 1
    assert item.raw_input["api_key"] == "[REDACTED]"
    assert item.raw_output["authorization"] == "[REDACTED]"


def test_model_trace_reset_removes_previous_request_data() -> None:
    recorder = ModelTraceRecorder()
    recorder.begin("planner_agent", {"topic": "旧请求"})

    recorder.reset()

    assert recorder.items == []


def test_model_trace_records_transport_text_separately_from_parsed_json() -> None:
    recorder = ModelTraceRecorder()
    index = recorder.begin("expert_agent", {"topic": "四君子汤"})
    recorder.record_transport(
        index,
        request_payload={
            "url": "https://example.test/v1/chat/completions",
            "body": {"messages": [{"role": "user", "content": "真实输入"}]},
        },
        response_text='  {"learning_tip":"真实原文"}\n',
        reasoning_text="先核对证据",
    )
    recorder.succeed(index, {"learning_tip": "真实原文"})

    item = recorder.items[0]
    assert item.transport_input["body"]["messages"][0]["content"] == "真实输入"
    assert item.raw_output_text == '  {"learning_tip":"真实原文"}\n'
    assert item.reasoning_text == "先核对证据"
    assert item.raw_output == {"learning_tip": "真实原文"}