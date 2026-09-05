import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import LearningHistoryPanel from './LearningHistoryPanel';
import { fetchWithAuth } from '../utils/api';

vi.mock('../utils/api', () => ({ fetchWithAuth: vi.fn() }));
beforeEach(() => vi.clearAllMocks());

it('loads history only when expanded and paginates without changing audit statistics', async () => {
  fetchWithAuth.mockResolvedValue({ ok: true, json: async () => ({ total: 30, has_more: true, items: [{id: 1, question_id: 'Q1', answer: '<script>alert(1)</script>'}] }) });
  render(<LearningHistoryPanel />);
  expect(fetchWithAuth).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /历史学习记录/ }));
  expect(await screen.findByText('历史答题：共 30 条')).toBeTruthy();
  expect(screen.getByText('<script>alert(1)</script>')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await waitFor(() => expect(fetchWithAuth).toHaveBeenLastCalledWith(expect.stringContaining('offset=25'), expect.anything()));
});

it('opens session messages and reports API failure instead of an empty history', async () => {
  fetchWithAuth.mockResolvedValue({ ok: true, json: async () => ({ total: 1, has_more: false, items: [{id: 'session-a', title: '旧会话'}] }) });
  render(<LearningHistoryPanel />);
  fireEvent.click(screen.getByRole('button', { name: /历史学习记录/ }));
  await screen.findByText('旧会话');
  fireEvent.change(screen.getByRole('combobox', { name: '历史记录类型' }), {target: {value: 'sessions'}});
  await screen.findByRole('button', { name: '查看会话内容' });
  fetchWithAuth.mockResolvedValue({ ok: false, status: 403 });
  fireEvent.click(screen.getByRole('button', { name: '查看会话内容' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('403');
});