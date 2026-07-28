import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import AppShell from './AppShell';

function renderShell(props = {}) {
  return render(<AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="dashboard" onNavigate={vi.fn()} onLogout={vi.fn()} {...props}><div>Dashboard content</div></AppShell>);
}

describe('AppShell', () => {
  it('renders a top navigation instead of the former global sidebar', () => {
    renderShell();

    expect(document.querySelector('.app-shell__topbar')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: '平台导航' })).toBeInTheDocument();
    expect(screen.queryByRole('complementary')).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: '平台首页' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: '返回主页' })).toHaveLength(2);
  });

  it('keeps the learning path compact and returns task details to the path', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    const { rerender } = renderShell({ currentPage: 'learning-path', onNavigate });

    expect(document.querySelector('.app-shell__page-header')).not.toBeInTheDocument();

    rerender(
      <AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="learning-path-tasks" onNavigate={onNavigate} onLogout={vi.fn()}>
        <div>Task content</div>
      </AppShell>,
    );
    await user.click(screen.getByRole('button', { name: '返回' }));

    expect(onNavigate).toHaveBeenCalledWith({ page: 'learning-path', params: {} });
  });

  it('opens teaching resources directly without a dropdown', async () => {
    const user = userEvent.setup();
    const onNavigate = vi.fn();
    renderShell({ onNavigate });
    const resources = screen.getByRole('link', { name: '教学资源' });

    expect(resources).not.toHaveAttribute('aria-haspopup');
    expect(resources.querySelector('svg')).not.toBeInTheDocument();
    await user.click(resources);
    expect(onNavigate).toHaveBeenCalledWith({ page: 'practice', params: {} });
    expect(screen.queryByRole('menu', { name: '教学资源菜单' })).not.toBeInTheDocument();
  });

  it('places the learning target dropdown first in the desktop navigation', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      const path = String(url);
      const payload = path.endsWith('/qualification-targets')
        ? { items: [
          { target_id: 'target-a', exam_track_id: 'track-a', official_name: '中医执业医师资格考试' },
          { target_id: 'target-b', exam_track_id: 'track-b', official_name: '中医执业助理医师资格考试' },
        ] }
        : path.endsWith('/personalization/learning-target') && options.method === 'PUT'
          ? { target: { exam_track_id: 'track-b' } }
          : path.endsWith('/personalization/learning-target')
            ? { target: { exam_track_id: 'track-a' } }
            : {};
      return Promise.resolve({ ok: true, status: 200, text: async () => JSON.stringify(payload) });
    }));
    const onNavigate = vi.fn();
    const targetChanged = vi.fn();
    window.addEventListener('shizhen:learning-target-changed', targetChanged);
    renderShell({ onNavigate });

    const navigation = screen.getByRole('navigation', { name: '平台导航' });
    const entries = [...navigation.querySelectorAll(':scope > .app-shell__nav-group')];
    expect(entries[0]).toHaveTextContent('考试类别');
    expect(entries[1]).toHaveTextContent('学习路径');

    const targetButton = screen.getByRole('button', { name: '考试类别' });
    await user.hover(targetButton);
    expect(targetButton).toHaveAttribute('aria-expanded', 'true');
    const currentTarget = await screen.findByRole('menuitemradio', { name: '中医执业医师资格考试' });
    const nextTarget = screen.getByRole('menuitemradio', { name: '中医执业助理医师资格考试' });
    expect(currentTarget).toHaveAttribute('aria-checked', 'true');
    expect(nextTarget).toHaveAttribute('aria-checked', 'false');
    await user.click(nextTarget);
    await waitFor(() => expect(targetButton).toHaveAttribute('aria-expanded', 'false'));
    expect(onNavigate).toHaveBeenCalledWith({
      page: 'learning-path',
      params: {
        targetId: 'target-b',
        examTrackId: 'track-b',
        textbookRouteId: '',
      },
    });
    expect(targetChanged).toHaveBeenCalledWith(expect.objectContaining({ detail: expect.objectContaining({ target_id: 'target-b' }) }));
    window.removeEventListener('shizhen:learning-target-changed', targetChanged);
  });

  it('returns to the dashboard from the brand identity', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ currentPage: 'personalization', onNavigate });

    const home = screen.getAllByRole('button', { name: '返回主页' })[0];
    await user.click(home);

    expect(onNavigate).toHaveBeenCalledWith({ page: 'dashboard', params: {} });
  });

  it('returns home and closes the mobile drawer from its brand identity', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ currentPage: 'personalization', onNavigate });

    await user.click(screen.getByRole('button', { name: '打开导航菜单' }));
    const drawer = screen.getByRole('dialog', { name: '主导航' });
    await user.click(within(drawer).getByRole('button', { name: '返回主页' }));

    expect(onNavigate).toHaveBeenCalledWith({ page: 'dashboard', params: {} });
    expect(drawer.parentElement).toHaveAttribute('data-state', 'closing');
  });

  it('opens the learning path directly without a dropdown', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ onNavigate });

    const learningPath = screen.getByRole('link', { name: '学习路径' });
    expect(learningPath).not.toHaveAttribute('aria-haspopup');
    await user.click(learningPath);

    expect(onNavigate).toHaveBeenCalledWith({ page: 'learning-path', params: {} });
    expect(screen.queryByRole('menuitem', { name: '路径规划' })).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: '当前阶段' })).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: '教材学习' })).not.toBeInTheDocument();
  });

  it('navigates with the persisted dropdown intent and closes the menu', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ onNavigate });

    await user.hover(screen.getByRole('link', { name: '训练工坊' }));
    await user.click(screen.getByRole('menuitem', { name: '试卷生成' }));

    expect(onNavigate).toHaveBeenCalledWith({ page: 'training-workshop', params: { taskType: 'paper_generation' } });
    expect(screen.queryByRole('menuitem', { name: '试卷生成' })).not.toBeInTheDocument();
  });

  it('closes the training menu 500ms after the pointer leaves, with an exit phase', async () => {
    vi.useFakeTimers();
    try {
      renderShell();
      const trigger = screen.getByRole('link', { name: '训练工坊' });
      const group = trigger.closest('.app-shell__nav-group');

      fireEvent.mouseEnter(group);
      expect(screen.getByRole('menu', { name: '训练工坊菜单' })).toHaveAttribute('data-state', 'open');
      fireEvent.mouseLeave(group);
      await act(() => vi.advanceTimersByTimeAsync(499));
      expect(screen.getByRole('menu', { name: '训练工坊菜单' })).toHaveAttribute('data-state', 'open');
      await act(() => vi.advanceTimersByTimeAsync(1));
      expect(screen.getByRole('menu', { name: '训练工坊菜单' })).toHaveAttribute('data-state', 'closing');
      await act(() => vi.runOnlyPendingTimersAsync());
      expect(screen.queryByRole('menu', { name: '训练工坊菜单' })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('opens the user profile directly from personalization without a dropdown', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ onNavigate });

    const personalization = screen.getByRole('link', { name: '个性数据' });
    expect(personalization).not.toHaveAttribute('aria-haspopup');
    await user.click(personalization);

    expect(onNavigate).toHaveBeenCalledWith({
      page: 'personalization',
      params: { view: 'user-profile' },
    });
    expect(screen.queryByRole('menu', { name: '个性数据菜单' })).not.toBeInTheDocument();
  });

  it('shows settings, notifications, and logout in the avatar menu', async () => {
    const onNavigate = vi.fn();
    const onLogout = vi.fn();
    const user = userEvent.setup();
    renderShell({ onNavigate, onLogout });

    await user.click(screen.getByRole('button', { name: '打开用户菜单' }));
    expect(screen.getByRole('menuitem', { name: '用户设置' })).toBeVisible();
    expect(screen.getByRole('menuitem', { name: /系统通知/ })).toBeVisible();
    expect(screen.getByRole('menuitem', { name: '退出登录' })).toBeVisible();

    await user.click(screen.getByRole('menuitem', { name: '用户设置' }));
    expect(onNavigate).toHaveBeenCalledWith({ page: 'settings', params: { view: 'account' } });
  });

  it('closes the avatar menu with Escape and an outside click', async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole('button', { name: '打开用户菜单' }));
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('menuitem', { name: '用户设置' })).not.toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: '打开用户菜单' }));
    await user.click(screen.getByText('Dashboard content'));
    await waitFor(() => expect(screen.queryByRole('menuitem', { name: '用户设置' })).not.toBeInTheDocument());
  });

  it('keeps the notification shortcut and workspace scroll contract', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    const { rerender } = renderShell({ onNavigate, currentPage: 'assistant' });

    expect(screen.getByRole('main')).toHaveAttribute('data-mode', 'workspace');
    expect(screen.getByRole('main')).toHaveAttribute('data-scroll-region', 'contained');
    await user.click(screen.getAllByRole('button', { name: '通知，0 条未读' })[0]);
    expect(onNavigate).toHaveBeenCalledWith({ page: 'settings', params: { view: 'governance' } });

    rerender(<AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="training-workshop" onNavigate={onNavigate} onLogout={vi.fn()}><div>Workshop</div></AppShell>);
    expect(screen.getByRole('main')).toHaveAttribute('data-mode', 'workspace');
    expect(screen.getByRole('main')).toHaveAttribute('data-scroll-region', 'page');
  });

  it('uses a touch-operable mobile drawer accordion', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ onNavigate });

    const menuButton = screen.getByRole('button', { name: '打开导航菜单' });
    await user.click(menuButton);
    expect(screen.getByRole('dialog', { name: '主导航' })).toBeVisible();
    const expandWorkshop = screen.getByRole('button', { name: '展开训练工坊' });
    await user.click(expandWorkshop);
    expect(expandWorkshop).toHaveAttribute('aria-expanded', 'true');
    await user.click(screen.getByRole('menuitem', { name: '题目训练' }));
    expect(onNavigate).toHaveBeenCalledWith({ page: 'training-workshop', params: { taskType: 'topic_training' } });
    await waitFor(() => expect(menuButton).toHaveAttribute('aria-expanded', 'false'));
  });

  it('keeps mobile drawer mounted for its exit motion', async () => {
    const user = userEvent.setup();
    const { container } = renderShell();
    await user.click(screen.getByRole('button', { name: '打开导航菜单' }));
    await user.click(screen.getByRole('button', { name: '关闭导航菜单' }));
    expect(container.querySelector('.app-shell__drawer-backdrop')).toHaveAttribute('data-state', 'closing');
    await waitFor(() => expect(container.querySelector('.app-shell__drawer-backdrop')).not.toBeInTheDocument(), { timeout: 500 });
  });
});
