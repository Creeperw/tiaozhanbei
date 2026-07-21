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
});
