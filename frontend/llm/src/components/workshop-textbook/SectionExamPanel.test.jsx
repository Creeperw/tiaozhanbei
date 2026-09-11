import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SectionExamPanel from './SectionExamPanel';
import { loadSectionQuestions, submitSectionExamAnswer } from './textbookChapterApi';

vi.mock('./textbookChapterApi', () => ({
  loadSectionQuestions: vi.fn(),
  submitSectionExamAnswer: vi.fn(),
}));

function verdict(overrides = {}) {
  return {
    question_id: 'QUESTION_1',
    question_type: 'single_choice',
    is_correct: true,
    score: 100,
    reference_answer: 'A',
    reference_options: ['A'],
    analysis: '阴阳对立制约是基础关系。',
    recorded: true,
    ...overrides,
  };
}

describe('SectionExamPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows the server verdict and reference answer, then hides them again', async () => {
    loadSectionQuestions.mockResolvedValue({
      items: [{
        question_id: 'QUESTION_1',
        question_type: 'single_choice',
        stem: '阴阳学说的核心是？',
        options: ['对立制约', '脏腑辨证'],
      }],
    });
    submitSectionExamAnswer.mockResolvedValue(verdict());
    render(<SectionExamPanel sectionName="第一节 基础概念" kpIds={['KP_1']} onBack={vi.fn()} />);

    fireEvent.click(await screen.findByRole('radio', { name: /对立制约/ }));
    fireEvent.click(screen.getByRole('button', { name: '查看答案' }));

    expect(await screen.findByText('回答正确')).toBeInTheDocument();
    expect(screen.getByText('阴阳对立制约是基础关系。')).toBeInTheDocument();
    expect(screen.getByText('参考答案').closest('.section-exam-answer')).toHaveTextContent('A');
    expect(submitSectionExamAnswer).toHaveBeenCalledWith(
      expect.objectContaining({ question_id: 'QUESTION_1', answer: 'A' }),
    );

    fireEvent.click(screen.getByRole('button', { name: '隐藏答案' }));
    expect(screen.queryByText('回答正确')).not.toBeInTheDocument();
  });

  it('renders a reference answer stored as a JSON array as plain text', async () => {
    loadSectionQuestions.mockResolvedValue({
      items: [{
        question_id: 'QUESTION_1',
        question_type: 'true_false',
        stem: '中医理论体系形成于先秦、秦、汉时期。',
      }],
    });
    submitSectionExamAnswer.mockResolvedValue(verdict({
      question_type: 'true_false',
      reference_answer: '√',
      reference_options: ['正确'],
    }));
    render(<SectionExamPanel sectionName="第一节" kpIds={['KP_1']} onBack={vi.fn()} />);

    fireEvent.click(await screen.findByRole('radio', { name: '正确' }));
    fireEvent.click(screen.getByRole('button', { name: '查看答案' }));

    expect(await screen.findByText('√')).toBeInTheDocument();
    expect(screen.queryByText('["√"]')).not.toBeInTheDocument();
    expect(screen.getByText('回答正确')).toBeInTheDocument();
  });

  it('does not claim a verdict for an ungraded subjective answer', async () => {
    loadSectionQuestions.mockResolvedValue({
      items: [{
        question_id: 'QUESTION_1',
        question_type: 'short_answer',
        stem: '请指出与气的生成关系最密切的两个脏，并说明理由。',
      }],
    });
    submitSectionExamAnswer.mockResolvedValue(verdict({
      question_type: 'short_answer',
      is_correct: null,
      score: null,
      reference_answer: '与气的生成关系最密切的脏是肺和脾。',
      reference_options: [],
    }));
    render(<SectionExamPanel sectionName="第一节" kpIds={['KP_1']} onBack={vi.fn()} />);

    fireEvent.change(await screen.findByPlaceholderText('请在此输入你的答案…'), {
      target: { value: '肺和脾' },
    });
    fireEvent.click(screen.getByRole('button', { name: '查看答案' }));

    expect(await screen.findByText('主观题不计分，请对照参考答案自评')).toBeInTheDocument();
    expect(screen.queryByText('回答错误')).not.toBeInTheDocument();
  });

  it('keeps the answer hidden and reports the failure when recording fails', async () => {
    loadSectionQuestions.mockResolvedValue({
      items: [{
        question_id: 'QUESTION_1',
        question_type: 'single_choice',
        stem: '阴阳学说的核心是？',
        options: ['对立制约', '脏腑辨证'],
      }],
    });
    submitSectionExamAnswer.mockRejectedValue(new Error('作答记录失败 (503)'));
    render(<SectionExamPanel sectionName="第一节" kpIds={['KP_1']} onBack={vi.fn()} />);

    fireEvent.click(await screen.findByRole('radio', { name: /对立制约/ }));
    fireEvent.click(screen.getByRole('button', { name: '查看答案' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('作答记录失败 (503)');
    expect(screen.queryByText('回答错误')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看答案' })).toBeEnabled();
  });

  it('replays the same request id when the learner re-reveals an unchanged answer', async () => {
    loadSectionQuestions.mockResolvedValue({
      items: [{
        question_id: 'QUESTION_1',
        question_type: 'single_choice',
        stem: '阴阳学说的核心是？',
        options: ['对立制约', '脏腑辨证'],
      }],
    });
    submitSectionExamAnswer.mockResolvedValue(verdict());
    render(<SectionExamPanel sectionName="第一节" kpIds={['KP_1']} onBack={vi.fn()} />);

    fireEvent.click(await screen.findByRole('radio', { name: /对立制约/ }));
    fireEvent.click(screen.getByRole('button', { name: '查看答案' }));
    await screen.findByText('回答正确');
    fireEvent.click(screen.getByRole('button', { name: '隐藏答案' }));
    fireEvent.click(screen.getByRole('button', { name: '查看答案' }));
    await screen.findByText('回答正确');

    const firstRequest = submitSectionExamAnswer.mock.calls[0][0].request_id;
    const secondRequest = submitSectionExamAnswer.mock.calls[1][0].request_id;
    expect(firstRequest).toBeTruthy();
    expect(secondRequest).toBe(firstRequest);
  });

  it('ignores a stale question response after the learner switches sections', async () => {
    let resolveFirst;
    loadSectionQuestions
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirst = resolve; }))
      .mockResolvedValueOnce({
        items: [{
          question_id: 'QUESTION_2',
          question_type: 'single_choice',
          stem: '新小节题目',
          options: ['选项'],
        }],
      });
    const { rerender } = render(<SectionExamPanel sectionName="第一节" kpIds={['KP_1']} onBack={vi.fn()} />);

    await waitFor(() => expect(loadSectionQuestions).toHaveBeenCalledTimes(1));
    rerender(<SectionExamPanel sectionName="第二节" kpIds={['KP_2']} onBack={vi.fn()} />);
    await screen.findByText('新小节题目');
    resolveFirst({
      items: [{
        question_id: 'QUESTION_1',
        question_type: 'single_choice',
        stem: '旧小节题目',
        options: ['选项'],
      }],
    });

    await waitFor(() => expect(screen.queryByText('旧小节题目')).not.toBeInTheDocument());
    expect(screen.getByText('新小节题目')).toBeInTheDocument();
  });
});
