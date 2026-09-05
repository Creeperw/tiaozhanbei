import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  compactAssistantContent,
  getAssistantPendingRun,
  resolveAssistantSessionId,
  streamAssistantMessage,
  streamAssistantMessageOutcome,
} from './chatSessionClient';

describe('chatSessionClient', () => {
  beforeEach(() => localStorage.clear());
  afterEach(() => vi.unstubAllGlobals());

  it('removes workflow protocol markers and hidden thinking from compact answers', () => {
    const raw = '<think>内部推理</think><<STATUS:searching:检索中>><<EV:{"type":"node_started","text":"教材 <<REFS:[{\\"title\\":\\"证据\\"}]>> 与普通 >> 文本"}>>四君子汤主治脾胃气虚证。';
    expect(compactAssistantContent(raw)).toBe('四君子汤主治脾胃气虚证。');
  });

  it('keeps only regenerated content after a rollback marker', () => {
    const raw = '旧回答<<ROLLBACK:审核未通过>><think>重写</think>新回答';
    expect(compactAssistantContent(raw)).toBe('新回答');
  });

  it('keeps raw output, reasoning, replacements and terminal prose out of progress', async () => {
    const events = [
      { event: 'run_started', text: '不可信的状态正文' },
      { event: 'agent_output_delta', delta: '"clarific' },
      { event: 'agent_reasoning_delta', delta: '内部推理片段' },
      { event: 'agent_output_replaced', public_output: '{"clarification":true}' },
      { event: 'model_input', agent: 'unrecognized_agent', raw_input: '内部输入' },
      { event: 'model_input', raw_input: '重复输入' },
      { event: 'unknown_future_event', text: '未经批准的字段' },
      { event: 'step_failed', message: '{"internal_error":"raw"}' },
      { event: 'run_interrupted', result: { status: 'interrupted' }, assistant_message: '每天可以学习多少分钟？' },
    ];
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )));
    const onProgress = vi.fn();
    const onUpdate = vi.fn();
    const outcome = await streamAssistantMessageOutcome('session-progress', '制定计划', { onProgress, onUpdate });

    expect(onProgress.mock.calls.map(([text]) => text)).toEqual([
      '正在理解你的需求…', '正在结合任务信息准备处理…',
      '当前环节未完成，正在整理处理结果…', '需要你补充信息，正在整理问题…',
    ]);
    expect(outcome.visible).toBe('每天可以学习多少分钟？');
    expect(onUpdate).toHaveBeenLastCalledWith('每天可以学习多少分钟？');
    expect(outcome.status).toBe('interrupted');
  });

  it('prefers an explicit session and otherwise restores the saved real session', () => {
    const sessions = [{ id: 'saved' }, { id: 'latest' }];
    expect(resolveAssistantSessionId(sessions, 'latest', 'saved')).toBe('latest');
    expect(resolveAssistantSessionId(sessions, null, 'saved')).toBe('saved');
    expect(resolveAssistantSessionId(sessions, null, 'missing')).toBe('saved');
  });

  it('does not start a second turn while the same conversation is still running', async () => {
    localStorage.setItem(
      'assistantPendingWorkflowRuns',
      JSON.stringify({ 'session-a': 'THREAD_RUNNING' }),
    );
    const request = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      thread_id: 'THREAD_RUNNING',
      status: 'running',
    }), { status: 200, headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', request);

    await expect(streamAssistantMessage('session-a', '第二条消息')).rejects.toMatchObject({
      code: 'workflow_running',
    });
    expect(request).toHaveBeenCalledTimes(1);
    expect(request.mock.calls[0][0]).toContain('/review-cards/runs/THREAD_RUNNING');
  });

  it('tolerates a startup 404 before the interrupted checkpoint becomes queryable', async () => {
    localStorage.setItem(
      'assistantPendingWorkflowRuns',
      JSON.stringify({ 'session-a': 'THREAD_STARTING' }),
    );
    const request = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Not Found' }), {
        status: 404,
        headers: { 'Content-Type': 'application/json' },
      }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        thread_id: 'THREAD_STARTING',
        status: 'interrupted',
      }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
    vi.stubGlobal('fetch', request);

    await expect(getAssistantPendingRun('session-a')).resolves.toEqual(expect.objectContaining({
      runId: 'THREAD_STARTING',
      status: 'interrupted',
    }));
    expect(request).toHaveBeenCalledTimes(2);
    expect(JSON.parse(localStorage.getItem('assistantPendingWorkflowRuns'))).toEqual({
      'session-a': 'THREAD_STARTING',
    });
  });

  it('restores the interrupted run id if a concurrent status poll cleared it', async () => {
    const terminal = {
      event: 'run_interrupted',
      result: { status: 'interrupted' },
      assistant_message: '请选择长期规划、短期计划或当日任务。',
    };
    const request = vi.fn().mockImplementation(async () => {
      localStorage.removeItem('assistantPendingWorkflowRuns');
      return new Response(`data: ${JSON.stringify(terminal)}\n\n`, {
        status: 200,
        headers: { 'Content-Type': 'text/event-stream' },
      });
    });
    vi.stubGlobal('fetch', request);

    await expect(streamAssistantMessage('session-a', '制定学习计划')).resolves.toBe(
      terminal.assistant_message,
    );
    const pending = JSON.parse(localStorage.getItem('assistantPendingWorkflowRuns'));
    expect(pending['session-a']).toMatch(/^THREAD_/);
  });
});
