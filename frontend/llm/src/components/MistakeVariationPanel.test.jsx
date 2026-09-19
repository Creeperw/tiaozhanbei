import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import MistakeVariationPanel from './MistakeVariationPanel';
import { loadMistakes, submitMistakeAnswerContext, submitTrainingWorkspaceTask } from '../pageDataLoaders';

vi.mock('../pageDataLoaders', () => ({
  loadMistakes: vi.fn(),
  submitMistakeAnswerContext: vi.fn(),
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
            answer_context_required: false,
            answer_context_completed: true,
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

  it('asks about an objective answer context before enabling variations', async () => {
    const pending = {
      mistake_id: 3,
      status: 'active',
      stem: '风寒感冒宜采用哪种治法？',
      question_type: 'single_choice',
      student_answer: '清热解毒',
      error_type: '待结合作答情况分析',
      answer_context_required: true,
      answer_context_completed: false,
      variation_available: false,
      variation_reason: '请先补充当时的作答把握和判断过程',
    };
    loadMistakes.mockResolvedValue({ mistakes: { items: [pending], total: 1, has_more: false }, error: '' });
    submitMistakeAnswerContext.mockResolvedValue({
      mistake: { ...pending, error_type: '审题遗漏', answer_context_completed: true, variation_available: true, variation_reason: '' },
      error: '',
    });
    render(<MistakeVariationPanel enabled />);
    fireEvent.click(await screen.findByText('风寒感冒宜采用哪种治法？'));
    expect(screen.getByRole('region', { name: '错题作答情况调研' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('当时的把握'), { target: { value: '犹豫后作答' } });
    fireEvent.change(screen.getByLabelText('你认为更接近的原因'), { target: { value: '审题遗漏' } });
    fireEvent.click(screen.getByRole('button', { name: '保存作答情况' }));
    await waitFor(() => expect(submitMistakeAnswerContext).toHaveBeenCalledWith(expect.objectContaining({
      mistakeId: 3,
      answerState: '犹豫后作答',
      reason: '审题遗漏',
    })));
    await waitFor(() => expect(screen.getByRole('button', { name: '生成变式' })).toBeEnabled());
  });

  it('renders choice options and submits the option label instead of the option text', async () => {
    const mistake = {
      mistake_id: 5,
      status: 'active',
      stem: '下列符合阴阳对立制约关系的是',
      question_type: 'single_choice',
      student_answer: '寒极生热',
      error_type: '概念混淆',
      score: 0,
      max_score: 100,
      answer_context_required: false,
      answer_context_completed: true,
      variation_available: true,
      variation_reason: '',
    };
    loadMistakes.mockResolvedValue({ mistakes: { items: [mistake], total: 1, has_more: false }, error: '' });
    submitTrainingWorkspaceTask
      .mockResolvedValueOnce({
        taskResult: {
          artifact: {
            content: {
              questions: [{
                question_version_id: 'QV_VARIATION_1',
                question_id: 'variation:1',
                stem: '患者畏寒肢冷、下利清谷，治当温中散寒，体现的阴阳关系是',
                question_type: 'single_choice',
                options: ['A. 阴阳互根', 'B. 阴阳对立制约', 'C. 阴阳消长'],
                kp_ids: ['KP_1'],
                kp_names: ['阴阳学说'],
                source_kind: 'variation',
              }],
            },
          },
        },
        error: '',
      })
      .mockResolvedValueOnce({ taskResult: { artifact: { content: { grading: { grading: { score: 100, max_score: 100 } } } } }, error: '' });

    render(<MistakeVariationPanel enabled />);
    fireEvent.click(await screen.findByText('下列符合阴阳对立制约关系的是'));
    fireEvent.click(screen.getByRole('button', { name: '生成变式' }));

    // 选项必须渲染成可选按钮，而不是让人自由输入。
    expect(await screen.findByText('阴阳对立制约')).toBeInTheDocument();
    expect(screen.queryByLabelText('你的答案')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: /阴阳对立制约/ }));
    fireEvent.click(screen.getByRole('button', { name: '提交批改' }));

    await waitFor(() => expect(submitTrainingWorkspaceTask).toHaveBeenLastCalledWith(expect.objectContaining({
      task: expect.objectContaining({
        inputs: expect.objectContaining({
          action: 'answer',
          question_version_id: 'QV_VARIATION_1',
          student_answer: 'B',
        }),
      }),
    })));
  });

  it('falls back to free text when a variation carries no options', async () => {
    const mistake = {
      mistake_id: 6,
      status: 'active',
      stem: '请论述四君子汤的配伍意义',
      question_type: 'short_answer',
      student_answer: '',
      error_type: '待结合作答情况分析',
      answer_context_required: false,
      answer_context_completed: true,
      variation_available: true,
      variation_reason: '',
    };
    loadMistakes.mockResolvedValue({ mistakes: { items: [mistake], total: 1, has_more: false }, error: '' });
    submitTrainingWorkspaceTask.mockResolvedValueOnce({
      taskResult: {
        artifact: {
          content: {
            questions: [{
              question_version_id: 'QV_VARIATION_2',
              stem: '请论述四君子汤中白术的配伍意义',
              question_type: 'short_answer',
              options: [],
              kp_names: ['四君子汤'],
              source_kind: 'variation',
            }],
          },
        },
      },
      error: '',
    });

    render(<MistakeVariationPanel enabled />);
    fireEvent.click(await screen.findByText('请论述四君子汤的配伍意义'));
    fireEvent.click(screen.getByRole('button', { name: '生成变式' }));

    expect(await screen.findByLabelText('你的答案')).toBeInTheDocument();
  });
});
