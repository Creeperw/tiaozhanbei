import React, { useEffect, useRef, useState } from 'react';
import {
  BookOpen,
  Bell,
  ChartNoAxesColumnIncreasing,
  ChevronDown,
  ClipboardList,
  Database,
  Dumbbell,
  FolderHeart,
  GraduationCap,
  Home,
  LogOut,
  Menu,
  MessageSquareMore,
  Settings,
  ShieldCheck,
  Sprout,
  UserRound,
  NotebookPen,
  X,
} from 'lucide-react';
import { getAppShellConfig } from '../appShell';
import HomeButton from './HomeButton';
import LearningTargetSelector from './LearningTargetSelector';
import UserProfileModal from './UserProfileModal';
import { useModalFocus } from './ui/useModalFocus';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';

const LEARNING_TARGET_CHANGED_EVENT = 'shizhen:learning-target-changed';
const NAV_MENU_EXIT_MS = 250;
const NAV_MENU_LEAVE_DELAY_MS = 200;

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
  'qualification-route': GraduationCap,
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

function ShellIdentity({ onNavigate }) {
  const mark = <Sprout aria-hidden="true" size={21} />;
  return (
    <button type="button" className="app-shell__identity app-shell__identity-toggle" aria-label="返回主页" onClick={() => onNavigate?.({ page: 'dashboard', params: {} })}>
      <div className="app-shell__mark">{mark}</div>
      <div className="app-shell__identity-copy">
        <strong>时珍智训</strong>
        <span>中医备考平台</span>
      </div>
    </button>
  );
}

function MenuItems({ items, onNavigate, onClose, currentIntent }) {
  return items.map((item) => {
    const active = item.intent?.page === currentIntent?.page
      && Object.entries(item.intent?.params || {}).every(([key, value]) => currentIntent?.params?.[key] === value);
    return (
      <button key={item.label} type="button" role="menuitem" aria-current={active ? 'page' : undefined} onClick={() => { onNavigate(item.intent); onClose?.(); }}>
        {item.label}
      </button>
    );
  });
}

function NavigationMenu({ item, currentPage, navigationContext, onNavigate, menuState, onOpen, onRequestClose, onCloseNow }) {
  const open = menuState === 'open';
  const mounted = menuState !== 'closed';
  const ref = useRef(null);
  useEffect(() => {
    const closeOutside = (event) => { if (mounted && !ref.current?.contains(event.target)) onRequestClose(item.key, 0); };
    document.addEventListener('mousedown', closeOutside);
    return () => document.removeEventListener('mousedown', closeOutside);
  }, [item.key, mounted, onRequestClose]);
  const focusItem = (position) => {
    const entries = ref.current?.querySelectorAll('[role="menuitem"]');
    entries?.[position < 0 ? entries.length - 1 : position]?.focus();
  };
  if (!item.children) {
    return <div className="app-shell__nav-group"><a href={`#${item.key}`} aria-current={currentPage === item.key ? 'page' : undefined} onClick={(event) => { event.preventDefault(); onNavigate(item.intent); }}>{item.label}</a></div>;
  }
  return (
    <div ref={ref} className="app-shell__nav-group" onMouseEnter={() => onOpen(item.key)} onMouseLeave={() => onRequestClose(item.key, NAV_MENU_LEAVE_DELAY_MS)} onFocus={() => onOpen(item.key)} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) onRequestClose(item.key, 0); }}>
      <a href={`#${item.key}`} aria-current={currentPage === item.key ? 'page' : undefined} aria-haspopup="menu" aria-expanded={open} onClick={(event) => { event.preventDefault(); onNavigate(item.intent || { page: item.key, params: {} }); }} onKeyDown={(event) => { if (event.key === 'ArrowDown') { event.preventDefault(); onOpen(item.key); window.setTimeout(() => focusItem(0), 0); } }}>
        {item.label}<ChevronDown aria-hidden="true" size={15} />
      </a>
      {mounted && <div className="app-shell__nav-menu" data-state={menuState} role="menu" aria-label={`${item.label}菜单`}><MenuItems items={item.children} currentIntent={{ page: currentPage, params: navigationContext }} onNavigate={onNavigate} onClose={() => onCloseNow(item.key)} /></div>}
    </div>
  );
}

