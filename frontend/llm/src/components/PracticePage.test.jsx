import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import PracticePage from './PracticePage';
import { fetchJsonWithAuthFallback } from '../utils/api';

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

vi.mock('./SimulatedPatientChat', () => ({
  default: () => <div data-testid="simulated-patient-chat" />,
}));

vi.mock('./MistakeRedoPanel', () => ({
  default: () => <div data-testid="mistake-redo-panel" />,
}));

vi.mock('./MistakeVariationPanel', () => ({
  default: () => <div data-testid="mistake-variation-panel" />,
}));

vi.mock('./TrainingHistoryPanel', () => ({
  default: () => <div data-testid="training-history-panel" />,
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
  default: ({ initialResource, directVideo, taskItemId }) => (
    <div
      data-testid="knowledge-card-library"
      data-resource={initialResource || ''}
      data-video-title={directVideo?.title || ''}
      data-task-item-id={taskItemId || ''}
    />
  ),
}));

vi.mock('./KnowledgePointTrainingHub', () => ({
  default: ({ initialKnowledgePoint, taskItemId }) => <div data-testid="knowledge-point-training-hub">
    {initialKnowledgePoint?.kpId || ''}:{initialKnowledgePoint?.kpName || ''}:{taskItemId}
  </div>,
}));

vi.mock('./QuestionFavoritesPanel', () => ({
  default: () => <div data-testid="question-favorites-panel" />,
}));

vi.mock('./StudyNotesPanel', () => ({
  default: () => <div data-testid="study-notes-panel" />,
}));

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(() => Promise.resolve({ data: {} })),
}));

