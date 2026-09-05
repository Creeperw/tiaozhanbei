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

  it('returns one seat per executed node in real start order', () => {
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

    expect(roles).toHaveLength(2);
    expect(roles.map((item) => item.key)).toEqual(['planner', 'knowledge']);
    expect(roles[0]).toMatchObject({
      label: '任务规划',
      status: 'done',
      statusLabel: '已完成',
    });
    expect(roles[1]).toMatchObject({
      label: '知识库管理',
      status: 'running',
      statusLabel: '执行中',
    });
  });

  it('keeps repeated invocations of the same agent as separate seats', () => {
    const roles = buildAgentPresentation([
      {
        id: 'expert-1',
        agent: 'expert_agent',
        status: 'done',
        startTime: 10,
        endTime: 20,
        logs: ['第一轮生成'],
        tools: [],
        intents: [],
      },
      {
        id: 'expert-2',
        agent: 'expert_agent',
        status: 'done',
        startTime: 30,
        endTime: 40,
        logs: ['第二轮生成'],
        tools: [],
        intents: [],
      },
    ]);

    expect(roles).toHaveLength(2);
    expect(roles.map((item) => item.nodes[0].id)).toEqual(['expert-1', 'expert-2']);
    expect(roles[0].summary).toBe('第一轮生成');
    expect(roles[1].summary).toBe('第二轮生成');
  });

  it('keeps the shared audit step labeled as the audit role outside paper generation', () => {
    const roles = buildAgentPresentation([
      {
        id: 'knowledge',
        stepId: 'knowledge',
        agent: 'knowledge_base_agent',
        status: 'done',
        startTime: 10,
        endTime: 20,
        logs: [],
        tools: [],
        intents: [],
      },
      {
        id: 'expert',
        stepId: 'expert',
        agent: 'knowledge_explanation_agent',
        status: 'done',
        startTime: 21,
        endTime: 30,
        logs: [],
        tools: [],
        intents: [],
      },
      {
        id: 'audit',
        stepId: 'audit',
        agent: 'audit_agent',
        status: 'running',
        startTime: 31,
        logs: [],
        tools: [],
        intents: [],
      },
    ]);

    const audit = roles.find((item) => item.key === 'audit');
    expect(audit.label).toBe('审核裁判');
    expect(audit.description).toBe('检查事实、质量与发布条件。');
  });

  it('uses paper-specific stage labels only for an unambiguous paper workflow', () => {
    const roles = buildAgentPresentation([
      {
        id: 'paper_blueprint',
        stepId: 'paper_blueprint',
        agent: 'paper_blueprint_agent',
        status: 'done',
        startTime: 10,
        endTime: 20,
        logs: [],
        tools: [],
        intents: [],
      },
      {
        id: 'audit',
        stepId: 'audit',
        agent: 'audit_agent',
        status: 'running',
        startTime: 21,
        logs: [],
        tools: [],
        intents: [],
      },
    ]);

    expect(roles.map((item) => item.label)).toEqual([
      '试卷蓝图设计',
      '试卷质量审核',
    ]);
    expect(roles[1].description).toBe('试卷质量审核阶段由审核裁判负责。');
  });

  it('drops internal system-only nodes from the user-facing stream', () => {
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
      },
      {
        id: 'scheduler',
        agent: 'review_scheduler',
        status: 'done',
        startTime: 5,
        endTime: 9,
        logs: [],
        tools: [],
        intents: [],
      },
    ]);

    expect(roles).toHaveLength(1);
    expect(roles[0].key).toBe('planner');
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
    expect(knowledge.tools).toHaveLength(1);
    expect(knowledge.tools[0].name).toBe('get_kp_with_content');
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

    expect(expert.modelCalls).toHaveLength(1);
    expect(expert.modelCalls[0].kind).toBe('output');
    expect(expert.modelCalls[0].input.task_type).toBe('knowledge_explanation');
    expect(expert.modelCalls[0].output.content).toBe('气血是人体基本物质');
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

  it('keeps public working notes separate from validated formal output', () => {
    const roles = buildAgentPresentation([
      {
        id: 'expert',
        agent: 'expert_agent',
        status: 'done',
        startTime: 10,
        endTime: 20,
        logs: [],
        tools: [],
        intents: [],
        workingOutput: '正在核对教材范围。',
        workingOutputStreaming: false,
        formalOutput: '经审核的知识讲解正文。',
        formalOutputStreaming: false,
        formalOutputReady: true,
      },
    ]);
    const expert = roles.find((item) => item.key === 'expert');

    expect(expert.workingOutput).toBe('正在核对教材范围。');
    expect(expert.formalOutput).toBe('经审核的知识讲解正文。');
    expect(expert.formalOutputReady).toBe(true);
    expect(expert.publicOutput).toBe('经审核的知识讲解正文。');
  });
});
