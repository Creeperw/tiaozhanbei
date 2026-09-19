import { beforeEach, describe, expect, it, vi } from 'vitest';

import { startSmartPaperRun } from './smartPaperRunClient';
import { getWorkflowRun, streamWorkflowTurn } from './workflowChatClient';

vi.mock('./utils/api', () => ({
  MAIN_API_BASE: 'http://127.0.0.1:7860',
  fetchWithAuth: vi.fn(),
  readJsonResponse: vi.fn(),
}));

vi.mock('./workflowChatClient', () => ({
  cancelWorkflowRun: vi.fn(),
  createWorkflowRunId: vi.fn(() => 'RUN_TEST_1'),
  getWorkflowRun: vi.fn(),
  streamWorkflowTurn: vi.fn(),
}));

function completedOutcome(paperId) {
  return {
    status: 'completed',
    result: {
      status: 'success',
      task_type: 'paper_generation',
      ui_actions: [{ destination: 'workshop.paper', params: { paper_id: paperId } }],
    },
  };
}

describe('startSmartPaperRun', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    window.localStorage.clear();
    getWorkflowRun.mockResolvedValue(null);
  });

  it('sends the explanation requirement as a structured constraint', async () => {
    streamWorkflowTurn.mockResolvedValue(completedOutcome('PAPER_1'));

    await startSmartPaperRun({
      topic: '太阳病篇',
      distribution: { short_answer: 5 },
      requiresExplanation: true,
    });

    const payload = streamWorkflowTurn.mock.calls[0][0];
    expect(payload.examConstraints).toMatchObject({
      requires_explanation: true,
      question_type_distribution: { short_answer: 5 },
      question_count: 5,
    });
    // 主题正文只承载范围，解析要求不得被拼进主题文本。
    expect(payload.examConstraints.topic).toBe('太阳病篇');
    expect(payload.answer).not.toContain('解析');
  });

  it('defaults the explanation requirement to false', async () => {
    streamWorkflowTurn.mockResolvedValue(completedOutcome('PAPER_2'));

    await startSmartPaperRun({
      topic: '四君子汤组成',
      distribution: { single_choice: 3 },
    });

    expect(streamWorkflowTurn.mock.calls[0][0].examConstraints).toMatchObject({
      requires_explanation: false,
    });
  });
});
