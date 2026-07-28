import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';
import { readJsonResponse } from './utils/api';

vi.mock('./utils/api', () => ({
  AUTH_API_BASE: 'http://api.test/api/v1/auth',
  fetchWithAuth: vi.fn(() => Promise.resolve({ ok: true })),
  readJsonResponse: vi.fn(() => Promise.resolve({ user: { username: 'admin', role: 'admin' } })),
}));

vi.mock('./components/AuthPage', () => ({ default: () => <div>Auth</div> }));
vi.mock('./components/HomePage', () => ({ default: () => <div>Home portal</div> }));
vi.mock('./components/DashboardPage', () => ({
  default: ({ navigationContext = {}, onKnowledgeContextChange }) => (
    <div data-testid="training-overview" data-view={navigationContext.view || ''} data-path-mode={navigationContext.pathMode || ''} data-stage-id={navigationContext.stageId || ''}>
      Training overview
      <button type="button" onClick={() => onKnowledgeContextChange?.({ trackId: 'track-a' })}>Publish target</button>
    </div>
  ),
}));
vi.mock('./components/learning-stage/LearningStageLanding', () => ({
  default: ({ onStageSelect }) => (
    <button
      type="button"
      onClick={() => onStageSelect({
        stage: { id: 'plan-stage-1', nodeId: 'plan:LP_1:stage:stage-1', title: '中医基础与文化语言' },
        index: 0,
        sourceRect: { left: 10, top: 20, width: 200, height: 300 },
      })}
    >
      Stage landing
    </button>
  ),
}));
vi.mock('./components/learning-stage/StagePageTransition', () => ({
  default: ({ selection, onMidpoint, onComplete }) => selection ? (
    <div>
      Stage transition
      <button type="button" onClick={() => onMidpoint(selection)}>Reach flip midpoint</button>
      <button type="button" onClick={onComplete}>Finish flip</button>
    </div>
  ) : null,
}));
vi.mock('./components/ChatInterface', () => ({ default: ({ embedded }) => <div>Assistant page {String(embedded)}</div> }));
vi.mock('./components/CompactAssistant', () => ({
  default: ({ onOpenFull }) => (
    <button type="button" aria-label="全局悬浮智能助教" onClick={() => onOpenFull('session-floating')}>
      Floating assistant
    </button>
  ),
}));
vi.mock('./components/KnowledgePage', () => ({
  default: ({ navigationContext = {} }) => (
    <div
      data-testid="knowledge-page"
      data-view={navigationContext.view || ''}
      data-route={navigationContext.route || ''}
      data-track-id={navigationContext.trackId || ''}
      data-source={navigationContext.source || ''}
    >Knowledge page</div>
  ),
}));
vi.mock('./components/PracticePage', () => ({
  default: function MockPracticePage({ navigationContext = {} }) {
    const [view, setView] = React.useState('overview');
    return (
      <div data-testid="practice-page" data-view={navigationContext.view || ''} data-task-type={navigationContext.taskType || ''} data-paper-id={navigationContext.paperId || ''}>
        Practice workspace
        <span data-testid="practice-local-view">{view}</span>
        <button type="button" onClick={() => setView('module')}>Open mock training module</button>
      </div>
    );
  },
}));
vi.mock('./components/PersonalizationHubPage', () => ({
  default: ({ navigationContext = {} }) => (
    <div data-testid="personalization-page" data-view={navigationContext.view || ''}>Personalization page</div>
  ),
}));
vi.mock('./components/SettingsHubPage', () => ({
  default: ({ navigationContext = {} }) => <div data-testid="settings-page" data-view={navigationContext.view || ''}>Settings page</div>,
}));
vi.mock('./components/AdminFeedbackPage', () => ({ default: () => <div>Admin page</div> }));
vi.mock('./components/AppShell', () => ({
  default: ({ children, currentPage, onNavigate }) => (
    <div data-testid="authenticated-shell" data-page={currentPage}>
      <button type="button" onClick={() => onNavigate({ page: 'assistant', params: {} })}>Go assistant</button>
      <button type="button" onClick={() => onNavigate({ page: 'knowledge', params: { view: 'atlas' } })}>Go knowledge</button>
      <button type="button" onClick={() => onNavigate({ page: 'knowledge', params: {} })}>Go default knowledge</button>
      <button type="button" onClick={() => onNavigate({ page: 'dashboard', params: {} })}>Go dashboard</button>
      <button type="button" onClick={() => onNavigate({ page: 'practice', params: {} })}>Go learning workshop</button>
      <button type="button" onClick={() => onNavigate({ page: 'practice', params: { view: 'stages' } })}>Go learning stages</button>
      <button type="button" onClick={() => onNavigate({ page: 'practice', params: { view: 'workspace' } })}>Go legacy training workspace</button>
      <button type="button" onClick={() => onNavigate({ page: 'training-workshop', params: {} })}>Go training workshop</button>
      <button type="button" onClick={() => onNavigate({ page: 'personalization', params: {} })}>Go personalization</button>
      <button type="button" onClick={() => onNavigate({ page: 'personalization', params: { view: 'memory' } })}>Go memory</button>
      <button type="button" onClick={() => onNavigate({ page: 'personalization', params: { view: 'governance' } })}>Go governance</button>
      <button type="button" onClick={() => onNavigate({ page: 'admin-feedback', params: {} })}>Go admin</button>
      {children}
    </div>
  ),
}));

