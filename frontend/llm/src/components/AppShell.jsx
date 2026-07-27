import React, { useEffect, useRef, useState } from 'react';
import { Bell, ChevronDown, LogOut, Menu, Sprout, UserRound, X } from 'lucide-react';
import { getAppShellConfig } from '../appShell';
import HomeButton from './HomeButton';
import LearningTargetSelector from './LearningTargetSelector';
import { useModalFocus } from './ui/useModalFocus';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';

function ShellIdentity() {
  return <div className="app-shell__identity"><div className="app-shell__mark"><Sprout aria-hidden="true" size={21} /></div><div><strong>时珍智训</strong><span>中医药备考平台</span></div></div>;
}

function MenuItems({ items, onNavigate, onClose }) {
  return items.map((item) => <button key={item.label} type="button" role="menuitem" onClick={() => { onNavigate(item.intent); onClose?.(); }}>{item.label}</button>);
}

function NavigationMenu({ item, currentPage, onNavigate, openKey, setOpenKey }) {
  const open = openKey === item.key;
  const ref = useRef(null);
  useEffect(() => {
    const closeOutside = (event) => { if (open && !ref.current?.contains(event.target)) setOpenKey(null); };
    document.addEventListener('mousedown', closeOutside);
    return () => document.removeEventListener('mousedown', closeOutside);
  }, [open, setOpenKey]);
  const focusItem = (position) => {
    const entries = ref.current?.querySelectorAll('[role="menuitem"]');
    entries?.[position < 0 ? entries.length - 1 : position]?.focus();
  };
  if (!item.children) {
    return <div className="app-shell__nav-group"><a href={`#${item.key}`} aria-current={currentPage === item.key ? 'page' : undefined} onClick={(event) => { event.preventDefault(); onNavigate(item.intent); }}>{item.label}</a></div>;
  }
  return <div ref={ref} className="app-shell__nav-group" onMouseEnter={() => setOpenKey(item.key)} onFocus={() => setOpenKey(item.key)} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpenKey(null); }}>
    <a href={`#${item.key}`} aria-current={currentPage === item.key ? 'page' : undefined} aria-haspopup="menu" aria-expanded={open} onClick={(event) => { event.preventDefault(); onNavigate({ page: item.key, params: {} }); }} onKeyDown={(event) => { if (event.key === 'ArrowDown') { event.preventDefault(); setOpenKey(item.key); window.setTimeout(() => focusItem(0), 0); } }}>
      {item.label}<ChevronDown aria-hidden="true" size={15} />
    </a>
    {open && <div className="app-shell__nav-menu" role="menu" aria-label={`${item.label}菜单`} onKeyDown={(event) => { const entries = [...ref.current.querySelectorAll('[role="menuitem"]')]; const index = entries.indexOf(document.activeElement); if (event.key === 'Escape') { setOpenKey(null); ref.current.querySelector('a')?.focus(); } if (event.key === 'ArrowDown') { event.preventDefault(); entries[(index + 1) % entries.length]?.focus(); } if (event.key === 'ArrowUp') { event.preventDefault(); entries[(index - 1 + entries.length) % entries.length]?.focus(); } if (event.key === 'Home') { event.preventDefault(); focusItem(0); } if (event.key === 'End') { event.preventDefault(); focusItem(-1); } }}><MenuItems items={item.children} onNavigate={onNavigate} onClose={() => setOpenKey(null)} /></div>}
  </div>;
}

