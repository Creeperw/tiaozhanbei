import React from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { cwd } from 'node:process';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import AppShell from './AppShell';

function renderShell(props = {}) {
  return render(<AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="dashboard" onNavigate={vi.fn()} onLogout={vi.fn()} {...props}><div>Dashboard content</div></AppShell>);
}

describe('AppShell', () => {
  it('marks the desktop AI assistant entry as the featured action', () => {
    const onNavigate = vi.fn();
    renderShell({ onNavigate });

    const entry = screen.getByRole('button', { name: 'AI 智能助手' });
    expect(entry).toHaveClass('app-shell__assistant-entry--featured');
    fireEvent.click(entry);
    expect(onNavigate).toHaveBeenCalledWith({ page: 'assistant', params: { newConversation: true } });
  });

  it('keeps the compact featured topbar layout from the home-login navigation branch', () => {
    renderShell();

    const stylesheet = readFileSync(resolve(cwd(), 'src/index.css'), 'utf8');
    expect(stylesheet).toMatch(/\.app-shell__topbar-inner\s*\{[^}]*min-height:\s*76px;/s);
    expect(stylesheet).toMatch(/\.app-shell__assistant-entry--featured\s*\{[^}]*background:\s*linear-gradient\(/s);
  });

  it('shows a login entry instead of the profile menu for visitors', async () => {
    const onLoginRequested = vi.fn();
    const user = userEvent.setup();
    renderShell({ currentUser: null, onLoginRequested });

    expect(screen.getByText('未登录')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '打开用户菜单' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '登录' }));
    expect(onLoginRequested).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole('button', { name: '打开导航菜单' }));
    const drawer = screen.getByRole('dialog', { name: '主导航' });
    expect(within(drawer).getByText('未登录')).toBeInTheDocument();
    await user.click(within(drawer).getByRole('button', { name: '登录' }));
    expect(onLoginRequested).toHaveBeenCalledTimes(2);
  });

  it('opens login instead of navigating when a visitor uses desktop menu actions', async () => {
    const onLoginRequested = vi.fn();
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ currentUser: null, onLoginRequested, onNavigate });

    const actions = [
      screen.getByRole('button', { name: '考试类别' }),
      screen.getByRole('link', { name: '学习路径' }),
      screen.getByRole('link', { name: '教学资源' }),
      screen.getByRole('link', { name: '练习工坊' }),
      screen.getByRole('link', { name: '个人数据' }),
      screen.getByRole('button', { name: 'AI 智能助手' }),
    ];

    for (const action of actions) await user.click(action);

    expect(onLoginRequested).toHaveBeenCalledTimes(actions.length);
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it('keeps visitor qualification options available while direct navigation entries route to login', async () => {
    const onLoginRequested = vi.fn();
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ currentUser: null, onLoginRequested, onNavigate });

    await user.hover(screen.getByRole('button', { name: '考试类别' }));
    const targetOption = screen.getByRole('menuitemradio', { name: '中医执业医师资格考试' });
    expect(screen.getByRole('menuitemradio', { name: '执业药师职业资格考试（中药学类）' })).toBeVisible();
    await user.click(targetOption);

    expect(onLoginRequested).toHaveBeenCalledTimes(1);
    expect(onNavigate).not.toHaveBeenCalled();
  });

  it('opens login from visitor mobile navigation actions instead of expanding or navigating', async () => {
    const onLoginRequested = vi.fn();
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ currentUser: null, onLoginRequested, onNavigate });

    await user.click(screen.getByRole('button', { name: '打开导航菜单' }));
    const firstDrawer = screen.getByRole('dialog', { name: '主导航' });
    await user.click(within(firstDrawer).getByRole('link', { name: '学习路径' }));
    expect(onLoginRequested).toHaveBeenCalledTimes(1);
    expect(onNavigate).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: '打开导航菜单' }));
    const secondDrawer = screen.getByRole('dialog', { name: '主导航' });
    await user.click(within(secondDrawer).getByRole('link', { name: '练习工坊' }));
    expect(onLoginRequested).toHaveBeenCalledTimes(2);
    expect(onNavigate).not.toHaveBeenCalled();
  });

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
    expect(screen.getByRole('main')).toHaveAttribute('data-scroll-region', 'page');

    rerender(
      <AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="learning-path-tasks" onNavigate={onNavigate} onLogout={vi.fn()}>
        <div>Task content</div>
      </AppShell>,
    );
    await user.click(screen.getByRole('button', { name: '返回' }));

    expect(onNavigate).toHaveBeenCalledWith({ page: 'learning-path', params: {} });
  });

  it('places the platform-home return beside the capability module title', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ currentPage: 'capability-detail', onNavigate });

    const pageHeader = document.querySelector('.app-shell__page-header');
    const platformHome = within(pageHeader).getByRole('button', { name: '返回平台首页' });

    expect(within(pageHeader).getByRole('heading', { level: 1, name: '平台核心能力' })).toBeInTheDocument();
    expect(within(pageHeader).queryByRole('button', { name: '返回主页' })).not.toBeInTheDocument();
    expect(platformHome).toBeInTheDocument();

    await user.click(platformHome);
    expect(onNavigate).toHaveBeenCalledWith({ page: 'dashboard', params: {} });
  });

  it('aligns the capability title group with the detail content edge', () => {
    const css = readFileSync(resolve(cwd(), 'src/index.css'), 'utf8');

    expect(css).toMatch(
      /\.app-shell__main\[data-page="capability-detail"\] > \.app-shell__page-header > div\s*\{[^}]*margin-left:\s*max\(\s*clamp\(20px, 4vw, 58px\),\s*calc\(\(100% - 1360px\) \/ 2 \+ clamp\(20px, 4vw, 58px\)\)\s*\);/,
    );
    expect(css).toMatch(
      /@media \(max-width: 700px\)[\s\S]*?\.app-shell__main\[data-page="capability-detail"\] > \.app-shell__page-header > div\s*\{[^}]*margin-left:\s*16px;/,
    );
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
    expect(targetButton.closest('.app-shell__target-group')).toHaveAttribute('data-has-current-target', 'true');
    const currentTarget = await screen.findByRole('menuitemradio', { name: '中医执业医师资格考试' });
    const nextTarget = screen.getByRole('menuitemradio', { name: '中医执业助理医师资格考试' });
    const currentTargetLabel = screen.getByText('当前 · 中医执业医师资格考试');
    expect(currentTargetLabel).toHaveClass('app-shell__target-current');
    expect(currentTargetLabel).toHaveAttribute('title', '中医执业医师资格考试');
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
    window.dispatchEvent(new CustomEvent('shizhen:learning-target-changed', {
      detail: { official_name: '执业药师职业资格考试（中药学类）' },
    }));
    await waitFor(() => expect(screen.getByText('当前 · 执业药师职业资格考试（中药学类）')).toBeInTheDocument());
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
    const currentIntent = {
      page: 'learning-path',
      params: { targetId: 'target-a', examTrackId: 'track-a' },
    };
    renderShell({ onNavigate, currentIntent, currentPage: 'learning-path' });

    const learningPath = screen.getByRole('link', { name: '学习路径' });
    expect(learningPath).not.toHaveAttribute('aria-haspopup');
    await user.click(learningPath);

    expect(onNavigate).toHaveBeenCalledWith({ page: 'learning-path', params: {} });
    expect(screen.queryByRole('menuitem', { name: '路径规划' })).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: '当前阶段' })).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: '教材学习' })).not.toBeInTheDocument();
  });

  it('opens the learning report by default and exposes personalization views in a dropdown', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ onNavigate });

    const reports = screen.getByRole('link', { name: '个人数据' });
    expect(reports).toHaveAttribute('aria-haspopup', 'menu');
    await user.hover(reports);
    expect(screen.getByRole('menuitem', { name: '学情报告' })).toBeVisible();
    expect(screen.getByRole('menuitem', { name: '学习画像' })).toBeVisible();
    expect(screen.getByRole('menuitem', { name: '复习与掌握' })).toBeVisible();
    await user.click(reports);

    expect(onNavigate).toHaveBeenCalledWith({
      page: 'personalization',
      params: { view: 'reports' },
    });
  });

  it('highlights the current personal data view in its dropdown', async () => {
    const user = userEvent.setup();
    renderShell({ currentPage: 'personalization', navigationContext: { view: 'user-profile' } });

    await user.hover(screen.getByRole('link', { name: '个人数据' }));
    expect(screen.getByRole('menuitem', { name: '学习画像' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('menuitem', { name: '学情报告' })).not.toHaveAttribute('aria-current');
  });

  it('shows account settings and logout in the avatar menu', async () => {
    const onNavigate = vi.fn();
    const onLogout = vi.fn();
    const user = userEvent.setup();
    renderShell({ onNavigate, onLogout });

    await user.click(screen.getByRole('button', { name: '打开用户菜单' }));
    expect(screen.queryByRole('menuitem', { name: '用户设置' })).not.toBeInTheDocument();
    expect(screen.queryByRole('menuitem', { name: /系统通知/ })).not.toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: '账号设置' })).toBeVisible();
    expect(screen.getByRole('menuitem', { name: '退出登录' })).toBeVisible();

  });

  it('routes avatar shortcuts to their matching destinations', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    const currentIntent = {
      page: 'learning-path',
      params: { targetId: 'target-a', examTrackId: 'track-a' },
    };
    renderShell({ onNavigate, currentIntent, currentPage: 'learning-path' });

    const clickShortcut = async (name) => {
      await user.click(screen.getByRole('button', { name: '打开用户菜单' }));
      await user.click(screen.getByRole('menuitem', { name: new RegExp(name) }));
    };

    await clickShortcut('我的题单');
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: { view: 'workspace', taskType: 'question_favorites', returnTo: currentIntent },
    });

    await clickShortcut('笔记本');
    expect(onNavigate).toHaveBeenLastCalledWith({
      page: 'practice',
      params: { view: 'workspace', taskType: 'study_notes', returnTo: currentIntent },
    });

    await user.click(screen.getByRole('button', { name: '打开用户菜单' }));
    expect(screen.queryByRole('menuitem', { name: /学情分析/ })).not.toBeInTheDocument();
  });

  it('closes the avatar menu with Escape and an outside click', async () => {
    const user = userEvent.setup();
    renderShell();

    await user.click(screen.getByRole('button', { name: '打开用户菜单' }));
    await user.keyboard('{Escape}');
    await waitFor(() => expect(screen.queryByRole('menuitem', { name: '账号设置' })).not.toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: '打开用户菜单' }));
    await user.click(screen.getByText('Dashboard content'));
    await waitFor(() => expect(screen.queryByRole('menuitem', { name: '账号设置' })).not.toBeInTheDocument());
  });

  it('keeps the notification shortcut and workspace scroll contract', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    const { rerender } = renderShell({ onNavigate, currentPage: 'assistant' });

    expect(screen.getByRole('main')).toHaveAttribute('data-mode', 'workspace');
    expect(screen.getByRole('main')).toHaveAttribute('data-scroll-region', 'contained');
    await user.click(screen.getAllByRole('button', { name: '通知，0 条未读' })[0]);
    expect(screen.getByRole('dialog', { name: '未处理通知' })).toBeVisible();
    expect(onNavigate).not.toHaveBeenCalled();

    rerender(<AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="training-workshop" onNavigate={onNavigate} onLogout={vi.fn()}><div>Workshop</div></AppShell>);
    expect(screen.getByRole('main')).toHaveAttribute('data-mode', 'workspace');
    expect(screen.getByRole('main')).toHaveAttribute('data-scroll-region', 'page');
  });

  it('uses a touch-operable mobile drawer', async () => {
    const onNavigate = vi.fn();
    const user = userEvent.setup();
    renderShell({ onNavigate });

    const menuButton = screen.getByRole('button', { name: '打开导航菜单' });
    await user.click(menuButton);
    expect(screen.getByRole('dialog', { name: '主导航' })).toBeVisible();
    await user.click(within(screen.getByRole('dialog', { name: '主导航' })).getByRole('link', { name: '练习工坊' }));
    expect(onNavigate).toHaveBeenCalledWith({ page: 'training-workshop', params: {} });
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
