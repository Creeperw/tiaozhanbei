import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import LearningStageLanding from './LearningStageLanding';
import {
  DEFAULT_LEARNING_STAGES,
  getStageLayout,
} from './learningStageModel';

describe('LearningStageLanding', () => {
  it.each([4, 5, 6])('renders %s backend-computed stages with an adaptive layout', (count) => {
    render(<LearningStageLanding stages={DEFAULT_LEARNING_STAGES.slice(0, count)} />);

    expect(screen.getAllByRole('button', { name: /进入.+阶段/ })).toHaveLength(count);
    expect(screen.getByTestId('learning-stage-grid')).toHaveAttribute('data-stage-count', String(count));
    expect(getStageLayout(count)).toHaveLength(count);
    expect(getStageLayout(count).at(-1).progress).toBeGreaterThan(getStageLayout(count)[0].progress);
  });

  it('emits the selected stage and its source rectangle for the page transition', () => {
    const onStageSelect = vi.fn();
    render(<LearningStageLanding onStageSelect={onStageSelect} />);

    fireEvent.click(screen.getByRole('button', { name: '进入基础筑基阶段' }));

    expect(onStageSelect).toHaveBeenCalledWith(expect.objectContaining({
      stage: expect.objectContaining({ id: 'foundation', title: '基础筑基' }),
      index: 0,
      sourceRect: expect.objectContaining({ width: expect.any(Number), height: expect.any(Number) }),
    }));
  });

  it('uses compact resources only for cards in the shorter half of the staircase', () => {
    render(<LearningStageLanding />);

    const cards = screen.getAllByRole('button', { name: /进入.+阶段/ });
    expect(cards.slice(0, 3).every((card) => card.dataset.resourceDensity === 'compact')).toBe(true);
    expect(cards.slice(3).every((card) => card.dataset.resourceDensity === 'full')).toBe(true);
  });

  it('does not reserve card height for redundant action copy', () => {
    render(<LearningStageLanding />);

    expect(screen.queryByText('查看学习路径')).not.toBeInTheDocument();
  });

  it('renders each supplied stage drawing as a decorative watermark', () => {
    const { container } = render(<LearningStageLanding />);

    const drawings = Array.from(container.querySelectorAll('.learning-stage-card__illustration'));
    expect(drawings).toHaveLength(6);
    expect(drawings.every((drawing) => drawing.getAttribute('alt') === '')).toBe(true);
    expect(drawings.every((drawing) => drawing.getAttribute('aria-hidden') === 'true')).toBe(true);
    expect(drawings.map((drawing) => drawing.getAttribute('src'))).toEqual([
      '/learning-stage/foundation.png',
      '/learning-stage/classics.png',
      '/learning-stage/formulas.png',
      '/learning-stage/clinical.png',
      '/learning-stage/specialty.png',
      '/learning-stage/mastery.png',
    ]);
  });

  it('uses the approved green-to-ink stage palette in order', () => {
    render(<LearningStageLanding />);

    const cards = screen.getAllByRole('button', { name: /进入.+阶段/ });
    expect(cards.map((card) => card.style.getPropertyValue('--stage-start'))).toEqual([
      '#3F8F68',
      '#347D70',
      '#33777B',
      '#3B6876',
      '#3B586A',
      '#293D4C',
    ]);
  });
});
