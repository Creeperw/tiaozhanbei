import React from 'react';
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import LearningStageSwitcher from './LearningStageSwitcher';

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

  it('keeps the dialog open while moving from the trigger area into the preview', () => {
    const onNavigate = vi.fn();
    const { container } = render(
      <LearningStageSwitcher
        currentStageId="classics"
        onCurrentStageChange={vi.fn()}
        onNavigate={onNavigate}
      />,
    );

    const root = container.querySelector('.learning-stage-switcher');
    expect(window.matchMedia).toHaveBeenCalledWith('(max-width: 740px)');
    fireEvent.mouseEnter(root);
    const dialog = screen.getByRole('dialog', { name: '学习阶段选择' });

    fireEvent.mouseLeave(root);
    act(() => vi.advanceTimersByTime(80));
    fireEvent.mouseEnter(dialog);
    act(() => vi.advanceTimersByTime(80));

    expect(screen.getByRole('dialog', { name: '学习阶段选择' })).toBeInTheDocument();
    const fullRouteButton = within(dialog).getByRole('button', { name: '查看完整进阶路线' });
    fireEvent.click(fullRouteButton);
    expect(onNavigate).toHaveBeenCalledWith({
      page: 'practice',
      params: { view: 'stages' },
    });
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

  it('acts as a focus-trapped modal and restores the trigger on close', async () => {
    render(
      <LearningStageSwitcher
        currentStageId="classics"
        onCurrentStageChange={vi.fn()}
        onNavigate={vi.fn()}
      />,
    );

    const trigger = screen.getByRole('button', { name: /当前阶段.*02.*经典研读/ });
    fireEvent.click(trigger);

    const dialog = screen.getByRole('dialog', { name: '学习阶段选择' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    const closeButton = within(dialog).getByRole('button', { name: '关闭学习阶段选择' });
    const fullRouteButton = within(dialog).getByRole('button', { name: '查看完整进阶路线' });
    await waitFor(() => expect(closeButton).toHaveFocus());

    fullRouteButton.focus();
    fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(closeButton).toHaveFocus();

    closeButton.focus();
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(fullRouteButton).toHaveFocus();

    fireEvent.click(closeButton);
    expect(screen.queryByRole('dialog', { name: '学习阶段选择' })).not.toBeInTheDocument();
    await waitFor(() => expect(trigger).toHaveFocus());
  });
});
