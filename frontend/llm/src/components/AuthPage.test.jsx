import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

    expect(screen.queryByLabelText('账号')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '登录' }));
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

  it('starts the unified registration journey with the main backend contract', async () => {
    const user = {
      user_id: 'USER_2',
      username: 'newlearner',
      display_name: '新同学',
      onboarding_required: true,
    };
    const request = vi.fn((url) => {
      if (url === '/health') return Promise.resolve(jsonResponse(200, { status: 'ok' }));
      if (url === '/api/v1/auth/register') return Promise.resolve(jsonResponse(201, { user }));
      if (url.endsWith('/training/onboarding/group-templates')) {
        return Promise.resolve(jsonResponse(200, { groups: [], questions: [] }));
      }
      if (url.endsWith('/api/v1/qualification-targets')) {
        return Promise.resolve(jsonResponse(200, { items: [] }));
      }
      if (url.endsWith('/training/onboarding/status')) {
        return Promise.resolve(jsonResponse(200, { status: 'pending', survey_answers: {} }));
      }
      return Promise.resolve(jsonResponse(500, { detail: 'Unexpected request' }));
    });
    const onLogin = vi.fn();
    vi.stubGlobal('fetch', request);
    render(<AuthPage onLogin={onLogin} />);

    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    fireEvent.change(screen.getByLabelText(/用户名/), { target: { value: 'newlearner' } });
    fireEvent.change(screen.getByLabelText(/显示名/), { target: { value: '新同学' } });
    fireEvent.change(screen.getByLabelText(/密码/), { target: { value: 'strong-password' } });
    fireEvent.click(screen.getByRole('button', { name: '创建账号并继续' }));

    expect(await screen.findByRole('heading', { name: '你目前属于哪一类学习者？' })).toBeInTheDocument();
    expect(onLogin).not.toHaveBeenCalled();
    const [, options] = request.mock.calls.find(([url]) => url === '/api/v1/auth/register');
    expect(JSON.parse(options.body)).toEqual({
      username: 'newlearner',
      display_name: '新同学',
      password: 'strong-password',
    });
    expect(screen.getByRole('progressbar', { name: '注册与学情调查进度' })).toHaveAttribute('aria-valuenow', '2');
  });

  it('keeps the authentication card out of the showcase until login is selected', () => {
    stubHealthyBackend();
    render(<AuthPage onLogin={vi.fn()} />);

    expect(screen.queryByRole('dialog', { name: '进入学习工作台' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('账号')).not.toBeInTheDocument();
    expect(screen.getByRole('img', { name: '中医药在线学习场景' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    const dialog = screen.getByRole('dialog', { name: '进入学习工作台' });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByLabelText('账号')).toBeInTheDocument();
    expect(within(dialog).queryByRole('img', { name: '中医药在线学习场景' })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '返回展示页' }));
    expect(screen.queryByLabelText('账号')).not.toBeInTheDocument();
  });

  it('explains that the backend must be started when the health check fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    render(<AuthPage onLogin={vi.fn()} />);

    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    expect(await screen.findByText(/认证服务尚未连接/)).toBeInTheDocument();
    expect(screen.getByText(/启动后端服务/)).toBeInTheDocument();
  });
});
