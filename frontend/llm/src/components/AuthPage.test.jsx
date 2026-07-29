import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AuthPage from './AuthPage';

const jsonResponse = (status, payload) => new Response(JSON.stringify(payload), {
  status,
  headers: { 'Content-Type': 'application/json' },
});

describe('AuthPage main-backend cookie contract', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const stubHealthyBackend = () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url === '/health') return Promise.resolve(jsonResponse(200, { status: 'ok' }));
      return Promise.resolve(jsonResponse(500, { detail: 'Unexpected request' }));
    }));
  };

  it('logs in with JSON and lets the server own the session cookie', async () => {
    const user = { user_id: 'USER_1', username: 'lin', display_name: '林同学' };
    const request = vi.fn((url) => (
      url === '/health'
        ? Promise.resolve(jsonResponse(200, { status: 'ok' }))
        : Promise.resolve(jsonResponse(200, { user }))
    ));
    const onLogin = vi.fn();
    vi.stubGlobal('fetch', request);
    render(<AuthPage onLogin={onLogin} />);

    expect(screen.getByLabelText('账号')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('账号'), { target: { value: 'lin' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'correct-horse-2026' } });
    fireEvent.click(screen.getByRole('button', { name: '进入时珍智训' }));

    await waitFor(() => expect(onLogin).toHaveBeenCalledWith(user));
    expect(request).toHaveBeenCalledWith('/api/v1/auth/login', expect.objectContaining({
      method: 'POST',
      credentials: 'include',
      body: JSON.stringify({ username: 'lin', password: 'correct-horse-2026' }),
    }));
    expect(localStorage.getItem('token')).toBeNull();
  });

  it('registers with the main backend contract without email verification fields', async () => {
    const user = { user_id: 'USER_2', username: 'newlearner', display_name: '新同学' };
    const request = vi.fn((url) => (
      url === '/health'
        ? Promise.resolve(jsonResponse(200, { status: 'ok' }))
        : Promise.resolve(jsonResponse(201, { user }))
    ));
    const onLogin = vi.fn();
    vi.stubGlobal('fetch', request);
    render(<AuthPage onLogin={onLogin} />);

    fireEvent.click(screen.getByRole('button', { name: '创建学习账号' }));
    expect(screen.getByText('创建账号后将直接登录并进入时珍智训首页。')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'newlearner' } });
    fireEvent.change(screen.getByLabelText('显示名（可选）'), { target: { value: '新同学' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'strong-password' } });
    fireEvent.click(screen.getByRole('button', { name: '提交' }));

    await waitFor(() => expect(onLogin).toHaveBeenCalledWith(user));
    const [, options] = request.mock.calls.find(([url]) => url === '/api/v1/auth/register');
    expect(JSON.parse(options.body)).toEqual({
      username: 'newlearner',
      display_name: '新同学',
      password: 'strong-password',
    });
  });

  it('renders the login form directly without a modal dialog', () => {
    stubHealthyBackend();
    render(<AuthPage onLogin={vi.fn()} />);

    expect(screen.getByLabelText('账号')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '进入时珍智训' })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('explains that the backend must be started when the health check fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    render(<AuthPage onLogin={vi.fn()} />);

    expect(await screen.findByText(/认证服务尚未连接/)).toBeInTheDocument();
    expect(screen.getByText(/启动后端服务/)).toBeInTheDocument();
  });
});