describe('PracticePage training modules', () => {
  afterEach(() => vi.clearAllMocks());

  it('shows the training workshop overview before a learner selects a module', () => {
    render(<PracticePage />);

    expect(screen.getByRole('heading', { name: '训练工坊' })).toBeInTheDocument();
    expect(screen.getByText('准备开始今天的训练')).toBeInTheDocument();
    const trainingModules = screen.getByRole('region', { name: '训练模块' });
    const trainingButtons = within(trainingModules).getAllByRole('button')
      .filter((button) => button.querySelector('strong'));
    expect(trainingButtons.map((button) => button.querySelector('strong')?.textContent)).toEqual([
      '专项训练',
      '专题训练',
      '智能组卷',
      '综合套题',
      '模拟病患',
      '错题重做',
    ]);
    expect(screen.getByRole('button', { name: /历史记录/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /收藏夹/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /笔记本/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /错题变式/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '训练工坊模块' })).not.toBeInTheDocument();
  });

  it('keeps the local workshop summary under training modules and upload as the last learning tool', () => {
    render(<PracticePage />);

    const trainingModules = screen.getByRole('region', { name: '训练模块' });
    expect(within(trainingModules).getByRole('region', { name: '学习概览' })).toBeInTheDocument();

    const learningTools = screen.getByRole('complementary', { name: '学习工具' });
    expect(
      within(learningTools).getAllByRole('button').map((button) => button.querySelector('strong')?.textContent),
    ).toEqual(['\u5386\u53f2\u8bb0\u5f55', '\u6536\u85cf\u5939', '\u7b14\u8bb0\u672c', '\u4e0a\u4f20\u8d44\u6e90']);
    expect(within(learningTools).getByText('\u652f\u6301 PDF / \u56fe\u7247 / Word / Excel / Markdown / TXT \u00b7 \u667a\u80fd\u89e3\u6790')).toBeInTheDocument();
  });

  it('renders the local overview statistics contract without replacing main workshop modules', () => {
    render(<PracticePage overviewStats={{
      streakDays: 8,
      todayAccuracy: 76,
      windowPracticeCount: 6,
      todayGoal: 20,
      averageAccuracy: 82,
      totalHours: 46,
      totalQuestions: 386,
    }} />);

    const learningOverview = screen.getByRole('region', { name: '学习概览' });
    expect(screen.getByText('8 天')).toBeInTheDocument();
    expect(screen.getByText('76%')).toBeInTheDocument();
    expect(within(learningOverview).getByText('6 题')).toBeInTheDocument();
    expect(within(learningOverview).getByText('82%')).toBeInTheDocument();
    expect(within(learningOverview).getByText('46 小时')).toBeInTheDocument();
    expect(within(learningOverview).getByText('累计练习 386 题')).toBeInTheDocument();
  });

  it('loads audited overview statistics, recent activity, and check-in status from main APIs', async () => {
    fetchJsonWithAuthFallback.mockImplementation(({ paths }) => {
      const path = paths[0];
      if (path.startsWith('/v1/learning-statistics/overview')) {
        return Promise.resolve({
          data: {
            lifetime: {
              questions_completed: 42,
              focus_minutes: 125,
            },
            current_window: {
              questions_completed: 7,
              score_rate: 0.84,
            },
          },
        });
      }
      if (path.startsWith('/v1/learning-activity/summary')) {
        return Promise.resolve({
          data: {
            calculated_at: '2026-07-26T20:00:00+08:00',
            recent_activities: [
              {
                activity_type: 'daily_checkin',
                resource_type: 'checkin',
                completion_status: 'completed',
                score: 1,
                created_at: '2026-07-26T19:45:00+08:00',
              },
              {
                activity_type: 'question_attempt',
                resource_type: 'question_favorites',
                completion_status: 'needs_review',
                score: 0.75,
                created_at: '2026-07-26T19:30:00+08:00',
              },
            ],
          },
        });
      }
      if (path === '/v1/checkin') {
        return Promise.resolve({ data: { streak: 5 } });
      }
      return Promise.resolve({ data: {} });
    });

    render(<PracticePage />);

    expect(await screen.findByText('5 天')).toBeInTheDocument();
    expect(await screen.findByText('75%')).toBeInTheDocument();
    const learningOverview = screen.getByRole('region', { name: '学习概览' });
    expect(within(learningOverview).getByText('近 30 天练习')).toBeInTheDocument();
    expect(within(learningOverview).getByText('7 题')).toBeInTheDocument();
    expect(within(learningOverview).getByText('84%')).toBeInTheDocument();
    expect(within(learningOverview).getByText('2.1 小时')).toBeInTheDocument();
    expect(within(learningOverview).getByText('累计练习 42 题')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /继续上次练习/ }));
    expect(await screen.findByTestId('question-favorites-panel')).toBeInTheDocument();
  });

  it('continues the main-backed module selected by the local overview contract', async () => {
    render(<PracticePage overviewStats={{ recentTaskKey: 'question_favorites' }} />);

    fireEvent.click(screen.getByRole('button', { name: /继续上次练习/ }));

    expect(await screen.findByTestId('question-favorites-panel')).toBeInTheDocument();
  });

  it('opens training history from the workshop tools and returns to the workshop overview', async () => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('button', { name: /历史记录/ }));

    expect(await screen.findByRole('heading', { name: '历史记录' })).toBeInTheDocument();
    expect(screen.getByTestId('training-history-panel')).toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '训练工坊模块' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tab', { name: '错题变式' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '返回训练工坊' }));
    expect(screen.getByRole('heading', { name: '训练工坊' })).toBeInTheDocument();
  });

  it.each([
    ['综合套题', 'atlas-practice-scope'],
    ['智能组卷', 'paper-generation-panel'],
    ['专题训练', 'knowledge-point-training-hub'],
    ['模拟病患', 'simulated-patient-chat'],
  ])('opens %s from the overview as a single page', async (title, panelTestId) => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('button', { name: new RegExp(title) }));

    if (!['模拟病患', '智能组卷'].includes(title)) {
      expect(await screen.findByRole('heading', { name: title })).toBeInTheDocument();
    }
    expect(screen.getByTestId(panelTestId)).toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '训练工坊模块' })).not.toBeInTheDocument();
  });

  it.each([
    ['收藏夹', 'question-favorites-panel'],
    ['笔记本', 'study-notes-panel'],
  ])('opens the %s personal library', async (title, panelTestId) => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('button', { name: new RegExp(title) }));

    expect(await screen.findByRole('heading', { name: title })).toBeInTheDocument();
    expect(screen.getByTestId(panelTestId)).toBeInTheDocument();
  });

  it('opens specialized training in the existing case-answer mode', async () => {
    render(<PracticePage />);

    fireEvent.click(screen.getByRole('button', { name: /专项训练/ }));

    expect(await screen.findByTestId('atlas-practice-scope')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '案例简答' })).toHaveAttribute('aria-selected', 'true');
  });

  it('returns a deep-linked daily task to its caller instead of the generic workshop overview', () => {
    const onNavigate = vi.fn();
    render(
      <PracticePage
        navigationContext={{
          view: 'workspace',
          taskType: 'question_training',
          returnTo: { page: 'qualification-route', params: {} },
        }}
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '返回今日学习' }));
    expect(onNavigate).toHaveBeenCalledWith({ page: 'qualification-route', params: {} });
  });

  it('returns avatar libraries to the exact learning path that opened them', () => {
    const onNavigate = vi.fn();
    const returnTo = {
      page: 'learning-path',
      params: { targetId: 'target-a', examTrackId: 'track-a' },
    };
    render(
      <PracticePage
        navigationContext={{
          view: 'workspace',
          taskType: 'question_favorites',
          returnTo,
        }}
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '返回学习路径' }));
    expect(onNavigate).toHaveBeenCalledWith(returnTo);
  });

  it('passes a direct daily video into the knowledge-card player', () => {
    render(
      <PracticePage
        navigationContext={{
          view: 'workspace',
          taskType: 'knowledge_cards',
          taskItemId: 'ITEM_VIDEO',
          resourceView: 'videos',
          directVideo: { title: '章节精讲', url: 'https://example.test/video.mp4' },
        }}
      />,
    );

    expect(screen.getByTestId('knowledge-card-library')).toHaveAttribute('data-resource', 'videos');
    expect(screen.getByTestId('knowledge-card-library')).toHaveAttribute('data-video-title', '章节精讲');
    expect(screen.getByTestId('knowledge-card-library')).toHaveAttribute('data-task-item-id', 'ITEM_VIDEO');
  });

  it('binds a daily knowledge-practice item to its formal knowledge point', async () => {
    render(<PracticePage navigationContext={{
      taskType: 'topic_training',
      taskItemId: 'ITEM_1',
      kpId: 'KP_SIJUNZI',
      kpName: '四君子汤',
    }} />);

    expect(await screen.findByTestId('knowledge-point-training-hub')).toHaveTextContent('KP_SIJUNZI:四君子汤:ITEM_1');
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

  it('keeps feedback inside the question workflow without a persistent training artifact panel', async () => {
    render(<PracticePage navigationContext={{ taskType: 'question_training' }} />);

    expect(await screen.findByTestId('atlas-practice-scope')).toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '移动端训练视图' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('practice-result-panel')).not.toBeInTheDocument();
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

    fireEvent.click(screen.getByRole('button', { name: /\u4e0a\u4f20\u8d44\u6e90/ }));
    fireEvent.click(screen.getByRole('button', { name: /\u4e0a\u4f20\u9898\u5e93/ }));

    expect(await screen.findByTestId('question-workspace-page')).toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '训练工坊模块' })).not.toBeInTheDocument();
    expect(screen.queryByRole('tablist', { name: '移动端训练视图' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('practice-result-panel')).not.toBeInTheDocument();
  });

  it.each([
    ['practice_grading', 'atlas-practice-scope'],
    ['case_training', 'simulated-patient-chat'],
    ['knowledge_cards', 'knowledge-card-library'],
  ])('keeps the legacy %s training intent functional', async (taskType, panelTestId) => {
    render(<PracticePage navigationContext={{ taskType }} />);

    expect(await screen.findByTestId(panelTestId)).toBeInTheDocument();
    expect(screen.queryByText('此模块正在准备中，暂不支持提交任务。')).not.toBeInTheDocument();
  });

  it('opens the training history module directly from its page intent', async () => {
    render(<PracticePage navigationContext={{ taskType: 'training_history' }} />);

    expect(await screen.findByTestId('training-history-panel')).toBeInTheDocument();
  });

  it('opens the AI patient simulation directly from its page intent', async () => {
    render(<PracticePage navigationContext={{ taskType: 'ai_patient_simulation' }} />);

    expect(await screen.findByTestId('simulated-patient-chat')).toBeInTheDocument();
  });

  it('opens paper generation from the workshop navigation', async () => {
    render(<PracticePage navigationContext={{ taskType: 'paper_workspace' }} />);

    expect(await screen.findByTestId('paper-generation-panel')).toBeInTheDocument();
  });
});
