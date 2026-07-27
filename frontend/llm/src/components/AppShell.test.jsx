import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import AppShell from './AppShell';

vi.mock('./UserProfileModal', () => ({
  default: ({ open }) => open ? <div role="dialog" aria-label="完善个人信息">profile modal</div> : null,
}));

afterEach(() => vi.unstubAllGlobals());

describe('AppShell', () => {
  it('exposes an accessible current page and mobile navigation drawer', async () => {
    const user = userEvent.setup();
    render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="dashboard"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <div>Dashboard content</div>
      </AppShell>,
    );

    expect(screen.getByRole('link', { name: '平台首页' })).toHaveAttribute('aria-current', 'page');
    const menuButton = screen.getByRole('button', { name: '打开导航菜单' });
    expect(menuButton).toHaveAttribute('aria-expanded', 'false');
    await user.click(menuButton);
    expect(menuButton).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('dialog', { name: '主导航' })).toBeVisible();
    expect(screen.getByRole('button', { name: '关闭导航菜单' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(menuButton).toHaveAttribute('aria-expanded', 'false');
    expect(menuButton).toHaveFocus();
  });

  it('opens the training workshop under the desktop top navigation without a duplicate heading', () => {
    const { container } = render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="training-workshop"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <div>Practice content</div>
      </AppShell>,
    );

    expect(container.querySelector('.app-shell__topbar')).toBeInTheDocument();
    expect(container.querySelector('.app-shell__sidebar')).not.toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveAttribute('data-mode', 'workspace');
    expect(screen.queryByRole('heading', { name: '训练工坊' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '学习工坊' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /侧栏/ })).not.toBeInTheDocument();
  });

  it('redirects the retired questions destination to the unified knowledge workspace', () => {
    render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="question-workspace"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <div>Question workspace content</div>
      </AppShell>,
    );

    expect(screen.queryByRole('link', { name: '知识仓库' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '我的题目' })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '知识治理' })).not.toBeInTheDocument();
  });

  it('navigates using an intent while preserving the destination label', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="dashboard"
        onNavigate={onNavigate}
        onLogout={vi.fn()}
      >
        <div>Dashboard content</div>
      </AppShell>,
    );

    await user.click(screen.getByRole('link', { name: '学习工坊' }));
    expect(onNavigate).toHaveBeenCalledWith({ page: 'practice', params: {} });
    await user.click(screen.getByRole('link', { name: '训练工坊' }));
    expect(onNavigate).toHaveBeenCalledWith({ page: 'training-workshop', params: {} });
  });

  it('keeps qualification selection in the top navigation and opens the matching homepage route', async () => {
    const onNavigate = vi.fn();
    const fetchMock = vi.fn((url, options = {}) => {
      const path = String(url);
      const payload = path.includes('/qualification-targets')
        ? {
          items: [
            { target_id: 'target-tcm', exam_track_id: 'track-tcm', official_name: '中医执业医师资格考试' },
            { target_id: 'target-integrated', exam_track_id: 'track-integrated', official_name: '中西医结合执业医师资格考试' },
          ],
        }
        : path.endsWith('/personalization/learning-target') && options.method === 'PUT'
          ? { success: true, target: { exam_track_id: 'track-integrated' } }
          : path.endsWith('/personalization/learning-target')
            ? { target: { exam_track_id: 'track-tcm' } }
            : {};
      return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify(payload) });
    });
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();

    render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="dashboard"
        onNavigate={onNavigate}
        onLogout={vi.fn()}
      >
        <div>Dashboard content</div>
      </AppShell>,
    );

    const selector = await screen.findByRole('combobox', { name: '资格考试路径' });
    expect(selector).toHaveValue('target-tcm');
    await user.selectOptions(selector, 'target-integrated');

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/personalization\/learning-target$/),
      expect.objectContaining({ method: 'PUT' }),
    ));
    expect(onNavigate).toHaveBeenCalledWith({
      page: 'qualification-route',
      params: { qualificationTargetId: 'target-integrated' },
    });
  });

  it('opens moved intervention notifications from the notification action', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="dashboard"
        onNavigate={onNavigate}
        onLogout={vi.fn()}
      >
        <div>Dashboard content</div>
      </AppShell>,
    );

    await user.click(screen.getAllByRole('button', { name: '通知，0 条未读' })[0]);
    expect(onNavigate).toHaveBeenCalledWith({ page: 'settings', params: { view: 'governance' } });
  });

  it('keeps a persistent desktop top navigation without a collapse control', () => {
    const { container } = render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="dashboard"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <div>Dashboard content</div>
      </AppShell>,
    );

    expect(screen.queryByRole('heading', { name: '培训助手首页' })).not.toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveAttribute('data-page', 'dashboard');
    expect(container.querySelector('.app-shell__topbar')).toBeInTheDocument();
    expect(container.querySelector('.app-shell__sidebar')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /侧栏/ })).not.toBeInTheDocument();
  });

  it('keeps the desktop top navigation mounted when changing modules', async () => {
    const { container, rerender } = render(
      <AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="dashboard" onNavigate={vi.fn()} onLogout={vi.fn()}>
        <div>Dashboard content</div>
      </AppShell>,
    );

    expect(container.querySelector('.app-shell__topbar')).toBeInTheDocument();

    rerender(
      <AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="assistant" onNavigate={vi.fn()} onLogout={vi.fn()}>
        <div>Assistant workspace</div>
      </AppShell>,
    );

    await waitFor(() => expect(container.querySelector('.app-shell__topbar')).toBeInTheDocument());
  });

  it('marks assistant and knowledge as workspace pages and omits a duplicate module heading', () => {
    const { rerender } = render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="assistant"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <div>Assistant workspace</div>
      </AppShell>,
    );

    expect(screen.getByRole('main')).toHaveAttribute('data-mode', 'workspace');
    expect(screen.getByRole('main')).toHaveAttribute('data-scroll-region', 'contained');
    expect(screen.queryByRole('heading', { name: '智能助教' })).not.toBeInTheDocument();

    rerender(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="knowledge"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <div>Knowledge workspace</div>
      </AppShell>,
    );
    expect(screen.getByRole('main')).toHaveAttribute('data-mode', 'workspace');
    expect(screen.getByRole('main')).toHaveAttribute('data-scroll-region', 'contained');
    expect(screen.queryByRole('heading', { name: '知识库' })).not.toBeInTheDocument();
  });

  it('keeps the personalization secondary navigation at the top without a duplicate page heading', () => {
    render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="personalization"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <nav aria-label="个性数据二级菜单">Secondary navigation</nav>
      </AppShell>,
    );

    expect(screen.getByRole('main')).toHaveAttribute('data-page', 'personalization');
    expect(screen.queryByText('当前模块')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '个性数据' })).not.toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '个性数据二级菜单' })).toBeInTheDocument();
  });

  it('keeps user settings secondary navigation at the top without a duplicate page heading', () => {
    render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="settings"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <nav aria-label="用户设置二级菜单">Secondary navigation</nav>
      </AppShell>,
    );

    expect(screen.getByRole('main')).toHaveAttribute('data-page', 'settings');
    expect(screen.queryByText('当前模块')).not.toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: '用户设置' })).not.toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '用户设置二级菜单' })).toBeInTheDocument();
  });

  it('gives dashboard and training workshop an independently scrollable page region', () => {
    const { rerender } = render(
      <AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="dashboard" onNavigate={vi.fn()} onLogout={vi.fn()}>
        <div style={{ height: 2000 }}>Long dashboard</div>
      </AppShell>,
    );

    expect(screen.getByRole('main')).toHaveAttribute('data-scroll-region', 'page');
    rerender(
      <AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="practice" onNavigate={vi.fn()} onLogout={vi.fn()}>
        <div style={{ height: 2000 }}>Long workshop</div>
      </AppShell>,
    );
    expect(screen.getByRole('main')).toHaveAttribute('data-scroll-region', 'page');
  });

  it('keeps the mobile drawer mounted for its exit motion before removing it', async () => {
    const user = userEvent.setup();
    const { container } = render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="dashboard"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <div>Dashboard content</div>
      </AppShell>,
    );

    await user.click(screen.getByRole('button', { name: '打开导航菜单' }));
    await user.click(screen.getByRole('button', { name: '关闭导航菜单' }));

    const exitingBackdrop = container.querySelector('.app-shell__drawer-backdrop');
    expect(exitingBackdrop).toHaveAttribute('data-state', 'closing');
    await waitFor(() => expect(container.querySelector('.app-shell__drawer-backdrop')).not.toBeInTheDocument(), { timeout: 500 });
  });

  it('keeps the current user profile accessible from the top navigation', async () => {
    const user = userEvent.setup();
    render(
      <AppShell
        currentUser={{ username: 'mmm', display_name: '明同学', role: 'user' }}
        currentPage="dashboard"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <div>Dashboard content</div>
      </AppShell>,
    );

    expect(screen.getByText('明同学')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '打开个人信息' }));
    expect(screen.getByRole('dialog', { name: '完善个人信息' })).toBeInTheDocument();
  });
});
