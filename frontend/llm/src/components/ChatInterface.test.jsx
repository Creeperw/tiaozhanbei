import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ChatInterface from './ChatInterface';
import { fetchWithAuth } from '../utils/api';

vi.mock('../utils/api', () => ({
  API_BASE: 'http://api.test',
  fetchWithAuth: vi.fn(),
}));

vi.mock('./AgentTimeline', () => ({ default: () => null }));

vi.mock('../stores/useLangGraphStore', () => {
  const state = {
    resetWorkflow: vi.fn(),
    dispatchEvent: vi.fn(),
    appendAnswer: vi.fn(),
    setReferences: vi.fn(),
    markNetworkInterrupted: vi.fn(),
  };
  return {
    buildTraceFromEvents: vi.fn(() => []),
    useLangGraphStore: (selector) => selector(state),
  };
});

function jsonResponse(payload) {
  return { ok: true, json: () => Promise.resolve(payload) };
}

function deferred() {
  let resolve;
  const promise = new Promise((next) => { resolve = next; });
  return { promise, resolve };
}

describe('ChatInterface session workspace', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it('restores a cached session immediately without a forced full-screen transition', async () => {
    const delayedRefresh = deferred();
    let sessionACalls = 0;
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/sessions')) {
        return Promise.resolve(jsonResponse([
          { id: 'session-a', title: '会话 A' },
          { id: 'session-b', title: '会话 B' },
        ]));
      }
      if (url.endsWith('/sessions/session-a/messages')) {
        sessionACalls += 1;
        if (sessionACalls === 1) return Promise.resolve(jsonResponse([{ id: 1, role: 'assistant', content: 'A 的缓存回答' }]));
        return delayedRefresh.promise;
      }
      if (url.endsWith('/sessions/session-b/messages')) {
        return Promise.resolve(jsonResponse([{ id: 2, role: 'assistant', content: 'B 的回答' }]));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    const { container } = render(
      <ChatInterface currentUser="alice" preferredSessionId="session-a" embedded />,
    );

    expect(await screen.findByText('A 的缓存回答')).toBeInTheDocument();
    fireEvent.click(screen.getAllByText('会话 B')[0]);
    expect(await screen.findByText('B 的回答')).toBeInTheDocument();

    fireEvent.click(screen.getAllByText('会话 A')[0]);
    expect(screen.getByText('A 的缓存回答')).toBeInTheDocument();
    expect(container.querySelector('[data-session-switching]')).not.toBeInTheDocument();

    delayedRefresh.resolve(jsonResponse([{ id: 1, role: 'assistant', content: 'A 的刷新回答' }]));
    expect(await screen.findByText('A 的刷新回答')).toBeInTheDocument();
  });
});
