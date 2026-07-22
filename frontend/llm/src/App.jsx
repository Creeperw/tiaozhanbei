import React, { useCallback, useEffect, useState } from 'react';
import AuthPage from './components/AuthPage';
import ChatInterface from './components/ChatInterface';
import KnowledgePage from './components/KnowledgePage';
import PersonalizationHubPage from './components/PersonalizationHubPage';
import SettingsPage from './components/SettingsPage';
import AdminFeedbackPage from './components/AdminFeedbackPage';
import HomePage from './components/HomePage';
import DashboardPage from './components/DashboardPage';
import PracticePage from './components/PracticePage';
import LearningStageLanding from './components/learning-stage/LearningStageLanding';
import StagePageTransition from './components/learning-stage/StagePageTransition';
import AppShell from './components/AppShell';
import { AUTH_API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';
import { getAppShellConfig } from './appShell';
import { createPageIntent, getIntentPage } from './pageIntent';

const pendingNavigationKey = 'competition.pending-navigation';

const initialPageIntent = () => {
  try {
    const stored = sessionStorage.getItem(pendingNavigationKey);
    if (!stored) return createPageIntent('dashboard');
    sessionStorage.removeItem(pendingNavigationKey);
    return createPageIntent(JSON.parse(stored));
  } catch {
    sessionStorage.removeItem(pendingNavigationKey);
    return createPageIntent('dashboard');
  }
};

export default function App() {
  const [currentUser, setCurrentUser] = useState(null);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [pageIntent, setPageIntent] = useState(initialPageIntent);
  const [knowledgeNavigationContext, setKnowledgeNavigationContext] = useState(null);
  const [stageTransition, setStageTransition] = useState(null);
  const currentPage = getIntentPage(pageIntent);
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

    const clearSession = () => setCurrentUser(null);
    window.addEventListener('competition:unauthorized', clearSession);
    verifySession();
    return () => {
      active = false;
      window.removeEventListener('competition:unauthorized', clearSession);
    };
  }, []);

  const handleLogin = (user) => {
    setCurrentUser(user);
    navigateToPage('dashboard');
  };

  const handleLogout = async () => {
    try {
      await fetchWithAuth(`${AUTH_API_BASE}/logout`, { method: 'POST' });
    } finally {
      setCurrentUser(null);
      setKnowledgeNavigationContext(null);
    }
  };

  const shellConfig = getAppShellConfig({ currentUser, currentPage, selectedSessionId });

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
        setPageIntent(createPageIntent(destination.page, { view: 'profile', ...params }));
        return;
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
      setPageIntent(createPageIntent(destination, { view: 'profile', ...params }));
      return;
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

  if (!currentUser) {
    return <AuthPage onLogin={handleLogin} />;
  }

  const renderAuthenticatedPage = () => {
    switch (shellConfig.currentPage) {
      case 'dashboard':
        return <HomePage currentUser={currentUser} onNavigate={navigateToPage} />;
      case 'assistant':
        return (
          <ChatInterface
            embedded
            currentUser={currentUser?.username || 'User'}
            currentUserRole={currentUser?.role || 'user'}
            onLogout={handleLogout}
            onBackHome={() => navigateToPage('dashboard')}
            onOpenKnowledge={() => navigateToPage('knowledge')}
            onOpenPersonalization={() => navigateToPage('personalization')}
            onOpenAdminFeedback={() => navigateToPage('admin-feedback')}
            onNavigate={navigateToPage}
            preferredSessionId={selectedSessionId}
            initialContext={pageIntent.params.context || ''}
          />
        );
      case 'practice':
        if (pageIntent.params.view === 'workspace') {
          return <PracticePage navigationContext={pageIntent.params} onBackHome={() => navigateToPage('dashboard')} />;
        }
        if (pageIntent.params.view === 'path') {
          return (
            <DashboardPage
              currentUser={currentUser}
              navigationContext={pageIntent.params}
              onNavigate={navigateToPage}
              onKnowledgeContextChange={setKnowledgeNavigationContext}
            />
          );
        }
        return (
          <LearningStageLanding
            onStageSelect={startStageTransition}
            onCreatePlan={() => navigateToPage({
              page: 'assistant',
              params: { context: '请结合我的学习状态，给我制定一份长期学习规划。' },
            })}
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
        return <PersonalizationHubPage navigationContext={pageIntent.params} onNavigate={navigateToPage} />;
      case 'settings':
        return <SettingsPage onBackHome={() => navigateToPage('dashboard')} />;
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
      onNavigate={navigateToPage}
      onLogout={handleLogout}
    >
      {renderAuthenticatedPage()}
      <StagePageTransition
        selection={stageTransition}
        onMidpoint={openStagePathAtMidpoint}
        onComplete={finishStageTransition}
      />
    </AppShell>
  );
}
