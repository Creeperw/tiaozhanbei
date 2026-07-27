import React, { useCallback, useEffect, useState } from 'react';
import AuthPage from './components/AuthPage';
import ChatInterface from './components/ChatInterface';
import KnowledgePage from './components/KnowledgePage';
import PersonalizationHubPage from './components/PersonalizationHubPage';
import SettingsHubPage from './components/SettingsHubPage';
import AdminFeedbackPage from './components/AdminFeedbackPage';
import HomePage from './components/HomePage';
import CapabilityDetailPage from './components/CapabilityDetailPage';
import LearningPathPage from './components/LearningPathPage';
import DashboardPage from './components/DashboardPage';
import PracticePage from './components/PracticePage';
import LearningStageLanding from './components/learning-stage/LearningStageLanding';
import TextbookChapterLearning from './components/workshop-textbook/TextbookChapterLearning';
import StagePageTransition from './components/learning-stage/StagePageTransition';
import AppShell from './components/AppShell';
import RegistrationJourney from './components/RegistrationJourney';
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

export default function App() {
  const [currentUser, setCurrentUser] = useState(null);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [pageIntent, setPageIntent] = useState(initialPageIntent);
  const [navigationRevision, setNavigationRevision] = useState(0);
  const [knowledgeNavigationContext, setKnowledgeNavigationContext] = useState(null);
  const [stageTransition, setStageTransition] = useState(null);
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
    if (!user?.onboarding_required) navigateToPage('dashboard');
  };

  const handleOnboardingSaved = (payload) => {
    setCurrentUser(payload?.user || {
      ...currentUser,
      onboarding_required: false,
    });
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
        setPageIntent(createPageIntent(destination.page, { ...params, view: params.view || 'user-profile' }));
        return;
      }
      if (destination.page === 'training-workshop') setNavigationRevision((value) => value + 1);
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
      setPageIntent(createPageIntent(destination, { ...params, view: params.view || 'user-profile' }));
      return;
    }
    if (destination === 'training-workshop') setNavigationRevision((value) => value + 1);
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

  if (currentUser.onboarding_required) {
    return (
      <RegistrationJourney
        existingUser={currentUser}
        onComplete={(user) => handleOnboardingSaved({ user })}
        onExit={handleLogout}
      />
    );
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
        return <LearningPathPage currentUser={currentUser} onNavigate={navigateToPage} />;
      case 'assistant':
        return (
          <ChatInterface
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
          return <PracticePage navigationContext={pageIntent.params} onBackHome={() => navigateToPage('dashboard')} />;
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
        return <PracticePage key={`training-workshop-${navigationRevision}`} navigationContext={pageIntent.params} />;
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
      onNavigate={navigateToPage}
      onLogout={handleLogout}
      onUserUpdated={(updatedUser) => setCurrentUser((current) => ({ ...current, ...updatedUser }))}
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
