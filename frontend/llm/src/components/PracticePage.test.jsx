import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PracticePage from './PracticePage';
import { loadTrainingWorkspaceModules } from '../pageDataLoaders.js';

vi.mock('../learningFocusTracker.js', () => ({
  createLearningFocusTracker: () => ({
    start: vi.fn(() => Promise.resolve()),
    stop: vi.fn(() => Promise.resolve()),
  }),
}));

vi.mock('../pageDataLoaders.js', () => ({
  loadPracticeAgentContext: vi.fn(() => Promise.resolve({ contextBrief: null, recentTrace: [] })),
  loadVariationSources: vi.fn(() => Promise.resolve({ sources: { items: [] }, error: '' })),
  loadTrainingWorkspaceModules: vi.fn(() => Promise.resolve({
    workspace: {
      modules: [{
        key: 'practice_grading',
        label: 'Practice grading',
        description: 'Practice grading',
        enabled: true,
        badge: 'Available',
      }, {
        key: 'mistake_variation',
        label: 'Mistake reinforcement',
        description: 'Mistake reinforcement',
        enabled: true,
        badge: 'Available',
      }, {
        key: 'case_training',
        label: 'Case training',
        description: 'Case training',
        enabled: true,
        badge: 'Available',
      }],
    },
    error: '',
  })),
  isTrainingTaskResultApproved: vi.fn(() => true),
  submitTrainingWorkspaceTask: vi.fn(),
}));

vi.mock('./exam-atlas/AtlasPracticePanel', () => ({
  default: ({ scope }) => <div data-testid="atlas-practice-scope">{scope}</div>,
}));

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(() => Promise.resolve({ data: {} })),
}));

describe('PracticePage personal question scope', () => {
  afterEach(() => vi.clearAllMocks());

  it('lets a KP-scoped learner choose public, personal, or combined questions', async () => {
    render(<PracticePage navigationContext={{
      trackId: 'TRACK_1',
      membershipId: 'MEM_1',
      kpId: 'KP_1',
      kpName: '阴阳学说',
    }} />);

    expect(await screen.findByTestId('atlas-practice-scope')).toHaveTextContent('public');
    fireEvent.click(screen.getByRole('button', { name: '我的题目' }));
    expect(screen.getByTestId('atlas-practice-scope')).toHaveTextContent('user');
    fireEvent.click(screen.getByRole('button', { name: '全部题目' }));
    expect(screen.getByTestId('atlas-practice-scope')).toHaveTextContent('all');
  });

  it('provides task, result, and evidence views for the mobile workspace', async () => {
    render(<PracticePage />);

    const viewTabs = await screen.findByRole('tablist', { name: '移动端训练视图' });
    expect(viewTabs).toBeInTheDocument();
    expect(within(viewTabs).getByRole('tab', { name: '任务' })).toHaveAttribute('aria-selected', 'true');

    fireEvent.click(within(viewTabs).getByRole('tab', { name: '结果' }));
    expect(within(viewTabs).getByRole('tab', { name: '结果' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('practice-result-panel')).toHaveAttribute('data-mobile-active', 'true');

    fireEvent.click(within(viewTabs).getByRole('tab', { name: '证据' }));
    expect(within(viewTabs).getByRole('tab', { name: '证据' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('practice-inspector')).toHaveAttribute('data-mobile-active', 'true');
  });

  it('selects a requested enabled training module from a workspace deep link', async () => {
    render(<PracticePage navigationContext={{ view: 'workspace', taskType: 'mistake_variation' }} />);

    const mistakeButton = await screen.findByRole('button', { name: /Mistake reinforcement/ });
    await waitFor(() => expect(mistakeButton).toHaveAttribute('aria-current', 'page'));
  });

  it('keeps an enabled fallback when a deep-linked training module is unavailable', async () => {
    loadTrainingWorkspaceModules.mockResolvedValueOnce({
      workspace: {
        modules: [{
          key: 'practice_grading',
          label: 'Practice grading',
          description: 'Practice grading',
          enabled: true,
          badge: 'Available',
        }, {
          key: 'case_training',
          label: 'Case training',
          description: 'Case training',
          enabled: false,
          badge: 'Unavailable',
        }],
      },
      error: '',
    });

    render(<PracticePage navigationContext={{ view: 'workspace', taskType: 'case_training' }} />);

    const practiceButton = await screen.findByRole('button', { name: /Practice grading/ });
    await waitFor(() => expect(practiceButton).toHaveAttribute('aria-current', 'page'));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('请求的训练模块暂未开放'));
  });
});
