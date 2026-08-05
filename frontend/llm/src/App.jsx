import React, { useCallback, useEffect, useRef, useState } from 'react';
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
import HomeOnboardingGuide from './components/HomeOnboardingGuide';
import { AUTH_API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';
import { getAppShellConfig } from './appShell';
import { createPageIntent, getIntentPage } from './pageIntent';
import { legacyPersonalizationSettingsView } from './settingsNavigation';
import { readCurrentPage } from './pageContext';

const pendingNavigationKey = 'competition.pending-navigation';
const persistedPageIntentKey = 'competition.current-page-intent';

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
    if (stored) {
      sessionStorage.removeItem(pendingNavigationKey);
      return normalizeInitialIntent(JSON.parse(stored));
    }
    const persisted = sessionStorage.getItem(persistedPageIntentKey);
    if (persisted) {
      const restored = JSON.parse(persisted);
      const params = { ...(restored?.params || {}) };
      // This is a one-shot command, not durable navigation state. Replaying
      // it on refresh would create another empty conversation.
      delete params.newConversation;
      return normalizeInitialIntent({ ...restored, params });
    }
    return createPageIntent('dashboard');
  } catch {
    sessionStorage.removeItem(pendingNavigationKey);
    sessionStorage.removeItem(persistedPageIntentKey);
    return createPageIntent('dashboard');
  }
};

const pageIntentForPersistence = (intent) => {
  const normalized = createPageIntent(intent);
  const params = { ...normalized.params };
  delete params.newConversation;
  return createPageIntent(normalized.page, params);
};

export default function App() {
  const [currentUser, setCurrentUser] = useState(null);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [authRequested, setAuthRequested] = useState(false);
  const [pageIntent, setPageIntent] = useState(initialPageIntent);
  const [navigationRevision, setNavigationRevision] = useState(0);
  const [knowledgeNavigationContext, setKnowledgeNavigationContext] = useState(null);
  const [stageTransition, setStageTransition] = useState(null);
  const [floatingAssistantSessionId, setFloatingAssistantSessionId] = useState(null);
  const [showHomeGuide, setShowHomeGuide] = useState(false);
  const authRequestId = useRef(0);
  const currentPage = getIntentPage(pageIntent);
  // 'qualification-route' 是 QualificationRoutePage（今日任务/学习路径）的内部页名，
  // App 外壳只识别 'learning-path'，这里归一化以保证返回等导航能正确落到学习路径页。
  const shellPage = currentPage === 'practice' && pageIntent.params.view === 'workspace'
    ? 'training-workshop'
    : currentPage === 'qualification-route'
      ? 'learning-path'
      : currentPage;
  const selectedSessionId = pageIntent.params.sessionId || null;

  useEffect(() => {
    try {
      sessionStorage.setItem(
        persistedPageIntentKey,
        JSON.stringify(pageIntentForPersistence(pageIntent)),
      );
    } catch {
      // Session navigation persistence is a convenience and must never block
      // the application when browser storage is unavailable.
    }
  }, [pageIntent]);

  useEffect(() => {
    let active = true;
    const requestId = ++authRequestId.current;
    const verifySession = async () => {
      try {
        const res = await fetchWithAuth(`${AUTH_API_BASE}/me`);
        const data = await readJsonResponse(res, {});
        if (active && requestId === authRequestId.current) {
          const verifiedUser = res.ok ? data.user || null : null;
          setCurrentUser(verifiedUser);
          setShowHomeGuide(Boolean(verifiedUser));
        }
      } catch {
        if (active && requestId === authRequestId.current) setCurrentUser(null);
      } finally {
        if (active) setCheckingAuth(false);
      }
    };

    const clearSession = () => {
      authRequestId.current += 1;
      setCurrentUser(null);
      setAuthRequested(false);
      setShowHomeGuide(false);
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
    setAuthRequested(false);
    setShowHomeGuide(true);
    navigateToPage('dashboard');
  };

  const handleLogout = async () => {
    try {
      await fetchWithAuth(`${AUTH_API_BASE}/logout`, { method: 'POST' });
    } finally {
      setCurrentUser(null);
      setKnowledgeNavigationContext(null);
      setPageIntent(createPageIntent('dashboard'));
      setShowHomeGuide(false);
      sessionStorage.removeItem(persistedPageIntentKey);
    }
  };

  const consumeAssistantNewConversation = useCallback(() => {
    setPageIntent((current) => {
      if (getIntentPage(current) !== 'assistant' || !current.params?.newConversation) {
        return current;
      }
      const params = { ...current.params };
      delete params.newConversation;
      return createPageIntent({ ...current, params });
    });
  }, []);

  const shellConfig = getAppShellConfig({ currentUser, currentPage: shellPage, selectedSessionId });
  const readAssistantPageContext = useCallback(() => readCurrentPage({
    pageType: shellConfig.currentPage,
    pageTitle: shellConfig.pageTitle,
  }), [shellConfig.currentPage, shellConfig.pageTitle]);

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

  if (authRequested && !currentUser) {
    return <AuthPage onLogin={handleLogin} onBack={() => setAuthRequested(false)} />;
  }

  const renderAuthenticatedPage = () => {
    switch (shellConfig.currentPage) {
      case 'dashboard':
        return <HomePage currentUser={currentUser} onNavigate={navigateToPage} onLoginRequested={() => setAuthRequested(true)} />;
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
            onNewConversationConsumed={consumeAssistantNewConversation}
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
        return <HomePage currentUser={currentUser} onNavigate={navigateToPage} onLoginRequested={() => setAuthRequested(true)} />;
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
      onLoginRequested={() => setAuthRequested(true)}
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
          readPageContext={readAssistantPageContext}
          onOpenFull={(sessionId) => {
            if (sessionId) setFloatingAssistantSessionId(sessionId);
            navigateToPage({
              page: 'assistant',
              params: sessionId ? { sessionId } : {},
            });
          }}
        />
      )}
      {showHomeGuide && <HomeOnboardingGuide onClose={() => setShowHomeGuide(false)} />}
    </AppShell>
  );
}
