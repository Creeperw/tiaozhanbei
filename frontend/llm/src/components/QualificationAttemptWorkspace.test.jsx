import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import QualificationAttemptWorkspace from './QualificationAttemptWorkspace';

const makeAttempt = (count = 40) => ({
  attempt_id: 'attempt-1',
  answer_mode: 'practice',
  status: 'in_progress',
  current_position: 1,
  marked_positions: [],
  items: Array.from({ length: count }, (_, index) => ({
    question_id: `q${index + 1}`,
    question_type: 'single_choice',
    question_content: `题目 ${index + 1}`,
    options: [
      { option_id: 'A', content: '甲' },
      { option_id: 'B', content: '乙' },
    ],
    answer: '',
  })),
});

describe('QualificationAttemptWorkspace', () => {
  it('opens and closes a scrollable answer card', () => {
    render(<QualificationAttemptWorkspace attempt={makeAttempt()} onExit={vi.fn()} />);

    const toggle = screen.getByRole('button', { name: '展开答题卡' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);

    expect(screen.getAllByRole('button', { name: '收起答题卡' })).toHaveLength(2);
    expect(screen.getByTestId('answer-card-grid')).toHaveClass('overflow-y-auto');
    fireEvent.click(screen.getAllByRole('button', { name: '收起答题卡' }).at(-1));
    expect(screen.queryByRole('complementary', { name: '答题卡' })).not.toBeInTheDocument();
  });

  it('shows the submitted answer and explanation in the result view', async () => {
    globalThis.fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, text: async () => JSON.stringify(makeAttempt(1)) })
      .mockResolvedValueOnce({ ok: true, text: async () => JSON.stringify({
        attempt_id: 'attempt-1', status: 'submitted', score: 1, max_score: 1,
        items: [{ position: 1, question_id: 'q1', submitted_answer: 'A', standard_answer: ['A'], explanation: '题目解析', is_correct: true, answer_status: 'graded' }],
      }) });
    render(<QualificationAttemptWorkspace attempt={makeAttempt(1)} onExit={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '提交答案' }));

    expect(await screen.findByText('题目解析')).toBeInTheDocument();
    expect(screen.getByText('正确答案')).toBeInTheDocument();
  });

  it('saves the attempt before exiting', async () => {
    const onExit = vi.fn();
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      text: async () => JSON.stringify(makeAttempt(1)),
    });
    render(<QualificationAttemptWorkspace attempt={makeAttempt(1)} onExit={onExit} />);

    fireEvent.click(screen.getByRole('button', { name: '退出并保存' }));

    await waitFor(() => expect(onExit).toHaveBeenCalledOnce());
    expect(globalThis.fetch).toHaveBeenCalledWith(
      '/api/v1/qualification-paper-attempts/attempt-1/progress',
      expect.objectContaining({ method: 'PUT', body: expect.stringContaining('"paused":true') }),
    );
  });
});