describe('authenticated application shell', () => {
  beforeEach(() => {
    localStorage.clear();
    sessionStorage.clear();
  });

  it('enters the system directly even when an existing session still carries the legacy onboarding flag', async () => {
    vi.mocked(readJsonResponse).mockResolvedValueOnce({
      user: { username: 'new-user', role: 'user', onboarding_required: true },
    });

    render(<App />);

    expect(await screen.findByText('Home portal')).toBeInTheDocument();
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'dashboard');
  });

  it('consumes a one-time external navigation intent for an audited paper', async () => {
    sessionStorage.setItem('competition.pending-navigation', JSON.stringify({
      page: 'practice',
      params: { view: 'workspace', taskType: 'paper_workspace', paperId: 'PAPER_1' },
    }));

    render(<App />);

    expect(await screen.findByText('Practice workspace')).toBeInTheDocument();
    expect(screen.getByTestId('practice-page')).toHaveAttribute('data-task-type', 'paper_workspace');
    expect(screen.getByTestId('practice-page')).toHaveAttribute('data-paper-id', 'PAPER_1');
    expect(sessionStorage.getItem('competition.pending-navigation')).toBeNull();
  });

  it('keeps the learning workshop and training workshop as separate AppShell pages', async () => {
    render(<App />);

    expect(await screen.findByText('Home portal')).toBeInTheDocument();
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'dashboard');

    fireEvent.click(screen.getByRole('button', { name: 'Go learning workshop' }));
    expect(screen.getByText('Training overview')).toBeInTheDocument();
    expect(screen.getByTestId('training-overview')).toHaveAttribute('data-view', '');
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'practice');

    fireEvent.click(screen.getByRole('button', { name: 'Go training workshop' }));
    expect(screen.getByText('Practice workspace')).toBeInTheDocument();
    expect(screen.getByTestId('practice-page')).toHaveAttribute('data-view', '');
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'training-workshop');

    fireEvent.click(screen.getByRole('button', { name: 'Go legacy training workspace' }));
    expect(screen.getByText('Practice workspace')).toBeInTheDocument();
    expect(screen.getByTestId('practice-page')).toHaveAttribute('data-view', 'workspace');
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'training-workshop');

    fireEvent.click(screen.getByRole('button', { name: 'Go assistant' }));
    expect(screen.getByText('Assistant page true')).toBeInTheDocument();
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'assistant');

    fireEvent.click(screen.getByRole('button', { name: 'Go knowledge' }));
    expect(screen.getByText('Knowledge page')).toBeInTheDocument();
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'knowledge');

    fireEvent.click(screen.getByRole('button', { name: 'Go admin' }));
    expect(screen.getByText('Admin page')).toBeInTheDocument();
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'admin-feedback');
  });

  it('opens the full assistant from the global floating assistant and hides the duplicate dock there', async () => {
    render(<App />);

    expect(await screen.findByRole('button', { name: '全局悬浮智能助教' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '全局悬浮智能助教' }));

    expect(screen.getByText('Assistant page true')).toBeInTheDocument();
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'assistant');
    expect(screen.queryByRole('button', { name: '全局悬浮智能助教' })).not.toBeInTheDocument();
  });

  it('resets the training workshop when its primary navigation entry is selected again', async () => {
    render(<App />);
    expect(await screen.findByText('Home portal')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Go training workshop' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open mock training module' }));
    expect(screen.getByTestId('practice-local-view')).toHaveTextContent('module');

    fireEvent.click(screen.getByRole('button', { name: 'Go training workshop' }));
    expect(screen.getByTestId('practice-local-view')).toHaveTextContent('overview');
  });

  it('uses a textbook fallback for primary knowledge navigation', async () => {
    render(<App />);
    expect(await screen.findByText('Home portal')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Go default knowledge' }));
    expect(screen.getByTestId('knowledge-page')).toHaveAttribute('data-view', 'atlas');
    expect(screen.getByTestId('knowledge-page')).toHaveAttribute('data-route', 'textbook_14_5');
    expect(screen.getByTestId('knowledge-page')).toHaveAttribute('data-source', 'navigation');

  });

  it('routes moved learning memory and governance links into user settings', async () => {
    render(<App />);
    expect(await screen.findByText('Home portal')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Go personalization' }));
    expect(screen.getByTestId('personalization-page')).toHaveAttribute('data-view', 'user-profile');

    fireEvent.click(screen.getByRole('button', { name: 'Go memory' }));
    expect(screen.getByTestId('settings-page')).toHaveAttribute('data-view', 'memory');

    fireEvent.click(screen.getByRole('button', { name: 'Go governance' }));
    expect(screen.getByTestId('settings-page')).toHaveAttribute('data-view', 'governance');
  });
});
