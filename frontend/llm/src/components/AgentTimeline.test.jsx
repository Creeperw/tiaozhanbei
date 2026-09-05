import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AgentTimeline from './AgentTimeline';

const nodes = [
  {
    id: 'planner', agent: 'planner_agent', name: 'planner_agent', status: 'done',
    startTime: 100, endTime: 220, logs: ['planner_agent开始处理', '执行路径已确定'],
    tools: [], intents: [],
  },
  {
    id: 'knowledge', agent: 'knowledge_base_agent', name: 'knowledge_base_agent', status: 'running',
    startTime: 230, logs: ['knowledge_base_agent开始处理'],
    inputSummary: {
      user_request: '讲解四君子汤', has_user_profile: true,
      has_compressed_history: true, recent_message_count: 3,
      has_external_information: true,
    },
    tools: [{ id: 'kp-tool', name: 'get_kp_with_content', args: { query: '四君子汤' }, status: 'running', startTime: 240 }],
    intents: [],
  },
];

describe('AgentTimeline dynamic collaboration desk', () => {
  beforeEach(() => { window.HTMLElement.prototype.scrollIntoView = vi.fn(); });

  it('only shows agents that actually participate in this run', () => {
    render(<AgentTimeline nodes={nodes} refs={[]} onClose={vi.fn()} />);
    expect(screen.getByRole('complementary', { name: '执行进度' })).toBeInTheDocument();
    expect(screen.getByText('任务规划')).toBeInTheDocument();
    expect(screen.getByText('知识库管理')).toBeInTheDocument();
    expect(screen.queryByText('记忆管理')).not.toBeInTheDocument();
    expect(screen.queryByText('学情诊断')).not.toBeInTheDocument();
    expect(screen.getByLabelText('本次协作摘要')).toHaveTextContent('2 个参与者');
  });

  it('shows sanitized context and public actions instead of raw prompts or internal schemas', async () => {
    const user = userEvent.setup();
    render(<AgentTimeline nodes={nodes} refs={[]} onClose={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: /收起知识库管理公开记录/ }));
    await user.click(screen.getByRole('button', { name: /展开知识库管理公开记录/ }));
    ['当前消息', '用户画像', '压缩历史对话', '近期历史对话', '外部信息'].forEach((label) => {
      expect(screen.getByText(label)).toBeInTheDocument();
    });
    expect(screen.getByText(/读取知识点内容：四君子汤/)).toBeInTheDocument();
    expect(screen.queryByText('get_kp_with_content')).not.toBeInTheDocument();
    expect(screen.queryByText('模型原始输出')).not.toBeInTheDocument();
    expect(screen.queryByText('你是中医学习专家')).not.toBeInTheDocument();
  });

  it('shows evidence in a dedicated traceable source view', async () => {
    const user = userEvent.setup();
    const evidenceNodes = [{
      ...nodes[1], status: 'done', endTime: 400,
      retrievals: [{
        kp_query: '四君子汤 功效', question_query: '',
        evidence_items: [{ evidence_id: 'E1', title: '方剂学教材', knowledge_point: '四君子汤', match_score: 0.92 }],
      }],
    }];
    render(<AgentTimeline nodes={evidenceNodes} refs={[]} onClose={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: /证据来源/ }));
    expect(screen.getByText('方剂学教材')).toBeInTheDocument();
    expect(screen.getByText('匹配度 92%')).toBeInTheDocument();
    expect(screen.getByText('条可追溯资料').parentElement).toHaveTextContent('1条可追溯资料');
  });

  it('explains the local repair and mandatory re-audit path', async () => {
    const user = userEvent.setup();
    const auditNode = {
      id: 'audit', agent: 'audit_agent', name: 'audit_agent', status: 'done',
      startTime: 500, endTime: 800, logs: ['内容质量检查完成。'], tools: [], intents: [],
      auditEvents: [
        { kind: 'planned', text: '审核已定位问题，准备仅返修受影响的环节', status: 'planned', targetStepIds: ['expert'], preservedStepIds: ['knowledge'], locations: ['第二段'], ts: 600 },
        { kind: 'completed', text: '复审通过，返修内容可以发布', status: 'pass', targetStepIds: [], preservedStepIds: [], locations: [], ts: 800 },
      ],
    };
    render(<AgentTimeline nodes={[auditNode]} refs={[]} onClose={vi.fn()} />);
    await user.click(screen.getByRole('button', { name: /审核返修/ }));
    expect(screen.getByText('审核已通过')).toBeInTheDocument();
    expect(screen.getByText('审核已定位问题，准备仅返修受影响的环节')).toBeInTheDocument();
    expect(screen.getByText('复审通过，返修内容可以发布')).toBeInTheDocument();
    expect(screen.getByText('仅返修 1 个受影响环节')).toBeInTheDocument();
  });
});
