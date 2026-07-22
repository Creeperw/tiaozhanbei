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

  it('logs in with JSON and lets the server own the session cookie', async () => {
    const user = { user_id: 'USER_1', username: 'lin', display_name: '林同学' };
    const request = vi.fn().mockResolvedValue(jsonResponse(200, { user }));
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

  it('registers with the main backend contract without email verification fields', async () => {
    const user = { user_id: 'USER_2', username: 'newlearner', display_name: '新同学' };
    const request = vi.fn().mockResolvedValue(jsonResponse(201, { user }));
    const onLogin = vi.fn();
    vi.stubGlobal('fetch', request);
    render(<AuthPage onLogin={onLogin} />);

    fireEvent.click(screen.getByRole('button', { name: '开始学习' }));
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'newlearner' } });
    fireEvent.change(screen.getByLabelText('显示名（可选）'), { target: { value: '新同学' } });
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'strong-password' } });
    fireEvent.click(screen.getByRole('button', { name: '提交' }));

    await waitFor(() => expect(onLogin).toHaveBeenCalledWith(user));
    const options = request.mock.calls[0][1];
    expect(request.mock.calls[0][0]).toBe('/api/v1/auth/register');
    expect(JSON.parse(options.body)).toEqual({
      username: 'newlearner',
      display_name: '新同学',
      password: 'strong-password',
    });
  });

  it('keeps the authentication card out of the showcase until login is selected', () => {
    render(<AuthPage onLogin={vi.fn()} />);

    expect(screen.queryByRole('dialog', { name: '进入学习工作台' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('账号')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '登录' }));

    expect(screen.getByRole('dialog', { name: '进入学习工作台' })).toBeInTheDocument();
    expect(screen.getByLabelText('账号')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '返回展示页' }));
    expect(screen.queryByLabelText('账号')).not.toBeInTheDocument();
  });
});
