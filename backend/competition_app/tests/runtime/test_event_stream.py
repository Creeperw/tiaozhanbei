from competition_app.runtime.event_stream import (
    RecordingEventSink,
    bind_event_sink,
    emit_runtime_event,
    project_public_business_text,
    public_runtime_event,
    public_workflow_result,
    reset_event_sink,
)


def test_business_stream_projection_hides_compiler_anchors_and_internal_ids() -> None:
    public = project_public_business_text(
        "<!-- UNIT_ID: UNIT_01 -->\n"
        "所属单元：《中医学基础》阴阳学说\n"
        "候选题 possible_new__abc123，原创题 GENERATED_secret123。"
    )

    assert "所属单元：《中医学基础》阴阳学说" in public
    assert "UNIT_ID" not in public
    assert "UNIT_01" not in public
    assert "possible_new__abc123" not in public
    assert "GENERATED_secret123" not in public


def test_public_model_event_keeps_lifecycle_but_hides_prompt_payloads() -> None:
    event = public_runtime_event(
        {
            "event": "model_input",
            "agent": "planner_agent",
            "call_id": "MODEL_CALL_1",
            "output_kind": "business",
            "step_id": "planner",
            "ts": 1_786_745_600_123,
            "raw_input": {
                "task_instructions": "system prompt must remain private",
                "payload": {"user_request": "hello"},
            },
        }
    )

    assert event == {
        "event": "model_input",
        "agent": "planner_agent",
        "call_id": "MODEL_CALL_1",
        "output_kind": "business",
        "step_id": "planner",
        "ts": 1_786_745_600_123,
    }


def test_recording_sink_persists_only_safe_model_lifecycle_metadata() -> None:
    forwarded = []
    sink = RecordingEventSink(forwarded.append, frozenset({"model_input"}))
    sink({
        "event": "model_input",
        "agent": "planner_agent",
        "call_id": "MODEL_CALL_1",
        "step_id": "planner",
        "raw_input": {"system_prompt": "private", "api_key": "secret"},
    })

    assert sink.drain() == [{
        "event": "model_input",
        "agent": "planner_agent",
        "call_id": "MODEL_CALL_1",
        "step_id": "planner",
    }]
    assert forwarded[0]["raw_input"]["system_prompt"] == "private"


def test_provider_reasoning_is_safely_projected_persisted_and_forwarded() -> None:
    reasoning_event = {
        "event": "reasoning_delta",
        "agent": "planner_agent",
        "call_id": "MODEL_CALL_1",
        "step_id": "planner",
        "delta": "先判断用户当前意图。EVID_SECRET_1",
    }

    assert public_runtime_event(reasoning_event) == {
        "event": "agent_reasoning_delta",
        "agent": "planner_agent",
        "call_id": "MODEL_CALL_1",
        "step_id": "planner",
        "delta": "先判断用户当前意图。",
    }

    forwarded = []
    sink = RecordingEventSink(forwarded.append)
    sink(reasoning_event)

    assert sink.drain() == [public_runtime_event(reasoning_event)]
    assert forwarded == [reasoning_event]


def test_emitted_runtime_event_has_server_epoch_millisecond_timestamp() -> None:
    events = []
    token = bind_event_sink(events.append)
    try:
        emit_runtime_event("step_started", step_id="knowledge", agent="knowledge_base_agent")
    finally:
        reset_event_sink(token)

    assert len(events) == 1
    assert events[0]["event"] == "step_started"
    assert isinstance(events[0]["ts"], int)
    assert events[0]["ts"] >= 1_700_000_000_000


