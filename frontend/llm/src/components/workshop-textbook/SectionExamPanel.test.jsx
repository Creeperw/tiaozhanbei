import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import SectionExamPanel from './SectionExamPanel';
import { loadSectionQuestions } from './textbookChapterApi';

vi.mock('./textbookChapterApi', () => ({
  loadSectionQuestions: vi.fn(),
}));

describe('SectionExamPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('loads a section question and lets the learner reveal and hide its answer', async () => {
    loadSectionQuestions.mockResolvedValue({
      items: [{
        question_id: 'QUESTION_1',
        question_type: 'single_choice',
        stem: '阴阳学说的核心是？',
        options: ['对立制约', '脏腑辨证'],
        reference_answer: 'A',
        analysis: '阴阳对立制约是基础关系。',
      }],
    });
    render(<SectionExamPanel sectionName="第一节 基础概念" kpIds={['KP_1']} onBack={vi.fn()} />);

    fireEvent.click(await screen.findByRole('radio', { name: /对立制约/ }));
    fireEvent.click(screen.getByRole('button', { name: '查看答案' }));

    expect(screen.getByText('回答正确')).toBeInTheDocument();
    expect(screen.getByText('阴阳对立制约是基础关系。')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '隐藏答案' }));
    expect(screen.queryByText('回答正确')).not.toBeInTheDocument();
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
          reference_answer: 'A',
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
        reference_answer: 'A',
      }],
    });

    await waitFor(() => expect(screen.queryByText('旧小节题目')).not.toBeInTheDocument());
    expect(screen.getByText('新小节题目')).toBeInTheDocument();
  });
});
