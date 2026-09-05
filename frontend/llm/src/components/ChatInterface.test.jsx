import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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
  default: ({ isOpen, title, nodes }) => (isOpen
    ? <aside aria-label="执行进度" data-node-count={nodes?.length || 0}>{title}</aside>
    : null),
}));

vi.mock('../stores/useLangGraphStore', async (importOriginal) => {
  const actual = await importOriginal();
  const state = {
    resetWorkflow: vi.fn(),
    dispatchEvent: vi.fn(),
    dispatchEvents: vi.fn(),
    appendAnswer: vi.fn(),
    setReferences: vi.fn(),
    markNetworkInterrupted: vi.fn(),
  };
  return {
    ...actual,
    useLangGraphStore: (selector) => selector(state),
  };
});

// jsdom 无法测量 Virtuoso 的容器尺寸，虚拟列表不会渲染任何消息项。
// 用直接渲染全部 items 的替身，让组件测试聚焦于业务逻辑。
vi.mock('react-virtuoso', async () => {
  const React = await import('react');
  const Virtuoso = React.forwardRef(({ data, itemContent }, ref) => {
    React.useImperativeHandle(ref, () => ({
      scrollToIndex: () => {},
    }));
    return (
      <div data-testid="virtuoso-list">
        {(data || []).map((item, index) => (
          <div key={item.id || `msg-${index}`}>{itemContent(index, item)}</div>
        ))}
      </div>
    );
  });
  return { Virtuoso, VirtuosoHandle: {} };
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

  it('keeps a newly created conversation selected after its first reply completes', async () => {
    let created = false;
    fetchWithAuth.mockImplementation((url, options = {}) => {
      if (url.endsWith('/conversations') && options.method === 'POST') {
        created = true;
        return Promise.resolve(jsonResponse({ id: 'session-new', title: '新对话' }));
      }
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse(created
          ? [
              { id: 'session-new', title: '学习计划咨询' },
              { id: 'session-old', title: '旧会话' },
            ]
          : [{ id: 'session-old', title: '旧会话' }]));
      }
      if (url.endsWith('/conversations/session-old/messages')) {
        return Promise.resolve(jsonResponse([
          { id: 1, role: 'assistant', content: '旧会话回答' },
        ]));
      }
      if (url.endsWith('/conversations/session-new/messages')) {
        return Promise.resolve(jsonResponse([]));
      }
      if (url.endsWith('/review-cards/stream') && options.method === 'POST') {
        return Promise.resolve(new Response(
          `data: ${JSON.stringify({
            event: 'run_completed',
            result: { status: 'success' },
            assistant_message: '新会话回答',
          })}\n\n`,
          { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
        ));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(
      <ChatInterface currentUser="alice" preferredSessionId="session-old" embedded />,
    );
    expect(await screen.findByText('旧会话回答')).toBeInTheDocument();

    fireEvent.click(within(screen.getByLabelText('会话列表')).getByRole('button', { name: '新对话' }));
    await screen.findByRole('heading', { name: '从这里开始' });
    fireEvent.change(screen.getByRole('textbox', { name: '向智能助教提问' }), {
      target: { value: '请制定学习计划' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }));

    expect(await screen.findByText('新会话回答')).toBeInTheDocument();
    expect(screen.queryByText('旧会话回答')).not.toBeInTheDocument();
    expect(localStorage.getItem('lastSessionId')).toBe('session-new');
  });

  it('keeps live trace events separate from assistant answer content', async () => {
    let messageFetches = 0;
    fetchWithAuth.mockImplementation((url, options = {}) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-trace', title: '轨迹会话' }]));
      }
      if (url.endsWith('/conversations/session-trace/messages')) {
        messageFetches += 1;
        return Promise.resolve(jsonResponse(messageFetches === 1 ? [] : [{
          id: 31,
          role: 'assistant',
          content: '这是正式回答。',
          trace_events: [
            { event: 'step_started', step_id: 'planner', agent: 'planner_agent' },
            { event: 'run_completed' },
          ],
        }]));
      }
      if (url.endsWith('/review-cards/stream') && options.method === 'POST') {
        const events = [
          { event: 'step_started', step_id: 'planner', agent: 'planner_agent' },
          { event: 'answer_started', publication_status: 'approved' },
          { event: 'answer_delta', delta: '这是正式回答。' },
          { event: 'answer_committed', publication_status: 'approved' },
          {
            event: 'run_completed',
            result: { status: 'success' },
            assistant_message: '这是正式回答。',
          },
        ];
        return Promise.resolve(new Response(
          events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''),
          { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
        ));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<ChatInterface currentUser="alice" preferredSessionId="session-trace" embedded />);
    const composer = await screen.findByRole('textbox', { name: '向智能助教提问' });
    fireEvent.change(composer, { target: { value: '开始讲解' } });
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }));

    await waitFor(() => {
      expect(screen.getByRole('article', { name: '智能助教回复' }))
        .toHaveTextContent('这是正式回答。');
    });
    const assistant = screen.getByRole('article', { name: '智能助教回复' });
    expect(assistant).toHaveTextContent('任务规划');
    expect(assistant.textContent).not.toContain('<<EV:');
  });

  it('binds an immediate post-click send to the new conversation instead of resuming the old checkpoint', async () => {
    const sessionCreation = deferred();
    const workflowStream = deferred();
    let created = false;
    const workflowRequests = [];
    fetchWithAuth.mockImplementation((url, options = {}) => {
      if (url.endsWith('/conversations') && options.method === 'POST') {
        return sessionCreation.promise;
      }
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse(created
          ? [
              { id: 'session-new', title: '新对话' },
              { id: 'session-old', title: '旧会话' },
            ]
          : [{ id: 'session-old', title: '旧会话' }]));
      }
      if (url.endsWith('/conversations/session-old/messages')) {
        return Promise.resolve(jsonResponse([
          { id: 1, role: 'assistant', content: '旧会话中的追问' },
        ]));
      }
      if (url.endsWith('/conversations/session-new/messages')) {
        return Promise.resolve(jsonResponse([]));
      }
      if (url.includes('/resume/stream')) {
        throw new Error(`must not resume old checkpoint: ${url}`);
      }
      if (url.endsWith('/review-cards/stream') && options.method === 'POST') {
        workflowRequests.push(JSON.parse(options.body));
        return workflowStream.promise;
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(
      <ChatInterface currentUser="alice" preferredSessionId="session-old" embedded />,
    );
    expect(await screen.findByText('旧会话中的追问')).toBeInTheDocument();
    localStorage.setItem('assistantPendingWorkflowRuns', JSON.stringify({
      'session-old': 'THREAD_OLD_INTERRUPTED',
    }));

    fireEvent.click(
      within(screen.getByLabelText('会话列表'))
        .getByRole('button', { name: '新对话' }),
    );
    // Deliberately do not wait for the POST /conversations response or a
    // re-render: this reproduces the system-browser click+Enter race.
    const composer = screen.getByRole('textbox', { name: '向智能助教提问' });
    fireEvent.change(composer, { target: { value: '我今天有哪些学习任务？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }));

    created = true;
    sessionCreation.resolve(jsonResponse({ id: 'session-new', title: '新对话' }));

    await waitFor(() => expect(workflowRequests).toHaveLength(1));
    expect(workflowRequests[0].conversation_id).toBe('session-new');
    expect(workflowRequests[0].messages).toEqual([
      expect.objectContaining({
        role: 'user',
        content: '我今天有哪些学习任务？',
      }),
    ]);
    expect(JSON.stringify(workflowRequests[0].messages)).not.toContain('旧会话中的追问');
    expect(fetchWithAuth.mock.calls.some(([url]) => url.includes('/resume/stream'))).toBe(false);
    // The new run ID exists only in the browser until POST /stream reaches the
    // backend.  Switching to the newly created session must not poll it early
    // and generate the known startup 404 race.
    await Promise.resolve();
    expect(fetchWithAuth.mock.calls.some(([url]) => url.includes('/review-cards/runs/'))).toBe(false);
    expect(localStorage.getItem('lastSessionId')).toBe('session-new');

    await act(async () => {
      workflowStream.resolve(new Response(
        `data: ${JSON.stringify({
          event: 'run_completed',
          result: { status: 'success' },
          assistant_message: '新会话独立回答',
        })}\n\n`,
        { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
      ));
      await Promise.resolve();
    });
    await waitFor(() => {
      const pending = JSON.parse(localStorage.getItem('assistantPendingWorkflowRuns') || '{}');
      expect(pending['session-new']).toBeUndefined();
    });
    expect(fetchWithAuth.mock.calls.some(([url]) => url.includes('/review-cards/runs/'))).toBe(false);
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
    // Other tests may leave a detached server-owned run finishing in the
    // background; assert the workspace refresh occurred instead of coupling
    // this test to an exact global request count.
    expect(listCalls).toBeGreaterThanOrEqual(2);
  });

  it('restores the inline agent stream for a still-running task after refresh', async () => {
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

    // 刷新后直接在会话消息中恢复执行过程，不再要求用户打开侧边栏。
    await waitFor(() => {
      const trace = container.querySelector('.agent-inline');
      expect(trace).toBeInTheDocument();
      expect(trace).toHaveTextContent('任务规划');
      expect(trace).toHaveTextContent('审核裁判');
    });
    expect(screen.queryByRole('complementary', { name: '执行进度' })).not.toBeInTheDocument();
    expect(fetchWithAuth).toHaveBeenCalledWith(
      expect.stringContaining('/review-cards/runs/THREAD_RUNNING_1'),
    );
  });

  it('keeps a pending checkpoint when the first refresh lookup races with run registration', async () => {
    localStorage.setItem('assistantPendingWorkflowRuns', JSON.stringify({
      'session-a': 'THREAD_STARTING_1',
    }));
    readJsonResponse.mockImplementation(async response => response._payload ?? {});
    let runLookups = 0;
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-a', title: '会话 A' }]));
      }
      if (url.endsWith('/conversations/session-a/messages')) {
        return Promise.resolve(jsonResponse([]));
      }
      if (url.endsWith('/review-cards/runs/THREAD_STARTING_1')) {
        runLookups += 1;
        if (runLookups === 1) {
          return Promise.resolve({
            ok: false,
            status: 404,
            _payload: { detail: 'Not Found' },
          });
        }
        return Promise.resolve({
          ...jsonResponse({}),
          _payload: { status: 'interrupted', thread_id: 'THREAD_STARTING_1' },
        });
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<ChatInterface currentUser="alice" preferredSessionId="session-a" embedded />);

    await waitFor(() => expect(runLookups).toBe(2));
    expect(JSON.parse(localStorage.getItem('assistantPendingWorkflowRuns'))).toEqual({
      'session-a': 'THREAD_STARTING_1',
    });
  });

  it('reasserts an interrupted checkpoint after a concurrent assistant clears local storage', async () => {
    const terminal = {
      event: 'run_interrupted',
      result: { status: 'interrupted' },
      assistant_message: '请选择长期规划、短期计划或当日任务。',
    };
    fetchWithAuth.mockImplementation((url, options = {}) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-a', title: '会话 A' }]));
      }
      if (url.endsWith('/conversations/session-a/messages')) {
        return Promise.resolve(jsonResponse([]));
      }
      if (url.endsWith('/review-cards/stream') && options.method === 'POST') {
        localStorage.removeItem('assistantPendingWorkflowRuns');
        return Promise.resolve(new Response(`data: ${JSON.stringify(terminal)}\n\n`, {
          status: 200,
          headers: { 'Content-Type': 'text/event-stream' },
        }));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<ChatInterface currentUser="alice" preferredSessionId="session-a" embedded />);
    const composer = await screen.findByRole('textbox', { name: '向智能助教提问' });
    fireEvent.change(composer, { target: { value: '制定学习计划' } });
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }));

    expect(await screen.findByText(terminal.assistant_message)).toBeInTheDocument();
    const pending = JSON.parse(localStorage.getItem('assistantPendingWorkflowRuns'));
    expect(pending['session-a']).toMatch(/^THREAD_/);
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
              evidence_items: [
                {
                  source_id: 'WEB_A',
                  content_summary: 'A 的网页来源\n网页摘要内容',
                  confidence: 0.9,
                  source_url: 'https://example.com/a',
                  resource_type: 'reference',
                },
                {
                  source_id: '中西医结合妇产科学_clean:00473',
                  content_summary: '教材切片内容摘要',
                  confidence: 0.87,
                  source_url: null,
                  resource_type: 'textbook',
                },
              ],
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
    // 教材证据（无 source_url）也应展示为知识库来源
    expect(screen.getAllByText('中西医结合妇产科学').length).toBeGreaterThan(0);
    expect(screen.getAllByText('教材').length).toBeGreaterThan(0);

    expect(screen.getByLabelText('多智能体执行过程')).toBeInTheDocument();
    expect(screen.queryByRole('complementary', { name: '执行进度' })).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByText('会话 B')[0]);
    expect(await screen.findByText('B 的回答')).toBeInTheDocument();
    expect(screen.queryByLabelText('多智能体执行过程')).not.toBeInTheDocument();
    expect(retrievalSidebar).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByText('A 的网页来源')).not.toBeInTheDocument();
  });

  it('deduplicates repeated evidence across retrieval rounds and sidebar counts', async () => {
    const repeatedEvidence = {
      evidence_id: 'E_SHARED',
      source_id: 'SOURCE_SHARED',
      source_label: '《中医文化学》· 第一章',
      content_summary: '整体观与和谐观是中医文化的重要精神。',
      confidence: 0.91,
      source_url: null,
      resource_type: 'textbook',
    };
    fetchWithAuth.mockImplementation((url) => {
      if (url.endsWith('/conversations')) {
        return Promise.resolve(jsonResponse([{ id: 'session-dedupe', title: '来源去重' }]));
      }
      if (url.endsWith('/conversations/session-dedupe/messages')) {
        return Promise.resolve(jsonResponse([{
          id: 41,
          role: 'assistant',
          content: '教材讲解正文。',
          trace_events: [
            {
              event: 'knowledge_retrieval',
              agent: 'knowledge_base_agent',
              retrieval_round: 1,
              evidence_items: [repeatedEvidence],
            },
            {
              event: 'knowledge_retrieval',
              agent: 'knowledge_base_agent',
              retrieval_round: 2,
              evidence_items: [
                repeatedEvidence,
                {
                  evidence_id: 'E_UNIQUE',
                  source_id: 'SOURCE_UNIQUE',
                  source_label: '《中医基础理论》· 绪论',
                  content_summary: '中医学理论体系重视整体联系。',
                  confidence: 0.88,
                  source_url: null,
                  resource_type: 'textbook',
                },
              ],
            },
          ],
        }]));
      }
      throw new Error(`unexpected request: ${url}`);
    });

    render(<ChatInterface currentUser="alice" preferredSessionId="session-dedupe" embedded />);

    const sourceTrigger = await screen.findByTitle('点击查看参考来源');
    expect(sourceTrigger).toHaveTextContent('参考来源 2 条');
    fireEvent.click(sourceTrigger);

    const sidebar = screen.getByText('检索详情').closest('.fixed');
    expect(within(sidebar).getByText('2 条')).toBeInTheDocument();
    expect(within(sidebar).getByText('来源列表 (2)')).toBeInTheDocument();
    expect(within(sidebar).getAllByText('《中医文化学》· 第一章')).toHaveLength(2);
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