def test_public_transport_and_failure_events_do_not_echo_provider_content() -> None:
    transport = public_runtime_event(
        {
            "event": "model_transport",
            "agent": "expert_agent",
            "request_payload": {"messages": [{"role": "system", "content": "secret"}]},
            "response_text": "internal compiler output",
        }
    )
    failure = public_runtime_event(
        {
            "event": "model_failed",
            "agent": "expert_agent",
            "error_type": "RuntimeError",
            "error_message": "request echoed: secret prompt",
            "transport_error": {"body": "secret prompt"},
        }
    )

    assert transport == {"event": "model_transport", "agent": "expert_agent"}
    assert failure == {
        "event": "model_failed",
        "agent": "expert_agent",
        "error_type": "RuntimeError",
    }


def test_public_system_output_exposes_complete_validated_stage_payload() -> None:
    event = public_runtime_event(
        {
            "event": "system_output",
            "agent": "planner_agent",
            "step_id": "planner",
            "output": {
                "artifact_id": "ART_PRIVATE",
                "payload": {
                    "task_type": "knowledge_explanation",
                    "selected_agents": ["knowledge_base_agent", "expert_agent", "audit_agent"],
                    "routing_reason": "用户需要教材证据讲解和练习。",
                    "casual_response": None,
                },
            },
        }
    )

    assert event["event"] == "system_output"
    assert event["agent"] == "planner_agent"
    assert event["step_id"] == "planner"
    assert '"task_type": "knowledge_explanation"' in event["public_output"]
    assert '"routing_reason": "用户需要教材证据讲解和练习。"' in event["public_output"]
    assert '"selected_agents": [' in event["public_output"]
    assert "ART_PRIVATE" not in str(event)


def test_public_knowledge_output_keeps_complete_validated_contract() -> None:
    event = public_runtime_event(
        {
            "event": "system_output",
            "agent": "knowledge_base_agent",
            "step_id": "knowledge",
            "output": {
                "payload": {
                    "evidence_items": [{"evidence_id": "EVID_SECRET_1"}],
                    "retrieval_summary": "依据 EVID_SECRET_1 整理。<<REFS:[{\"evidence_id\":\"EVID_SECRET_1\"}]>>",
                },
            },
        }
    )

    assert '"evidence_id": "EVID_SECRET_1"' in event["public_output"]
    assert '"retrieval_summary"' in event["public_output"]
    assert "<<REFS" in event["public_output"]


def test_public_knowledge_output_includes_complete_retrieval_summary() -> None:
    event = public_runtime_event(
        {
            "event": "system_output",
            "agent": "knowledge_base_agent",
            "step_id": "knowledge",
            "output": {
                "payload": {
                    "evidence_items": [{"evidence_id": "E_1"}],
                    "retrieval_summary": "教材原文：这是不应出现在执行记录中的召回内容。",
                },
            },
        }
    )

    assert "教材原文：这是不应出现在执行记录中的召回内容。" in event["public_output"]


def test_public_output_accepts_orchestrator_complete_output_summary() -> None:
    event = public_runtime_event(
        {
            "event": "system_output",
            "agent": "memory_agent",
            "step_id": "memory",
            "output_summary": {
                "artifact_type": "memory_context",
                "producer": "memory_agent",
                "payload": {
                    "learner_context": {
                        "confirmed_preferences": {"daily_minutes": 75},
                        "relevant_memories": [{"kind": "learning_goal"}],
                    },
                    "memory_candidates": [{"kind": "preference"}],
                    "private_note": "完整业务说明",
                    "api_key": "must not be exposed",
                },
            },
        }
    )

    assert '"confirmed_preferences"' in event["public_output"]
    assert '"memory_candidates"' in event["public_output"]
    assert '"private_note": "完整业务说明"' in event["public_output"]
    assert '"api_key": "[REDACTED]"' in event["public_output"]
    assert "must not be exposed" not in str(event)


