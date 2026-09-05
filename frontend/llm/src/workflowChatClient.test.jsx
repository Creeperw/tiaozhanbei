import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  cancelWorkflowRun,
  compactWorkflowHistoryContent,
  getResumableWorkflowRunId,
  runtimeEventToTrace,
  streamWorkflowTurn,
  STREAM_UI_BATCH_MS,
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
    const transport = {
      type: 'model_call',
      kind: 'transport',
      responseText: '内容 >> 嵌套 >> 结束',
    };
    const raw = [
      `<<EV:${JSON.stringify(transport)}>>`,
      '最终回答正文。',
      `<<EV:${JSON.stringify({ type: 'planning_start', agent: 'planner_agent' })}>>`,
    ].join('');

    expect(compactWorkflowHistoryContent('assistant', raw)).toBe('最终回答正文。');
  });

  it('maps authoritative backend steps to the existing execution timeline', () => {
    expect(runtimeEventToTrace({ event: 'step_started', step_id: 'planner', agent: 'planner_agent', ts: 1786745600123 })).toEqual({
      type: 'planning_start',
      text: 'planner_agent开始处理',
      agent: 'planner_agent',
      stepId: 'planner',
      ts: 1786745600123,
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
    expect(runtimeEventToTrace({ event: 'run_interrupted', ts: 30 })).toEqual({
      type: 'workflow_interrupted', text: '等待用户补充信息', ts: 30,
    });
    expect(runtimeEventToTrace({
      event: 'step_failed', step_id: 'knowledge', agent: 'knowledge_base_agent', message: '检索失败', ts: 40,
    })).toEqual({
      type: 'step_error',
      text: '检索失败',
      agent: 'knowledge_base_agent',
      stepId: 'knowledge',
      ts: 40,
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
      text: '专家智能体正在读取任务信息',
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
      text: '专家智能体已生成本轮结果',
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
      text: '学情诊断智能体正在调用模型',
      agent: 'diagnosis_agent',
      stepId: 'diagnosis',
      callId: 'MODEL_CALL_2',
      requestPayload: { topic: '四君子汤' },
      responseText: '掌握程度良好',
    });
  });

  it('maps provider reasoning events into the visible thought stream', () => {
    expect(runtimeEventToTrace({
      event: 'agent_reasoning_started', agent: 'planner_agent', step_id: 'planner',
    })).toEqual({
      type: 'reasoning_stream', kind: 'started', text: '',
      agent: 'planner_agent', stepId: 'planner',
    });
    expect(runtimeEventToTrace({
      event: 'agent_reasoning_delta', agent: 'planner_agent', step_id: 'planner',
      delta: '正在判定当前消息语义',
    })).toEqual({
      type: 'reasoning_stream', kind: 'delta', text: '正在判定当前消息语义',
      agent: 'planner_agent', stepId: 'planner',
    });
    expect(runtimeEventToTrace({
      event: 'agent_reasoning_committed', agent: 'planner_agent', step_id: 'planner',
    })).toEqual({
      type: 'reasoning_stream', kind: 'committed', text: '',
      agent: 'planner_agent', stepId: 'planner',
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
    expect(JSON.parse(options.body)).not.toHaveProperty('available_minutes');
  });

  it('sends a budget only when a product surface supplies one explicitly', async () => {
    const request = vi.fn().mockResolvedValue(new Response(
      'data: {"event":"run_completed","result":{"status":"success"}}\n\n',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ));
    vi.stubGlobal('fetch', request);

    await streamWorkflowTurn({
      conversationId: 'CONV_BUDGET',
      runId: 'THREAD_BUDGET',
      answer: '生成练习',
      availableMinutes: 45,
    });

    expect(JSON.parse(request.mock.calls[0][1].body).available_minutes).toBe(45);
  });

  it('treats run_cancelled as a successful terminal cancellation', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      'data: {"event":"run_cancelled","status":"cancelled","assistant_message":"已停止生成。"}\n\n',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )));

    await expect(streamWorkflowTurn({
      conversationId: 'CONV_CANCEL',
      runId: 'THREAD_CANCEL',
      answer: '制定规划',
    })).resolves.toEqual({
      status: 'cancelled',
      result: undefined,
      message: '已停止生成。',
    });
  });

  it('requests authoritative server-side cancellation', async () => {
    const request = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ status: 'cancellation_requested', thread_id: 'THREAD_CANCEL' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ));
    vi.stubGlobal('fetch', request);

    await expect(cancelWorkflowRun('THREAD_CANCEL')).resolves.toEqual({
      status: 'cancellation_requested',
      thread_id: 'THREAD_CANCEL',
    });
    expect(request.mock.calls[0][0]).toContain('/review-cards/runs/THREAD_CANCEL/cancel');
    expect(request.mock.calls[0][1]).toEqual(expect.objectContaining({ method: 'POST' }));
  });

  it('forwards publication-gated answer chunks without turning them into trace events', async () => {
    const events = [
      { event: 'answer_started', publication_status: 'approved' },
      { event: 'answer_delta', delta: '四君子汤' },
      { event: 'answer_delta', delta: '具有益气健脾之效。' },
      { event: 'answer_committed', publication_status: 'approved' },
      { event: 'run_completed', result: { status: 'success' }, assistant_message: '四君子汤具有益气健脾之效。' },
    ];
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )));
    const received = [];

    const outcome = await streamWorkflowTurn({
      conversationId: 'CONV_STREAM',
      runId: 'THREAD_STREAM',
      answer: '讲解四君子汤',
      onEvent: (event, trace) => received.push([event.event, trace]),
    });

    expect(received.slice(0, 4).map(([name]) => name)).toEqual([
      'answer_started', 'answer_delta', 'answer_delta', 'answer_committed',
    ]);
    expect(received.slice(0, 4).every(([, trace]) => trace == null)).toBe(true);
    expect(outcome.message).toBe('四君子汤具有益气健脾之效。');
  });

  it('batches burst events for the UI while preserving order and flushing terminal events', async () => {
    vi.useFakeTimers();
    const events = [
      { event: 'run_started' },
      { event: 'answer_started', publication_status: 'approved' },
      { event: 'answer_delta', delta: '甲' },
      { event: 'answer_delta', delta: '乙' },
      { event: 'run_completed', result: { status: 'success' }, assistant_message: '甲乙' },
    ];
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    )));
    const batches = [];

    const outcomePromise = streamWorkflowTurn({
      conversationId: 'CONV_BATCH',
      runId: 'THREAD_BATCH',
      answer: '批量',
      onEvents: batch => batches.push(batch.map(([event]) => event.event)),
    });
    await vi.runAllTimersAsync();
    const outcome = await outcomePromise;

    expect(outcome.status).toBe('completed');
    expect(batches).toEqual([[
      'run_started', 'answer_started', 'answer_delta', 'answer_delta', 'run_completed',
    ]]);
    expect(STREAM_UI_BATCH_MS).toBeGreaterThan(0);
    vi.useRealTimers();
  });

  it('maps learner-safe per-agent output chunks into the live trace', () => {
    expect(runtimeEventToTrace({
      event: 'agent_output_started', output_phase: 'working', agent: 'planner_agent', step_id: 'planner', ts: 10,
    })).toEqual(expect.objectContaining({
      type: 'agent_output_stream', kind: 'started', phase: 'working', agent: 'planner_agent', stepId: 'planner',
    }));
    expect(runtimeEventToTrace({
      event: 'agent_output_delta', output_phase: 'working', agent: 'planner_agent', step_id: 'planner', delta: '已识别任务', ts: 11,
    })).toEqual(expect.objectContaining({
      type: 'agent_output_stream', kind: 'delta', phase: 'working', text: '已识别任务',
    }));
    expect(runtimeEventToTrace({
      event: 'agent_output_committed', output_phase: 'working', agent: 'planner_agent', step_id: 'planner', ts: 12,
    })).toEqual(expect.objectContaining({
      type: 'agent_output_stream', kind: 'committed', phase: 'working',
    }));
    expect(runtimeEventToTrace({
      event: 'agent_output_replaced', agent: 'expert_agent', step_id: 'expert',
      output_phase: 'formal', public_output: '经结构校验后的完整候选内容', ts: 13,
    })).toEqual(expect.objectContaining({
      type: 'agent_output_stream', kind: 'replaced', phase: 'formal',
      text: '经结构校验后的完整候选内容',
    }));
  });

  it('treats a persisted completed repair as a successful re-audit', () => {
    expect(runtimeEventToTrace({
      event: 'audit_revision_completed', status: 'completed', ts: 20,
    })).toEqual(expect.objectContaining({
      type: 'repair_event', kind: 'completed', status: 'completed',
    }));
    expect(runtimeEventToTrace({
      event: 'audit_revision_completed', status: 'needs_human_review', ts: 21,
    })).toEqual(expect.objectContaining({
      type: 'repair_event', kind: 'completed', status: 'needs_human_review',
    }));
  });

  it('maps external search results to completed inline tool events', () => {
    expect(runtimeEventToTrace({
      event: 'web_search_status', provider: 'exa', resource_type: 'reference',
      status: 'success', result_count: 3, ts: 20,
    })).toEqual(expect.objectContaining({
      type: 'tool_event',
      name: 'web_search',
      agent: 'knowledge_base_agent',
      status: 'done',
      text: 'EXA · 参考资料 · 3 条结果',
      args: expect.objectContaining({ resourceType: 'reference', resultCount: 3 }),
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

  it('fails with a friendly message when the stream ends without a terminal event', async () => {
    const request = vi.fn().mockResolvedValue(new Response(
      [
        'data: {"event":"run_started"}\n\n',
        'data: {"event":"step_completed","agent":"planner_agent"}\n\n',
      ].join(''),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ));
    vi.stubGlobal('fetch', request);

    await expect(streamWorkflowTurn({
      conversationId: 'CONV_1',
      runId: 'THREAD_CUT',
      answer: '讲解知识点',
    })).rejects.toThrow('处理被中断，未收到完整结果');
  });

  it('stops the stream promptly on user-initiated abort', async () => {
    const request = vi.fn().mockResolvedValue(new Response(
      new ReadableStream({ start() {} }),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ));
    vi.stubGlobal('fetch', request);

    const controller = new AbortController();
    const outcome = streamWorkflowTurn({
      conversationId: 'CONV_1',
      runId: 'THREAD_ABORT',
      answer: 'x',
      signal: controller.signal,
    });
    const assertion = expect(outcome).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort();
    await assertion;
  });
});
