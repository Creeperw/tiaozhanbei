import { describe, expect, it } from 'vitest';

import { buildTraceFromEvents, reduceLangGraphEvent } from './useLangGraphStore';

const emptyState = {
  nodes: [],
  currentActiveNodeId: null,
  isRollingBack: false,
  finalAnswerId: null,
  finalAnswerContent: '',
  references: [],
  showPreliminaryAnswer: true,
};

describe('LangGraph six-agent trace state', () => {
  it('keeps authoritative runtime agents as distinct execution nodes', () => {
    const nodes = buildTraceFromEvents([
      { type: 'planning_start', agent: 'planner_agent', stepId: 'planner', text: '开始规划', ts: 10 },
      { type: 'planning_done', agent: 'planner_agent', stepId: 'planner', text: '规划完成', ts: 20 },
      { type: 'context_start', agent: 'memory_agent', stepId: 'memory', text: '读取记忆', ts: 21 },
      { type: 'context_done', agent: 'memory_agent', stepId: 'memory', text: '记忆完成', ts: 25 },
      { type: 'context_start', agent: 'diagnosis_agent', stepId: 'diagnosis', text: '分析学情', ts: 26 },
      { type: 'execution_start', agent: 'knowledge_base_agent', stepId: 'knowledge', text: '检索知识', ts: 30 },
      { type: 'execution_start', agent: 'expert_agent', stepId: 'expert', text: '生成内容', ts: 40 },
      { type: 'feedback_start', agent: 'audit_agent', stepId: 'audit', text: '审核内容', ts: 50 },
    ]);

    expect(nodes.map((node) => node.agent)).toEqual([
      'planner_agent',
      'memory_agent',
      'diagnosis_agent',
      'knowledge_base_agent',
      'expert_agent',
      'audit_agent',
    ]);
  });

  it('does not append duplicate blank tool calls', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'execution_start', agent: 'knowledge_base_agent', stepId: 'knowledge', text: '检索', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'tool_start', agent: 'knowledge_base_agent', name: 'web_search', query: '', ts: 11,
    });
    state = reduceLangGraphEvent(state, {
      type: 'tool_start', agent: 'knowledge_base_agent', name: 'web_search', query: '', ts: 12,
    });

    expect(state.nodes[0].tools).toHaveLength(1);
    expect(state.nodes[0].agent).toBe('knowledge_base_agent');
  });

  it('keeps the audit node waiting for human review without a rollback', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'feedback_start', agent: 'audit_agent', stepId: 'audit', text: '审核内容', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'human_review_waiting', text: '内容正在等待人工复核', ts: 20,
    });

    expect(state.nodes.find((node) => node.id === 'audit')?.status).toBe('waiting_human_review');
    expect(state.isRollingBack).toBe(false);
  });

  it('keeps every compiled agent visible before its step starts', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'planning_start', agent: 'planner_agent', stepId: 'planner', text: '开始规划', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'planning_done',
      agent: 'planner_agent',
      stepId: 'planner',
      text: '执行路径已确定',
      ts: 20,
      plannedNodes: [
        { step_id: 'paper_assembly', agent: 'paper_assembly_agent' },
        { step_id: 'audit', agent: 'audit_agent' },
      ],
    });

    expect(state.nodes.find((node) => node.agent === 'audit_agent')?.status).toBe('pending');
    state = reduceLangGraphEvent(state, { type: 'workflow_done', ts: 30 });
    expect(state.nodes.find((node) => node.agent === 'audit_agent')?.status).toBe('skipped');
  });

  it('removes planned-only agents when an interrupted run has already ended', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'planning_done',
      agent: 'planner_agent',
      stepId: 'planner',
      text: '执行路径已确定',
      ts: 20,
      plannedNodes: [
        { step_id: 'knowledge', agent: 'knowledge_base_agent' },
        { step_id: 'audit', agent: 'audit_agent' },
      ],
    });
    state = reduceLangGraphEvent(state, {
      type: 'execution_start', agent: 'knowledge_base_agent', stepId: 'knowledge', text: '开始检索', ts: 21,
    });
    state = reduceLangGraphEvent(state, {
      type: 'execution_done', agent: 'knowledge_base_agent', stepId: 'knowledge', text: '检索完成', ts: 25,
    });
    state = reduceLangGraphEvent(state, { type: 'workflow_interrupted', ts: 30 });

    expect(state.nodes.find((node) => node.agent === 'knowledge_base_agent')?.status).toBe('done');
    expect(state.nodes.find((node) => node.agent === 'audit_agent')?.status).toBe('skipped');
  });

  it('attributes a step failure to the authoritative agent instead of audit', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'execution_start', agent: 'knowledge_base_agent', stepId: 'knowledge', text: '开始检索', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'step_error', agent: 'knowledge_base_agent', stepId: 'knowledge', text: '检索失败', ts: 20,
    });

    expect(state.nodes.find((node) => node.id === 'knowledge')?.status).toBe('error');
    expect(state.nodes.some((node) => node.agent === 'audit_agent')).toBe(false);
  });

  it('does not create a fake expert node when a workflow waits for user input', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'context_start', agent: 'diagnosis_agent', stepId: 'diagnosis', text: '检查信息', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'context_done', agent: 'diagnosis_agent', stepId: 'diagnosis', text: '需要补充信息', ts: 20,
    });
    state = reduceLangGraphEvent(state, { type: 'workflow_interrupted', ts: 30 });

    expect(state.nodes.map((node) => node.agent)).toEqual(['diagnosis_agent']);
    expect(state.currentActiveNodeId).toBeNull();
  });

  it('does not reopen a terminal node for late model or tool details', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'execution_start', agent: 'knowledge_base_agent', stepId: 'knowledge', text: '开始检索', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'execution_done', agent: 'knowledge_base_agent', stepId: 'knowledge', text: '检索完成', ts: 20,
    });
    state = reduceLangGraphEvent(state, {
      type: 'model_call', kind: 'output', agent: 'knowledge_base_agent', stepId: 'knowledge', output: { ok: true }, ts: 21,
    });
    state = reduceLangGraphEvent(state, {
      type: 'tool_start', agent: 'knowledge_base_agent', stepId: 'knowledge', name: 'web_search', query: '四君子汤', ts: 22,
    });

    expect(state.nodes.find((node) => node.id === 'knowledge')?.status).toBe('done');
  });

  it('accumulates streamed public working notes separately from formal output', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'planning_start', agent: 'planner_agent', stepId: 'planner', text: '开始规划', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'agent_output_stream', kind: 'started', phase: 'working', agent: 'planner_agent', stepId: 'planner', ts: 11,
    });
    state = reduceLangGraphEvent(state, {
      type: 'agent_output_stream', kind: 'delta', phase: 'working', text: '已识别为', agent: 'planner_agent', stepId: 'planner', ts: 12,
    });
    state = reduceLangGraphEvent(state, {
      type: 'agent_output_stream', kind: 'delta', phase: 'working', text: '知识讲解。', agent: 'planner_agent', stepId: 'planner', ts: 13,
    });
    state = reduceLangGraphEvent(state, {
      type: 'agent_output_stream', kind: 'committed', phase: 'working', agent: 'planner_agent', stepId: 'planner', ts: 14,
    });

    const planner = state.nodes.find((node) => node.id === 'planner');
    expect(planner.workingOutput).toBe('已识别为知识讲解。');
    expect(planner.workingOutputStreaming).toBe(false);
    expect(planner.formalOutput).toBe('');
    expect(planner.publicOutput).toBe('已识别为知识讲解。');
    expect(planner.publicOutputStreaming).toBe(false);
  });

  it('preserves the live working draft when the validated formal output arrives', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'execution_start', agent: 'expert_agent', stepId: 'expert', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'agent_output_stream', kind: 'started', phase: 'working',
      agent: 'expert_agent', stepId: 'expert', ts: 11,
    });
    state = reduceLangGraphEvent(state, {
      type: 'agent_output_stream', kind: 'delta', phase: 'working', text: '模型实时草稿',
      agent: 'expert_agent', stepId: 'expert', ts: 12,
    });
    state = reduceLangGraphEvent(state, {
      type: 'agent_output_stream', kind: 'replaced', phase: 'formal', text: '校验后的完整内容',
      agent: 'expert_agent', stepId: 'expert', ts: 13,
    });

    const expert = state.nodes.find((node) => node.id === 'expert');
    expect(expert.workingOutput).toBe('模型实时草稿');
    expect(expert.formalOutput).toBe('校验后的完整内容');
    expect(expert.formalOutputReady).toBe(true);
    expect(expert.publicOutput).toBe('校验后的完整内容');
    expect(expert.publicOutputStreaming).toBe(false);
  });

  it('clears a recoverable audit error after forced re-audit passes', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'feedback_start', agent: 'audit_agent', stepId: 'audit', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'step_error', agent: 'audit_agent', stepId: 'audit', text: '需要局部返修', ts: 11,
    });
    state = reduceLangGraphEvent(state, {
      type: 'feedback_regenerate', text: '正在返修', ts: 12,
    });
    state = reduceLangGraphEvent(state, {
      type: 'feedback_done', approved: true, text: '复审通过', ts: 13,
    });

    const auditNodes = state.nodes.filter((node) => (
      node.agent === 'audit_agent' || node.name === 'Feedback'
    ));
    expect(auditNodes.length).toBeGreaterThan(0);
    expect(auditNodes.every((node) => node.status === 'done')).toBe(true);
    expect(auditNodes.every((node) => !node.error)).toBe(true);
  });

  it('closes stale upstream activity when audit starts', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'execution_start', agent: 'knowledge_base_agent', stepId: 'knowledge', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'reasoning_stream', kind: 'started',
      agent: 'knowledge_base_agent', stepId: 'knowledge', ts: 11,
    });
    state = reduceLangGraphEvent(state, {
      type: 'tool_start', agent: 'knowledge_base_agent', stepId: 'knowledge',
      name: 'web_search', query: '五行学说', ts: 12,
    });
    state = reduceLangGraphEvent(state, {
      type: 'feedback_start', agent: 'audit_agent', stepId: 'audit', text: '开始审核', ts: 20,
    });

    const knowledge = state.nodes.find((node) => node.agent === 'knowledge_base_agent');
    const audit = state.nodes.find((node) => node.agent === 'audit_agent');
    expect(knowledge.status).toBe('done');
    expect(knowledge.reasoningStreaming).toBe(false);
    expect(knowledge.tools.every((tool) => tool.status === 'done')).toBe(true);
    expect(audit.status).toBe('running');
    expect(state.currentActiveNodeId).toBe(audit.id);
  });

  it('accumulates provider reasoning events on the matching agent', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'execution_start', agent: 'knowledge_base_agent', stepId: 'knowledge', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'reasoning_stream', kind: 'started', text: '',
      agent: 'knowledge_base_agent', stepId: 'knowledge', ts: 11,
    });
    state = reduceLangGraphEvent(state, {
      type: 'reasoning_stream', kind: 'delta', text: '先核对教材证据，',
      agent: 'knowledge_base_agent', stepId: 'knowledge', ts: 12,
    });
    state = reduceLangGraphEvent(state, {
      type: 'reasoning_stream', kind: 'delta', text: '再判断概念边界。',
      agent: 'knowledge_base_agent', stepId: 'knowledge', ts: 13,
    });

    let knowledge = state.nodes.find((node) => node.agent === 'knowledge_base_agent');
    expect(knowledge.reasoning).toBe('先核对教材证据，再判断概念边界。');
    expect(knowledge.reasoningStreaming).toBe(true);

    state = reduceLangGraphEvent(state, {
      type: 'reasoning_stream', kind: 'committed', text: '',
      agent: 'knowledge_base_agent', stepId: 'knowledge', ts: 14,
    });
    knowledge = state.nodes.find((node) => node.agent === 'knowledge_base_agent');
    expect(knowledge.reasoningStreaming).toBe(false);
  });

  it('attaches model input/output calls to the matching agent node', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'execution_start', agent: 'expert_agent', stepId: 'expert', text: '生成内容', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'model_call',
      kind: 'input',
      agent: 'expert_agent',
      stepId: 'expert',
      callId: 'MODEL_CALL_1',
      input: { task_type: 'knowledge_explanation', topic: '气血' },
      ts: 11,
    });
    state = reduceLangGraphEvent(state, {
      type: 'model_call',
      kind: 'output',
      agent: 'expert_agent',
      stepId: 'expert',
      callId: 'MODEL_CALL_1',
      output: { content: '气血是人体基本物质' },
      ts: 12,
    });

    const expertNode = state.nodes.find((node) => node.agent === 'expert_agent');
    expect(expertNode.modelCalls).toHaveLength(2);
    expect(expertNode.modelCalls[0].kind).toBe('input');
    expect(expertNode.modelCalls[0].input.task_type).toBe('knowledge_explanation');
    expect(expertNode.modelCalls[1].kind).toBe('output');
    expect(expertNode.modelCalls[1].output.content).toBe('气血是人体基本物质');
  });

  it('keeps an orphan model detail pending until an authoritative step starts', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'model_call',
      kind: 'output',
      agent: 'planner_agent',
      callId: 'MODEL_CALL_0',
      output: { task_type: 'knowledge_explanation' },
      ts: 5,
    });

    expect(state.nodes).toHaveLength(1);
    expect(state.nodes[0].agent).toBe('planner_agent');
    expect(state.nodes[0].status).toBe('pending');
    expect(state.nodes[0].modelCalls).toHaveLength(1);
  });

  it('records observed external-search calls as completed tool rows', () => {
    let state = reduceLangGraphEvent(emptyState, {
      type: 'knowledge_start', agent: 'knowledge_base_agent', stepId: 'knowledge', ts: 10,
    });
    state = reduceLangGraphEvent(state, {
      type: 'tool_event', agent: 'knowledge_base_agent', stepId: 'knowledge',
      name: 'web_search', status: 'done',
      args: { provider: 'exa', resourceType: 'reference', resultCount: 3 },
      text: 'EXA · 参考资料 · 3 条结果', ts: 11,
    });

    const knowledgeNode = state.nodes.find((node) => node.agent === 'knowledge_base_agent');
    expect(knowledgeNode.tools).toEqual([
      expect.objectContaining({
        name: 'web_search', status: 'done', resultSnippet: 'EXA · 参考资料 · 3 条结果',
      }),
    ]);
  });
});
