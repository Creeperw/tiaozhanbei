import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import MistakeVariationPanel from './MistakeVariationPanel';
import { loadMistakes } from '../pageDataLoaders';

vi.mock('../pageDataLoaders', () => ({
  loadMistakes: vi.fn(),
  submitTrainingWorkspaceTask: vi.fn(),
}));

vi.mock('../utils/api', () => ({
  fetchJsonWithAuthFallback: vi.fn(),
}));

describe('MistakeVariationPanel', () => {
  afterEach(() => vi.clearAllMocks());

  it('loads every mistake page and keeps ineligible mistakes visible', async () => {
    loadMistakes
      .mockResolvedValueOnce({
        mistakes: {
          items: [{
            mistake_id: 1,
            status: 'active',
            stem: '四君子汤的功用是什么？',
            question_type: 'fill_blank',
            student_answer: '温中散寒',
            error_type: '知识混淆',
            score: 0,
            max_score: 100,
            variation_available: false,
            variation_reason: '该错题尚缺已审核题目版本或知识点证据',
          }],
          total: 2,
          has_more: true,
        },
        error: '',
      })
      .mockResolvedValueOnce({
        mistakes: {
          items: [{
            mistake_id: 2,
            status: 'active',
            stem: '四君子汤的君药是什么？',
            question_type: 'single_choice',
            student_answer: '白术',
            error_type: '角色判断错误',
            variation_available: true,
            variation_reason: '',
          }],
          total: 2,
          has_more: false,
        },
        error: '',
      });

    render(<MistakeVariationPanel enabled />);

    expect(await screen.findByText('四君子汤的功用是什么？')).toBeInTheDocument();
    expect(await screen.findByText('四君子汤的君药是什么？')).toBeInTheDocument();
    expect(screen.getByText(/当前筛选共 2 条/)).toBeInTheDocument();
    expect(loadMistakes).toHaveBeenNthCalledWith(1, expect.objectContaining({ offset: 0, limit: 100 }));
    expect(loadMistakes).toHaveBeenNthCalledWith(2, expect.objectContaining({ offset: 1, limit: 100 }));

    fireEvent.click(screen.getByText('四君子汤的功用是什么？'));
    expect(await screen.findByText('该错题尚缺已审核题目版本或知识点证据')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成变式' })).toBeDisabled();

    fireEvent.click(screen.getByText('四君子汤的君药是什么？'));
    await waitFor(() => expect(screen.getByRole('button', { name: '生成变式' })).toBeEnabled());
  });
});
