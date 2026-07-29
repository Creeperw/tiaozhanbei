import React, { useCallback, useEffect, useState } from 'react';
import AuthPage from './components/AuthPage';
import ChatInterface from './components/ChatInterface';
import KnowledgePage from './components/KnowledgePage';
import PersonalizationHubPage from './components/PersonalizationHubPage';
import SettingsHubPage from './components/SettingsHubPage';
import AdminFeedbackPage from './components/AdminFeedbackPage';
import HomePage from './components/HomePage';
import CapabilityDetailPage from './components/CapabilityDetailPage';
import QualificationRoutePage from './components/QualificationRoutePage';
import LearningPathPage from './components/LearningPathPage';
import DashboardPage from './components/DashboardPage';
import PracticePage from './components/PracticePage';
import LearningStageLanding from './components/learning-stage/LearningStageLanding';
import TextbookChapterLearning from './components/workshop-textbook/TextbookChapterLearning';
import StagePageTransition from './components/learning-stage/StagePageTransition';
import AppShell from './components/AppShell';
import CompactAssistant from './components/CompactAssistant';
import { useModalFocus } from './components/ui/useModalFocus';
import { AUTH_API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';
import { getAppShellConfig } from './appShell';
import { createPageIntent, getIntentPage } from './pageIntent';
import { legacyPersonalizationSettingsView } from './settingsNavigation';

const pendingNavigationKey = 'competition.pending-navigation';

const normalizeInitialIntent = (intent) => {
  const nextIntent = createPageIntent(intent);
  const settingsView = nextIntent.page === 'personalization'
    ? legacyPersonalizationSettingsView(nextIntent.params.view)
    : null;
  return settingsView
    ? createPageIntent('settings', { ...nextIntent.params, view: settingsView })
    : nextIntent;
};

const initialPageIntent = () => {
  try {
    const stored = sessionStorage.getItem(pendingNavigationKey);
    if (!stored) return createPageIntent('dashboard');
    sessionStorage.removeItem(pendingNavigationKey);
    return normalizeInitialIntent(JSON.parse(stored));
  } catch {
    sessionStorage.removeItem(pendingNavigationKey);
    return createPageIntent('dashboard');
  }
};

function AuthOverlay({ open, onClose, onLogin }) {
  const dialogRef = useModalFocus(open);
  useEffect(() => {
    if (!open) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = previousOverflow; };
  }, [open]);
  if (!open) return null;
  return (
    <div className="auth-overlay" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        className="auth-overlay__dialog"
        role="dialog"
        aria-modal="true"
        aria-label="账号登录"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
        onKeyDown={(event) => {
          if (event.key === 'Escape') onClose();
        }}
      >
        <button type="button" className="auth-overlay__close" aria-label="关闭登录页面" onClick={onClose}>×</button>
        <AuthPage onLogin={onLogin} />
      </section>
    </div>
  );
}

