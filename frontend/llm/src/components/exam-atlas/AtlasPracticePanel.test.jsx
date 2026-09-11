import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import AtlasPracticePanel from './AtlasPracticePanel';

function jsonResponse(payload, ok = true) {
  // Legacy fixtures also describe a one-question list; grade responses stay unchanged.
  if ('available' in payload) {
    const questions = payload.question ? [payload.question] : [];
    payload = { ...payload, questions, total: questions.length };
  }
  return Promise.resolve({
    ok,
    status: ok ? 200 : 500,
    text: async () => JSON.stringify(payload),
  });
}

describe('AtlasPracticePanel', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('loads all questions once, preserves drafts and results across arbitrary navigation', async () => {
    const requests = [];
    const questions = [1, 2, 3].map((id) => ({
      question_id: `q-${id}`, question_type: 'short_answer', stem: `题干${id}`,
      kp_ids: ['kp-list'], options: [], source_scope: 'public',
    }));
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      requests.push({ url, options });
      if (url.includes('/practice/questions')) return jsonResponse({ questions, total: 3 });
      if (url.includes('/practice/next')) {
        expect(url).toContain('question_id=q-3');
        return jsonResponse({ available: true, question: { ...questions[2], request_id: 'claim-3' } });
      }
      if (url.endsWith('/practice/grade')) return jsonResponse({
        grading: { score: 85, is_correct: true, analysis: '批改完成' }, writeback: { status: 'applied' },
      });
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<AtlasPracticePanel knowledgePoint={{ kpId: 'kp-list' }} />);
    await screen.findByText('题干1');
    const list = screen.getByRole('complementary', { name: '题目列表' });
    expect(within(list).getAllByRole('button')).toHaveLength(3);
    expect(requests).toHaveLength(1);
    fireEvent.change(screen.getByLabelText('你的答案'), { target: { value: '第一题草稿' } });
    fireEvent.click(screen.getByRole('button', { name: '第3题，作答中' }));
    expect(screen.getByText('题干3')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下一题' })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('你的答案'), { target: { value: '第三题答案' } });
    fireEvent.click(screen.getByRole('button', { name: '提交并批改' }));
    await screen.findByText(/得分 85/);
    const body = JSON.parse(requests.find(({ url }) => url.endsWith('/grade')).options.body);
    expect(body).toMatchObject({ question_id: 'q-3', request_id: 'claim-3', student_answer: '第三题答案' });
    expect(screen.queryByRole('button', { name: '下一题' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '第1题，作答中' }));
    expect(screen.getByLabelText('你的答案')).toHaveValue('第一题草稿');
    fireEvent.click(screen.getByRole('button', { name: '下一题' }));
    expect(screen.getByText('题干2')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '第3题，回答正确' }));
    expect(screen.getByText(/得分 85/)).toBeInTheDocument();
    expect(requests.filter(({ url }) => url.includes('/practice/questions'))).toHaveLength(1);
  });

  it('loads a KP-scoped public question and submits only learner-visible fields', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      requests.push({ url, options });
      if ((url.includes('/practice/next') || url.includes('/practice/questions')) && url.includes('kp_id=kp-yinyang')) {
        return jsonResponse({
          available: true,
          kp_id: 'kp-yinyang',
          question: {
            question_id: 'question-1',
            question_type: 'short_answer',
            stem: '阴阳关系的基本特征是什么？',
            options: [],
            kp_ids: ['kp-yinyang'],
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
    expect(screen.queryByTestId('question-difficulty-label')).not.toBeInTheDocument();
    // Unlabelled questions offer the manual difficulty tagging control.
    expect(screen.getByText('本题暂无难度标注')).toBeInTheDocument();
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
      if (url.includes('/practice/next') || url.includes('/practice/questions')) {
        return jsonResponse({
          available: true,
          question: {
            question_id: 'user-question-1',
            question_type: 'short_answer',
            stem: '个人题目',
            options: [],
            kp_ids: ['kp-user'],
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

  it('loads and grades the frozen snapshot for a bound daily task item', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      requests.push({ url, options });
      if (url.includes('/daily-task-items/ITEM_BOUND/practice/')) {
        return jsonResponse({
          available: true,
          progress: { reviewed: 0, required: 1 },
          question: {
            question_id: 'question-bound', question_type: 'short_answer', stem: '绑定题目',
            options: [], kp_ids: ['kp-bound'], request_id: 'request-bound', source_scope: 'daily_task',
            snapshot_id: 1, question_version_id: 'version-bound',
          },
        });
      }
      if (url.endsWith('/practice/grade')) {
        return jsonResponse({ grading: { score: 100, is_correct: true, analysis: '完成' }, writeback: { status: 'applied' } });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(<AtlasPracticePanel taskItemId="ITEM_BOUND" />);
    expect(await screen.findByText('绑定题目')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('你的答案'), { target: { value: '绑定答案' } });
    fireEvent.click(screen.getByRole('button', { name: '提交并批改' }));

    expect(await screen.findByText(/得分 100/)).toBeInTheDocument();
    expect(requests[0].url).toContain('/daily-task-items/ITEM_BOUND/practice/questions');
    expect(requests.some(({ url }) => url.includes('/practice/next?snapshot_id=1'))).toBe(true);
    const body = JSON.parse(requests.find(({ url }) => url.endsWith('/practice/grade')).options.body);
    expect(body).toMatchObject({ request_id: 'request-bound', daily_task_item_id: 'ITEM_BOUND' });
  });

  it('shows completion after every frozen snapshot reaches terminal review', async () => {
    vi.stubGlobal('fetch', vi.fn(() => jsonResponse({
      available: false,
      reason: 'daily_task_item_completed',
      progress: { reviewed: 3, required: 3 },
    })));

    render(<AtlasPracticePanel taskItemId="ITEM_COMPLETE" />);

    expect(await screen.findByText('今日知识点练习已完成')).toHaveAttribute('role', 'status');
  });

  it('hides next on a single-question list before and after grading', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url) => {
      requests.push(String(url));
      if (url.includes('/practice/next') || url.includes('/practice/questions')) {
        return jsonResponse({
          available: true,
          kp_id: 'kp-single',
          question: {
            question_id: 'question-single',
            question_type: 'single_choice',
            stem: '唯一一道题',
            options: [{ option_id: 'A', content: '选项甲' }],
            kp_ids: ['kp-single'],
            request_id: 'request-single',
          },
        });
      }
      if (url.includes('exclude_question_id=question-single')) {
        return jsonResponse({
          available: false,
          kp_id: 'kp-single',
          question: null,
        });
      }
      if (url.endsWith('/practice/grade')) {
        return jsonResponse({ grading: { score: 100, is_correct: true, analysis: '完成' }, writeback: { status: 'applied' } });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(<AtlasPracticePanel knowledgePoint={{ kpId: 'kp-single', kpName: '单题知识点' }} />);
    expect(await screen.findByText('唯一一道题')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '下一题' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('radio', { name: /选项甲/ }));
    fireEvent.click(screen.getByRole('button', { name: '提交并批改' }));
    await screen.findByText(/得分 100/);

    expect(screen.queryByRole('button', { name: '下一题' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '返回上一题' })).toBeInTheDocument();
    expect(requests.filter((url) => url.includes('/practice/questions'))).toHaveLength(1);
    expect(requests.find((url) => url.includes('exclude_question_id'))).toBeUndefined();
    expect(screen.getByText(/得分 100/)).toBeInTheDocument();
  });

  it('does not render the removed answer hint control or panel', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.includes('/practice/next') || url.includes('/practice/questions')) {
        return jsonResponse({
          available: true,
          question: {
            question_id: 'question-hint',
            question_type: 'short_answer',
            stem: '阴阳关系的基本特征是什么？',
            options: [],
            kp_ids: ['kp-yinyang'],
            request_id: 'request-hint',
          },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(<AtlasPracticePanel knowledgePoint={{ kpId: 'kp-yinyang', kpName: '阴阳学说' }} />);

    expect(await screen.findByText('阴阳关系的基本特征是什么？')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '查看答题提示' })).not.toBeInTheDocument();
    expect(screen.queryByTestId('practice-hint-panel')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: '你的答案' })).toBeInTheDocument();
  });

  it('lets the learner tag an unlabelled question and then shows the difficulty', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      requests.push({ url, options });
      if (url.includes('/practice/next') || url.includes('/practice/questions')) {
        return jsonResponse({
          available: true,
          question: {
            question_id: 'question-unlabelled',
            question_type: 'single_choice',
            stem: '没有难度标注的题',
            options: [{ option_id: 'A', content: '选项甲' }],
            kp_ids: ['kp-tag'],
            request_id: 'request-tag',
          },
        });
      }
      if (url.endsWith('/practice/difficulty-tag')) {
        return jsonResponse({ saved: true, question_id: 'question-unlabelled', difficulty: 4 });
      }
      if (url.endsWith('/practice/grade')) {
        return jsonResponse({ grading: { score: 90, is_correct: true, analysis: '完成' }, writeback: { status: 'applied' } });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(<AtlasPracticePanel knowledgePoint={{ kpId: 'kp-tag', kpName: '标记知识点' }} />);

    expect(await screen.findByText('没有难度标注的题')).toBeInTheDocument();
    expect(screen.getByText('本题暂无难度标注')).toBeInTheDocument();
    expect(screen.queryByTestId('question-difficulty-label')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '4星' }));

    expect(await screen.findByTestId('question-difficulty-label')).toHaveTextContent('难度 4星');
    expect(screen.queryByText('本题暂无难度标注')).not.toBeInTheDocument();
    const tagRequest = requests.find(({ url }) => url.endsWith('/practice/difficulty-tag'));
    expect(tagRequest).toBeDefined();
    expect(tagRequest.options.method).toBe('PUT');
    expect(JSON.parse(tagRequest.options.body)).toEqual({
      question_id: 'question-unlabelled',
      difficulty: 4,
    });
  });

  it('shows a real difficulty label without offering the tagging control', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.includes('/practice/next') || url.includes('/practice/questions')) {
        return jsonResponse({
          available: true,
          question: {
            question_id: 'question-labeled',
            question_type: 'single_choice',
            stem: '已有难度标注的题',
            options: [{ option_id: 'A', content: '选项甲' }],
            kp_ids: ['kp-labeled'],
            request_id: 'request-labeled',
            difficulty: 3,
            difficulty_source: 'curated_question_bank',
          },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(<AtlasPracticePanel knowledgePoint={{ kpId: 'kp-labeled', kpName: '标注知识点' }} />);

    expect(await screen.findByText('已有难度标注的题')).toBeInTheDocument();
    expect(screen.getByTestId('question-difficulty-label')).toHaveTextContent('难度 3星');
    expect(screen.queryByText('本题暂无难度标注')).not.toBeInTheDocument();
  });
});
