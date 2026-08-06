import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AgentTimeline from './AgentTimeline';

const nodes = [
  {
    id: 'planner',
    agent: 'planner_agent',
    name: 'planner_agent',
    status: 'done',
    startTime: 100,
    endTime: 220,
    logs: ['planner_agent开始处理', '执行路径已确定'],
    tools: [],
    intents: [],
  },
  {
    id: 'knowledge',
    agent: 'knowledge_base_agent',
    name: 'knowledge_base_agent',
    status: 'running',
    startTime: 230,
    logs: ['knowledge_base_agent开始处理'],
    tools: [{
      id: 'kp-tool',
      name: 'get_kp_with_content',
      args: { query: '四君子汤' },
      status: 'running',
      startTime: 240,
    }],
    intents: [],
  },
];

describe('AgentTimeline six-agent task desk', () => {
  beforeEach(() => {
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it('shows all six formal agents with natural-language status labels', () => {
    render(<AgentTimeline nodes={nodes} refs={[]} onClose={vi.fn()} />);

    expect(screen.getByRole('complementary', { name: '执行进度' })).toBeInTheDocument();
    ['任务规划', '记忆管理', '学情诊断', '知识库管理', '专家', '审核裁判'].forEach((label) => {
      expect(screen.getByText(label)).toBeInTheDocument();
    });
    expect(screen.getByText('执行中')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getAllByText('本次无需参与')).toHaveLength(4);
    expect(screen.queryByText('Planner')).not.toBeInTheDocument();
    expect(screen.queryByText('Executor')).not.toBeInTheDocument();
    expect(screen.queryByText('Tool Calls')).not.toBeInTheDocument();
    expect(screen.queryByText('running')).not.toBeInTheDocument();
  });

  it('reveals internal nodes and tool payload only after expanding technical details', async () => {
    const user = userEvent.setup();
    render(<AgentTimeline nodes={nodes} refs={[]} onClose={vi.fn()} />);

    expect(screen.queryByText('planner_agent')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /展开任务规划技术详情/ }));
    expect(screen.getByText('planner_agent')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /展开知识库管理技术详情/ }));
    expect(screen.getByText('get_kp_with_content')).toBeInTheDocument();
    expect(screen.getByText(/四君子汤/)).toBeInTheDocument();
  });

  it('reveals model input/output payloads under the owning agent details', async () => {
    const user = userEvent.setup();
    render(<AgentTimeline nodes={[{
      id: 'expert',
      agent: 'expert_agent',
      name: 'expert_agent',
      status: 'done',
      startTime: 100,
      endTime: 220,
      logs: ['expert_agent开始处理'],
      tools: [],
      intents: [],
      modelCalls: [
        {
          id: 'MODEL_CALL_1-input-1',
          kind: 'input',
          agent: 'expert_agent',
          input: { task_type: 'knowledge_explanation', topic: '气血' },
          ts: 110,
        },
        {
          id: 'MODEL_CALL_1-output-1',
          kind: 'output',
          agent: 'expert_agent',
          output: { content: '气血是人体基本物质' },
          ts: 210,
        },
      ],
    }]} refs={[]} onClose={vi.fn()} />);

    expect(screen.queryByText('模型调用详情')).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: /展开专家技术详情/ }));
    expect(screen.getByText('模型调用详情')).toBeInTheDocument();
    expect(screen.getByText('模型输入')).toBeInTheDocument();
    expect(screen.getByText('模型输出')).toBeInTheDocument();
    expect(screen.getByText(/knowledge_explanation/)).toBeInTheDocument();
    expect(screen.getByText(/气血是人体基本物质/)).toBeInTheDocument();
  });
  it('prefers real natural-language transport payloads over structured input/output', async () => {
    const user = userEvent.setup();
    render(<AgentTimeline nodes={[{
      id: 'expert',
      agent: 'expert_agent',
      name: 'expert_agent',
      status: 'done',
      startTime: 100,
      endTime: 220,
      logs: ['expert_agent开始处理'],
      tools: [],
      intents: [],
      modelCalls: [
        {
          id: 'MODEL_CALL_1-input-1',
          callId: 'MODEL_CALL_1',
          kind: 'input',
          agent: 'expert_agent',
          input: { task_type: 'knowledge_explanation', topic: '气血' },
          ts: 110,
        },
        {
          id: 'MODEL_CALL_1-output-1',
          callId: 'MODEL_CALL_1',
          kind: 'output',
          agent: 'expert_agent',
          output: { content: '气血是人体基本物质' },
          ts: 210,
        },
        {
          id: 'MODEL_CALL_1-transport-1',
          callId: 'MODEL_CALL_1',
          kind: 'transport',
          agent: 'expert_agent',
          requestPayload: {
            url: 'https://provider/chat/completions',
            body: {
              model: 'deepseek-v4-flash-0731',
              messages: [
                { role: 'system', content: '你是中医学习专家。' },
                { role: 'user', content: '请讲解气血的概念，并说明气血不足的表现。' },
              ],
            },
          },
          responseText: '{"title":"气血讲解","explanation_content":"气血是构成人体和维持生命活动的基本物质。","evidence_refs":["EVID_1"]}',
          ts: 211,
        },
      ],
    }]} refs={[]} onClose={vi.fn()} />);

    await user.click(screen.getByRole('button', { name: /展开专家技术详情/ }));
    expect(screen.getByText('模型调用详情')).toBeInTheDocument();
    expect(screen.getByText('发送给模型的自然语言请求')).toBeInTheDocument();
    expect(screen.getByText('请讲解气血的概念，并说明气血不足的表现。')).toBeInTheDocument();
    expect(screen.getByText('模型原始输出')).toBeInTheDocument();
    expect(screen.getByText(/气血是构成人体和维持生命活动的基本物质/)).toBeInTheDocument();
    // 结构化输入不再作为主视图展示，折叠在「查看结构化数据」之下。
    expect(screen.getByText('查看结构化数据')).toBeInTheDocument();
  });
});
