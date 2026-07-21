import { afterEach, describe, expect, it, vi } from 'vitest';

import { runtimeEventToTrace, streamWorkflowTurn } from './workflowChatClient';

describe('workflow chat event adapter', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('maps authoritative backend steps to the existing execution timeline', () => {
    expect(runtimeEventToTrace({ event: 'step_started', step_id: 'planner', agent: 'planner_agent' })).toEqual({
      type: 'planning_start',
      text: 'planner_agent开始处理',
      agent: 'planner_agent',
      stepId: 'planner',
    });
    expect(runtimeEventToTrace({ event: 'step_completed', step_id: 'audit', agent: 'audit_agent' })).toEqual({
      type: 'feedback_done',
      text: 'audit_agent处理完成',
      agent: 'audit_agent',
      stepId: 'audit',
    });
    expect(runtimeEventToTrace({ event: 'run_completed' })).toEqual({
      type: 'workflow_done', text: '处理完成',
    });
  });

  it('consumes the main LangGraph SSE contract and keeps conversation/run ids separate', async () => {
    const events = [
      { event: 'run_started', thread_id: 'THREAD_1' },
      {
        event: 'run_completed',
        result: { status: 'success' },
        assistant_message: '长期规划已经整理好。',
      },
    ];
    const request = vi.fn().mockResolvedValue(new Response(
      events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ));
    vi.stubGlobal('fetch', request);
    const received = [];

    const outcome = await streamWorkflowTurn({
      conversationId: 'CONV_1',
      runId: 'THREAD_1',
      answer: '制定长期规划',
      messages: [],
      onEvent: event => received.push(event.event),
    });

    expect(outcome).toEqual({
      status: 'completed',
      result: { status: 'success' },
      message: '长期规划已经整理好。',
    });
    expect(received).toEqual(['run_started', 'run_completed']);
    const [url, options] = request.mock.calls[0];
    expect(url).toBe('/api/v1/review-cards/stream');
    expect(options.credentials).toBe('include');
    expect(JSON.parse(options.body)).toEqual(expect.objectContaining({
      conversation_id: 'CONV_1',
      thread_id: 'THREAD_1',
    }));
  });

  it('uses the resume endpoint for an interrupted run', async () => {
    const terminal = {
      event: 'run_interrupted',
      result: { status: 'interrupted' },
      assistant_message: '请补充每日可用时间。',
    };
    const request = vi.fn().mockResolvedValue(new Response(
      `data: ${JSON.stringify(terminal)}\n\n`,
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ));
    vi.stubGlobal('fetch', request);

    const outcome = await streamWorkflowTurn({
      conversationId: 'CONV_1',
      runId: 'THREAD_PENDING',
      answer: '每天 2 小时',
      resume: true,
    });

    expect(request.mock.calls[0][0]).toBe(
      '/api/v1/review-cards/runs/THREAD_PENDING/resume/stream',
    );
    expect(JSON.parse(request.mock.calls[0][1].body)).toEqual({ answer: '每天 2 小时' });
    expect(outcome.status).toBe('interrupted');
  });
});
