import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ChatInterface from './ChatInterface';
import { fetchWithAuth } from '../utils/api';
import { formatMessageTime } from '../chatTime';

vi.mock('../utils/api', () => ({
  API_BASE: 'http://api.test',
  MAIN_API_BASE: 'http://main-api.test/api/v1',
  fetchWithAuth: vi.fn(),
  readJsonResponse: vi.fn(),
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
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([
          { id: 'session-a', title: '会话 A' },
          { id: 'session-b', title: '会话 B' },
        ]));
      }
      if (url.endsWith('/conversations/session-a/messages')) {
        sessionACalls += 1;
        if (sessionACalls === 1) return Promise.resolve(jsonResponse([{ id: 1, role: 'assistant', content: 'A 的缓存回答' }]));
        return delayedRefresh.promise;
      }
      if (url.endsWith('/conversations/session-b/messages')) {
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

  it('presents useful starter actions and a clearly labelled composer', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-empty', title: '新对话' }]));
      }
      if (url.endsWith('/conversations/session-empty/messages')) {
        return Promise.resolve(jsonResponse([]));
      }
      throw new Error(`unexpected request: ${url}`);
    });
    render(<ChatInterface currentUser="alice" preferredSessionId="session-empty" embedded />);

    expect(await screen.findByRole('heading', { name: '从这里开始' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '制定学习计划' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '讲解知识点' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成练习试卷' })).toBeInTheDocument();
    const composer = screen.getByRole('textbox', { name: '向智能助教提问' });
    fireEvent.click(screen.getByRole('button', { name: '讲解知识点' }));
    expect(composer).toHaveValue('请结合教材证据讲解一个知识点，并给我一道练习题。');
  });

  it('renders assistant messages as readable articles with Chinese speaker labels', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-answer', title: '知识讲解' }]));
      }
      if (url.endsWith('/conversations/session-answer/messages')) {
        return Promise.resolve(jsonResponse([
          { id: 7, role: 'assistant', content: '这是正式回答。', timestamp: '20:00' },
        ]));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<ChatInterface currentUser="alice" preferredSessionId="session-answer" embedded />);

    expect(await screen.findByRole('article', { name: '智能助教回复' })).toHaveTextContent('这是正式回答。');
    expect(screen.getByText('智能助教')).toBeInTheDocument();
    expect(screen.queryByText('You')).not.toBeInTheDocument();
  });

  it('restores persisted workflow actions and keeps them navigable after reopening a session', async () => {
    const onNavigate = vi.fn();
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-paper', title: '组卷' }]));
      }
      if (url.endsWith('/conversations/session-paper/messages')) {
        return Promise.resolve(jsonResponse([{
          id: 8,
          role: 'assistant',
          content: '试卷已经生成。',
          actions: [{
            label: '开始答题',
            destination: 'workshop.paper',
            params: { paper_id: 'PAPER_1' },
          }],
        }]));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(
      <ChatInterface
        currentUser="alice"
        preferredSessionId="session-paper"
        embedded
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '开始答题' }));
    expect(onNavigate).toHaveBeenCalledWith(expect.objectContaining({
      page: 'practice',
      params: expect.objectContaining({
        taskType: 'paper_workspace',
        paperId: 'PAPER_1',
      }),
    }));
  });

  it('formats persisted ISO timestamps for people instead of exposing transport data', () => {
    expect(formatMessageTime('2026-07-20T12:34:00+08:00', new Date('2026-07-21T09:00:00+08:00')))
      .toBe('07月20日 12:34');
    expect(formatMessageTime('2026-07-21T08:05:00+08:00', new Date('2026-07-21T09:00:00+08:00')))
      .toBe('08:05');
    expect(formatMessageTime('not-a-date')).toBe('not-a-date');
  });
});
