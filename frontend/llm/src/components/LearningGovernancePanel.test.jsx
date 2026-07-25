import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import LearningGovernancePanel from './LearningGovernancePanel';
import { fetchWithAuth, readJsonResponse } from '../utils/api';

vi.mock('../utils/api', () => ({
  API_BASE: 'http://api.test',
  fetchWithAuth: vi.fn(),
  readJsonResponse: vi.fn((response, fallback) => Promise.resolve(response?.payload ?? fallback)),
}));

const notificationSettings = {
  in_app_enabled: true,
  categories: { review_due: true, intervention: true, plan_review: true },
  digest_frequency: 'realtime',
  quiet_hours: { start: '22:00', end: '07:00' },
};

describe('LearningGovernancePanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    fetchWithAuth.mockImplementation((url) => {
      if (url.includes('/v1/notifications?')) return Promise.resolve({ ok: true, payload: { unread_count: 2, items: [] } });
      if (url.includes('/v1/interventions?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      if (url.includes('/v1/plan-reviews?')) return Promise.resolve({ ok: true, payload: { items: [] } });
      if (url.endsWith('/personalization/learner-settings')) return Promise.resolve({ ok: true, payload: { settings: { notification_preferences: notificationSettings } } });
      return Promise.resolve({ ok: false, payload: { detail: 'Unexpected request' } });
    });
  });

  it('uses a notification settings button in place of the automatic governance label', async () => {
    render(<LearningGovernancePanel />);

    expect(screen.getByRole('button', { name: '通知设置' })).toBeVisible();
    expect(screen.queryByText('自动治理中心')).not.toBeInTheDocument();
    expect(await screen.findByText('需要你处理的学习信号')).toBeInTheDocument();
  });

  it('opens and closes the notification preferences dialog', async () => {
    render(<LearningGovernancePanel />);

    fireEvent.click(screen.getByRole('button', { name: '通知设置' }));
    const dialog = await screen.findByRole('dialog', { name: '通知设置' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(screen.getByRole('heading', { name: '通知与主动提醒' })).toBeVisible();
    expect(screen.getByLabelText('勿扰开始')).toHaveValue('22:00');

    fireEvent.click(screen.getByRole('button', { name: '关闭通知设置' }));
    await waitFor(() => expect(screen.queryByRole('dialog', { name: '通知设置' })).not.toBeInTheDocument());
  });
});
