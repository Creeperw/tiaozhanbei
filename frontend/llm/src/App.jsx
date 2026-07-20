import React, { useEffect, useState } from 'react';
import AuthPage from './components/AuthPage';
import ChatInterface from './components/ChatInterface';
import KnowledgePage from './components/KnowledgePage';
import PersonalizationHubPage from './components/PersonalizationHubPage';
import SettingsPage from './components/SettingsPage';
import AdminFeedbackPage from './components/AdminFeedbackPage';
import HomePage from './components/HomePage';
import DashboardPage from './components/DashboardPage';
import PracticePage from './components/PracticePage';
import AppShell from './components/AppShell';
import { API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';
import { getAppShellConfig } from './appShell';
import { createPageIntent, getIntentPage } from './pageIntent';

export default function App() {
  const [token, setToken] = useState(localStorage.getItem('token'));
  const [currentUser, setCurrentUser] = useState(null);
  const [checkingAuth, setCheckingAuth] = useState(true);
  const [pageIntent, setPageIntent] = useState(() => createPageIntent('dashboard'));
  const [knowledgeNavigationContext, setKnowledgeNavigationContext] = useState(null);
  const currentPage = getIntentPage(pageIntent);
  const selectedSessionId = pageIntent.params.sessionId || null;

  useEffect(() => {
    const verifyToken = async () => {
      if (token) {
        try {
          const res = await fetchWithAuth(`${API_BASE}/users/me`);
          if (res.ok) {
            const data = await readJsonResponse(res, {});
            setCurrentUser(data);
          } else {
            setToken(null);
            localStorage.removeItem('token');
          }
        } catch {
          setToken(null);
          localStorage.removeItem('token');
        }
      }
      setCheckingAuth(false);
    };

    verifyToken();
  }, [token]);

  const handleLogin = (username) => {
    setToken(localStorage.getItem('token'));
    setCurrentUser({ username });
    navigateToPage('dashboard');
  };

  const handleLogout = () => {
    localStorage.removeItem('token');
    setToken(null);
    setCurrentUser(null);
    setKnowledgeNavigationContext(null);
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

  if (checkingAuth) {
    return <div className="flex h-screen items-center justify-center bg-[#f8fafc] text-gray-400">Loading...</div>;
  }

  if (!token) {
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
            preferredSessionId={selectedSessionId}
            initialContext={pageIntent.params.context || ''}
          />
        );
      case 'practice':
        return pageIntent.params.view === 'workspace'
          ? <PracticePage navigationContext={pageIntent.params} onBackHome={() => navigateToPage('dashboard')} />
          : (
            <DashboardPage
              currentUser={currentUser}
              navigationContext={pageIntent.params}
              onNavigate={navigateToPage}
              onKnowledgeContextChange={setKnowledgeNavigationContext}
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
    </AppShell>
  );
}
