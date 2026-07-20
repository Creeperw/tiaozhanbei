import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import AppShell from './AppShell';

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

  it('opens the training workshop in a compact workspace shell without a duplicate heading', async () => {
    const user = userEvent.setup();
    render(
      <AppShell
        currentUser={{ username: 'alice', role: 'user' }}
        currentPage="practice"
        onNavigate={vi.fn()}
        onLogout={vi.fn()}
      >
        <div>Practice content</div>
      </AppShell>,
    );

    expect(screen.getByRole('complementary')).toHaveAttribute('data-collapsed', 'true');
    expect(screen.getByRole('main')).toHaveAttribute('data-mode', 'workspace');
    expect(screen.queryByRole('heading', { name: '训练工坊' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: '学习工坊' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '展开侧栏' }));
    expect(screen.getByRole('complementary')).toHaveAttribute('data-collapsed', 'false');
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

    expect(screen.getByRole('link', { name: '知识仓库' })).toHaveAttribute('aria-current', 'page');
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
  });

  it('starts the desktop shell collapsed and expands only from the 时珍智训 icon', async () => {
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

    expect(screen.queryByRole('heading', { name: '培训助手首页' })).not.toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveAttribute('data-page', 'dashboard');
    expect(screen.getByRole('complementary')).toHaveAttribute('data-collapsed', 'true');

    await user.click(screen.getByRole('button', { name: '展开侧栏' }));
    expect(screen.getByRole('complementary')).toHaveAttribute('data-collapsed', 'false');
  });

  it('keeps a manually expanded sidebar open when changing modules', async () => {
    const user = userEvent.setup();
    const { rerender } = render(
      <AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="dashboard" onNavigate={vi.fn()} onLogout={vi.fn()}>
        <div>Dashboard content</div>
      </AppShell>,
    );

    expect(screen.getByRole('complementary')).toHaveAttribute('data-collapsed', 'true');
    await user.click(screen.getByRole('button', { name: '展开侧栏' }));
    expect(screen.getByRole('complementary')).toHaveAttribute('data-collapsed', 'false');

    rerender(
      <AppShell currentUser={{ username: 'alice', role: 'user' }} currentPage="assistant" onNavigate={vi.fn()} onLogout={vi.fn()}>
        <div>Assistant workspace</div>
      </AppShell>,
    );

    await waitFor(() => expect(screen.getByRole('complementary')).toHaveAttribute('data-collapsed', 'false'));
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
    expect(screen.queryByRole('heading', { name: '知识库' })).not.toBeInTheDocument();
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
});
