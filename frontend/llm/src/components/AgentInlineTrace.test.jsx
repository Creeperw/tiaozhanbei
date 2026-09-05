import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import AgentInlineTrace from './AgentInlineTrace';

const baseRole = {
  key: 'knowledge',
  label: '知识库智能体',
  description: '检索并整理可核验资料',
  status: 'running',
  workingOutput: '### 当前整理思路\n已定位到**阴阳对立制约**相关教材内容。\n\n- 对立是双方属性相反\n- 制约是双方相互限制',
  workingOutputStreaming: true,
  formalOutput: '',
  formalOutputStreaming: false,
  formalOutputReady: false,
  publicOutput: '### 当前整理思路\n已定位到**阴阳对立制约**相关教材内容。',
  publicOutputStreaming: true,
  activities: [],
  summary: '',
  tools: [{ id: 'tool-1', name: 'web_search', status: 'running', args: { query: '阴阳对立制约 教材' } }],
  retrievals: [{ retrieval_round: 1, kp_query: '阴阳对立制约', evidence_items: [{ source_id: 'e-1' }, { source_id: 'e-2' }] }],
  modelCalls: [{ callId: 'model-1' }],
  auditEvents: [],
};

describe('AgentInlineTrace', () => {
  it('renders streaming stage output and tool calls inline', () => {
    const { container } = render(
      <AgentInlineTrace roles={[baseRole]} status="running" elapsedSeconds={8} evidenceCount={2} isGenerating />,
    );

    expect(screen.getByLabelText('多智能体执行过程')).toHaveTextContent('正在处理：知识库智能体');
    expect(screen.getByText('正在输出')).toBeInTheDocument();
    expect(screen.getByText('公开工作过程')).toBeInTheDocument();
    expect(screen.getByText('当前整理思路')).toBeInTheDocument();
    expect(screen.getByText('对立是双方属性相反')).toBeInTheDocument();
    expect(screen.getByText(/已定位到/)).toBeInTheDocument();
    expect(screen.getByText('正在调用语言模型')).toBeInTheDocument();
    expect(screen.getByText('搜索外部资料')).toBeInTheDocument();
    expect(screen.getByText('知识库检索 · 第 1 轮')).toBeInTheDocument();
    expect(screen.getByText('2 条结果')).toBeInTheDocument();
    expect(container.querySelector('.agent-working__caret')).toBeInTheDocument();
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
  });

  it('collapses and expands without opening a sidebar', () => {
    render(<AgentInlineTrace roles={[{
      ...baseRole,
      status: 'done',
      workingOutputStreaming: false,
      formalOutput: '### 正式资料结果\n阴阳对立制约是阴阳关系的重要内容。',
      formalOutputReady: true,
      publicOutputStreaming: false,
    }]} status="success" />);
    const toggle = screen.getByRole('button', { name: /多智能体协作完成/ });

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('已生成：知识整理结果')).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('已生成：知识整理结果')).toBeInTheDocument();
  });

  it('keeps the stage visibly active while its public output is streaming', () => {
    render(
      <AgentInlineTrace
        roles={[{ ...baseRole, status: 'done' }]}
        status="success"
        elapsedSeconds={12}
        isGenerating
      />,
    );

    expect(screen.getByText(/正在输出：检索并整理可核验资料/)).toBeInTheDocument();
    expect(screen.getByText(/已定位到/)).toBeInTheDocument();
    expect(document.querySelector('.agent-working__caret')).toBeInTheDocument();
  });

  it('shows an honest long-running message before any stage result exists', () => {
    render(
      <AgentInlineTrace
        roles={[{
          ...baseRole,
          workingOutput: '',
          workingOutputStreaming: false,
          publicOutput: '',
          publicOutputStreaming: false,
        }]}
        status="running"
        elapsedSeconds={30}
        isGenerating
      />,
    );

    expect(screen.getByText(/耗时较长，但任务仍在继续/)).toBeInTheDocument();
    expect(screen.getByText('正在调用语言模型')).toBeInTheDocument();
  });

  it('renders the concrete model reasoning in an expandable thought block', () => {
    render(<AgentInlineTrace roles={[{
      ...baseRole,
      reasoning: '先识别当前消息属于知识讲解，再核对可用教材证据。',
      reasoningStreaming: true,
    }]} status="running" isGenerating />);

    expect(screen.getByText('正在思考')).toBeInTheDocument();
    expect(screen.getByText(/先识别当前消息属于知识讲解/)).toBeInTheDocument();
    expect(screen.getByText(/实时展示模型思考过程/)).toBeInTheDocument();
  });

  it('auto-collapses working notes and exposes formal output as a compact expandable node', () => {
    const { rerender } = render(
      <AgentInlineTrace roles={[baseRole]} status="running" isGenerating />,
    );
    expect(screen.getByText('对立是双方属性相反')).toBeInTheDocument();

    const completedRole = {
      ...baseRole,
      status: 'done',
      workingOutputStreaming: false,
      publicOutputStreaming: false,
      formalOutput: '### 经校验的资料整理\n阴阳之间既相互对立，也相互制约。',
      formalOutputReady: true,
    };
    rerender(<AgentInlineTrace roles={[completedRole]} status="success" />);

    const thoughtToggle = screen.getByRole('button', { name: /工作过程/ });
    const resultToggle = screen.getByRole('button', { name: /已生成：知识整理结果/ });
    expect(thoughtToggle).toHaveAttribute('aria-expanded', 'false');
    expect(resultToggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('经校验的资料整理')).not.toBeInTheDocument();

    fireEvent.click(resultToggle);
    expect(resultToggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('经校验的资料整理')).toBeInTheDocument();

    fireEvent.click(thoughtToggle);
    expect(thoughtToggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('对立是双方属性相反')).toBeInTheDocument();
  });

  it('renders audit repair events with correct terminal statuses', () => {
    const auditRole = {
      ...baseRole,
      status: 'done',
      auditEvents: [
        { kind: 'repair_planned', text: '审核已定位问题', status: 'planned', locations: ['组卷环节'] },
        { kind: 'repair_step_started', text: '正在局部返修目标环节', status: 'running' },
        { kind: 'repair_step_completed', text: '目标环节返修完成', status: 'success' },
        { kind: 'repair_reaudit_started', text: '局部返修完成，正在强制复审', status: 'running' },
        { kind: 'repair_completed', text: '复审通过，返修内容可以发布', status: 'pass' },
      ],
    };
    render(<AgentInlineTrace roles={[auditRole]} status="success" />);

    // 已完成事件（planned/success/pass）显示"完成"
    expect(screen.getByText('审核已定位问题').closest('li')).toHaveTextContent('完成');
    expect(screen.getByText('目标环节返修完成').closest('li')).toHaveTextContent('完成');
    expect(screen.getByText('复审通过，返修内容可以发布').closest('li')).toHaveTextContent('完成');
    // 进行中事件（running）显示"运行中"
    expect(screen.getByText('正在局部返修目标环节').closest('li')).toHaveTextContent('运行中');
    expect(screen.getByText('局部返修完成，正在强制复审').closest('li')).toHaveTextContent('运行中');
  });
});
