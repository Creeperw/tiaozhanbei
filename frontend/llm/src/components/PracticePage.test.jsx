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

vi.mock('./CaseTrainingPanel', () => ({
  default: () => <div data-testid="ai-patient-simulation-panel" />,
}));

vi.mock('./MistakeVariationPanel', () => ({
  default: () => <div data-testid="mistake-variation-panel" />,
}));

vi.mock('./PaperGenerationPanel', () => ({
  default: () => <div data-testid="paper-generation-panel" />,
}));

vi.mock('./SmartPaperPanel', () => ({
  default: () => <div data-testid="paper-generation-panel" />,
}));

vi.mock('./QualificationPaperPanel', () => ({
  default: () => <div data-testid="atlas-practice-scope" />,
}));

vi.mock('./QuestionWorkspacePage', () => ({
  default: () => <div data-testid="question-workspace-page" />,
}));

vi.mock('./KnowledgeCardLibrary', () => ({
  default: () => <div data-testid="knowledge-card-library" />,
}));

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(() => Promise.resolve({ data: {} })),
}));

describe('PracticePage training modules', () => {
  afterEach(() => vi.clearAllMocks());

  it('shows the training workshop overview before a learner selects a module', () => {
    render(<PracticePage />);

    expect(screen.getByRole('heading', { name: '训练工坊，实战精进' })).toBeInTheDocument();
    const trainingPath = screen.getByRole('region', { name: '训练路径' });
    const trainingButtons = within(trainingPath).getAllByRole('button');
    expect(trainingButtons.map((button) => button.querySelector('strong')?.textContent)).toEqual([
      '综合套题',
      '智能组卷',
      '专项训练',
      '专题训练',
      '模拟病患',
      '上传题库',
    ]);
    expect(screen.getByRole('button', { name: /错题库/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /错题变式/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '训练工坊模块' })).not.toBeInTheDocument();
  });

  it('opens mistake variations from the mistake library and returns to the workshop overview', async () => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('button', { name: /错题库/ }));

    expect(await screen.findByRole('heading', { name: '错题库' })).toBeInTheDocument();
    expect(screen.getByTestId('mistake-variation-panel')).toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '训练工坊模块' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '错题变式' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回训练工坊' }));
    expect(screen.getByRole('heading', { name: '训练工坊，实战精进' })).toBeInTheDocument();
  });

  it.each([
    ['综合套题', 'atlas-practice-scope'],
    ['智能组卷', 'paper-generation-panel'],
    ['专题训练', 'atlas-practice-scope'],
    ['模拟病患', 'ai-patient-simulation-panel'],
  ])('opens %s from the overview as a single page', async (title, panelTestId) => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('button', { name: new RegExp(title) }));

    expect(await screen.findByRole('heading', { name: title })).toBeInTheDocument();
    expect(screen.getByTestId(panelTestId)).toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '训练工坊模块' })).not.toBeInTheDocument();
  });

  it('opens specialized training in the existing case-answer mode', async () => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('button', { name: /专项训练/ }));

    expect(await screen.findByTestId('atlas-practice-scope')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '案例简答' })).toHaveAttribute('aria-selected', 'true');
  });

  it('opens a single training page without the shared module tabs', async () => {
    render(<PracticePage navigationContext={{
      trackId: 'TRACK_1',
      membershipId: 'MEM_1',
      kpId: 'KP_1',
      kpName: '阴阳学说',
      taskType: 'question_training',
    }} />);

    expect(await screen.findByTestId('atlas-practice-scope')).toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '训练工坊模块' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '题目训练' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: 'AI 病患模拟' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '错题变式' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '试卷生成' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '综合套题' })).toBeInTheDocument();
    expect(screen.queryByText('循证训练台')).not.toBeInTheDocument();
    expect(screen.queryByText('当前目标：')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Knowledge cards/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: '题目范围' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '正式题库' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '我的题目' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '全部题目' })).not.toBeInTheDocument();
  });

  it('provides task and result views without the legacy evidence inspector', async () => {
    render(<PracticePage navigationContext={{ taskType: 'question_training' }} />);

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

  it('keeps the qualification-paper workflow separate from legacy training results', async () => {
    render(<PracticePage navigationContext={{ taskType: 'question_training' }} />);

    expect(await screen.findByTestId('atlas-practice-scope')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '提交模拟答案' })).not.toBeInTheDocument();
  });

  it('opens the upload question bank as its own page', async () => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('button', { name: /上传题库/ }));

    expect(await screen.findByTestId('question-workspace-page')).toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '训练工坊模块' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '移动端训练视图' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('practice-result-panel')).not.toBeInTheDocument();
  });

  it.each([
    ['practice_grading', 'atlas-practice-scope'],
    ['case_training', 'ai-patient-simulation-panel'],
    ['knowledge_cards', 'knowledge-card-library'],
  ])('keeps the legacy %s training intent functional', async (taskType, panelTestId) => {
    render(<PracticePage navigationContext={{ taskType }} />);

    expect(await screen.findByTestId(panelTestId)).toBeInTheDocument();
    expect(screen.queryByText('此模块正在准备中，暂不支持提交任务。')).not.toBeInTheDocument();
  });

  it('opens the mistake variation module directly from its page intent', async () => {
    render(<PracticePage navigationContext={{ taskType: 'mistake_variation' }} />);

    expect(await screen.findByTestId('mistake-variation-panel')).toBeInTheDocument();
  });

  it('opens the AI patient simulation directly from its page intent', async () => {
    render(<PracticePage navigationContext={{ taskType: 'ai_patient_simulation' }} />);

    expect(await screen.findByTestId('ai-patient-simulation-panel')).toBeInTheDocument();
  });

  it('opens paper generation from the workshop navigation', async () => {
    render(<PracticePage navigationContext={{ taskType: 'paper_workspace' }} />);

    expect(await screen.findByTestId('paper-generation-panel')).toBeInTheDocument();
  });
});
