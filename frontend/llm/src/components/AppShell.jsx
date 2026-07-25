import React, { useEffect, useRef, useState } from 'react';
import {
  BookOpen,
  Bell,
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
  UserRound,
  X,
} from 'lucide-react';
import { getAppShellConfig } from '../appShell';
import HomeButton from './HomeButton';
import UserProfileModal from './UserProfileModal';
import { useModalFocus } from './ui/useModalFocus';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';

const navIconMap = {
  dashboard: Home,
  assistant: MessageSquareMore,
  practice: ClipboardList,
  'training-workshop': Dumbbell,
  knowledge: BookOpen,
  'question-workspace': ClipboardList,
  personalization: ChartNoAxesColumnIncreasing,
  settings: Settings,
  'admin-feedback': ShieldCheck,
  'admin-knowledge': Database,
};

function NavItems({ items, currentPage, onNavigate }) {
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
            onClick={(event) => {
              event.preventDefault();
              onNavigate({ page: item.key, params: {} });
            }}
          >
            <Icon aria-hidden="true" size={19} />
            <span>{item.label}</span>
          </a>
        );
      })}
    </nav>
  );
}

function ShellIdentity() {
  const mark = <Sprout aria-hidden="true" size={21} />;
  return (
    <div className="app-shell__identity">
      <div className="app-shell__mark">{mark}</div>
      <div>
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
          <ShellIdentity />
          <button type="button" data-autofocus className="icon-button" aria-label="关闭导航菜单" onClick={onClose}>
            <X aria-hidden="true" size={20} />
          </button>
        </div>
        <NavItems
          items={shell.primaryNav}
          currentPage={shell.currentPage}
          onNavigate={(intent) => { onNavigate(intent); onClose(); }}
        />
        {shell.supportNav.length > 0 && (
          <div className="app-shell__support">
            <span className="app-shell__section-label">支持入口</span>
            <NavItems
              items={shell.supportNav}
              currentPage={shell.currentPage}
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

export default function AppShell({ currentUser, currentPage, onNavigate, onLogout, onUserUpdated, children }) {
  const shell = getAppShellConfig({ currentUser, currentPage });
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerMounted, setDrawerMounted] = useState(false);
  const [unreadNotifications, setUnreadNotifications] = useState(0);
  const drawerExitTimerRef = useRef(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [accountProfile, setAccountProfile] = useState(null);
  const displayName = currentUser?.display_name || currentUser?.username || 'User';
  const avatarUrl = accountProfile?.avatar_url || null;
  const avatarInitial = displayName.trim().slice(0, 1).toUpperCase() || '用';
  const shouldShowHomeButton = shell.homeAction && !['settings', 'personalization', 'practice', 'training-workshop'].includes(shell.currentPage);
  const shouldShowPageHeader = shell.currentPage !== 'dashboard'
    && shell.shellMode !== 'workspace'
    && !['personalization', 'settings'].includes(shell.currentPage);
  const scrollRegion = ['assistant', 'knowledge'].includes(shell.currentPage) ? 'contained' : 'page';

  useEffect(() => () => window.clearTimeout(drawerExitTimerRef.current), []);
  useEffect(() => {
    let cancelled = false;
    const loadUnread = async () => {
      try {
        const response = await fetchWithAuth(`${API_BASE}/v1/notifications?status=unread&limit=1`);
        const payload = await readJsonResponse(response, {});
        if (!cancelled && response.ok) setUnreadNotifications(Number(payload.unread_count) || 0);
      } catch {
        if (!cancelled) setUnreadNotifications(0);
      }
    };
    loadUnread();
    return () => { cancelled = true; };
  }, [currentPage]);

  useEffect(() => {
    let cancelled = false;
    const loadAccountProfile = async () => {
      try {
        const response = await fetchWithAuth(`${API_BASE}/v1/auth/me/profile`);
        const payload = await readJsonResponse(response, {});
        if (!cancelled && response.ok) setAccountProfile(payload.profile || null);
      } catch {
        if (!cancelled) setAccountProfile(null);
      }
    };
    loadAccountProfile();
    return () => { cancelled = true; };
  }, [currentUser?.user_id]);

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

  const handleProfileSaved = (updatedUser, profile) => {
    if (profile) setAccountProfile(profile);
    if (updatedUser) onUserUpdated?.(updatedUser);
  };

  return (
    <div className="app-shell" data-mode={shell.shellMode}>
      <aside className="app-shell__sidebar" data-collapsed="false">
        <div className="app-shell__sidebar-head">
          <ShellIdentity />
        </div>

        <NavItems items={shell.primaryNav} currentPage={shell.currentPage} onNavigate={onNavigate} />

        {shell.supportNav.length > 0 && (
          <div className="app-shell__support">
            <span className="app-shell__section-label">支持入口</span>
            <NavItems items={shell.supportNav} currentPage={shell.currentPage} onNavigate={onNavigate} />
          </div>
        )}

        <div className="app-shell__account">
          <button type="button" className="app-shell__avatar-button" aria-label="打开个人信息" onClick={() => setProfileOpen(true)}>
            {avatarUrl ? <img src={avatarUrl} alt="" /> : <span>{avatarInitial}</span>}
            <i aria-hidden="true"><UserRound size={13} /></i>
          </button>
          <div className="app-shell__account-summary" onClick={() => setProfileOpen(true)} role="button" tabIndex={0} onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setProfileOpen(true); } }}>
            <span className="app-shell__section-label">当前用户</span>
            <strong>{displayName}</strong>
            <small>{currentUser?.role === 'admin' ? '管理员支持权限' : '个人学习者'}</small>
          </div>
          <div className="app-shell__account-actions">
            <button type="button" className="icon-button relative" aria-label={`通知，${unreadNotifications} 条未读`} onClick={() => onNavigate({ page: 'settings', params: { view: 'governance' } })}>
              <Bell aria-hidden="true" size={17} />
              {unreadNotifications > 0 && <span className="absolute -right-1 -top-1 min-w-4 rounded-full bg-amber-500 px-1 text-[10px] font-semibold leading-4 text-white">{unreadNotifications > 99 ? '99+' : unreadNotifications}</span>}
            </button>
            <button type="button" className="icon-button" aria-label="退出登录" onClick={onLogout}>
              <LogOut aria-hidden="true" size={17} />
            </button>
          </div>
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
          <ShellIdentity />
          <button type="button" className="icon-button relative" aria-label={`通知，${unreadNotifications} 条未读`} onClick={() => onNavigate({ page: 'settings', params: { view: 'governance' } })}><Bell aria-hidden="true" size={18} />{unreadNotifications > 0 && <span className="absolute -right-1 -top-1 min-w-4 rounded-full bg-amber-500 px-1 text-[10px] font-semibold leading-4 text-white">{unreadNotifications > 99 ? '99+' : unreadNotifications}</span>}</button>
        </header>

        <main
          className="app-shell__main"
          data-page={shell.currentPage}
          data-mode={shell.shellMode}
          data-scroll-region={scrollRegion}
        >
          {shouldShowPageHeader && (
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
      <UserProfileModal
        open={profileOpen}
        currentUser={currentUser}
        onClose={() => setProfileOpen(false)}
        onSaved={handleProfileSaved}
      />
    </div>
  );
}
