import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SettingsPage from './SettingsPage';
import { fetchWithAuth, readJsonResponse } from '../utils/api';

vi.mock('../utils/api', () => ({
  API_BASE: 'http://api.test',
  fetchWithAuth: vi.fn(),
  readJsonResponse: vi.fn((response, fallback) => Promise.resolve(response?.payload ?? fallback)),
}));

function deferred() {
  let resolve;
  const promise = new Promise((next) => { resolve = next; });
  return { promise, resolve };
}

describe('SettingsPage state model', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows loading, dirty and saving states while keeping save sticky and intentional', async () => {
    const loadingRequest = deferred();
    const savingRequest = deferred();
    fetchWithAuth
      .mockImplementationOnce(() => loadingRequest.promise)
      .mockImplementationOnce(() => savingRequest.promise);

    render(<SettingsPage />);
    expect(screen.getByRole('status')).toHaveTextContent('正在加载设置');

    loadingRequest.resolve({ ok: true, payload: { settings: { analysis_frequency: 'daily' }, locked_fields: [] } });
    expect(await screen.findByRole('button', { name: '保存更改' })).toBeDisabled();

    fireEvent.click(screen.getByRole('button', { name: '每周一次' }));
    expect(screen.getByRole('button', { name: '保存更改' })).toBeEnabled();
    expect(screen.getByText('有未保存的更改')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '保存更改' }));
    expect(screen.getByRole('button', { name: '正在保存…' })).toBeDisabled();

    savingRequest.resolve({ ok: true, payload: {} });
    expect(await screen.findByRole('status')).toHaveTextContent('设置已保存');
    expect(screen.getByRole('button', { name: '保存更改' })).toBeDisabled();
    expect(readJsonResponse).toHaveBeenCalled();
  });

  it('surfaces a load failure without replacing the rest of the platform shell', async () => {
    fetchWithAuth.mockRejectedValueOnce(new Error('网络不可用'));
    render(<SettingsPage />);

    expect(await screen.findByRole('alert')).toHaveTextContent('网络不可用');
    expect(screen.getByRole('button', { name: '重新加载' })).toBeInTheDocument();
  });
});