function LearningTargetNavigationMenu({ menuState, onOpen, onRequestClose, onCloseNow, onTargetSelected }) {
  const open = menuState === 'open';
  const mounted = menuState !== 'closed';
  const ref = useRef(null);
  const openSelectedPath = (selection) => {
    onCloseNow('learning-target');
    onTargetSelected(selection);
  };
  useEffect(() => {
    const closeOutside = (event) => { if (mounted && !ref.current?.contains(event.target)) onRequestClose('learning-target', 0); };
    document.addEventListener('mousedown', closeOutside);
    return () => document.removeEventListener('mousedown', closeOutside);
  }, [mounted, onRequestClose]);
  return (
    <div ref={ref} className="app-shell__nav-group app-shell__target-group" onMouseEnter={() => onOpen('learning-target')} onMouseLeave={() => onRequestClose('learning-target', NAV_MENU_LEAVE_DELAY_MS)} onFocus={() => onOpen('learning-target')} onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) onRequestClose('learning-target', 0); }}>
      <button type="button" aria-haspopup="menu" aria-expanded={open} onClick={() => open ? onRequestClose('learning-target', 0) : onOpen('learning-target')}>考试类别<ChevronDown aria-hidden="true" size={15} /></button>
      {mounted && <div className="app-shell__nav-menu app-shell__target-menu" data-state={menuState} aria-label="选择考试类别"><LearningTargetSelector className="app-shell__target-selector" variant="menu" onSelected={openSelectedPath} /></div>}
    </div>
  );
}

function MobileNavItems({ items, currentPage, navigationContext, onNavigate, onClose, onTargetSelected }) {
  const [expanded, setExpanded] = useState(null);
  const openSelectedPath = (selection) => {
    onTargetSelected(selection);
    onClose();
  };
  return (
    <nav aria-label="移动平台导航" className="app-shell__drawer-nav">
      {items.map((item) => {
        if (item.kind === 'learning-target') {
          return (
            <div key={item.key} className="app-shell__drawer-module app-shell__drawer-target">
              <button type="button" aria-expanded={expanded === item.key} onClick={() => setExpanded(expanded === item.key ? null : item.key)}>{item.label}<ChevronDown aria-hidden="true" size={17} /></button>
              {expanded === item.key && <LearningTargetSelector className="app-shell__target-selector" onSelected={openSelectedPath} />}
            </div>
          );
        }
        return (
          <div key={item.key} className="app-shell__drawer-module">
            <div>
              <a href={`#${item.key}`} aria-current={currentPage === item.key ? 'page' : undefined} onClick={(event) => { event.preventDefault(); onNavigate(item.intent || { page: item.key, params: {} }); onClose(); }}>{item.label}</a>
              {item.children && <button type="button" aria-label={`展开${item.label}`} aria-expanded={expanded === item.key} onClick={() => setExpanded(expanded === item.key ? null : item.key)}><ChevronDown aria-hidden="true" size={17} /></button>}
            </div>
            {item.children && expanded === item.key && <div role="menu"><MenuItems items={item.children} currentIntent={{ page: currentPage, params: navigationContext }} onNavigate={onNavigate} onClose={onClose} /></div>}
          </div>
        );
      })}
    </nav>
  );
}

function QualificationTargetSection({
  options,
  selectedTargetId,
  loading,
  error,
  onSelect,
  idSuffix = 'desktop',
}) {
  const titleId = `qualification-target-title-${idSuffix}`;
  return (
    <section className="app-shell__target-section" aria-labelledby={titleId}>
      <header>
        <GraduationCap aria-hidden="true" size={17} />
        <span id={titleId}>资格考试路径</span>
      </header>
      <label className="app-shell__target-control">
        <span>当前考试</span>
        <select
          aria-label="资格考试路径"
          value={selectedTargetId}
          disabled={loading || !options.length}
          onChange={(event) => onSelect(event.target.value)}
        >
          {!options.length && <option value="">{loading ? '正在读取考试目录…' : '暂无可用考试'}</option>}
          {options.map((item) => (
            <option key={item.target_id} value={item.target_id}>{item.official_name}</option>
          ))}
        </select>
      </label>
      <p className={error ? 'is-error' : ''} role={error ? 'alert' : undefined}>
        {error || '选择后打开对应教材学习路线'}
      </p>
      <button
        type="button"
        className="app-shell__target-open"
        disabled={loading || !selectedTargetId}
        onClick={() => onSelect(selectedTargetId)}
      >
        打开当前学习路线
      </button>
    </section>
  );
}

