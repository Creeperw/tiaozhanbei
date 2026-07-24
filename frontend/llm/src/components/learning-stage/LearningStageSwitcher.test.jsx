import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import LearningStageSwitcher from './LearningStageSwitcher';

const stages = [
  { node_id: 'stage-1', title: '中医基础与文化语言', status: 'completed' },
  { node_id: 'stage-2', title: '中药方剂与经典基础', status: 'in_progress' },
  { node_id: 'stage-3', title: '经典与现代医学基础', status: 'unassessed' },
];

describe('LearningStageSwitcher desktop preview', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: false,
      media: '(max-width: 740px)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })));
  });

  afterEach(() => {
    vi.runOnlyPendingTimers();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('uses backend plan stages and keeps the preview open while moving into it', () => {
    const onNavigate = vi.fn();
    const onCurrentStageChange = vi.fn();
    const { container } = render(
      <LearningStageSwitcher
        stages={stages}
        currentStageId="stage-2"
        onCurrentStageChange={onCurrentStageChange}
        onNavigate={onNavigate}
      />,
    );

    const trigger = screen.getByRole('button', { name: /当前阶段.*02.*中药方剂与经典基础/ });
    expect(trigger).toBeInTheDocument();
    const root = container.querySelector('.learning-stage-switcher');
    fireEvent.mouseEnter(root);
    const dialog = screen.getByRole('dialog', { name: '学习阶段选择' });

    fireEvent.mouseLeave(root);
    act(() => vi.advanceTimersByTime(80));
    fireEvent.mouseEnter(dialog);
    act(() => vi.advanceTimersByTime(80));
    expect(dialog).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole('button', { name: /经典与现代医学基础/ }));
    expect(onCurrentStageChange).toHaveBeenCalledWith('stage-3', stages[2]);
  });
});

describe('LearningStageSwitcher mobile sheet', () => {
  beforeEach(() => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: true,
      media: '(max-width: 740px)',
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })));
  });

  afterEach(() => vi.unstubAllGlobals());

  it('traps focus and links back to the complete backend-driven stage page', async () => {
    const onNavigate = vi.fn();
    render(
      <LearningStageSwitcher
        stages={stages}
        currentStageId="stage-2"
        onCurrentStageChange={vi.fn()}
        onNavigate={onNavigate}
      />,
    );

    const trigger = screen.getByRole('button', { name: /当前阶段.*02.*中药方剂与经典基础/ });
    fireEvent.click(trigger);
    const dialog = screen.getByRole('dialog', { name: '学习阶段选择' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    const closeButton = within(dialog).getByRole('button', { name: '关闭学习阶段选择' });
    const fullRouteButton = within(dialog).getByRole('button', { name: '查看完整进阶路线' });
    await waitFor(() => expect(closeButton).toHaveFocus());

    fireEvent.click(fullRouteButton);
    expect(onNavigate).toHaveBeenCalledWith({
      page: 'practice',
      params: { view: 'stages' },
    });

    fireEvent.click(trigger);
    fireEvent.click(within(screen.getByRole('dialog', { name: '学习阶段选择' }))
      .getByRole('button', { name: '关闭学习阶段选择' }));
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
