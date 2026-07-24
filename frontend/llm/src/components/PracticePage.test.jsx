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
  loadMistakes: vi.fn(() => Promise.resolve({ mistakes: { items: [], total: 0 }, error: '' })),
  loadTrainingWorkspaceModules: vi.fn(() => Promise.resolve({
    workspace: {
      modules: [{
        key: 'question_training',
        label: 'Question training',
        description: 'Question training',
        enabled: true,
        badge: 'Available',
      }, {
        key: 'knowledge_cards',
        label: 'Knowledge cards',
        description: 'Knowledge cards',
        enabled: true,
        badge: 'Available',
      }, {
        key: 'paper_workspace',
        label: 'Paper workspace',
        description: 'Paper workspace',
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
  default: ({ scope, onResult }) => <div data-testid="atlas-practice-scope">
    {scope}
    <button type="button" onClick={() => onResult?.({
      grading: {
        score: 100,
        is_correct: true,
        analysis: '本次回答正确。',
        question_explanation: '四君子汤以人参为君，配伍白术、茯苓和炙甘草，共奏益气健脾之功。',
        explanation_source: 'generated_on_first_attempt',
      },
      writeback: { status: 'applied' },
    }, { question_id: 'Q_1', question_type: 'single_choice' })}>提交模拟答案</button>
  </div>,
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
    expect(screen.getByRole('tab', { name: '客观题' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '案例简答' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: 'AI 病患模拟' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '错题变式' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '我的题目' }));
    expect(screen.getByTestId('atlas-practice-scope')).toHaveTextContent('user');
    fireEvent.click(screen.getByRole('button', { name: '全部题目' }));
    expect(screen.getByTestId('atlas-practice-scope')).toHaveTextContent('all');
  });

  it('provides task and result views without the legacy evidence inspector', async () => {
    render(<PracticePage />);

    const viewTabs = await screen.findByRole('tablist', { name: '移动端训练视图' });
    expect(viewTabs).toBeInTheDocument();
    expect(within(viewTabs).getByRole('tab', { name: '任务' })).toHaveAttribute('aria-selected', 'true');

    fireEvent.click(within(viewTabs).getByRole('tab', { name: '结果' }));
    expect(within(viewTabs).getByRole('tab', { name: '结果' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('practice-result-panel')).toHaveAttribute('data-mobile-active', 'true');
    expect(within(viewTabs).queryByRole('tab', { name: '证据' })).not.toBeInTheDocument();
    expect(screen.queryByText('证据检查器')).not.toBeInTheDocument();
    expect(screen.queryByTestId('practice-inspector')).not.toBeInTheDocument();
  });

  it('shows the persisted question explanation separately from grading analysis', async () => {
    render(<PracticePage />);

    fireEvent.click(await screen.findByRole('button', { name: '提交模拟答案' }));

    expect(await screen.findByText('题目解析')).toBeInTheDocument();
    expect(screen.getByText(/共奏益气健脾之功/)).toBeInTheDocument();
    expect(screen.getByText(/首次作答自动生成并保存/)).toBeInTheDocument();
  });

  it('selects a requested enabled training module from a workspace deep link', async () => {
    render(<PracticePage navigationContext={{ view: 'workspace', taskType: 'mistake_variation' }} />);

    const questionButton = await screen.findByRole('button', { name: /Question training/ });
    await waitFor(() => expect(questionButton).toHaveAttribute('aria-current', 'page'));
  });

  it('keeps an enabled fallback when a deep-linked training module is unavailable', async () => {
    loadTrainingWorkspaceModules.mockResolvedValueOnce({
      workspace: {
        modules: [{
          key: 'question_training',
          label: 'Question training',
          description: 'Question training',
          enabled: true,
          badge: 'Available',
        }, {
          key: 'knowledge_cards',
          label: 'Knowledge cards',
          description: 'Knowledge cards',
          enabled: false,
          badge: 'Unavailable',
        }],
      },
      error: '',
    });

    render(<PracticePage navigationContext={{ view: 'workspace', taskType: 'knowledge_card_generation' }} />);

    const practiceButton = await screen.findByRole('button', { name: /Question training/ });
    await waitFor(() => expect(practiceButton).toHaveAttribute('aria-current', 'page'));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('请求的训练模块暂未开放'));
  });
});
