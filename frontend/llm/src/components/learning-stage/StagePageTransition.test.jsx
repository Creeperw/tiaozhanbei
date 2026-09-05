import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import StagePageTransition from './StagePageTransition';
import { STAGE_FLIP_DURATION_MS } from './learningStageModel';

vi.mock('framer-motion', () => ({
  motion: {
    div: ({ children, ...props }) => {
      const {
        animate: _animate,
        initial: _initial,
        transition: _transition,
        style,
        ...domProps
      } = props;
      return <div {...domProps} style={style}>{children}</div>;
    },
  },
  useReducedMotion: () => false,
}));

describe('StagePageTransition', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('switches the route at the flip midpoint and keeps the overlay until completion', () => {
    vi.useFakeTimers();
    const onMidpoint = vi.fn();
    const onComplete = vi.fn();
    render(
      <StagePageTransition
        selection={{
          stage: { id: 'foundation', title: '基础筑基', colors: ['#66b65d', '#4b9b4c'] },
          index: 0,
          sourceRect: { left: 80, top: 120, width: 260, height: 360 },
        }}
        onMidpoint={onMidpoint}
        onComplete={onComplete}
      />,
    );

    expect(screen.getByRole('status', { name: '正在进入基础筑基阶段' })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(STAGE_FLIP_DURATION_MS / 2));
    expect(onMidpoint).toHaveBeenCalledTimes(1);
    expect(onComplete).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(STAGE_FLIP_DURATION_MS / 2));
    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it('carries the stage artwork through both flip faces and wraps long copy inside a bounded panel', () => {
    render(
      <StagePageTransition
        selection={{
          stage: {
            id: 'foundation',
            title: '中医基础理论与文化认知综合学习阶段',
            duration: '1-2个月',
            illustration: '/learning-stage/foundation.png',
            illustrationPosition: '70% 72%',
            colors: ['#3F8F68', '#2E7150'],
          },
          index: 0,
          sourceRect: { left: 80, top: 120, width: 260, height: 360 },
        }}
      />,
    );

    const artwork = [...document.querySelectorAll('img.stage-page-transition__illustration')];
    expect(artwork).toHaveLength(2);
    artwork.forEach((image) => {
      expect(image).toHaveAttribute('src', '/learning-stage/foundation.png');
      expect(image).toHaveAttribute('alt', '');
      expect(image).toHaveAttribute('aria-hidden', 'true');
      expect(image).toHaveClass('stage-page-transition__illustration');
    });
    expect(document.querySelectorAll('.stage-page-transition__copy')).toHaveLength(2);
    expect(document.querySelectorAll('.stage-page-transition__title')).toHaveLength(2);
  });
});
