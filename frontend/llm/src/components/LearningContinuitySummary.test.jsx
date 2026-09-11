import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import LearningContinuitySummary from './LearningContinuitySummary';
import { fetchWithAuth, readJsonResponse } from '../utils/api';

vi.mock('../utils/api', () => ({ API_BASE: '/api', fetchWithAuth: vi.fn(), readJsonResponse: vi.fn() }));
beforeEach(() => {
  vi.resetAllMocks();
  fetchWithAuth.mockResolvedValue({ ok: true });
});
const history = {
  record_scope: 'legacy_archive', audit_status: 'not_evaluated', has_history: true,
  recent_behavior_window_days: 30, recent_active_days: 17, recent_focus_minutes: 599, attempt_count: 54,
};

it('shows actual learning history separately from textbook completion', async () => {
  readJsonResponse.mockResolvedValue(history);
  render(<LearningContinuitySummary userKey="A" />);
  const summary = await screen.findByLabelText('已有学习经历');
  expect(summary).toHaveTextContent('17 天');
  expect(summary).toHaveTextContent('599 分钟');
  expect(summary).toHaveTextContent('54 次');
  expect(summary).toHaveTextContent('不代表全部学习进度');
  expect(summary).not.toHaveTextContent('%');
});

it('never treats a fetch failure as zero progress', async () => {
  fetchWithAuth.mockRejectedValue(new Error('offline'));
  render(<LearningContinuitySummary userKey="A" />);
  expect(await screen.findByRole('status')).toHaveTextContent('不代表学习进度为零');
});

it('clears old user history immediately when the owner changes', async () => {
  readJsonResponse.mockResolvedValueOnce(history).mockResolvedValueOnce({ ...history, has_history: false });
  const view = render(<LearningContinuitySummary userKey="A" />);
  await screen.findByLabelText('已有学习经历');
  view.rerender(<LearningContinuitySummary userKey="B" />);
  expect(screen.queryByLabelText('已有学习经历')).not.toBeInTheDocument();
  await waitFor(() => expect(readJsonResponse).toHaveBeenCalledTimes(2));
});