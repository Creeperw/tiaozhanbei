import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AtlasPracticePanel from './AtlasPracticePanel';

function jsonResponse(payload, ok = true) {
  return Promise.resolve({
    ok,
    status: ok ? 200 : 500,
    text: async () => JSON.stringify(payload),
  });
}

describe('AtlasPracticePanel', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('loads a KP-scoped public question and submits only learner-visible fields', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      requests.push({ url, options });
      if (url.includes('/practice/next') && url.includes('kp_id=kp-yinyang')) {
        return jsonResponse({
          available: true,
          kp_id: 'kp-yinyang',
          question: {
            question_id: 'question-1',
            question_type: 'short_answer',
            stem: '阴阳关系的基本特征是什么？',
            options: [],
            kp_ids: ['kp-yinyang'],
            difficulty: 2,
            request_id: 'request-1',
          },
        });
      }
      if (url.endsWith('/practice/grade')) {
        return jsonResponse({
          grading: { score: 88, is_correct: true, analysis: '回答覆盖核心关系。' },
          writeback: { status: 'applied' },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(<AtlasPracticePanel knowledgePoint={{ kpId: 'kp-yinyang', kpName: '阴阳学说' }} />);

    expect(await screen.findByText('阴阳关系的基本特征是什么？')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('你的答案'), { target: { value: '对立制约，互根互用。' } });
    fireEvent.click(screen.getByRole('button', { name: '提交并批改' }));

    expect(await screen.findByText(/得分 88/)).toBeInTheDocument();
    const gradeRequest = requests.find(({ url }) => url.endsWith('/practice/grade'));
    const body = JSON.parse(gradeRequest.options.body);
    expect(body).toMatchObject({
      question_id: 'question-1',
      stem: '阴阳关系的基本特征是什么？',
      student_answer: '对立制约，互根互用。',
      request_id: 'request-1',
    });
    expect(body).not.toHaveProperty('standard_answer');
    expect(body).not.toHaveProperty('rubric');
  });

  it('requests the selected personal question scope without changing the grade payload', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      requests.push({ url, options });
      if (url.includes('/training/practice/next')) {
        return jsonResponse({
          available: true,
          question: {
            question_id: 'user-question-1',
            question_type: 'short_answer',
            stem: '个人题目',
            options: [],
            kp_ids: ['kp-user'],
            difficulty: 2,
            request_id: 'user-request-1',
            source_scope: 'user',
          },
        });
      }
      if (url.endsWith('/practice/grade')) {
        return jsonResponse({ grading: { score: 90, is_correct: true, analysis: '完成' }, writeback: { status: 'applied' } });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(
      <AtlasPracticePanel
        knowledgePoint={{ kpId: 'kp-user', kpName: '个人知识点' }}
        scope="user"
      />,
    );

    expect(await screen.findByText('个人题目')).toBeInTheDocument();
    expect(requests[0].url).toContain('scope=user');
    expect(screen.getByText(/个人题目/)).toBeInTheDocument();
  });

  it('clears the previous answer and result when another knowledge point loads', async () => {
    let resolveSecond;
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.includes('kp-first')) {
        return jsonResponse({
          available: true,
          question: {
            question_id: 'question-first',
            question_type: 'short_answer',
            stem: '旧知识点题目',
            options: [],
            kp_ids: ['kp-first'],
            difficulty: 2,
            request_id: 'request-first',
          },
        });
      }
      if (url.endsWith('/practice/grade')) {
        return jsonResponse({
          grading: { score: 80, is_correct: true, analysis: '旧题批改结果' },
          writeback: { status: 'applied' },
        });
      }
      if (url.includes('kp-second')) {
        return new Promise((resolve) => { resolveSecond = resolve; });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    const { rerender } = render(
      <AtlasPracticePanel knowledgePoint={{ kpId: 'kp-first', kpName: '旧知识点' }} />,
    );
    await screen.findByText('旧知识点题目');
    fireEvent.change(screen.getByLabelText('你的答案'), { target: { value: '旧答案' } });
    fireEvent.click(screen.getByRole('button', { name: '提交并批改' }));
    await screen.findByText(/得分 80/);

    rerender(<AtlasPracticePanel knowledgePoint={{ kpId: 'kp-second', kpName: '新知识点' }} />);
    await waitFor(() => expect(screen.queryByText('旧知识点题目')).not.toBeInTheDocument());
    resolveSecond(await jsonResponse({
      available: true,
      question: {
        question_id: 'question-second',
        question_type: 'short_answer',
        stem: '新知识点题目',
        options: [],
        kp_ids: ['kp-second'],
        difficulty: 2,
        request_id: 'request-second',
      },
    }));

    await screen.findByText('新知识点题目');
    expect(screen.getByLabelText('你的答案')).toHaveValue('');
    expect(screen.queryByText(/得分 80/)).not.toBeInTheDocument();
  });

  it('shows a non-submittable empty state when no formal question exists', async () => {
    vi.stubGlobal('fetch', vi.fn(() => jsonResponse({
      available: false,
      kp_id: 'kp-empty',
      question: null,
    })));

    render(<AtlasPracticePanel knowledgePoint={{ kpId: 'kp-empty', kpName: '待补题知识点' }} />);

    expect(await screen.findByText('当前暂无可用客观题')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '提交并批改' })).not.toBeInTheDocument();
  });
});
