import React, { useEffect, useRef, useState } from 'react';
import {
  BookOpen,
  ChartNoAxesColumnIncreasing,
  ClipboardList,
  Database,
  Dumbbell,
  Home,
  LogOut,
  Menu,
  MessageSquareMore,
  Settings,
  ShieldCheck,
  Sprout,
  X,
} from 'lucide-react';
import { getAppShellConfig } from '../appShell';
import HomeButton from './HomeButton';
import { useModalFocus } from './ui/useModalFocus';

const navIconMap = {
  dashboard: Home,
  assistant: MessageSquareMore,
  practice: Dumbbell,
  knowledge: BookOpen,
  'question-workspace': ClipboardList,
  personalization: ChartNoAxesColumnIncreasing,
  settings: Settings,
  'admin-feedback': ShieldCheck,
  'admin-knowledge': Database,
};

function NavItems({ items, currentPage, collapsed, onNavigate }) {
  return (
    <nav aria-label="平台导航" className="app-shell__nav">
      {items.map((item) => {
        const Icon = navIconMap[item.key] || Home;
        const active = currentPage === item.key;
        return (
          <a
            key={item.key}
            href={`#${item.key}`}
            aria-current={active ? 'page' : undefined}
            className="app-shell__nav-item"
            title={collapsed ? item.label : undefined}
            onClick={(event) => {
              event.preventDefault();
              onNavigate({ page: item.key, params: {} });
            }}
          >
            <Icon aria-hidden="true" size={19} />
            <span className={collapsed ? 'sr-only' : undefined}>{item.label}</span>
          </a>
        );
      })}
    </nav>
  );
}

function ShellIdentity({ collapsed, onToggleCollapsed }) {
  const mark = <Sprout aria-hidden="true" size={21} />;
  return (
    <div className="app-shell__identity">
      {onToggleCollapsed ? (
        <button
          type="button"
          className="app-shell__mark app-shell__identity-toggle"
          aria-label={collapsed ? '展开侧栏' : '折叠侧栏'}
          aria-expanded={!collapsed}
          title={collapsed ? '展开侧栏' : '折叠侧栏'}
          onClick={onToggleCollapsed}
        >
          {mark}
        </button>
      ) : <div className="app-shell__mark">{mark}</div>}
      <div className={collapsed ? 'sr-only' : undefined}>
        <strong>时珍智训</strong>
        <span>中医药备考平台</span>
      </div>
    </div>
  );
}

function MobileDrawer({ mounted, open, shell, onClose, onNavigate, onLogout, displayName }) {
  const dialogRef = useModalFocus(open);
  if (!mounted) return null;
  return (
    <div className="app-shell__drawer-backdrop" data-state={open ? 'open' : 'closing'} onMouseDown={onClose}>
      <aside
        ref={dialogRef}
        aria-label="主导航"
        role="dialog"
        aria-modal="true"
        tabIndex={-1}
        className="app-shell__drawer"
        data-state={open ? 'open' : 'closing'}
        aria-hidden={open ? undefined : true}
        inert={open ? undefined : true}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key !== 'Escape') return;
          event.stopPropagation();
          onClose();
        }}
      >
        <div className="app-shell__drawer-head">
          <ShellIdentity collapsed={false} />
          <button type="button" data-autofocus className="icon-button" aria-label="关闭导航菜单" onClick={onClose}>
            <X aria-hidden="true" size={20} />
          </button>
        </div>
        <NavItems
          items={shell.primaryNav}
          currentPage={shell.currentPage}
          collapsed={false}
          onNavigate={(intent) => { onNavigate(intent); onClose(); }}
        />
        {shell.supportNav.length > 0 && (
          <div className="app-shell__support">
            <span className="app-shell__section-label">支持入口</span>
            <NavItems
              items={shell.supportNav}
              currentPage={shell.currentPage}
              collapsed={false}
              onNavigate={(intent) => { onNavigate(intent); onClose(); }}
            />
          </div>
        )}
        <div className="app-shell__drawer-account">
          <span>{displayName}</span>
          <button type="button" className="button button--secondary" onClick={onLogout}>
            <LogOut aria-hidden="true" size={16} />退出登录
          </button>
        </div>
      </aside>
    </div>
  );
}

