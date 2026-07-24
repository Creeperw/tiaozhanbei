import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PracticePage from './PracticePage';

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
  default: ({ scope }) => <div data-testid="atlas-practice-scope">{scope}</div>,
}));

vi.mock('./CaseTrainingPanel', () => ({
  default: () => <div data-testid="ai-patient-simulation-panel" />,
}));

vi.mock('./MistakeVariationPanel', () => ({
  default: () => <div data-testid="mistake-variation-panel" />,
}));

vi.mock('./PaperGenerationPanel', () => ({
  default: () => <div data-testid="paper-generation-panel" />,
}));

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(() => Promise.resolve({ data: {} })),
}));

describe('PracticePage training modules', () => {
  afterEach(() => vi.clearAllMocks());

  it('removes retired workshop header, knowledge cards, and question source controls', async () => {
    render(<PracticePage navigationContext={{
      trackId: 'TRACK_1',
      membershipId: 'MEM_1',
      kpId: 'KP_1',
      kpName: '阴阳学说',
    }} />);

    expect(await screen.findByTestId('atlas-practice-scope')).toHaveTextContent('public');
    expect(screen.getByRole('tablist', { name: '训练工坊模块' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '题目训练' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'AI 病患模拟' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '错题变式' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '试卷生成' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '客观题' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '案例简答' })).toBeInTheDocument();
    expect(screen.queryByText('循证训练台')).not.toBeInTheDocument();
    expect(screen.queryByText('当前目标：')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Knowledge cards/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: '题目范围' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '正式题库' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '我的题目' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '全部题目' })).not.toBeInTheDocument();
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

  it('opens the mistake variation module directly from its page intent', async () => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('tab', { name: '错题变式' }));
    expect(await screen.findByTestId('mistake-variation-panel')).toBeInTheDocument();
  });

  it('opens the AI patient simulation directly from its page intent', async () => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('tab', { name: 'AI 病患模拟' }));
    expect(await screen.findByTestId('ai-patient-simulation-panel')).toBeInTheDocument();
  });

  it('opens paper generation from the workshop navigation', async () => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('tab', { name: '试卷生成' }));
    expect(await screen.findByTestId('paper-generation-panel')).toBeInTheDocument();
  });
});
