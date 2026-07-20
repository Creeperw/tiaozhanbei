import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import QuestionWorkspacePage from './QuestionWorkspacePage';

function response(payload, ok = true, status = 200) {
  return Promise.resolve({
    ok,
    status,
    text: async () => JSON.stringify(payload),
  });
}

const previewItem = {
  question_id: 'UQ_1',
  question_type: '简答题',
  stem: '阴阳相互关系的基本内容是什么？',
  answer: '对立制约、互根互用。',
  analysis: '基础解析',
  kp_ids: ['KP_YINYANG'],
  status: 'preview_ready',
  review_reason: '',
};

describe('QuestionWorkspacePage', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('uploads an allowed file, previews questions, and confirms an item', async () => {
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      if (url.endsWith('/question-workspace/questions')) return response({ items: [] });
      if (url.endsWith('/question-workspace/imports') && !options.method) {
        return response({ total: 0, items: [] });
      }
      if (url.endsWith('/question-workspace/imports') && options.method === 'POST') {
        expect(options.body).toBeInstanceOf(FormData);
        return response({
          job_id: 'UQJ_1',
          status: 'preview_ready',
          item_count: 1,
          items: [previewItem],
        }, true, 201);
      }
      if (url.endsWith('/question-workspace/items/UQ_1/confirm')) {
        return response({
          question_id: 'UQ_1',
          status: 'active',
          vector_index: { ok: false, rebuild_required: true },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<QuestionWorkspacePage />);

    await screen.findByText('还没有已激活的个人题目');
    const file = new File(['## 题目 1'], 'questions.md', { type: 'text/markdown' });
    fireEvent.change(screen.getByLabelText('选择题目文件'), { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: '解析并预览' }));

    const preview = await screen.findByRole('region', { name: '待确认题目' });
    expect(within(preview).getByText(previewItem.stem)).toBeInTheDocument();
    fireEvent.click(within(preview).getByRole('button', { name: '确认导入' }));
    await waitFor(() => expect(within(preview).getAllByText('已激活')).toHaveLength(2));
    expect(screen.getByText('题目已激活；个人索引将在服务可用后重建。')).toBeInTheDocument();
  });

  it('revises a human-review item into preview-ready state', async () => {
    const reviewItem = {
      ...previewItem,
      answer: '',
      status: 'needs_human_review',
      review_reason: '缺少答案，需要人工修订',
    };
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      if (url.endsWith('/question-workspace/questions')) return response({ items: [] });
      if (url.endsWith('/question-workspace/imports') && !options.method) {
        return response({ total: 0, items: [] });
      }
      if (url.endsWith('/question-workspace/imports') && options.method === 'POST') {
        return response({ job_id: 'UQJ_2', status: 'needs_human_review', item_count: 1, items: [reviewItem] }, true, 201);
      }
      if (url.endsWith('/question-workspace/items/UQ_1') && options.method === 'PATCH') {
        const body = JSON.parse(options.body);
        expect(body.answer).toBe('人工补充答案');
        return response({ question_id: 'UQ_1', status: 'preview_ready' });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<QuestionWorkspacePage />);

    const file = new File(['## 题目 1'], 'questions.txt', { type: 'text/plain' });
    fireEvent.change(screen.getByLabelText('选择题目文件'), { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: '解析并预览' }));
    const preview = await screen.findByRole('region', { name: '待确认题目' });
    fireEvent.change(within(preview).getByLabelText('修订参考答案'), { target: { value: '人工补充答案' } });
    fireEvent.click(within(preview).getByRole('button', { name: '保存修订' }));

    await waitFor(() => expect(within(preview).getByText('待确认')).toBeInTheDocument());
    expect(within(preview).getByRole('button', { name: '确认导入' })).toBeEnabled();
  });

  it('restores a persisted preview from owner-scoped import history', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/question-workspace/questions')) return response({ items: [] });
      if (url.endsWith('/question-workspace/imports')) {
        return response({
          total: 1,
          items: [{
            job_id: 'UQJ_RESTORE',
            status: 'needs_human_review',
            item_count: 1,
            original_filename: '待修订题目.md',
            error_message: '',
          }],
        });
      }
      if (url.endsWith('/question-workspace/imports/UQJ_RESTORE/items')) {
        return response({ items: [{
          ...previewItem,
          answer: '',
          status: 'needs_human_review',
          review_reason: '缺少答案，需要人工修订',
        }] });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<QuestionWorkspacePage />);

    const history = await screen.findByRole('region', { name: '导入历史' });
    expect(within(history).getByText('待修订题目.md')).toBeInTheDocument();
    fireEvent.click(within(history).getByRole('button', { name: '继续处理' }));

    const preview = await screen.findByRole('region', { name: '待确认题目' });
    expect(within(preview).getByText(previewItem.stem)).toBeInTheDocument();
    expect(within(preview).getByLabelText('修订参考答案')).toBeInTheDocument();
  });

  it('rejects a preview and deactivates an active personal question', async () => {
    const activeItem = { ...previewItem, question_id: 'UQ_ACTIVE', status: 'active' };
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      if (url.endsWith('/question-workspace/questions')) return response({ items: [activeItem] });
      if (url.endsWith('/question-workspace/imports') && options.method === 'POST') {
        return response({
          job_id: 'UQJ_REJECT',
          status: 'preview_ready',
          item_count: 1,
          items: [previewItem],
        }, true, 201);
      }
      if (url.endsWith('/question-workspace/imports')) return response({ total: 0, items: [] });
      if (url.endsWith('/question-workspace/items/UQ_1/reject')) {
        expect(options.method).toBe('POST');
        return response({ question_id: 'UQ_1', status: 'rejected' });
      }
      if (url.endsWith('/question-workspace/questions/UQ_ACTIVE/deactivate')) {
        expect(options.method).toBe('POST');
        return response({ question_id: 'UQ_ACTIVE', status: 'inactive' });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    render(<QuestionWorkspacePage />);
    await screen.findByText(activeItem.stem);

    const file = new File(['## 题目 1'], 'questions.md', { type: 'text/markdown' });
    fireEvent.change(screen.getByLabelText('选择题目文件'), { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: '解析并预览' }));

    const preview = await screen.findByRole('region', { name: '待确认题目' });
    fireEvent.click(within(preview).getByRole('button', { name: '拒绝' }));
    await waitFor(() => expect(within(preview).getAllByText('已拒绝')).toHaveLength(2));
    const activeSection = screen.getByRole('region', { name: '已激活个人题目' });
    fireEvent.click(within(activeSection).getByRole('button', { name: '停用' }));
    await waitFor(() => expect(within(activeSection).queryByText(activeItem.stem)).not.toBeInTheDocument());
  });

  it('shows a local validation error for unsupported files without uploading', async () => {
    const fetchMock = vi.fn((url) => {
      if (url.endsWith('/question-workspace/questions')) return response({ items: [] });
      if (url.endsWith('/question-workspace/imports')) return response({ total: 0, items: [] });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<QuestionWorkspacePage />);

    const file = new File(['bad'], 'questions.exe', { type: 'application/octet-stream' });
    fireEvent.change(screen.getByLabelText('选择题目文件'), { target: { files: [file] } });
    fireEvent.click(screen.getByRole('button', { name: '解析并预览' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('仅支持 PDF、Markdown 和 TXT 文件');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