def test_public_workflow_result_excludes_internal_execution_artifacts() -> None:
    result = public_workflow_result(
        {
            "status": "success",
            "execution_id": "EXE_1",
            "task_type": "knowledge_explanation",
            "direct_response": "面向学习者的自然语言",
            "ui_actions": [],
            "agent_outputs": [{"payload": {"candidate_id": "INTERNAL_1"}}],
            "model_trace": [{"raw_input": {"task_instructions": "secret"}}],
            "snapshot_path": "/tmp/internal.json",
            "writeback_intents": [{"operation": "internal"}],
            "coordination": {"repair_trace": [{"owner_step_ids": ["expert"]}]},
        }
    )

    assert result == {
        "status": "success",
        "execution_id": "EXE_1",
        "task_type": "knowledge_explanation",
        "direct_response": "面向学习者的自然语言",
        "ui_actions": [],
    }


def test_public_workflow_result_hides_human_review_details() -> None:
    result = public_workflow_result(
        {
            "status": "waiting_human_review",
            "execution_id": "EXE_REVIEW",
            "task_type": "paper_generation",
            "review": {
                "audit_report": "内部审核报告",
                "findings": ["内部审核问题"],
            },
        }
    )

    assert result == {
        "status": "waiting_human_review",
        "execution_id": "EXE_REVIEW",
        "task_type": "paper_generation",
    }


def test_public_audit_output_includes_complete_formal_review_result() -> None:
    output = public_runtime_event(
        {
            "event": "system_output",
            "agent": "audit_agent",
            "output": {
                "payload": {
                    "decision": "needs_human_review",
                    "audit_report": "内部审核报告：不得展示",
                    "findings": ["内部问题：不得展示"],
                }
            },
        }
    )

    assert '"decision": "needs_human_review"' in output["public_output"]
    assert '"audit_report": "内部审核报告：不得展示"' in output["public_output"]
    assert '"findings": [' in output["public_output"]
    assert "内部问题：不得展示" in output["public_output"]


def test_public_runtime_events_hide_exception_echoes_and_internal_repair_ids() -> None:
    retry = public_runtime_event(
        {
            "event": "step_retrying",
            "step_id": "expert",
            "agent": "expert_agent",
            "attempt": 1,
            "error_type": "RuntimeError",
            "error_message": "echoed system prompt and user profile",
        }
    )
    repair = public_runtime_event(
        {
            "event": "repair_planned",
            "trigger_step_id": "audit",
            "issue_ids": ["INTERNAL_RULE_1"],
            "location_labels": ["第二段"],
            "rerun_step_ids": ["expert"],
            "status": "planned",
        }
    )

    assert "error_message" not in retry
    assert retry["error_type"] == "RuntimeError"
    assert "issue_ids" not in repair
    assert repair["location_labels"] == ["第二段"]


def test_public_graph_compilation_only_exposes_execution_topology() -> None:
    event = public_runtime_event(
        {
            "event": "graph_compiled",
            "nodes": [
                {
                    "step_id": "expert",
                    "agent": "expert_agent",
                    "depends_on": ["knowledge"],
                    "action": "internal_action_name",
                    "audit_subject": {"internal": "contract"},
                }
            ],
        }
    )

    assert event == {
        "event": "graph_compiled",
        "nodes": [
            {
                "step_id": "expert",
                "agent": "expert_agent",
                "depends_on": ["knowledge"],
            }
        ],
        "control_edges": [],
    }


def test_public_reasoning_events_stream_bounded_sanitized_deltas() -> None:
    started = public_runtime_event(
        {
            "event": "reasoning_started",
            "agent": "expert_agent",
            "call_id": "MODEL_CALL_2",
            "step_id": "expert",
        }
    )
    delta = public_runtime_event(
        {
            "event": "reasoning_delta",
            "agent": "expert_agent",
            "call_id": "MODEL_CALL_2",
            "step_id": "expert",
            "delta": (
                "先核对教材 EVID_SECRET_1 与 <<REFS:[{\"evidence_id\":"
                "\"EVID_SECRET_1\"}]>>，再组织讲解。\n"
                "<!-- UNIT_ID: UNIT_01 -->"
            ),
        }
    )
    committed = public_runtime_event(
        {
            "event": "reasoning_committed",
            "agent": "expert_agent",
            "call_id": "MODEL_CALL_2",
            "step_id": "expert",
        }
    )

    assert started["event"] == "agent_reasoning_started"
    assert started["agent"] == "expert_agent"
    assert started["call_id"] == "MODEL_CALL_2"
    assert started["step_id"] == "expert"
    assert delta["event"] == "agent_reasoning_delta"
    assert delta["agent"] == "expert_agent"
    assert delta["call_id"] == "MODEL_CALL_2"
    assert "EVID_SECRET_1" not in delta["delta"]
    assert "<<REFS" not in delta["delta"]
    assert "UNIT_01" not in delta["delta"]
    assert "UNIT_ID" not in delta["delta"]
    assert "先核对教材" in delta["delta"]
    assert committed["event"] == "agent_reasoning_committed"
    assert committed["agent"] == "expert_agent"
    assert committed["call_id"] == "MODEL_CALL_2"
    assert committed["step_id"] == "expert"


