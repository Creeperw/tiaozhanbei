import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
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
    expect(screen.getByRole('link', { name: '平台首页' })).toHaveAttribute('aria-current', 'page');
  });

  it('opens a desktop module menu by hover and keyboard focus', async () => {
    const user = userEvent.setup();
    renderShell();
    const workshop = screen.getByRole('link', { name: '学习工坊' });

    await user.hover(workshop);
    expect(screen.getByRole('menuitem', { name: '智能助教' })).toBeVisible();

    await user.unhover(workshop);
    workshop.focus();
    await user.keyboard('{ArrowDown}');
    await waitFor(() => expect(screen.getByRole('menuitem', { name: '智能助教' })).toHaveFocus());
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
    expect(screen.queryByRole('menuitem', { name: '用户设置' })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '打开用户菜单' }));
    await user.click(screen.getByText('Dashboard content'));
    expect(screen.queryByRole('menuitem', { name: '用户设置' })).not.toBeInTheDocument();
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
    expect(onNavigate).toHaveBeenCalledWith({ page: 'training-workshop', params: { taskType: 'question_training' } });
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
