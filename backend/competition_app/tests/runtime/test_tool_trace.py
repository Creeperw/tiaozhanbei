from competition_app.runtime.trace import ToolTrace


def test_tool_trace_contains_only_safe_summaries() -> None:
    trace = ToolTrace(
        tool_name="search_question_candidates",
        agent="knowledge_base_agent",
        status="success",
        duration_ms=12,
        safe_input_summary={"resolved_kp_count": 1},
        safe_output_summary={"candidate_count": 2, "channels": ["bridge"]},
    )

    assert "reference_answer" not in trace.model_dump_json()