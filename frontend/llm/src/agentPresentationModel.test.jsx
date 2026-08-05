import { describe, expect, it } from 'vitest';

import {
  AGENT_ROLES,
  agentStatusLabel,
  buildAgentPresentation,
  resolveAgentRole,
  sanitizeAgentLog,
} from './agentPresentationModel';

describe('six-agent presentation model', () => {
  it('maps runtime agents and deterministic services to the formal six-agent boundary', () => {
    expect(resolveAgentRole('planner_agent')).toBe('planner');
    expect(resolveAgentRole('memory_agent')).toBe('memory');
    expect(resolveAgentRole('diagnosis_agent')).toBe('diagnosis');
    expect(resolveAgentRole('default_route_resolver')).toBe('diagnosis');
    expect(resolveAgentRole('learning_plan_service')).toBe('diagnosis');
    expect(resolveAgentRole('knowledge_base_agent')).toBe('knowledge');
    expect(resolveAgentRole('expert_agent')).toBe('expert');
    expect(resolveAgentRole('paper_blueprint_agent')).toBe('expert');
    expect(resolveAgentRole('paper_assembly_agent')).toBe('expert');
    expect(resolveAgentRole('knowledge_explanation_agent')).toBe('expert');
    expect(resolveAgentRole('audit_agent')).toBe('audit');
    expect(resolveAgentRole('review_scheduler')).toBe('system');
  });

  it('always returns six stable user-facing seats and marks unused seats clearly', () => {
    const roles = buildAgentPresentation([
      {
        id: 'planner',
        agent: 'planner_agent',
        status: 'done',
        startTime: 10,
        endTime: 30,
        logs: ['planner_agent开始处理', '执行路径已确定'],
        tools: [],
        intents: [],
      },
      {
        id: 'knowledge',
        agent: 'knowledge_base_agent',
        status: 'running',
        startTime: 31,
        logs: ['正在检索教材证据'],
        tools: [],
        intents: [],
      },
    ]);

    expect(roles).toHaveLength(6);
    expect(roles.map((item) => item.key)).toEqual(AGENT_ROLES.map((item) => item.key));
    expect(roles.find((item) => item.key === 'planner')).toMatchObject({
      label: '任务规划',
      status: 'done',
      statusLabel: '已完成',
    });
    expect(roles.find((item) => item.key === 'knowledge')).toMatchObject({
      label: '知识库管理',
      status: 'running',
      statusLabel: '执行中',
    });
    expect(roles.find((item) => item.key === 'memory')).toMatchObject({
      status: 'skipped',
      statusLabel: '本次无需参与',
    });
  });

  it('deduplicates user summaries and keeps blank repeated tool calls out of details', () => {
    const roles = buildAgentPresentation([
      {
        id: 'knowledge',
        agent: 'knowledge_base_agent',
        status: 'done',
        startTime: 10,
        endTime: 40,
        logs: ['knowledge_base_agent开始处理', '发起工具调用', '发起工具调用', '教材检索完成'],
        tools: [
          { id: 'blank-1', name: 'web_search', args: { query: '' }, status: 'done', startTime: 12 },
          { id: 'blank-2', name: 'web_search', args: { query: '' }, status: 'done', startTime: 13 },
          { id: 'useful', name: 'get_kp_with_content', args: { query: '四君子汤' }, status: 'done', startTime: 14 },
        ],
        intents: [],
      },
    ]);
    const knowledge = roles.find((item) => item.key === 'knowledge');

    expect(knowledge.details).toEqual(['开始查找教材、题目与相关资料。', '教材检索完成']);
    expect(knowledge.tools).toHaveLength(2);
    expect(knowledge.tools.map((item) => item.name)).toEqual(['web_search', 'get_kp_with_content']);
    expect(knowledge.summary).toBe('教材检索完成');
  });

  it('uses natural Chinese labels for runtime states and logs', () => {
    expect(agentStatusLabel('running')).toBe('执行中');
    expect(agentStatusLabel('error')).toBe('执行失败');
    expect(agentStatusLabel('waiting_human_review')).toBe('等待人工复核');
    expect(sanitizeAgentLog('audit_agent处理完成')).toBe('内容质量检查完成。');
    expect(sanitizeAgentLog('')).toBe('');
  });

  it('aggregates model input/output/transport calls onto the owning seat', () => {
    const roles = buildAgentPresentation([
      {
        id: 'expert',
        agent: 'expert_agent',
        status: 'done',
        startTime: 40,
        endTime: 80,
        logs: ['expert_agent开始处理', '内容生成完成'],
        tools: [],
        intents: [],
        modelCalls: [
          {
            id: 'MODEL_CALL_1-input-1',
            callId: 'MODEL_CALL_1',
            kind: 'input',
            agent: 'expert_agent',
            input: { task_type: 'knowledge_explanation', topic: '气血' },
            ts: 41,
          },
          {
            id: 'MODEL_CALL_1-output-1',
            callId: 'MODEL_CALL_1',
            kind: 'output',
            agent: 'expert_agent',
            output: { content: '气血是人体基本物质' },
            ts: 79,
          },
        ],
      },
    ]);
    const expert = roles.find((item) => item.key === 'expert');

    expect(expert.modelCalls).toHaveLength(2);
    expect(expert.modelCalls[0].kind).toBe('input');
    expect(expert.modelCalls[1].kind).toBe('output');
  });

  it('drops model calls without meaningful payloads', () => {
    const roles = buildAgentPresentation([
      {
        id: 'planner',
        agent: 'planner_agent',
        status: 'done',
        startTime: 10,
        endTime: 20,
        logs: [],
        tools: [],
        intents: [],
        modelCalls: [
          { id: 'empty-1', kind: 'input', agent: 'planner_agent', input: null, ts: 11 },
          { id: 'empty-2', kind: 'output', agent: 'planner_agent', output: '', ts: 12 },
        ],
      },
    ]);
    const planner = roles.find((item) => item.key === 'planner');

    expect(planner.modelCalls).toHaveLength(0);
  });
});
