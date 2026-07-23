import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
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

  it('keeps training modules directly between the learning path and yesterday feedback', () => {
    render(
      <DashboardDailyWorkspace
        showFocus={false}
        fullscreen
        greeting="你好"
        focus={{ title: '开始学习', description: '', duration: '' }}
        schedule={[]}
        feedback={[]}
        trainingContent={<div data-testid="training-modules">训练模块</div>}
        pathContent={<section>路径</section>}
        assistantContent={<aside>助教</aside>}
      />,
    );

    const workspace = screen.getByRole('region', { name: '今日学习工作区' });
    const learningColumn = screen.getByTestId('dashboard-learning-column');
    const modules = screen.getByTestId('training-modules');
    const path = screen.getByRole('region', { name: '学习路径区域' });
    const feedback = screen.getByRole('region', { name: '昨日学习反馈' });

    expect(workspace).toHaveAttribute('data-training-layout', 'right-stack-raised');
    expect(learningColumn).toContainElement(modules);
    expect(path.compareDocumentPosition(modules) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(modules.compareDocumentPosition(feedback) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(path.querySelector('.dashboard-daily__path-title')).toHaveTextContent('Learning path学习路径');
  });

  it('puts the path title, source tabs, and classic route select in one header control group', () => {
    render(
      <DashboardDailyWorkspace
        showFocus={false}
        greeting="你好"
        focus={{ title: '开始学习', description: '', duration: '' }}
        schedule={[]}
        feedback={[]}
        pathControls={(
          <>
            <div role="tablist" aria-label="学习路径来源">
              <button type="button" role="tab" aria-selected="false">我的学习路径</button>
              <button type="button" role="tab" aria-selected="true">经典路线</button>
            </div>
            <label>路线<select aria-label="经典学习路线"><option>中医经典路线</option></select></label>
          </>
        )}
        pathContent={<section>路径</section>}
        assistantContent={<aside>助教</aside>}
      />,
    );

    const controls = screen.getByRole('group', { name: '学习路径控制区' });
    expect(within(controls).getByRole('heading', { name: '学习路径' })).toBeInTheDocument();
    expect(within(controls).getByRole('tablist', { name: '学习路径来源' })).toBeInTheDocument();
    expect(within(controls).getByRole('combobox', { name: '经典学习路线' })).toBeInTheDocument();
  });

  it('uses one right rail that defaults to today task and switches to the collapsed assistant', () => {
    render(
      <DashboardDailyWorkspace
        showFocus={false}
        greeting="你好"
        focus={{ title: '开始学习', description: '', duration: '' }}
        schedule={[]}
        feedback={[]}
        todayTaskContent={<section aria-label="今日任务">今日任务内容</section>}
        pathContent={<section>路径</section>}
        assistantCollapsed
        assistantContent={(
          <aside aria-label="智能助教">
            助教内容
            <input aria-label="助教草稿" />
          </aside>
        )}
      />,
    );

    const rail = screen.getByRole('complementary', { name: '学习工作栏' });
    const taskTab = within(rail).getByRole('tab', { name: '今日任务' });
    const assistantTab = within(rail).getByRole('tab', { name: 'AI 助教' });
    expect(taskTab).toHaveAttribute('aria-selected', 'true');
    expect(taskTab).toHaveAttribute('tabindex', '0');
    expect(assistantTab).toHaveAttribute('aria-selected', 'false');
    expect(assistantTab).toHaveAttribute('tabindex', '-1');
    expect(within(rail).getByRole('region', { name: '今日任务' })).toBeInTheDocument();
    expect(within(rail).getByLabelText('智能助教')).not.toBeVisible();

    fireEvent.click(assistantTab);
    expect(taskTab).toHaveAttribute('aria-selected', 'false');
    expect(assistantTab).toHaveAttribute('aria-selected', 'true');
    expect(within(rail).getByLabelText('智能助教')).toBeVisible();
    expect(within(rail).queryByRole('region', { name: '今日任务' })).not.toBeInTheDocument();

    const draft = within(rail).getByRole('textbox', { name: '助教草稿' });
    fireEvent.change(draft, { target: { value: '保留这段输入' } });
    fireEvent.click(taskTab);
    expect(draft).toHaveValue('保留这段输入');
    expect(within(rail).getByLabelText('智能助教')).not.toBeVisible();

    fireEvent.keyDown(taskTab, { key: 'ArrowRight' });
    expect(assistantTab).toHaveAttribute('aria-selected', 'true');
    expect(assistantTab).toHaveFocus();
    fireEvent.keyDown(assistantTab, { key: 'Home' });
    expect(taskTab).toHaveAttribute('aria-selected', 'true');
    expect(taskTab).toHaveFocus();
    fireEvent.keyDown(taskTab, { key: 'ArrowLeft' });
    expect(assistantTab).toHaveAttribute('aria-selected', 'true');
    expect(assistantTab).toHaveFocus();
    fireEvent.keyDown(assistantTab, { key: 'ArrowRight' });
    expect(taskTab).toHaveAttribute('aria-selected', 'true');
    expect(taskTab).toHaveFocus();
  });

  it('keeps focus outside the workspace and feedback below the path in the learning column', () => {
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
    const learningColumn = screen.getByTestId('dashboard-learning-column');
    const workRail = within(workspace).getByRole('complementary', { name: '学习工作栏' });

    expect(within(focus).getByText('方剂学第 3 章')).toBeInTheDocument();
    expect(workRail).toBeInTheDocument();
    expect(within(workspace).getByLabelText('现有鱼骨图')).toBeInTheDocument();
    expect(within(workspace).getByRole('region', { name: '学习路径区域' })).toContainElement(screen.getByLabelText('现有鱼骨图'));
    expect(within(workspace).getByText('任务状态：进行中')).toHaveClass('sr-only');
    expect(within(workspace).getByText('第 1 项')).toBeInTheDocument();
    expect(workspace).not.toContainElement(focus);
    expect(workspace).toContainElement(feedback);
    expect(learningColumn).toContainElement(feedback);
    expect(workRail).not.toContainElement(feedback);
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
