import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import HomeOnboardingGuide from './HomeOnboardingGuide';

const SPOTLIGHT_TITLES = [
  '一、确定学习目标',
  '二、了解当前学情',
  '三、开始中医学习',
  '四、检验学习成果',
  '五、解决学习问题',
  '六、个性数据更新',
];

describe('HomeOnboardingGuide', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('matchMedia', vi.fn(() => ({
      matches: false,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    })));
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('starts with the current learning-target guide and six-step progress', () => {
    render(<HomeOnboardingGuide onClose={vi.fn()} />);

    expect(screen.getByText('一、确定学习目标')).toBeInTheDocument();
    expect(screen.getByText('新手引导 · 1/6')).toBeInTheDocument();
    expect(document.querySelectorAll('.home-guide__step-progress > span')).toHaveLength(6);
    expect(screen.getByRole('button', { name: '下一步' })).toBeInTheDocument();
  });

  it('moves through all current spotlight steps and returns to the prior step', () => {
    render(<HomeOnboardingGuide onClose={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    expect(screen.getByRole('heading', { name: SPOTLIGHT_TITLES[0] })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '上一步' })).not.toBeInTheDocument();

    for (const title of SPOTLIGHT_TITLES.slice(1)) {
      fireEvent.click(screen.getByRole('button', { name: '下一步' }));
      expect(screen.getByRole('heading', { name: title })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '上一步' })).toBeInTheDocument();
    }

    fireEvent.click(screen.getByRole('button', { name: '上一步' }));
    expect(screen.getByRole('heading', { name: SPOTLIGHT_TITLES[4] })).toBeInTheDocument();
  });

  it('closes after the final spotlight step', () => {
    const onClose = vi.fn();
    render(<HomeOnboardingGuide onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    for (let index = 1; index < SPOTLIGHT_TITLES.length; index += 1) {
      fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    }
    expect(screen.getByRole('button', { name: '开始使用' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '开始使用' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