export default function App() {
  const [currentUser, setCurrentUser] = useState(null);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [authOpen, setAuthOpen] = useState(false);
  const [pageIntent, setPageIntent] = useState(initialPageIntent);
  const [navigationRevision, setNavigationRevision] = useState(0);
  const [knowledgeNavigationContext, setKnowledgeNavigationContext] = useState(null);
  const [stageTransition, setStageTransition] = useState(null);
  const [floatingAssistantSessionId, setFloatingAssistantSessionId] = useState(null);
  const currentPage = getIntentPage(pageIntent);
  const shellPage = currentPage === 'practice' && pageIntent.params.view === 'workspace'
    ? 'training-workshop'
    : currentPage;
  const selectedSessionId = pageIntent.params.sessionId || null;

  useEffect(() => {
    let active = true;
    const verifySession = async () => {
      try {
        const res = await fetchWithAuth(`${AUTH_API_BASE}/me`);
        const data = await readJsonResponse(res, {});
        if (active) {
          setCurrentUser(res.ok ? data.user || null : null);
        }
      } catch {
        if (active) setCurrentUser(null);
      } finally {
        if (active) setCheckingAuth(false);
      }
    };

    const clearSession = () => {
      setCurrentUser(null);
      setPageIntent(createPageIntent('dashboard'));
    };
    window.addEventListener('competition:unauthorized', clearSession);
    verifySession();
    return () => {
      active = false;
      window.removeEventListener('competition:unauthorized', clearSession);
    };
  }, []);

  const handleLogin = (user) => {
    setCurrentUser(user);
    setAuthOpen(false);
    navigateToPage('dashboard');
  };

  const handleLogout = async () => {
    try {
      await fetchWithAuth(`${AUTH_API_BASE}/logout`, { method: 'POST' });
    } finally {
      setCurrentUser(null);
      setKnowledgeNavigationContext(null);
      setPageIntent(createPageIntent('dashboard'));
    }
  };

  const shellConfig = getAppShellConfig({ currentUser, currentPage: shellPage, selectedSessionId });

  const navigateToPage = (destination, context = null) => {
    if (typeof destination === 'object') {
      const params = destination.params || {};
      if (destination.page === 'knowledge') {
        const carriesAtlasContext = Boolean(
          params.trackId || params.membershipId || params.route || params.lv1 || params.lv2 || params.kpId || params.kp_id,
        );
        const preferredContext = carriesAtlasContext
          ? {}
          : knowledgeNavigationContext?.trackId
            ? knowledgeNavigationContext
            : { route: 'textbook_14_5' };
        setPageIntent(createPageIntent({
          ...destination,
          params: { view: 'atlas', source: 'navigation', ...preferredContext, ...params },
        }));
        return;
      }
      if (destination.page === 'personalization') {
        const settingsView = legacyPersonalizationSettingsView(params.view);
        if (settingsView) {
          setPageIntent(createPageIntent('settings', { ...params, view: settingsView }));
          return;
        }
        setPageIntent(createPageIntent(destination.page, { ...params, view: params.view || 'reports' }));
        return;
      }
      if (
        destination.page === 'training-workshop'
        || (destination.page === 'practice' && params.view === 'workspace')
        || (destination.page === 'assistant' && params.newConversation)
      ) {
        setNavigationRevision((value) => value + 1);
      }
      setPageIntent(createPageIntent(destination));
      return;
    }
    const params = typeof context === 'string' ? { sessionId: context } : (context || {});
    if (destination === 'knowledge') {
      const carriesAtlasContext = Boolean(
        params.trackId || params.membershipId || params.route || params.lv1 || params.lv2 || params.kpId || params.kp_id,
      );
      const preferredContext = carriesAtlasContext
        ? {}
        : knowledgeNavigationContext?.trackId
          ? knowledgeNavigationContext
          : { route: 'textbook_14_5' };
      setPageIntent(createPageIntent(destination, {
        view: 'atlas', source: 'navigation', ...preferredContext, ...params,
      }));
      return;
    }
    if (destination === 'personalization') {
      const settingsView = legacyPersonalizationSettingsView(params.view);
      if (settingsView) {
        setPageIntent(createPageIntent('settings', { ...params, view: settingsView }));
        return;
      }
      setPageIntent(createPageIntent(destination, { ...params, view: params.view || 'reports' }));
      return;
    }
    if (
      destination === 'training-workshop'
      || (destination === 'practice' && params.view === 'workspace')
      || (destination === 'assistant' && params.newConversation)
    ) {
      setNavigationRevision((value) => value + 1);
    }
    setPageIntent(createPageIntent(destination, params));
  };

  const startStageTransition = useCallback((selection) => {
    setStageTransition((current) => current || selection);
  }, []);

  const openStagePathAtMidpoint = useCallback((selection) => {
    setPageIntent(createPageIntent({
      page: 'practice',
      params: {
        view: 'path',
        pathMode: 'personalized',
        stageId: selection.stage.nodeId || selection.stage.id,
        stageIndex: selection.index,
      },
    }));
  }, []);

  const finishStageTransition = useCallback(() => setStageTransition(null), []);

  if (checkingAuth) {
    return <div className="flex h-screen items-center justify-center bg-[#f8fafc] text-gray-400">Loading...</div>;
  }

  const renderAuthenticatedPage = () => {
    switch (shellConfig.currentPage) {
      case 'dashboard':
        return <HomePage currentUser={currentUser} onNavigate={navigateToPage} />;
      case 'capability-detail':
        return (
          <CapabilityDetailPage
            capabilityKey={pageIntent.params.capability}
            onNavigate={navigateToPage}
          />
        );
      case 'learning-path':
        return <QualificationRoutePage key={pageIntent.params.examTrackId || 'current-learning-path'} currentUser={currentUser} onNavigate={navigateToPage} />;
      case 'learning-path-tasks':
        return (
          <LearningPathPage
            key={pageIntent.params.examTrackId || 'current-learning-path'}
            currentUser={currentUser}
            onNavigate={navigateToPage}
          />
        );
      case 'assistant':
        return (
          <ChatInterface
            key={`assistant-${navigationRevision}`}
            embedded
            currentUser={currentUser?.username || 'User'}
            currentUserRole={currentUser?.role || 'user'}
            onLogout={handleLogout}
            onBackHome={() => navigateToPage('dashboard')}
            onOpenKnowledge={() => navigateToPage('knowledge')}
            onOpenPersonalization={() => navigateToPage({ page: 'settings', params: { view: 'memory' } })}
            onOpenAdminFeedback={() => navigateToPage('admin-feedback')}
            onNavigate={navigateToPage}
            preferredSessionId={selectedSessionId}
            initialContext={pageIntent.params.context || ''}
            forceNewConversation={Boolean(pageIntent.params.newConversation)}
          />
        );
      case 'practice':
        if (pageIntent.params.view === 'textbook-chapters') {
          return (
            <TextbookChapterLearning
              navigationContext={pageIntent.params}
              onNavigate={navigateToPage}
            />
          );
        }
        if (pageIntent.params.view === 'workspace') {
          return (
            <PracticePage
              key={`practice-workspace-${navigationRevision}`}
              navigationContext={pageIntent.params}
              onNavigate={navigateToPage}
              onBackHome={() => navigateToPage('dashboard')}
            />
          );
        }
        if (pageIntent.params.view === 'stages') {
          return (
            <LearningStageLanding
              onStageSelect={startStageTransition}
              onCreatePlan={() => navigateToPage({
                page: 'assistant',
                params: { context: '请结合我的学习状态，给我制定一份长期学习规划。' },
              })}
            />
          );
        }
        return (
          <DashboardPage
            currentUser={currentUser}
            navigationContext={pageIntent.params}
            onNavigate={navigateToPage}
            onKnowledgeContextChange={setKnowledgeNavigationContext}
          />
        );
      case 'training-workshop':
        return (
          <PracticePage
            key={`training-workshop-${navigationRevision}`}
            navigationContext={pageIntent.params}
            onNavigate={navigateToPage}
            onBackHome={() => navigateToPage('dashboard')}
          />
        );
      case 'knowledge':
        return (
          <KnowledgePage
            onBackHome={() => navigateToPage('dashboard')}
            onNavigate={navigateToPage}
            currentUser={currentUser}
            navigationContext={{
              ...pageIntent.params,
              view: shellConfig.knowledgeView || pageIntent.params.view,
            }}
          />
        );
      case 'personalization':
        return <PersonalizationHubPage navigationContext={pageIntent.params} onNavigate={navigateToPage} currentUser={currentUser} />;
      case 'settings':
        return <SettingsHubPage navigationContext={pageIntent.params} onNavigate={navigateToPage} />;
      case 'admin-feedback':
        return <AdminFeedbackPage onBackHome={() => navigateToPage('dashboard')} />;
      default:
        return <HomePage currentUser={currentUser} onNavigate={navigateToPage} />;
    }
  };

  return (
    <AppShell
      currentUser={currentUser}
      currentPage={shellConfig.currentPage}
      currentIntent={pageIntent}
      navigationContext={pageIntent.params}
      onNavigate={navigateToPage}
      onLogout={handleLogout}
      onLoginRequested={() => setAuthOpen(true)}
      onUserUpdated={(updatedUser) => setCurrentUser((current) => ({ ...current, ...updatedUser }))}
    >
      {renderAuthenticatedPage()}
      <StagePageTransition
        selection={stageTransition}
        onMidpoint={openStagePathAtMidpoint}
        onComplete={finishStageTransition}
      />
      {shellConfig.currentPage !== 'assistant' && (
        <CompactAssistant
          className="global-assistant-dock"
          currentUser={currentUser?.display_name || currentUser?.username || '同学'}
          preferredSessionId={floatingAssistantSessionId}
          contextLabel={shellConfig.pageTitle}
          initiallyCollapsed
          characterHint="多智能体助教"
          onOpenFull={(sessionId) => {
            if (sessionId) setFloatingAssistantSessionId(sessionId);
            navigateToPage({
              page: 'assistant',
              params: sessionId ? { sessionId } : {},
            });
          }}
        />
      )}
      <AuthOverlay open={authOpen} onClose={() => setAuthOpen(false)} onLogin={handleLogin} />
    </AppShell>
  );
}
