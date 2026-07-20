import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import HomePage from './HomePage';

function response(payload, ok = true, status = 200) {
  return { ok, status, text: async () => JSON.stringify(payload) };
}

describe('HomePage', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('renders the reference hero, learning summaries, and every portal action', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response({
      continue_learning: [{ session_id: 's-1', title: '中医基础理论精要' }],
      today_tasks: [{ title: '复习方剂学', duration: '45 分钟' }],
      status_cards: [{ key: 'accuracy', value: '65%' }],
    }))));

    render(<HomePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    expect(screen.getByText('今天，让学习更有方向')).toBeInTheDocument();
    expect(screen.getByText('循序精进')).toBeInTheDocument();
    expect(await screen.findByText('中医基础理论精要')).toBeInTheDocument();
    expect(screen.getByRole('progressbar', { name: '学习进度' })).toHaveAttribute('aria-valuenow', '65');

    for (const label of ['继续学习', '待办任务', '智能问答', '资料检索', '知识图谱', '题目工作区', '专项练习', '错题巩固', '案例实训']) {
      expect(screen.getByRole('button', { name: new RegExp(label) })).toBeInTheDocument();
    }
  });

  it('navigates smart Q&A, upload, and mistake reinforcement to their real modules', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response({}))));
    const onNavigate = vi.fn();
    render(<HomePage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    await screen.findByRole('button', { name: /智能问答/ });
    fireEvent.click(screen.getByRole('button', { name: /智能问答/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({ page: 'assistant', params: {} });

    fireEvent.click(screen.getByRole('button', { name: '上传资料' }));
    expect(onNavigate).toHaveBeenLastCalledWith({ page: 'knowledge', params: { view: 'personal' } });

    fireEvent.click(screen.getByRole('button', { name: /错题巩固/ }));
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: { view: 'workspace', taskType: 'mistake_variation' },
    });
  });

  it('shows the first server announcement without blocking learning actions', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response({
      announcements: [{ title: '本周专题练习已更新' }],
    }))));

    render(<HomePage currentUser={{ username: 'alice' }} onNavigate={vi.fn()} />);

    expect(await screen.findByRole('status')).toHaveTextContent('本周专题练习已更新');
    expect(screen.getByRole('button', { name: /智能问答/ })).toBeEnabled();
  });

  it('keeps portal navigation available when the dashboard summary request fails', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(response({ detail: '首页数据暂不可用' }, false, 503))));
    const onNavigate = vi.fn();
    render(<HomePage currentUser={{ username: 'alice' }} onNavigate={onNavigate} />);

    expect(await screen.findByRole('alert')).toHaveTextContent('首页数据暂不可用');
    expect(screen.queryByRole('progressbar', { name: '学习进度' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /智能问答/ }));
    await waitFor(() => expect(onNavigate).toHaveBeenCalledWith({ page: 'assistant', params: {} }));
  });
});
