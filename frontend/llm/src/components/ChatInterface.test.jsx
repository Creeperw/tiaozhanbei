import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ChatInterface from './ChatInterface';
import { fetchWithAuth, readJsonResponse } from '../utils/api';
import { formatMessageTime } from '../chatTime';

vi.mock('../utils/api', () => ({
  API_BASE: 'http://api.test',
  MAIN_API_BASE: 'http://main-api.test/api/v1',
  fetchWithAuth: vi.fn(),
  readJsonResponse: vi.fn(),
}));

vi.mock('./AgentTimeline', () => ({
  default: ({ isOpen, title }) => (isOpen
    ? <aside aria-label="执行进度">{title}</aside>
    : null),
}));

vi.mock('../stores/useLangGraphStore', () => {
  const state = {
    resetWorkflow: vi.fn(),
    dispatchEvent: vi.fn(),
    appendAnswer: vi.fn(),
    setReferences: vi.fn(),
    markNetworkInterrupted: vi.fn(),
  };
  return {
    buildTraceFromEvents: vi.fn((events = []) => events),
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
    document.execCommand = vi.fn(() => true);
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

  it('allows another conversation to start while the first conversation keeps running', async () => {
    const streams = [];
    fetchWithAuth.mockImplementation((url, options = {}) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([
          { id: 'session-a', title: '会话 A' },
          { id: 'session-b', title: '会话 B' },
        ]));
      }
      if (url.endsWith('/conversations/session-a/messages')
        || url.endsWith('/conversations/session-b/messages')) {
        return Promise.resolve(jsonResponse([]));
      }
      if (url.endsWith('/review-cards/stream') && options.method === 'POST') {
        const channel = new TransformStream();
        const writer = channel.writable.getWriter();
        streams.push(writer);
        void writer.write(new TextEncoder().encode(
          `data: ${JSON.stringify({ event: 'run_started' })}\n\n`,
        ));
        return Promise.resolve(new Response(channel.readable, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<ChatInterface currentUser="alice" preferredSessionId="session-a" embedded />);
    const composer = await screen.findByRole('textbox', { name: '向智能助教提问' });
    fireEvent.change(composer, { target: { value: '会话 A 的任务' } });
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
    await waitFor(() => expect(streams).toHaveLength(1));

    fireEvent.click(screen.getAllByText('会话 B')[0]);
    await screen.findByRole('heading', { name: '从这里开始' });
    fireEvent.change(screen.getByRole('textbox', { name: '向智能助教提问' }), {
      target: { value: '会话 B 的任务' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }));
    await waitFor(() => expect(streams).toHaveLength(2));

    await Promise.all(streams.map(async (writer, index) => {
      await writer.write(new TextEncoder().encode(
        `data: ${JSON.stringify({
          event: 'run_completed',
          result: { status: 'success' },
          assistant_message: `会话 ${index + 1} 已完成`,
        })}\n\n`,
      ));
      await writer.close();
    }));
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
    const sessionRail = screen.getByLabelText('会话列表');
    expect(sessionRail).toHaveClass('w-[244px]');
    expect(within(sessionRail).getByRole('button', { name: '新对话' })).toHaveClass('h-9');
    expect(screen.getByRole('button', { name: '制定学习计划' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '讲解知识点' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '生成练习试卷' })).toBeInTheDocument();
    expect(screen.queryByTitle('新建对话')).not.toBeInTheDocument();
    expect(screen.getByRole('banner')).toHaveClass('h-11');
    expect(document.querySelector('.assistant-composer')).toHaveClass('border-t-0');
    expect(screen.getByRole('textbox', { name: '向智能助教提问' })).toHaveClass('focus-visible:outline-none');
    fireEvent.click(screen.getByTitle('收起侧边栏'));
    expect(screen.getByRole('button', { name: '展开侧边栏' })).toBeInTheDocument();
    const composer = screen.getByRole('textbox', { name: '向智能助教提问' });
    fireEvent.click(screen.getByRole('button', { name: '讲解知识点' }));
    expect(composer).toHaveValue('请结合教材证据讲解一个知识点，并给我一道练习题。');
  });

  it('restores the newest existing session when no preferred or saved session exists', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([
          { id: 'session-newest', title: '最近对话' },
          { id: 'session-older', title: '较早对话' },
        ]));
      }
      if (url.endsWith('/conversations/session-newest/messages')) {
        return Promise.resolve(jsonResponse([
          { id: 11, role: 'assistant', content: '恢复最近一次回答' },
        ]));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<ChatInterface currentUser="alice" embedded />);

    expect(await screen.findByText('恢复最近一次回答')).toBeInTheDocument();
    expect(localStorage.getItem('lastSessionId')).toBe('session-newest');
  });

  it('consumes a forced new-conversation command after creating exactly one session', async () => {
    const onNewConversationConsumed = vi.fn();
    let created = 0;
    fetchWithAuth.mockImplementation((url, options = {}) => {
      if (url.endsWith('/conversations') && options.method === 'POST') {
        created += 1;
        return Promise.resolve(jsonResponse({ id: 'session-created', title: '新对话' }));
      }
      if (url.endsWith('/conversations')) return Promise.resolve(jsonResponse([]));
      if (url.endsWith('/conversations/session-created/messages')) return Promise.resolve(jsonResponse([]));
      throw new Error(`unexpected request: ${url}`);
    });

    const { rerender } = render(
      <ChatInterface
        currentUser="alice"
        embedded
        forceNewConversation
        onNewConversationConsumed={onNewConversationConsumed}
      />,
    );

    await waitFor(() => expect(onNewConversationConsumed).toHaveBeenCalledOnce());
    expect(created).toBe(1);
    rerender(
      <ChatInterface
        currentUser="alice"
        embedded
        onNewConversationConsumed={onNewConversationConsumed}
      />,
    );
    await Promise.resolve();
    expect(created).toBe(1);
  });

  it('reloads sessions from the selected exam workspace after the target changes', async () => {
    let listCalls = 0;
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        listCalls += 1;
        return Promise.resolve(jsonResponse(listCalls === 1
          ? [{ id: 'session-old-exam', title: '旧证会话' }]
          : [{ id: 'session-new-exam', title: '新证会话' }]));
      }
      if (url.endsWith('/conversations/session-old-exam/messages')) {
        return Promise.resolve(jsonResponse([{ id: 1, role: 'assistant', content: '旧证内容' }]));
      }
      if (url.endsWith('/conversations/session-new-exam/messages')) {
        return Promise.resolve(jsonResponse([{ id: 2, role: 'assistant', content: '新证内容' }]));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<ChatInterface currentUser="alice" embedded />);
    expect(await screen.findByText('旧证内容')).toBeInTheDocument();

    window.dispatchEvent(new CustomEvent('shizhen:learning-target-changed', {
      detail: { exam_track_id: 'track-new' },
    }));

    expect(await screen.findByText('新证内容')).toBeInTheDocument();
    expect(screen.queryByText('旧证内容')).not.toBeInTheDocument();
    expect(listCalls).toBe(2);
  });

  it('restores the collaboration receipt for a still-running task after refresh', async () => {
    // 模拟刷新前的本地 pending run 记录（localStorage 持久化）。
    localStorage.setItem('assistantPendingWorkflowRuns', JSON.stringify({
      'session-a': 'THREAD_RUNNING_1',
    }));
    // getWorkflowRun 依赖 readJsonResponse 解析响应体。
    readJsonResponse.mockImplementation(async (response) => response._payload ?? {});
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-a', title: '会话 A' }]));
      }
      if (url.endsWith('/conversations/session-a/messages')) {
        // 任务仍在运行：历史消息里还没有 assistant 回答。
        return Promise.resolve(jsonResponse([
          { id: 1, role: 'user', content: '给我一套试卷' },
        ]));
      }
      if (url.endsWith('/review-cards/runs/THREAD_RUNNING_1')) {
        // 后端修复 1：running 状态携带 progress_events。
        return Promise.resolve({
          ...jsonResponse({}),
          _payload: {
            status: 'running',
            thread_id: 'THREAD_RUNNING_1',
            progress_events: [
              { event: 'step_started', agent: 'planner_agent', step_id: 'planner' },
              { event: 'step_completed', agent: 'planner_agent', step_id: 'planner' },
              { event: 'graph_compiled', nodes: [] },
              { event: 'step_started', agent: 'audit_agent', step_id: 'audit' },
            ],
          },
        });
      }
      throw new Error(`unexpected request: ${url}`);
    });

    const { container } = render(
      <ChatInterface currentUser="alice" preferredSessionId="session-a" embedded />,
    );

    // 修复 2：轮询恢复出占位 assistant 的协作回执（"正在协作"）。
    await waitFor(() => {
      const receipt = container.querySelector('.agent-collaboration-receipt');
      expect(receipt).toBeInTheDocument();
      expect(receipt.getAttribute('aria-label')).toContain('协作');
    });
    expect(fetchWithAuth).toHaveBeenCalledWith(
      expect.stringContaining('/review-cards/runs/THREAD_RUNNING_1'),
    );
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

  it('keeps the assistant visible when system-browser clipboard access is denied', async () => {
    const writeText = vi.fn().mockRejectedValue(new DOMException('denied', 'NotAllowedError'));
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-copy', title: '复制测试' }]));
      }
      if (url.endsWith('/conversations/session-copy/messages')) {
        return Promise.resolve(jsonResponse([
          { id: 17, role: 'assistant', content: '需要安全复制的回答。' },
        ]));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<ChatInterface currentUser="alice" preferredSessionId="session-copy" embedded />);

    const article = await screen.findByRole('article', { name: '智能助教回复' });
    fireEvent.click(within(article).getByRole('button', { name: '复制' }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith('需要安全复制的回答。'));
    expect(document.execCommand).toHaveBeenCalledWith('copy');
    expect(article).toBeInTheDocument();
    expect(within(article).getByRole('button', { name: '已复制' })).toBeInTheDocument();
  });

  it('keeps Ctrl+C native to the assistant instead of leaking it to shell shortcuts', async () => {
    const shellKeyDown = vi.fn();
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-shortcut', title: '快捷键测试' }]));
      }
      if (url.endsWith('/conversations/session-shortcut/messages')) {
        return Promise.resolve(jsonResponse([]));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(
      <div onKeyDown={shellKeyDown}>
        <ChatInterface currentUser="alice" preferredSessionId="session-shortcut" embedded />
      </div>,
    );
    const composer = await screen.findByRole('textbox', { name: '向智能助教提问' });
    fireEvent.change(composer, { target: { value: '保留当前页面' } });
    fireEvent.keyDown(composer, { key: 'c', ctrlKey: true });

    expect(shellKeyDown).not.toHaveBeenCalled();
    expect(composer).toHaveValue('保留当前页面');
    expect(screen.getByRole('heading', { name: '从这里开始' })).toBeInTheDocument();
  });

  it('closes session-owned detail sidebars when switching conversations', async () => {
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([
          { id: 'session-a', title: '会话 A' },
          { id: 'session-b', title: '会话 B' },
        ]));
      }
      if (url.endsWith('/conversations/session-a/messages')) {
        return Promise.resolve(jsonResponse([{
          id: 21,
          role: 'assistant',
          content: 'A 的回答',
          trace_events: [
            { event: 'step_completed', agent: 'planner_agent' },
            {
              event: 'knowledge_retrieval',
              agent: 'knowledge_base_agent',
              kp_query: '知识点 A',
              question_query: '题目 A',
              evidence_items: [{
                source_id: 'WEB_A',
                content_summary: 'A 的网页来源\n网页摘要内容',
                confidence: 0.9,
                source_url: 'https://example.com/a',
                resource_type: 'reference',
              }],
            },
          ],
        }]));
      }
      if (url.endsWith('/conversations/session-b/messages')) {
        return Promise.resolve(jsonResponse([{ id: 22, role: 'assistant', content: 'B 的回答' }]));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<ChatInterface currentUser="alice" preferredSessionId="session-a" embedded />);

    fireEvent.click(await screen.findByTitle('点击查看参考来源'));
    const retrievalSidebar = screen.getByText('检索详情').closest('.fixed');
    expect(retrievalSidebar).not.toHaveAttribute('aria-hidden');
    expect(screen.getAllByText('A 的网页来源').length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole('button', { name: /查看多智能体协作过程/ }));
    expect(screen.getByRole('complementary', { name: '执行进度' })).toBeInTheDocument();

    fireEvent.click(screen.getAllByText('会话 B')[0]);
    expect(await screen.findByText('B 的回答')).toBeInTheDocument();
    expect(screen.queryByRole('complementary', { name: '执行进度' })).not.toBeInTheDocument();
    expect(retrievalSidebar).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByText('A 的网页来源')).not.toBeInTheDocument();
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
        returnTo: { page: 'assistant', params: { sessionId: 'session-paper' } },
      }),
    }));
  });

  it('opens persisted video actions in the embedded video player', async () => {
    const onNavigate = vi.fn();
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-video', title: '视频学习' }]));
      }
      if (url.endsWith('/conversations/session-video/messages')) {
        return Promise.resolve(jsonResponse([{
          id: 9,
          role: 'assistant',
          content: '章节视频已经准备好。',
          actions: [{
            label: '观看视频',
            destination: 'workshop.knowledge_video',
            params: {
              task_item_id: 'ITEM_VIDEO',
              video: { title: '章节精讲', url: 'https://example.test/video.mp4' },
            },
          }],
        }]));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(
      <ChatInterface
        currentUser="alice"
        preferredSessionId="session-video"
        embedded
        onNavigate={onNavigate}
      />,
    );

    fireEvent.click(await screen.findByRole('button', { name: '观看视频' }));
    expect(onNavigate).toHaveBeenCalledWith(expect.objectContaining({
      page: 'practice',
      params: expect.objectContaining({
        taskType: 'video_learning',
        resourceView: 'videos',
        taskItemId: 'ITEM_VIDEO',
        directVideo: { title: '章节精讲', url: 'https://example.test/video.mp4' },
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
