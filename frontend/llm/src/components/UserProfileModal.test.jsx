import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import UserProfileModal from './UserProfileModal';

const jsonResponse = (status, payload) => new Response(JSON.stringify(payload), {
  status,
  headers: { 'Content-Type': 'application/json' },
});

const currentUser = { user_id: 'USER_1', username: 'mmm', display_name: 'mmm', role: 'user' };

describe('UserProfileModal', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('loads, edits and saves account data through the authenticated profile API', async () => {
    const profile = {
      display_name: '小明',
      gender: 'male',
      birth_date: '2000-01-02',
      region: '北京市',
      contact_email: 'ming@example.com',
      signature: '稳步学习',
      avatar_url: null,
    };
    const request = vi.fn((url, options = {}) => {
      if (url === '/api/v1/auth/me/profile' && !options.method) return Promise.resolve(jsonResponse(200, { user: currentUser, profile }));
      if (url === '/api/v1/auth/me/profile' && options.method === 'PATCH') return Promise.resolve(jsonResponse(200, { user: { ...currentUser, display_name: '新昵称' }, profile: { ...profile, display_name: '新昵称' } }));
      return Promise.resolve(jsonResponse(500, { detail: 'Unexpected request' }));
    });
    vi.stubGlobal('fetch', request);
    const onSaved = vi.fn();
    render(<UserProfileModal open currentUser={currentUser} onClose={vi.fn()} onSaved={onSaved} />);

    expect(await screen.findByDisplayValue('小明')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('昵称'), { target: { value: '新昵称' } });
    fireEvent.click(screen.getByRole('button', { name: '保存信息' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ display_name: '新昵称' }), expect.objectContaining({ display_name: '新昵称' })));
    const [, options] = request.mock.calls.find(([url, options]) => url === '/api/v1/auth/me/profile' && options.method === 'PATCH');
    expect(JSON.parse(options.body)).toMatchObject({ display_name: '新昵称', region: '北京市', contact_email: 'ming@example.com' });
  });

  it('rejects a selected avatar larger than one megabyte before uploading', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(jsonResponse(200, { user: currentUser, profile: {} }))));
    render(<UserProfileModal open currentUser={currentUser} onClose={vi.fn()} onSaved={vi.fn()} />);

    await screen.findByLabelText('昵称');
    const largeFile = new File([new Uint8Array(1024 * 1024 + 1)], 'large.png', { type: 'image/png' });
    fireEvent.change(screen.getByLabelText('上传头像图片'), { target: { files: [largeFile] } });

    expect(await screen.findByText('头像图片不能超过 1 MB')).toBeInTheDocument();
  });
});