function LearningTargetNavigationMenu({ openKey, setOpenKey }) {
  const open = openKey === 'learning-target';
  const ref = useRef(null);
  const triggerRef = useRef(null);

  const openSelectedPath = (selection) => {
    setOpenKey(null);
    window.dispatchEvent(new CustomEvent('competition:learning-target-selected', { detail: selection }));
  };

  useEffect(() => {
    const closeOutside = (event) => {
      if (open && !ref.current?.contains(event.target)) setOpenKey(null);
    };
    document.addEventListener('mousedown', closeOutside);
    return () => document.removeEventListener('mousedown', closeOutside);
  }, [open, setOpenKey]);

  return <div ref={ref} className="app-shell__nav-group app-shell__target-group" onMouseEnter={() => setOpenKey('learning-target')} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpenKey(null); }}>
    <button ref={triggerRef} type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpenKey(open ? null : 'learning-target')} onKeyDown={(event) => { if (event.key === 'ArrowDown') { event.preventDefault(); setOpenKey('learning-target'); window.setTimeout(() => ref.current?.querySelector('[role="menuitemradio"]')?.focus(), 0); } }}>
      学习目标<ChevronDown aria-hidden="true" size={15} />
    </button>
    {open && <div className="app-shell__nav-menu app-shell__target-menu" aria-label="选择学习目标" onKeyDown={(event) => { const entries = [...ref.current.querySelectorAll('[role="menuitemradio"]')]; const index = entries.indexOf(document.activeElement); if (event.key === 'Escape') { event.stopPropagation(); setOpenKey(null); triggerRef.current?.focus(); } if (event.key === 'ArrowDown' && entries.length) { event.preventDefault(); entries[(index + 1) % entries.length]?.focus(); } if (event.key === 'ArrowUp' && entries.length) { event.preventDefault(); entries[(index - 1 + entries.length) % entries.length]?.focus(); } if (event.key === 'Home' && entries.length) { event.preventDefault(); entries[0]?.focus(); } if (event.key === 'End' && entries.length) { event.preventDefault(); entries[entries.length - 1]?.focus(); } }}>
      <LearningTargetSelector className="app-shell__target-selector" variant="menu" onSelected={openSelectedPath} />
    </div>}
  </div>;
}

function MobileDrawer({ mounted, open, shell, onClose, onNavigate, onLogout }) {
  const dialogRef = useModalFocus(open);
  const [expanded, setExpanded] = useState(null);
  if (!mounted) return null;
  return <div className="app-shell__drawer-backdrop" data-state={open ? 'open' : 'closing'} onMouseDown={onClose}><aside ref={dialogRef} role="dialog" aria-modal="true" aria-label="主导航" tabIndex={-1} className="app-shell__drawer" data-state={open ? 'open' : 'closing'} onMouseDown={(event) => event.stopPropagation()} onKeyDown={(event) => event.key === 'Escape' && onClose()}><div className="app-shell__drawer-head"><ShellIdentity /><button type="button" data-autofocus className="icon-button" aria-label="关闭导航菜单" onClick={onClose}><X aria-hidden="true" /></button></div><nav aria-label="移动平台导航" className="app-shell__drawer-nav">{shell.primaryNav.map((item) => item.kind === 'learning-target' ? <div key={item.key} className="app-shell__drawer-module app-shell__drawer-target"><button type="button" aria-expanded={expanded === item.key} onClick={() => setExpanded(expanded === item.key ? null : item.key)}>{item.label}<ChevronDown aria-hidden="true" size={17} /></button>{expanded === item.key && <LearningTargetSelector className="app-shell__target-selector" />}</div> : <div key={item.key} className="app-shell__drawer-module"><div><a href={`#${item.key}`} aria-current={shell.currentPage === item.key ? 'page' : undefined} onClick={(event) => { event.preventDefault(); onNavigate(item.intent || { page: item.key, params: {} }); onClose(); }}>{item.label}</a>{item.children && <button type="button" aria-label={`展开${item.label}`} aria-expanded={expanded === item.key} onClick={() => setExpanded(expanded === item.key ? null : item.key)}><ChevronDown aria-hidden="true" size={17} /></button>}</div>{item.children && expanded === item.key && <div role="menu"><MenuItems items={item.children} onNavigate={onNavigate} onClose={onClose} /></div>}</div>)}</nav><button type="button" className="app-shell__drawer-logout" onClick={onLogout}><LogOut aria-hidden="true" size={17} />退出登录</button></aside></div>;
}