const profileMenuItems = [
  {
    key: 'question_favorites',
    label: '收藏夹',
    description: '题目收藏',
    icon: FolderHeart,
    intent: { page: 'practice', params: { view: 'workspace', taskType: 'question_favorites' } },
  },
  {
    key: 'study_notes',
    label: '笔记本',
    description: '学习笔记',
    icon: NotebookPen,
    intent: { page: 'practice', params: { view: 'workspace', taskType: 'study_notes' } },
  },
];

const notificationView = (item) => {
  const category = String(item?.category || '').toLowerCase();
  const sourceType = String(item?.source?.type || '').toLowerCase();
  return category.includes('conflict') || sourceType.includes('conflict') ? 'conflicts' : 'governance';
};

const notificationTime = (value) => {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
};

function NotificationPopover({ open, items, loading, onClose, onSelect, panelRef }) {
  if (!open) return null;
  return (
    <section ref={panelRef} className="app-shell__notification-popover" role="dialog" aria-label="未处理通知">
      <header>
        <div><strong>通知</strong><span>{items.length ? `${items.length} 条未处理` : '暂无未处理通知'}</span></div>
        <button type="button" aria-label="关闭通知" onClick={onClose}><X aria-hidden="true" size={17} /></button>
      </header>
      <div className="app-shell__notification-list">
        {loading && <div className="app-shell__notification-empty" role="status">正在读取通知…</div>}
        {!loading && items.map((item) => (
          <button key={item.notification_id} type="button" className="app-shell__notification-item" onClick={() => onSelect(item)}>
            <span className="app-shell__notification-dot" aria-hidden="true" />
            <span><strong>{item.title || '学习提醒'}</strong><small>{item.message || '查看通知详情'}</small><time>{notificationTime(item.created_at)}</time></span>
          </button>
        ))}
        {!loading && items.length === 0 && <div className="app-shell__notification-empty"><Bell aria-hidden="true" size={22} /><span>目前没有需要处理的通知</span></div>}
      </div>
      <button type="button" className="app-shell__notification-all" onClick={() => { onClose(); onSelect(null); }}>查看全部系统通知</button>
    </section>
  );
}

