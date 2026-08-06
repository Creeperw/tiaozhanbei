import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  compactWorkflowHistoryContent,
  getResumableWorkflowRunId,
  runtimeEventToTrace,
  streamWorkflowTurn,
} from './workflowChatClient';

describe('workflow chat event adapter', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('keeps model traces out of formal assistant history', () => {
    const raw = [
      '<<EV:{"type":"model_call","input":{"large":"trace"}}>>',
      '<think>内部推理</think>',
      '这是最终可见回复。',
      '<<REFS:[{"title":"教材"}]>>',
    ].join('');

    expect(compactWorkflowHistoryContent('assistant', raw)).toBe('这是最终可见回复。');
    expect(compactWorkflowHistoryContent('user', '用户原始消息')).toBe('用户原始消息');
  });

  it('strips EV markers even when embedded JSON contains ">>" sequences', () => {
    const raw = [
      '<<EV:{"type":"model_call","kind":"transport","responseText":"内容 >> 嵌套 >> 结束"}}>>',
      '最终回答正文。',
      '<<EV:{"type":"planning_start","agent":"planner_agent"}}>>',
    ].join('');

    expect(compactWorkflowHistoryContent('assistant', raw)).toBe('最终回答正文。');
  });

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
    expect(runtimeEventToTrace({ event: 'run_waiting_human_review' })).toEqual({
      type: 'human_review_waiting', text: '内容正在等待人工复核',
    });
    expect(runtimeEventToTrace({
      event: 'graph_compiled',
      nodes: [{ step_id: 'audit', agent: 'audit_agent' }],
    })).toEqual({
      type: 'planning_done',
      text: '执行路径已确定',
      plannedNodes: [{ step_id: 'audit', agent: 'audit_agent' }],
    });
  });

  it('exposes model input/output/transport as timeline model calls', () => {
    expect(runtimeEventToTrace({
      event: 'model_input',
      agent: 'expert_agent',
      step_id: 'expert',
      call_id: 'MODEL_CALL_1',
      raw_input: { task_type: 'knowledge_explanation', topic: '气血' },
    })).toEqual({
      type: 'model_call',
      kind: 'input',
      text: 'expert_agent 模型输入',
      agent: 'expert_agent',
      stepId: 'expert',
      callId: 'MODEL_CALL_1',
      input: { task_type: 'knowledge_explanation', topic: '气血' },
    });
    expect(runtimeEventToTrace({
      event: 'model_output',
      agent: 'expert_agent',
      step_id: 'expert',
      call_id: 'MODEL_CALL_1',
      raw_output: { content: '气血是人体基本物质' },
    })).toEqual({
      type: 'model_call',
      kind: 'output',
      text: 'expert_agent 模型输出',
      agent: 'expert_agent',
      stepId: 'expert',
      callId: 'MODEL_CALL_1',
      output: { content: '气血是人体基本物质' },
    });
    expect(runtimeEventToTrace({
      event: 'model_transport',
      agent: 'diagnosis_agent',
      step_id: 'diagnosis',
      call_id: 'MODEL_CALL_2',
      request_payload: { topic: '四君子汤' },
      response_text: '掌握程度良好',
    })).toEqual({
      type: 'model_call',
      kind: 'transport',
      text: 'diagnosis_agent 模型传输',
      agent: 'diagnosis_agent',
      stepId: 'diagnosis',
      callId: 'MODEL_CALL_2',
      requestPayload: { topic: '四君子汤' },
      responseText: '掌握程度良好',
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
      messages: [
        { id: 'USER_0', role: 'user', content: '上一轮问题' },
        {
          id: 'ASSISTANT_0',
          role: 'assistant',
          content: '<<EV:{"type":"model_call","input":{"large":"trace"}}>>上一轮最终回答',
        },
      ],
      currentPage: {
        tool_name: 'read_current_page',
        page_type: 'knowledge',
        visible_text: '阴阳学说的基本内容',
      },
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
      current_page: expect.objectContaining({
        page_type: 'knowledge',
        visible_text: '阴阳学说的基本内容',
      }),
      messages: [
        { message_id: 'USER_0', role: 'user', content: '上一轮问题' },
        { message_id: 'ASSISTANT_0', role: 'assistant', content: '上一轮最终回答' },
      ],
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

  it('keeps human-review terminal results out of the failure path', async () => {
    const terminal = {
      event: 'run_waiting_human_review',
      result: {
        status: 'waiting_human_review',
        review: { findings: ['实时信息来源需要人工核验。'] },
      },
      assistant_message: '当前信息已提交人工复核：实时信息来源需要人工核验。',
    };
    const request = vi.fn().mockResolvedValue(new Response(
      `data: ${JSON.stringify(terminal)}\n\n`,
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ));
    vi.stubGlobal('fetch', request);

    await expect(streamWorkflowTurn({
      conversationId: 'CONV_1',
      runId: 'THREAD_REVIEW',
      answer: '今天天气怎样？',
    })).resolves.toEqual({
      status: 'waiting_human_review',
      result: terminal.result,
      message: terminal.assistant_message,
    });
  });

  it('discards a stale pending run when the backend checkpoint no longer exists', async () => {
    const request = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: 'Not Found' }),
      { status: 404, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', request);

    await expect(getResumableWorkflowRunId('THREAD_STALE')).resolves.toBeNull();
    expect(request.mock.calls[0][0]).toBe(
      '/api/v1/review-cards/runs/THREAD_STALE',
    );
  });

  it('only resumes a run that is actually waiting for user input', async () => {
    const request = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ thread_id: 'THREAD_WAITING', status: 'interrupted' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', request);

    await expect(getResumableWorkflowRunId('THREAD_WAITING')).resolves.toBe(
      'THREAD_WAITING',
    );
  });
});
