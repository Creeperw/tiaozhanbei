import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import App from './App';

vi.mock('./utils/api', () => ({
  API_BASE: 'http://api.test',
  fetchWithAuth: vi.fn(() => Promise.resolve({ ok: true })),
  readJsonResponse: vi.fn(() => Promise.resolve({ username: 'admin', role: 'admin' })),
}));

vi.mock('./components/AuthPage', () => ({ default: () => <div>Auth</div> }));
vi.mock('./components/HomePage', () => ({ default: () => <div>Home portal</div> }));
vi.mock('./components/DashboardPage', () => ({
  default: ({ onKnowledgeContextChange }) => (
    <div>
      Training overview
      <button type="button" onClick={() => onKnowledgeContextChange?.({ trackId: 'track-a' })}>Publish target</button>
    </div>
  ),
}));
vi.mock('./components/ChatInterface', () => ({ default: ({ embedded }) => <div>Assistant page {String(embedded)}</div> }));
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
  default: ({ navigationContext = {} }) => <div data-testid="practice-page" data-view={navigationContext.view || ''}>Practice workspace</div>,
}));
vi.mock('./components/PersonalizationHubPage', () => ({
  default: ({ navigationContext = {} }) => (
    <div data-testid="personalization-page" data-view={navigationContext.view || ''}>Personalization page</div>
  ),
}));
vi.mock('./components/SettingsPage', () => ({ default: () => <div>Settings page</div> }));
vi.mock('./components/AdminFeedbackPage', () => ({ default: () => <div>Admin page</div> }));
vi.mock('./components/AppShell', () => ({
  default: ({ children, currentPage, onNavigate }) => (
    <div data-testid="authenticated-shell" data-page={currentPage}>
      <button type="button" onClick={() => onNavigate({ page: 'assistant', params: {} })}>Go assistant</button>
      <button type="button" onClick={() => onNavigate({ page: 'knowledge', params: { view: 'atlas' } })}>Go knowledge</button>
      <button type="button" onClick={() => onNavigate({ page: 'knowledge', params: {} })}>Go default knowledge</button>
      <button type="button" onClick={() => onNavigate({ page: 'dashboard', params: {} })}>Go dashboard</button>
      <button type="button" onClick={() => onNavigate({ page: 'practice', params: {} })}>Go training overview</button>
      <button type="button" onClick={() => onNavigate({ page: 'practice', params: { view: 'workspace' } })}>Go training workspace</button>
      <button type="button" onClick={() => onNavigate({ page: 'personalization', params: {} })}>Go personalization</button>
      <button type="button" onClick={() => onNavigate({ page: 'personalization', params: { view: 'memory' } })}>Go memory</button>
      <button type="button" onClick={() => onNavigate({ page: 'admin-feedback', params: {} })}>Go admin</button>
      {children}
    </div>
  ),
}));

describe('authenticated application shell', () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem('token', 'token');
  });

  it('routes the portal, training overview, training workspace, assistant, knowledge, and administration inside one AppShell', async () => {
    render(<App />);

    expect(await screen.findByText('Home portal')).toBeInTheDocument();
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'dashboard');

    fireEvent.click(screen.getByRole('button', { name: 'Go training overview' }));
    expect(screen.getByText('Training overview')).toBeInTheDocument();
    expect(screen.getByTestId('authenticated-shell')).toHaveAttribute('data-page', 'practice');

    fireEvent.click(screen.getByRole('button', { name: 'Go training workspace' }));
    expect(screen.getByText('Practice workspace')).toBeInTheDocument();
    expect(screen.getByTestId('practice-page')).toHaveAttribute('data-view', 'workspace');

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

  it('uses the current learning target for primary knowledge navigation and a textbook fallback without one', async () => {
    render(<App />);
    expect(await screen.findByText('Home portal')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Go default knowledge' }));
    expect(screen.getByTestId('knowledge-page')).toHaveAttribute('data-view', 'atlas');
    expect(screen.getByTestId('knowledge-page')).toHaveAttribute('data-route', 'textbook_14_5');
    expect(screen.getByTestId('knowledge-page')).toHaveAttribute('data-source', 'navigation');

    fireEvent.click(screen.getByRole('button', { name: 'Go training overview' }));
    fireEvent.click(screen.getByRole('button', { name: 'Publish target' }));
    fireEvent.click(screen.getByRole('button', { name: 'Go default knowledge' }));
    expect(screen.getByTestId('knowledge-page')).toHaveAttribute('data-track-id', 'track-a');
    expect(screen.getByTestId('knowledge-page')).toHaveAttribute('data-source', 'navigation');
  });

  it('routes profile and memory to independent personalization views', async () => {
    render(<App />);
    expect(await screen.findByText('Home portal')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Go personalization' }));
    expect(screen.getByTestId('personalization-page')).toHaveAttribute('data-view', 'profile');

    fireEvent.click(screen.getByRole('button', { name: 'Go memory' }));
    expect(screen.getByTestId('personalization-page')).toHaveAttribute('data-view', 'memory');
  });
});
