from competition_app.runtime.terminal_trace import TerminalTrace


def test_terminal_trace_prints_sanitized_step_boundaries(capsys) -> None:
    trace = TerminalTrace(enabled=True, level="full")
    trace.model_input("diagnosis_agent", {"api_key": "secret", "topic": "四君子汤"})
    trace.model_output("diagnosis_agent", {"summary": "初始复习", "risk_flags": []})
    trace.validation("diagnosis_agent", valid=True, detail="LearningAnalysisModelOutput")
    trace.system_output("diagnosis_agent", {"kp_id": "005390", "stage_id": "T0"})

    output = capsys.readouterr().out
    assert "[diagnosis_agent] 模型输入" in output
    assert "[diagnosis_agent] 模型原始输出" in output
    assert "[diagnosis_agent] 协议校验: 通过" in output
    assert "[diagnosis_agent] 系统产物" in output
    assert "secret" not in output
    assert "005390" in output


def test_terminal_trace_levels_limit_visible_boundaries(capsys) -> None:
    summary_trace = TerminalTrace(enabled=True, level="summary")
    summary_trace.model_input("expert_agent", {"topic": "四君子汤"})
    summary_trace.model_output("expert_agent", {"learning_tip": "主动回忆"})
    summary_trace.validation("expert_agent", valid=True, detail="ExpertModelOutput")
    summary_trace.system_output("expert_agent", {"resource_draft_id": "DRAFT_1"})
    summary_output = capsys.readouterr().out

    assert "协议校验" in summary_output
    assert "模型输入" not in summary_output
    assert "模型原始输出" not in summary_output
    assert "系统产物" not in summary_output

    model_trace = TerminalTrace(enabled=True, level="model")
    model_trace.model_input("expert_agent", {"topic": "四君子汤"})
    model_trace.model_output("expert_agent", {"learning_tip": "主动回忆"})
    model_trace.system_output("expert_agent", {"resource_draft_id": "DRAFT_1"})
    model_output = capsys.readouterr().out

    assert "模型原始输出" in model_output
    assert "模型输入" not in model_output
    assert "系统产物" not in model_output


def test_full_terminal_trace_prints_safe_tool_summary_only(capsys) -> None:
    trace = TerminalTrace(enabled=True, level="full")

    trace.tool_event(
        "knowledge_base_agent",
        {
            "tool_name": "search_question_candidates",
            "candidate_count": 2,
            "channels": ["bridge", "vector"],
        },
    )

    output = capsys.readouterr().out
    assert "search_question_candidates" in output
    assert "candidate_count" in output
    assert "reference_answer" not in output
    assert "题目内容" not in output