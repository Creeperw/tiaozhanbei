import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import QualificationPaperPanel from './QualificationPaperPanel';

describe('QualificationPaperPanel', () => {
  beforeEach(() => {
    global.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, text: async () => JSON.stringify({
        exams: [{ exam_id: 'tcm', name: '中医执业医师资格考试' }],
        papers: [{ template_id: 'p1', exam_id: 'tcm', year: '2024', paper_type: '真题', title: '2024 年真题', question_count: 2 }],
      }) })
      .mockResolvedValueOnce({ ok: true, text: async () => JSON.stringify({
        attempt_id: 'attempt-1', answer_mode: 'practice', status: 'not_started', current_position: 1, marked_positions: [], items: [{
          question_id: 'q1', question_type: 'single_choice', question_content: '题目', options: [{ option_id: 'A', content: '甲' }], answer: '',
        }],
      }) });
  });

  it('filters qualification papers and creates a practice attempt', async () => {
    render(<QualificationPaperPanel enabled />);

    expect(await screen.findByRole('heading', { name: '五类资格考试套题' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '中医执业医师资格考试' }));
    expect(await screen.findByText('2024 年真题')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /2024 年真题/ }));
    fireEvent.click(screen.getByRole('button', { name: '练习模式' }));

    await screen.findByRole('button', { name: '退出考试' });
    expect(global.fetch).toHaveBeenLastCalledWith('/api/v1/qualification-papers/p1/attempts', expect.objectContaining({ method: 'POST' }));
  });
});