function DesktopTopbar({
  shell, navigationContext, displayName, avatarUrl, avatarInitial, unreadNotifications,
  notificationOpen, onToggleNotifications, onNavigate, onLogout, onOpenProfile, onTargetSelected,
}) {
  const [openKey, setOpenKey] = useState(null);
  const [closingKey, setClosingKey] = useState(null);
  const [profileMenuMounted, setProfileMenuMounted] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const openTimerRef = useRef(null);
  const unmountTimerRef = useRef(null);
  const profileMenuRef = useRef(null);
  const navMenuKeyRef = useRef(null);
  const navMenuCloseTimerRef = useRef(null);
  const navMenuExitTimerRef = useRef(null);

  useEffect(() => () => {
    window.clearTimeout(openTimerRef.current);
    window.clearTimeout(unmountTimerRef.current);
    window.clearTimeout(navMenuCloseTimerRef.current);
    window.clearTimeout(navMenuExitTimerRef.current);
  }, []);

  const openNavMenu = (key) => {
    window.clearTimeout(navMenuCloseTimerRef.current);
    window.clearTimeout(navMenuExitTimerRef.current);
    navMenuKeyRef.current = key;
    setClosingKey(null);
    setOpenKey(key);
  };

  const closeNavMenuNow = (key) => {
    if (navMenuKeyRef.current !== key) return;
    window.clearTimeout(navMenuCloseTimerRef.current);
    window.clearTimeout(navMenuExitTimerRef.current);
    navMenuKeyRef.current = null;
    setClosingKey(null);
    setOpenKey(null);
  };

  const requestNavMenuClose = (key, delay = 0) => {
    window.clearTimeout(navMenuCloseTimerRef.current);
    window.clearTimeout(navMenuExitTimerRef.current);
    navMenuCloseTimerRef.current = window.setTimeout(() => {
      if (navMenuKeyRef.current !== key) return;
      setClosingKey(key);
    }, delay);
    navMenuExitTimerRef.current = window.setTimeout(() => {
      if (navMenuKeyRef.current !== key) return;
      navMenuKeyRef.current = null;
      setClosingKey(null);
      setOpenKey(null);
    }, delay + NAV_MENU_EXIT_MS);
  };

  const menuStateFor = (key) => openKey !== key ? 'closed' : closingKey === key ? 'closing' : 'open';

  const openProfileMenu = () => {
    window.clearTimeout(unmountTimerRef.current);
    setProfileMenuMounted(true);
    setProfileMenuOpen(true);
  };

  const closeProfileMenu = () => {
    window.clearTimeout(openTimerRef.current);
    if (!profileMenuMounted) return;
    setProfileMenuOpen(false);
    unmountTimerRef.current = window.setTimeout(() => setProfileMenuMounted(false), 180);
  };

  const toggleProfileMenu = () => {
    if (profileMenuOpen) closeProfileMenu();
    else {
      onToggleNotifications(false);
      openProfileMenu();
    }
  };

  useEffect(() => {
    if (!profileMenuOpen) return undefined;
    const closeOnOutsidePointer = (event) => {
      if (!profileMenuRef.current?.contains(event.target)) closeProfileMenu();
    };
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') closeProfileMenu();
    };
    document.addEventListener('pointerdown', closeOnOutsidePointer);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsidePointer);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [profileMenuOpen]);

  const navigateFromProfileMenu = (intent) => {
    closeProfileMenu();
    onNavigate(intent);
  };

  return (
    <header className="app-shell__topbar">
      <div className="app-shell__topbar-inner">
        <ShellIdentity onNavigate={onNavigate} />
        <nav aria-label="平台导航" className="app-shell__desktop-nav">
          {shell.primaryNav.map((item) => item.kind === 'learning-target'
            ? <LearningTargetNavigationMenu key={item.key} menuState={menuStateFor(item.key)} onOpen={openNavMenu} onRequestClose={requestNavMenuClose} onCloseNow={closeNavMenuNow} onTargetSelected={onTargetSelected} />
            : <NavigationMenu key={item.key} item={item} currentPage={shell.currentPage} navigationContext={navigationContext} onNavigate={onNavigate} menuState={menuStateFor(item.key)} onOpen={openNavMenu} onRequestClose={requestNavMenuClose} onCloseNow={closeNavMenuNow} />)}
        </nav>
        <div className="app-shell__topbar-actions">
          <button type="button" className="app-shell__assistant-entry" aria-label="AI 智能助教" onClick={() => onNavigate({ page: 'assistant', params: { newConversation: true } })}>
            <MessageSquareMore aria-hidden="true" size={18} /><span>AI 智能助教</span>
          </button>
          <button type="button" data-notification-trigger className="app-shell__topbar-icon" aria-label={'通知，' + unreadNotifications + ' 条未读'} aria-haspopup="dialog" aria-expanded={notificationOpen} onClick={() => { closeProfileMenu(); onToggleNotifications(); }}>
            <Bell aria-hidden="true" size={19} />
            {unreadNotifications > 0 && <span className="app-shell__notification-badge">{unreadNotifications > 99 ? '99+' : unreadNotifications}</span>}
          </button>
          <div ref={profileMenuRef} className="app-shell__profile-menu-wrap">
            <button type="button" className="app-shell__topbar-account" aria-label="打开用户菜单" aria-expanded={profileMenuOpen} aria-haspopup="menu" onClick={toggleProfileMenu}>
              <span className="app-shell__topbar-avatar">{avatarUrl ? <img src={avatarUrl} alt="" /> : avatarInitial}</span>
              <span className="app-shell__topbar-user">{displayName}</span>
              <ChevronDown aria-hidden="true" size={15} />
            </button>
            {profileMenuMounted && (
              <div className="app-shell__profile-menu" data-state={profileMenuOpen ? 'open' : 'closing'} role="menu" aria-label="个人菜单">
                <div className="app-shell__profile-menu-identity">
                  <span className="app-shell__profile-menu-avatar" aria-hidden="true">
                    {avatarUrl ? <img src={avatarUrl} alt="" /> : avatarInitial}
                  </span>
                  <span className="app-shell__profile-menu-identity-copy">
                    <strong>{displayName}</strong>
                    <small>我的学习空间</small>
                  </span>
                </div>
                <div className="app-shell__profile-menu-shortcuts">
                  {profileMenuItems.map(({ key, label, description, icon: Icon, intent }) => (
                    <button key={key} type="button" className={'app-shell__profile-shortcut app-shell__profile-shortcut--' + key} role="menuitem" onClick={() => navigateFromProfileMenu(intent)}>
                      <span className="app-shell__profile-shortcut-icon">{React.createElement(Icon, { "aria-hidden": true, size: 21 })}</span>
                      <span className="app-shell__profile-shortcut-copy"><strong>{label}</strong><small>{description}</small></span>
                    </button>
                  ))}
                </div>
                <div className="app-shell__profile-menu-divider" />
                <div className="app-shell__profile-menu-actions">
                  <button type="button" role="menuitem" onClick={() => { closeProfileMenu(); onOpenProfile(); }}><UserRound aria-hidden="true" size={18} /><span>账号设置</span></button>
                  <button type="button" role="menuitem" className="app-shell__profile-menu-logout" onClick={onLogout}><LogOut aria-hidden="true" size={18} /><span>退出登录</span></button>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
function MobileDrawer({
  mounted,
  open,
  shell,
  onClose,
  onNavigate,
  onLogout,
  displayName,
  navigationContext,
  onTargetSelected,
}) {
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
          <ShellIdentity onNavigate={(intent) => { onNavigate(intent); onClose(); }} />
          <button type="button" data-autofocus className="icon-button" aria-label="关闭导航菜单" onClick={onClose}>
            <X aria-hidden="true" size={20} />
          </button>
        </div>
        <MobileNavItems
          items={shell.primaryNav}
          currentPage={shell.currentPage}
          navigationContext={navigationContext}
          onNavigate={onNavigate}
          onClose={onClose}
          onTargetSelected={onTargetSelected}
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

export default function AppShell({ currentUser, currentPage, navigationContext = {}, onNavigate, onLogout, onUserUpdated, children }) {
  const shell = getAppShellConfig({ currentUser, currentPage });
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [drawerMounted, setDrawerMounted] = useState(false);
  const [unreadNotifications, setUnreadNotifications] = useState(0);
  const [notificationItems, setNotificationItems] = useState([]);
  const [notificationsLoading, setNotificationsLoading] = useState(true);
  const [notificationOpen, setNotificationOpen] = useState(false);
  const notificationPanelRef = useRef(null);
  const drawerExitTimerRef = useRef(null);
  const [profileOpen, setProfileOpen] = useState(false);
  const [accountProfile, setAccountProfile] = useState(null);
  const displayName = currentUser?.display_name || currentUser?.username || 'User';
  const avatarUrl = accountProfile?.avatar_url || null;
  const avatarInitial = displayName.trim().slice(0, 1).toUpperCase() || '用';
  const shouldShowHomeButton = shell.homeAction && !['settings', 'personalization', 'practice', 'training-workshop'].includes(shell.currentPage);
  const shouldShowPageHeader = !['dashboard', 'learning-path'].includes(shell.currentPage)
    && shell.shellMode !== 'workspace'
    && !['personalization', 'settings'].includes(shell.currentPage);
  const scrollRegion = ['assistant', 'knowledge'].includes(shell.currentPage) ? 'contained' : 'page';

  useEffect(() => () => window.clearTimeout(drawerExitTimerRef.current), []);
  useEffect(() => {
    let cancelled = false;
    const loadUnread = async () => {
      setNotificationsLoading(true);
      try {
        const response = await fetchWithAuth(`${API_BASE}/v1/notifications?status=unread&limit=6`);
        const payload = await readJsonResponse(response, {});
        if (!cancelled && response.ok) {
          setUnreadNotifications(Number(payload.unread_count) || 0);
          setNotificationItems(Array.isArray(payload.items) ? payload.items : []);
        }
      } catch {
        if (!cancelled) {
          setUnreadNotifications(0);
          setNotificationItems([]);
        }
      } finally {
        if (!cancelled) setNotificationsLoading(false);
      }
    };
    loadUnread();
    return () => { cancelled = true; };
  }, [currentPage]);

  useEffect(() => {
    if (!notificationOpen) return undefined;
    const closeOutside = (event) => {
      if (!notificationPanelRef.current?.contains(event.target) && !event.target.closest?.('[data-notification-trigger]')) setNotificationOpen(false);
    };
    const closeOnEscape = (event) => { if (event.key === 'Escape') setNotificationOpen(false); };
    document.addEventListener('pointerdown', closeOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('pointerdown', closeOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [notificationOpen]);

  const openNotificationDestination = async (item) => {
    setNotificationOpen(false);
    const view = item ? notificationView(item) : 'governance';
    if (item?.notification_id) {
      setNotificationItems((current) => current.filter((entry) => entry.notification_id !== item.notification_id));
      setUnreadNotifications((current) => Math.max(0, current - 1));
      try {
        await fetchWithAuth(`${API_BASE}/v1/notifications/${item.notification_id}`, {
          method: 'PATCH',
          body: JSON.stringify({ status: 'read' }),
        });
      } catch {
        // Navigation remains available even when the read receipt cannot be saved.
      }
    }
    onNavigate({ page: 'settings', params: { view, ...(item?.notification_id ? { notificationId: item.notification_id } : {}) } });
  };

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

  const handleTopbarTargetSelected = (selected) => {
    if (!selected?.target_id) return;
    window.dispatchEvent(new CustomEvent(LEARNING_TARGET_CHANGED_EVENT, { detail: selected }));
    onNavigate({
      page: 'learning-path',
      params: {
        targetId: selected.target_id,
        examTrackId: selected.exam_track_id || '',
        textbookRouteId: selected.textbook_route_id || '',
      },
    });
  };

  return (
    <div className="app-shell" data-mode={shell.shellMode}>
      <DesktopTopbar
        shell={shell}
        navigationContext={navigationContext}
        displayName={displayName}
        avatarUrl={avatarUrl}
        avatarInitial={avatarInitial}
        unreadNotifications={unreadNotifications}
        notificationOpen={notificationOpen}
        onToggleNotifications={(force) => setNotificationOpen((current) => typeof force === 'boolean' ? force : !current)}
        onNavigate={onNavigate}
        onLogout={onLogout}
        onOpenProfile={() => setProfileOpen(true)}
        onTargetSelected={handleTopbarTargetSelected}
      />

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
          <ShellIdentity onNavigate={onNavigate} />
          <button type="button" data-notification-trigger className="icon-button relative" aria-label={`通知，${unreadNotifications} 条未读`} aria-haspopup="dialog" aria-expanded={notificationOpen} onClick={() => setNotificationOpen((current) => !current)}><Bell aria-hidden="true" size={18} />{unreadNotifications > 0 && <span className="absolute -right-1 -top-1 min-w-4 rounded-full bg-amber-500 px-1 text-[10px] font-semibold leading-4 text-white">{unreadNotifications > 99 ? '99+' : unreadNotifications}</span>}</button>
        </header>

        <main
          className="app-shell__main"
          data-page={shell.currentPage}
          data-mode={shell.shellMode}
          data-scroll-region={scrollRegion}
        >
          {shouldShowPageHeader && (
            <header className="app-shell__page-header">
              {shouldShowHomeButton && <HomeButton
                onClick={() => onNavigate({ page: shell.homeAction.key, params: {} })}
                label={shell.homeAction.label}
                className={shell.currentPage === 'learning-path-tasks' ? 'app-shell__learning-path-back' : ''}
              />}
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
        navigationContext={navigationContext}
        displayName={displayName}
        onClose={closeDrawer}
        onNavigate={onNavigate}
        onLogout={onLogout}
        onTargetSelected={handleTopbarTargetSelected}
      />
      <UserProfileModal
        open={profileOpen}
        currentUser={currentUser}
        onClose={() => setProfileOpen(false)}
        onSaved={handleProfileSaved}
      />
      <NotificationPopover
        open={notificationOpen}
        items={notificationItems}
        loading={notificationsLoading}
        panelRef={notificationPanelRef}
        onClose={() => setNotificationOpen(false)}
        onSelect={openNotificationDestination}
      />
    </div>
  );
}
