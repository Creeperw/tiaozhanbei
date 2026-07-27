import React, { useEffect, useRef, useState } from 'react';
import {
  BookOpen,
  Bell,
  BookMarked,
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
import UserProfileModal from './UserProfileModal';
import { useModalFocus } from './ui/useModalFocus';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import { loadLearningTarget, saveLearningTarget } from './exam-atlas/examAtlasApi';

const LEARNING_TARGET_CHANGED_EVENT = 'shizhen:learning-target-changed';

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
  { key: 'mistake_variation', label: '收藏夹', description: '错题库', icon: FolderHeart },
  { key: 'question_favorites', label: '笔记本', description: '题目收藏', icon: BookMarked },
  { key: 'study_notes', label: '学情分析', description: '学习笔记', icon: NotebookPen },
];

function DesktopTopbar({
  shell, displayName, avatarUrl, avatarInitial, unreadNotifications,
  targetOptions, selectedTargetId, targetLoading, targetError,
  onNavigate, onLogout, onOpenProfile, onSelectTarget,
}) {
  const [profileMenuMounted, setProfileMenuMounted] = useState(false);
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  const openTimerRef = useRef(null);
  const unmountTimerRef = useRef(null);
  const profileMenuRef = useRef(null);

  useEffect(() => () => {
    window.clearTimeout(openTimerRef.current);
    window.clearTimeout(unmountTimerRef.current);
  }, []);

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
    else openProfileMenu();
  };

  useEffect(() => {
    if (!profileMenuOpen) return undefined;
    const closeOnOutsidePointer = (event) => {
      if (!profileMenuRef.current?.contains(event.target)) closeProfileMenu();
    };
    document.addEventListener('pointerdown', closeOnOutsidePointer);
    return () => document.removeEventListener('pointerdown', closeOnOutsidePointer);
  }, [profileMenuOpen]);

  const navigateFromProfileMenu = (taskType) => {
    closeProfileMenu();
    onNavigate({ page: 'practice', params: { view: 'workspace', taskType } });
  };

  return (
    <header className="app-shell__topbar">
      <div className="app-shell__topbar-inner">
        <ShellIdentity />
        <NavItems items={[...shell.primaryNav, ...shell.supportNav]} currentPage={shell.currentPage} onNavigate={onNavigate} />
        <div className="app-shell__topbar-actions">
          <label className={'app-shell__topbar-target' + (targetError ? ' is-error' : '')} title={targetError || '选择后打开对应教材学习路线'}>
            <GraduationCap aria-hidden="true" size={17} />
            <span className="sr-only">资格考试路径</span>
            <select aria-label="资格考试路径" value={selectedTargetId} disabled={targetLoading || !targetOptions.length} onChange={(event) => onSelectTarget(event.target.value)}>
              {!targetOptions.length && <option value="">{targetLoading ? '正在读取考试目录…' : '暂无可用考试'}</option>}
              {targetOptions.map((item) => <option key={item.target_id} value={item.target_id}>{item.official_name}</option>)}
            </select>
            <ChevronDown aria-hidden="true" size={15} />
          </label>
          <button type="button" className="app-shell__assistant-entry" aria-label="AI 智能助教" onClick={() => onNavigate({ page: 'assistant', params: { newConversation: true } })}>
            <MessageSquareMore aria-hidden="true" size={18} /><span>AI 智能助教</span>
          </button>
          <button type="button" className="app-shell__topbar-icon" aria-label={'通知，' + unreadNotifications + ' 条未读'} onClick={() => onNavigate({ page: 'settings', params: { view: 'governance' } })}>
            <Bell aria-hidden="true" size={19} />
            {unreadNotifications > 0 && <span className="app-shell__notification-badge">{unreadNotifications > 99 ? '99+' : unreadNotifications}</span>}
          </button>
          <div ref={profileMenuRef} className="app-shell__profile-menu-wrap">
            <button type="button" className="app-shell__topbar-account" aria-label="打开个人菜单" aria-expanded={profileMenuOpen} aria-haspopup="menu" onClick={toggleProfileMenu}>
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
                  {profileMenuItems.map(({ key, label, description, icon: Icon }) => (
                    <button key={key} type="button" className={'app-shell__profile-shortcut app-shell__profile-shortcut--' + key} role="menuitem" onClick={() => navigateFromProfileMenu(key)}>
                      <span className="app-shell__profile-shortcut-icon">{React.createElement(Icon, { "aria-hidden": true, size: 21 })}</span>
                      <span className="app-shell__profile-shortcut-copy"><strong>{label}</strong><small>{description}</small></span>
                    </button>
                  ))}
                </div>
                <div className="app-shell__profile-menu-divider" />
                <div className="app-shell__profile-menu-actions">
                  <button type="button" role="menuitem" onClick={() => { closeProfileMenu(); onOpenProfile(); }}><UserRound aria-hidden="true" size={18} /><span>账号设置</span></button>
                  <button type="button" role="menuitem" className="app-shell__profile-menu-logout" onClick={onLogout}><LogOut aria-hidden="true" size={18} /><span>退出</span></button>
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
  targetOptions,
  selectedTargetId,
  targetLoading,
  targetError,
  onSelectTarget,
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
        <QualificationTargetSection
          options={targetOptions}
          selectedTargetId={selectedTargetId}
          loading={targetLoading}
          error={targetError}
          onSelect={onSelectTarget}
          idSuffix="mobile"
        />
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
  const [targetOptions, setTargetOptions] = useState([]);
  const [selectedTargetId, setSelectedTargetId] = useState('');
  const [targetLoading, setTargetLoading] = useState(true);
  const [targetError, setTargetError] = useState('');
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

  useEffect(() => {
    let cancelled = false;
    const loadQualificationTarget = async () => {
      setTargetLoading(true);
      setTargetError('');
      try {
        const [catalogResponse, targetPayload] = await Promise.all([
          fetchWithAuth(`${API_BASE}/v1/qualification-targets`),
          loadLearningTarget(),
        ]);
        const catalog = await readJsonResponse(catalogResponse, { items: [] });
        if (!catalogResponse.ok) throw new Error(catalog.detail || '资格考试目录暂时无法读取');
        const options = Array.isArray(catalog.items) ? catalog.items : [];
        const activeTarget = targetPayload?.target || targetPayload || {};
        const selected = options.find((item) => item.exam_track_id === activeTarget.exam_track_id) || options[0];
        if (!cancelled) {
          setTargetOptions(options);
          setSelectedTargetId(selected?.target_id || '');
        }
      } catch (requestError) {
        if (!cancelled) setTargetError(requestError.message || '资格考试目录暂时无法读取');
      } finally {
        if (!cancelled) setTargetLoading(false);
      }
    };
    loadQualificationTarget();
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

  const selectQualificationTarget = async (targetId) => {
    const selected = targetOptions.find((item) => item.target_id === targetId);
    if (!selected || targetLoading) return;
    const previousTargetId = selectedTargetId;
    setSelectedTargetId(targetId);
    setTargetLoading(true);
    setTargetError('');
    try {
      await saveLearningTarget(selected.exam_track_id);
      window.dispatchEvent(new CustomEvent(LEARNING_TARGET_CHANGED_EVENT, { detail: selected }));
      onNavigate({ page: 'qualification-route', params: { qualificationTargetId: selected.target_id } });
      if (drawerOpen) closeDrawer();
    } catch (requestError) {
      setSelectedTargetId(previousTargetId);
      setTargetError(requestError.message || '资格考试路径保存失败');
    } finally {
      setTargetLoading(false);
    }
  };

  return (
    <div className="app-shell" data-mode={shell.shellMode}>
      <DesktopTopbar
        shell={shell}
        displayName={displayName}
        avatarUrl={avatarUrl}
        avatarInitial={avatarInitial}
        unreadNotifications={unreadNotifications}
        targetOptions={targetOptions}
        selectedTargetId={selectedTargetId}
        targetLoading={targetLoading}
        targetError={targetError}
        onNavigate={onNavigate}
        onLogout={onLogout}
        onOpenProfile={() => setProfileOpen(true)}
        onSelectTarget={selectQualificationTarget}
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
        targetOptions={targetOptions}
        selectedTargetId={selectedTargetId}
        targetLoading={targetLoading}
        targetError={targetError}
        onSelectTarget={selectQualificationTarget}
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
