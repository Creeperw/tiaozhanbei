import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import HomeOnboardingGuide from './HomeOnboardingGuide';

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

  it('shortens every first scene to 70% and keeps only the six-step progress indicator', () => {
    render(<HomeOnboardingGuide onClose={vi.fn()} />);

    expect(screen.getByRole('heading', { name: '定位平台首页' })).toBeInTheDocument();
    expect(document.querySelectorAll('.home-guide__step-progress > span')).toHaveLength(6);
    expect(document.querySelector('.home-guide__demo .home-guide__step-progress')).not.toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1_343));
    expect(screen.getByRole('heading', { name: '定位平台首页' })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.getByRole('heading', { name: '认识六大顶部模块' })).toBeInTheDocument();

    for (const moduleName of ['平台首页', '学习目标', '学习路径', '学习工坊', '训练工坊', '个性数据']) {
      expect(screen.getAllByText(moduleName).length).toBeGreaterThan(0);
    }

    act(() => vi.advanceTimersByTime(2_520));
    expect(screen.getByRole('heading', { name: '认识四大核心功能' })).toBeInTheDocument();
    for (const capability of ['多智能体协同', '个性化学习路径', '知识图谱驱动', '数据驱动成长']) {
      expect(screen.getByText(capability)).toBeInTheDocument();
    }
  });

  it('shows five certificates and limits the second step to two scenes', () => {
    render(<HomeOnboardingGuide onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    expect(screen.getByText('演示 1/2 · 学习目标')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '打开学习目标' })).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1_344));
    expect(screen.getByText('演示 2/2 · 学习目标')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '选择资格证书' })).toBeInTheDocument();
    for (const certificate of [
      '中医执业医师资格考试',
      '中医执业助理医师资格考试',
      '中西医结合执业医师资格考试',
      '中西医结合执业助理医师资格考试',
      '执业药师职业资格考试（中药学类）',
    ]) {
      expect(screen.getByText(certificate)).toBeInTheDocument();
    }

    act(() => vi.advanceTimersByTime(2_520));
    expect(screen.getByText('演示 1/2 · 学习目标')).toBeInTheDocument();
    expect(screen.queryByText('演示 3/3 · 学习目标')).not.toBeInTheDocument();
  });

  it('uses the real short-term and long-term planning diagrams in step three', () => {
    render(<HomeOnboardingGuide onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    expect(screen.getByRole('heading', { name: '进入学习路径' })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1_344));

    expect(screen.getByRole('heading', { name: '查看长期学习规划' })).toBeInTheDocument();
    expect(screen.getByTestId('long-term-path-demo')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '学习阶段' })).toBeInTheDocument();
    expect(document.querySelector('.home-guide__destination-pointer--long-path')).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(2_520));
    expect(screen.getByRole('heading', { name: '查看短期学习路径' })).toBeInTheDocument();
    expect(screen.getByTestId('short-term-path-demo')).toBeInTheDocument();
    expect(screen.getByLabelText('一级知识学习路径')).toBeInTheDocument();
    expect(document.querySelector('.home-guide__destination-pointer--short-path')).toBeInTheDocument();
  });

  it('creates a new assistant conversation and scrolls the real textbook library in step four', () => {
    render(<HomeOnboardingGuide onClose={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    fireEvent.click(screen.getByRole('button', { name: '下一步' }));

    expect(screen.getByRole('heading', { name: '打开学习工坊' })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1_344));

    expect(screen.getByRole('heading', { name: '在智能助教中新建对话' })).toBeInTheDocument();
    expect(screen.getByTestId('assistant-new-chat-demo')).toBeInTheDocument();
    expect(screen.getByLabelText('演示会话列表')).toBeInTheDocument();
    expect(document.querySelector('.home-guide__assistant-session--created')).toHaveTextContent('新对话');
    expect(document.querySelector('.home-guide__destination-pointer--new-chat')).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(2_520));
    expect(screen.getByRole('heading', { name: '浏览教材学习内容' })).toBeInTheDocument();
    expect(screen.getByTestId('textbook-scroll-demo')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '教材学习列表' })).toBeInTheDocument();
    expect(document.querySelector('.home-guide__textbook-track')).toBeInTheDocument();
  });

  it('runs the real consultation interface and scrolls the real smart-paper interface in step five', () => {
    render(<HomeOnboardingGuide onClose={vi.fn()} />);
    for (let index = 0; index < 4; index += 1) {
      fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    }

    expect(screen.getByRole('heading', { name: '打开训练工坊' })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1_344));

    expect(screen.getByRole('heading', { name: '模拟问诊演练' })).toBeInTheDocument();
    expect(screen.getByTestId('consultation-live-demo')).toBeInTheDocument();
    expect(screen.getByText('医生您好，我这几天总觉得胃脘胀满，吃饭后更明显。')).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1_180));
    expect(screen.getByText('大约一周，嗳气比较多，胃口也差了一些。')).toBeInTheDocument();

    act(() => vi.advanceTimersByTime(1_340));
    expect(screen.getByRole('heading', { name: '使用智能组卷' })).toBeInTheDocument();
    expect(screen.getByTestId('smart-paper-scroll-demo')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '试卷存档' })).toBeInTheDocument();
    expect(document.querySelector('.home-guide__paper-track')).toBeInTheDocument();
  });

  it('limits step six to two scenes and displays the three real insight charts', () => {
    render(<HomeOnboardingGuide onClose={vi.fn()} />);
    for (let index = 0; index < 5; index += 1) {
      fireEvent.click(screen.getByRole('button', { name: '下一步' }));
    }

    expect(screen.getByRole('heading', { name: '进入个性数据' })).toBeInTheDocument();
    expect(screen.getByText('演示 1/2 · 个性数据')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1_344));
    expect(screen.getByRole('heading', { name: '查看真实学情图表' })).toBeInTheDocument();
    expect(screen.getByText('演示 2/2 · 个性数据')).toBeInTheDocument();
    expect(screen.getByTestId('profile-insights-demo')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '学习能力雷达图' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '每日学习趋势' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '学习活跃度' })).toBeInTheDocument();
    expect(screen.getByText('新手引导 · 6/6')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '开始使用' })).toBeInTheDocument();
  });
});