def test_public_reasoning_strips_echoed_system_prompt_json() -> None:
    delta = public_runtime_event(
        {
            "event": "reasoning_delta",
            "agent": "expert_agent",
            "call_id": "MODEL_CALL_3",
            "step_id": "expert",
            "delta": (
                '{"role": "system", "content": "你是中医药教学系统中的'
                'paper_blueprint_compiler。只完成当前角色任务，只使用输入中'
                '直接相关的事实和证据；不确定内容写“待确认”。"}'
                " 好的，现在开始按蓝图逐字提取输出合同。"
            ),
        }
    )

    assert "paper_blueprint_compiler" not in delta["delta"]
    assert "system" not in delta["delta"]
    assert "待确认" not in delta["delta"]
    assert "按蓝图逐字提取输出合同" in delta["delta"]


def test_public_reasoning_strips_echoed_message_array() -> None:
    delta = public_runtime_event(
        {
            "event": "reasoning_delta",
            "agent": "expert_agent",
            "call_id": "MODEL_CALL_4",
            "step_id": "expert",
            "delta": (
                '[{"role": "system", "content": "secret compiler contract"},'
                ' {"role": "user", "content": "请生成试卷"}]'
                " 继续：先确认题型偏好。"
            ),
        }
    )

    assert "secret compiler contract" not in delta["delta"]
    assert "请生成试卷" not in delta["delta"]
    assert "先确认题型偏好" in delta["delta"]


def test_public_reasoning_keeps_natural_language_with_inline_json_reference() -> None:
    delta = public_runtime_event(
        {
            "event": "reasoning_delta",
            "agent": "expert_agent",
            "call_id": "MODEL_CALL_5",
            "step_id": "expert",
            "delta": (
                "检索返回 {\"evidence_count\": 3} 条证据，"
                "我据此组织讲解。"
            ),
        }
    )

    assert "evidence_count" not in delta["delta"]
    assert "我据此组织讲解" in delta["delta"]


def test_public_reasoning_truncates_prompt_echoed_as_plain_text() -> None:
    delta = public_runtime_event(
        {
            "event": "reasoning_delta",
            "agent": "knowledge_base_agent",
            "call_id": "MODEL_CALL_6",
            "step_id": "knowledge",
            "delta": (
                "我先比较检索结果的覆盖范围。"
                "你是中医药教学系统中的knowledge_base_agent。"
                "只完成当前角色任务，只使用输入中直接相关的事实和证据。"
                "现在继续检查题型覆盖。"
            ),
        }
    )

    assert delta["delta"] == "我先比较检索结果的覆盖范围。现在继续检查题型覆盖。"


def test_public_reasoning_does_not_release_incomplete_prompt_json_prefix() -> None:
    delta = public_runtime_event(
        {
            "event": "reasoning_delta",
            "agent": "paper_blueprint_agent",
            "call_id": "MODEL_CALL_7",
            "step_id": "paper_blueprint",
            "delta": '{"role":"system","content":"# 输出契约',
        }
    )

    assert delta["delta"] == ""