export default function AppShell({ currentUser, currentPage, onNavigate, onLogout, children }) {
  const shell = getAppShellConfig({ currentUser, currentPage });
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerMounted, setDrawerMounted] = useState(false);
  const [openKey, setOpenKey] = useState(null);
  const [accountOpen, setAccountOpen] = useState(false);
  const [unreadNotifications, setUnreadNotifications] = useState(0);
  const exitTimer = useRef(null);
  const displayName = currentUser?.display_name || currentUser?.username || 'User';
  const initial = displayName.trim().slice(0, 1).toUpperCase() || '用';
  const scrollRegion = ['assistant', 'knowledge'].includes(shell.currentPage) ? 'contained' : 'page';
  const shouldShowPageHeader = shell.currentPage !== 'dashboard' && shell.shellMode !== 'workspace' && !['personalization', 'settings', 'capability-detail'].includes(shell.currentPage);
  const accountMenuRef = useRef(null);
  useEffect(() => () => clearTimeout(exitTimer.current), []);
  useEffect(() => {
    if (!accountOpen) return undefined;
    const closeAccountMenu = (event) => {
      if (!accountMenuRef.current?.contains(event.target)) setAccountOpen(false);
    };
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') setAccountOpen(false);
    };
    document.addEventListener('mousedown', closeAccountMenu);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeAccountMenu);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [accountOpen]);
  useEffect(() => { let cancelled = false; fetchWithAuth(`${API_BASE}/v1/notifications?status=unread&limit=1`).then((response) => readJsonResponse(response, {}).then((payload) => { if (!cancelled && response.ok) setUnreadNotifications(Number(payload.unread_count) || 0); })).catch(() => { if (!cancelled) setUnreadNotifications(0); }); return () => { cancelled = true; }; }, [currentPage]);
  const openDrawer = () => { clearTimeout(exitTimer.current); setDrawerMounted(true); setDrawerOpen(true); };
  const closeDrawer = () => { setDrawerOpen(false); clearTimeout(exitTimer.current); exitTimer.current = setTimeout(() => setDrawerMounted(false), 160); };
  const navigate = (entry) => { onNavigate(entry); setAccountOpen(false); };
  useEffect(() => {
    const openSelectedLearningPath = (event) => {
      const selection = event.detail || {};
      navigate({
        page: 'learning-path',
        params: {
          targetId: selection.target_id || '',
          examTrackId: selection.exam_track_id || '',
          textbookRouteId: selection.textbook_route_id || '',
        },
      });
      if (drawerOpen) closeDrawer();
    };
    window.addEventListener('competition:learning-target-selected', openSelectedLearningPath);
    return () => window.removeEventListener('competition:learning-target-selected', openSelectedLearningPath);
  });
  return <div className="app-shell" data-mode={shell.shellMode}><header className="app-shell__topbar"><div className="app-shell__topbar-inner"><ShellIdentity /><nav aria-label="平台导航" className="app-shell__desktop-nav">{shell.primaryNav.map((item) => item.kind === 'learning-target' ? <LearningTargetNavigationMenu key={item.key} openKey={openKey} setOpenKey={setOpenKey} /> : <NavigationMenu key={item.key} item={item} currentPage={shell.currentPage} onNavigate={navigate} openKey={openKey} setOpenKey={setOpenKey} />)}</nav><div className="app-shell__account-menu"><button type="button" className="icon-button relative" aria-label={`通知，${unreadNotifications} 条未读`} onClick={() => navigate({ page: 'settings', params: { view: 'governance' } })}><Bell aria-hidden="true" size={18} />{unreadNotifications > 0 && <span className="app-shell__notification-count">{unreadNotifications > 99 ? '99+' : unreadNotifications}</span>}</button><div ref={accountMenuRef}><button type="button" className="app-shell__avatar-button" aria-label="打开用户菜单" aria-expanded={accountOpen} onClick={() => setAccountOpen(!accountOpen)}><span>{initial}</span><UserRound aria-hidden="true" size={14} /></button>{accountOpen && <div role="menu" className="app-shell__account-dropdown"><button type="button" role="menuitem" onClick={() => navigate({ page: 'settings', params: { view: 'account' } })}>用户设置</button><button type="button" role="menuitem" onClick={() => navigate({ page: 'settings', params: { view: 'governance' } })}>系统通知{unreadNotifications ? `（${unreadNotifications}）` : ''}</button><button type="button" role="menuitem" onClick={onLogout}>退出登录</button></div>}</div></div></div></header><div className="app-shell__workspace"><header className="app-shell__mobile-header"><button type="button" className="icon-button" aria-label="打开导航菜单" aria-expanded={drawerOpen} onClick={openDrawer}><Menu aria-hidden="true" size={21} /></button><ShellIdentity /><button type="button" className="icon-button" aria-label={`通知，${unreadNotifications} 条未读`} onClick={() => navigate({ page: 'settings', params: { view: 'governance' } })}><Bell aria-hidden="true" size={18} /></button></header><main className="app-shell__main" data-page={shell.currentPage} data-mode={shell.shellMode} data-scroll-region={scrollRegion}>{shouldShowPageHeader && <header className="app-shell__page-header"><HomeButton onClick={() => navigate({ page: 'dashboard', params: {} })} label="返回主页" /><div><span className="app-shell__section-label">当前模块</span><h1>{shell.pageTitle}</h1></div></header>}{children}</main></div><MobileDrawer mounted={drawerMounted} open={drawerOpen} shell={shell} onClose={closeDrawer} onNavigate={navigate} onLogout={onLogout} /></div>;
}
