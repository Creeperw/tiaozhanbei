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
    fetchWithAuth.mockImplementation((url) => Promise.resolve({
      ok: true,
      payload: url.endsWith('/personalization/api-settings')
        ? { providers: {} }
        : { settings: { analysis_frequency: 'daily' }, locked_fields: [] },
    }));
  });

  it('saves provider keys server-side and clears the input after saving', async () => {
    fetchWithAuth.mockImplementation((url, options = {}) => {
      if (url.endsWith('/personalization/learner-settings')) {
        return Promise.resolve({ ok: true, payload: { settings: { analysis_frequency: 'daily' }, locked_fields: [] } });
      }
      if (options.method === 'PUT') {
        return Promise.resolve({
          ok: true,
          payload: { providers: { deepseek: { configured: true, masked: 'sk-a…1234' } } },
        });
      }
      return Promise.resolve({ ok: true, payload: { providers: {} } });
    });

    render(<SettingsPage />);
    const input = await screen.findByLabelText('DeepSeek API Key');
    fireEvent.change(input, { target: { value: 'sk-api-secret-1234' } });
    fireEvent.click(screen.getByRole('button', { name: '保存 API 配置' }));

    expect(await screen.findByText('已配置并持久化')).toBeInTheDocument();
    expect(input).toHaveValue('');
    expect(fetchWithAuth).toHaveBeenCalledWith(
      'http://api.test/personalization/api-settings',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({ deepseek_api_key: 'sk-api-secret-1234' }),
      }),
    );
  });

  it('shows loading, dirty and saving states while keeping save sticky and intentional', async () => {
    const loadingRequest = deferred();
    const savingRequest = deferred();
    fetchWithAuth.mockImplementation((url, options = {}) => {
      if (url.endsWith('/personalization/api-settings')) {
        return Promise.resolve({ ok: true, payload: { providers: {} } });
      }
      if (options.method === 'PUT') return savingRequest.promise;
      return loadingRequest.promise;
    });

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
