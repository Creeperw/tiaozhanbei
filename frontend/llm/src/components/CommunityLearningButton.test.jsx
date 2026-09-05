import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import CommunityLearningButton from './CommunityLearningButton';

describe('CommunityLearningButton', () => {
  beforeEach(() => {
    vi.stubGlobal('requestAnimationFrame', vi.fn(() => 9));
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('does not run an invisible Canvas loop on coarse-pointer devices', () => {
    vi.stubGlobal('matchMedia', vi.fn((query) => ({
      matches: query.includes('prefers-reduced-motion') ? false : false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })));
    render(<CommunityLearningButton onClick={vi.fn()} />);

    fireEvent.focus(screen.getByRole('button', { name: '开始今日学习' }));
    expect(requestAnimationFrame).not.toHaveBeenCalled();
  });
});
