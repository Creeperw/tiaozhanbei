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
});
