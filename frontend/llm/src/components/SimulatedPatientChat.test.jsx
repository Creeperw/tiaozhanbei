import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import SimulatedPatientChat from './SimulatedPatientChat';

const mockFetchWithAuth = vi.fn();
const mockReadJsonResponse = vi.fn((res, fallback) => {
  if (!res || !res.ok) return fallback || {};
  return res._json || {};
});

vi.mock('../utils/api', () => ({
  fetchWithAuth: (...args) => mockFetchWithAuth(...args),
  readJsonResponse: (...args) => mockReadJsonResponse(...args),
}));

const mockEmptyList = { success: true, data: { total: 0, list: [] } };

function mockAPICall(action, data) {
  mockFetchWithAuth.mockImplementationOnce(() =>
    Promise.resolve({ ok: true, _json: data })
  );
}

function mockAPIForActions(actionMap) {
  mockFetchWithAuth.mockImplementation((url, opts) => {
    const body = JSON.parse(opts.body || '{}');
    const action = body.action;
    const data = actionMap[action];
    if (data) return Promise.resolve({ ok: true, _json: data });
    return Promise.resolve({ ok: true, _json: { success: false, error: `unmocked: ${action}` } });
  });
}

describe('SimulatedPatientChat', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
    // jsdom compatibility
    Element.prototype.scrollIntoView = vi.fn();
    // Default: all queries return empty
    mockAPIForActions({
      stats: { success: true, data: { total: 5, rate: 80 } },
      dialog_history: mockEmptyList,
      collections: mockEmptyList,
      mistakes: mockEmptyList,
    });
  });

  it('renders welcome mode by default', async () => {
    await act(async () => { render(<SimulatedPatientChat />); });
    await waitFor(() => {
      expect(screen.getByText('欢迎医生')).toBeDefined();
      expect(screen.getByText('开始今天的问诊吧')).toBeDefined();
    });
  });

  it('shows mode selection after clicking start button', async () => {
    await act(async () => { render(<SimulatedPatientChat />); });
    await waitFor(() => screen.getByText('开始今天的问诊吧'));
    await act(async () => { fireEvent.click(screen.getByText('开始今天的问诊吧')); });
    await waitFor(() => {
      expect(screen.getByText('随心练')).toBeDefined();
      expect(screen.getByText('题型专练')).toBeDefined();
    });
  });

  it('starts a session and shows first patient message', async () => {
    await act(async () => { render(<SimulatedPatientChat />); });
    await waitFor(() => screen.getByText('开始今天的问诊吧'));
    await act(async () => { fireEvent.click(screen.getByText('开始今天的问诊吧')); });
    await waitFor(() => screen.getByText('随心练'));
    await act(async () => { fireEvent.click(screen.getByText('随心练')); });
    await waitFor(() => screen.getByText('开始问诊'));

    // Intercept the start API call
    mockAPIForActions({
      stats: { success: true, data: { total: 5, rate: 80 } },
      dialog_history: mockEmptyList,
      collections: mockEmptyList,
      mistakes: mockEmptyList,
      start: {
        success: true, session_id: 'sp-test-001',
        data: { patient_reply: '医生您好，我最近肚子疼。', patient_info: { gender: '男', age_range: '中年', body_type: '适中' } },
        turn_count: 1, help_available: false,
      },
    });

    await act(async () => { fireEvent.click(screen.getByText('开始问诊')); });
    await waitFor(() => {
      expect(screen.getByText('医生您好，我最近肚子疼。')).toBeDefined();
    });
  });

  it('collapses and expands sidebar', async () => {
    await act(async () => { render(<SimulatedPatientChat />); });
    await waitFor(() => screen.getByText('欢迎医生'));
    const toggle = document.querySelector('.sp-chat__collapse-toggle');
    expect(toggle).toBeDefined();
    await act(async () => { fireEvent.click(toggle); });
    const sidebar = document.querySelector('.sp-chat__sidebar');
    expect(sidebar.className).toContain('is-collapsed');
  });

  it('displays stats cards on welcome page', async () => {
    await act(async () => { render(<SimulatedPatientChat />); });
    await waitFor(() => {
      expect(screen.getByText('5')).toBeDefined(); // total
      expect(screen.getByText('80%')).toBeDefined(); // rate
    });
  });

  it('shows empty history in sidebar', async () => {
    await act(async () => { render(<SimulatedPatientChat />); });
    await waitFor(() => {
      expect(screen.getByText('我的诊室')).toBeDefined();
    });
  });
});
