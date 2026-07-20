import React from 'react';
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import DashboardDailyWorkspace from './DashboardDailyWorkspace';

describe('DashboardDailyWorkspace', () => {
  it('can omit the daily-focus banner so the workspace becomes the leading content', () => {
    render(
      <DashboardDailyWorkspace
        showFocus={false}
        greeting="你好"
        focus={{ title: '开始学习', description: '', duration: '' }}
        schedule={[]}
        feedback={[]}
        pathContent={<section>路径</section>}
        assistantContent={<aside>助教</aside>}
      />,
    );

    expect(screen.queryByRole('region', { name: '今日核心任务' })).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: '今日学习工作区' })).toBeInTheDocument();
  });

  it('marks the workspace-only view as a fullscreen layout', () => {
    render(
      <DashboardDailyWorkspace
        showFocus={false}
        fullscreen
        greeting="你好"
        focus={{ title: '开始学习', description: '', duration: '' }}
        schedule={[]}
        feedback={[]}
        pathContent={<section>路径</section>}
        assistantContent={<aside>助教</aside>}
      />,
    );

    expect(screen.getByTestId('dashboard-daily')).toHaveAttribute('data-layout', 'fullscreen');
  });

  it('uses the dedicated visual layout for the fullscreen training workshop', () => {
    render(
      <DashboardDailyWorkspace
        showFocus={false}
        fullscreen
        greeting="你好"
        focus={{ title: '开始学习', description: '', duration: '' }}
        schedule={[]}
        feedback={[]}
        pathContent={<section>路径</section>}
        assistantContent={<aside>助教</aside>}
      />,
    );

    expect(screen.getByTestId('dashboard-daily')).toHaveClass('dashboard-daily--training-workshop');
  });

  it('keeps training modules above the learning path and stacks helper panels on the right', () => {
    render(
      <DashboardDailyWorkspace
        showFocus={false}
        fullscreen
        greeting="你好"
        focus={{ title: '开始学习', description: '', duration: '' }}
        schedule={[]}
        feedback={[]}
        pathTopContent={<div data-testid="training-modules">训练模块</div>}
        pathContent={<section>路径</section>}
        assistantContent={<aside>助教</aside>}
      />,
    );

    const workspace = screen.getByRole('region', { name: '今日学习工作区' });
    const learningColumn = screen.getByTestId('dashboard-learning-column');
    const modules = screen.getByTestId('training-modules');
    const path = screen.getByRole('region', { name: '学习路径区域' });
    const schedule = within(workspace).getByRole('complementary', { name: '今日安排' });

    expect(workspace).toHaveAttribute('data-training-layout', 'right-stack-raised');
    expect(schedule.querySelector('.dashboard-daily__schedule-title')).toBeInTheDocument();
    expect(learningColumn).toContainElement(modules);
    expect(modules.compareDocumentPosition(path) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(path.querySelector('.dashboard-daily__path-title')).toHaveTextContent('Learning path学习路径');
  });

  it('keeps focus and feedback outside the schedule, fishbone, and assistant row', () => {
    render(
      <DashboardDailyWorkspace
        greeting="你好，admin"
        trackLabel="中医执业医师资格考试"
        focus={{ title: '方剂学第 3 章', description: '掌握方证对应', duration: '25 分钟' }}
        schedule={[
          { id: 'review', title: '回顾昨日错题', state: 'completed', duration: '8 分钟' },
          { id: 'formula', title: '方剂学第 3 章', state: 'current', duration: '25 分钟' },
        ]}
        feedback={[
          { key: 'accuracy', label: '正确率', value: '82%' },
          { key: 'memory', label: '活跃学习记忆', value: '6' },
        ]}
        primaryAction={<button type="button">继续学习</button>}
        secondaryAction={<button type="button">上传个人文档</button>}
        pathContent={<section aria-label="现有鱼骨图">鱼骨图</section>}
        assistantContent={<aside aria-label="智能助教">助教</aside>}
      />,
    );

    const focus = screen.getByRole('region', { name: '今日核心任务' });
    const workspace = screen.getByRole('region', { name: '今日学习工作区' });
    const feedback = screen.getByRole('region', { name: '昨日学习反馈' });

    expect(within(focus).getByText('方剂学第 3 章')).toBeInTheDocument();
    expect(within(workspace).getByRole('complementary', { name: '今日安排' })).toBeInTheDocument();
    expect(within(workspace).getByLabelText('现有鱼骨图')).toBeInTheDocument();
    expect(within(workspace).getByRole('region', { name: '学习路径区域' })).toContainElement(screen.getByLabelText('现有鱼骨图'));
    expect(within(workspace).getByRole('region', { name: '智能助教栏' })).toContainElement(screen.getByLabelText('智能助教'));
    expect(within(workspace).getByText('任务状态：进行中')).toHaveClass('sr-only');
    expect(within(workspace).getByText('第 1 项')).toBeInTheDocument();
    expect(workspace).not.toContainElement(focus);
    expect(workspace).not.toContainElement(feedback);
    expect(screen.getAllByRole('button', { name: '继续学习' })).toHaveLength(1);
  });

  it('renders honest empty states without fabricating schedule or feedback values', () => {
    render(
      <DashboardDailyWorkspace
        greeting="你好"
        focus={{ title: '开始今日学习', description: '', duration: '' }}
        schedule={[]}
        feedback={[]}
        pathContent={<section>路径</section>}
        assistantContent={<aside>助教</aside>}
      />,
    );

    expect(screen.getByText('今天还没有待办任务')).toBeInTheDocument();
    expect(screen.getByText('完成一次学习后，这里会生成反馈摘要')).toBeInTheDocument();
  });
});