export default function AppShell({ currentUser, currentPage, onNavigate, onLogout, children }) {
  const shell = getAppShellConfig({ currentUser, currentPage });
  const [collapsed, setCollapsed] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerMounted, setDrawerMounted] = useState(false);
  const drawerExitTimerRef = useRef(null);
  const displayName = currentUser?.username || 'User';
  const shouldShowHomeButton = shell.homeAction && !['settings', 'personalization', 'practice'].includes(shell.currentPage);
  const scrollRegion = ['assistant', 'knowledge'].includes(shell.currentPage) ? 'contained' : 'page';

  useEffect(() => () => window.clearTimeout(drawerExitTimerRef.current), []);

  const openDrawer = () => {
    window.clearTimeout(drawerExitTimerRef.current);
    setDrawerMounted(true);
    setDrawerOpen(true);
  };

  const closeDrawer = () => {
    setDrawerOpen(false);
    window.clearTimeout(drawerExitTimerRef.current);
    drawerExitTimerRef.current = window.setTimeout(() => setDrawerMounted(false), 160);
  };

  return (
    <div className="app-shell" data-mode={shell.shellMode}>
      <aside className="app-shell__sidebar" data-collapsed={String(collapsed)}>
        <div className="app-shell__sidebar-head">
          <ShellIdentity collapsed={collapsed} onToggleCollapsed={() => setCollapsed((value) => !value)} />
        </div>

        <NavItems items={shell.primaryNav} currentPage={shell.currentPage} collapsed={collapsed} onNavigate={onNavigate} />

        {shell.supportNav.length > 0 && (
          <div className="app-shell__support">
            <span className={collapsed ? 'sr-only' : 'app-shell__section-label'}>支持入口</span>
            <NavItems items={shell.supportNav} currentPage={shell.currentPage} collapsed={collapsed} onNavigate={onNavigate} />
          </div>
        )}

        <div className="app-shell__account">
          <div className={collapsed ? 'sr-only' : undefined}>
            <span className="app-shell__section-label">当前用户</span>
            <strong>{displayName}</strong>
            <small>{currentUser?.role === 'admin' ? '管理员支持权限' : '个人学习者'}</small>
          </div>
          <button type="button" className="icon-button" aria-label="退出登录" onClick={onLogout}>
            <LogOut aria-hidden="true" size={17} />
          </button>
        </div>
      </aside>

      <div className="app-shell__workspace">
        <header className="app-shell__mobile-header">
          <button
            type="button"
            className="icon-button"
            aria-label="打开导航菜单"
            aria-expanded={drawerOpen}
            onClick={openDrawer}
          >
            <Menu aria-hidden="true" size={21} />
          </button>
          <ShellIdentity collapsed={false} />
          <span className="app-shell__mobile-user">{displayName}</span>
        </header>

        <main
          className="app-shell__main"
          data-page={shell.currentPage}
          data-mode={shell.shellMode}
          data-scroll-region={scrollRegion}
        >
          {shell.currentPage !== 'dashboard' && shell.shellMode !== 'workspace' && (
            <header className="app-shell__page-header">
              {shouldShowHomeButton && <HomeButton onClick={() => onNavigate({ page: shell.homeAction.key, params: {} })} label={shell.homeAction.label} />}
              <div>
                <span className="app-shell__section-label">当前模块</span>
                <h1>{shell.pageTitle}</h1>
              </div>
            </header>
          )}
          {children}
        </main>
      </div>

      <MobileDrawer
        mounted={drawerMounted}
        open={drawerOpen}
        shell={shell}
        displayName={displayName}
        onClose={closeDrawer}
        onNavigate={onNavigate}
        onLogout={onLogout}
      />
    </div>
  );
}
