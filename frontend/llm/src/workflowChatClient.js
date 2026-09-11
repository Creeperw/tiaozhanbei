import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';
import { removeTraceEventsFromContent } from './chatProtocol';

const RUN_STARTUP_LOOKUP_ATTEMPTS = 3;
const RUN_STARTUP_LOOKUP_DELAY_MS = 120;
// React/Markdown rendering is considerably more expensive than parsing an
// SSE frame.  Keep the wire stream lossless, but deliver bursts to the UI at
// a bounded cadence so a provider that emits many tiny deltas does not cause
// one React render per token.
export const STREAM_UI_BATCH_MS = 50;

export const createWorkflowRunId = () => {
  const suffix = globalThis.crypto?.randomUUID?.().replaceAll('-', '')
    || `${Date.now()}${Math.random().toString(16).slice(2)}`;
  return `THREAD_${suffix}`;
};

const agentPhase = (agent = '') => {
  if (agent === 'planner_agent' || agent === 'route_agent' || agent === 'default_route_resolver') return 'planning';
  if (agent === 'audit_agent') return 'feedback';
  if (agent === 'memory_agent' || agent === 'diagnosis_agent') return 'context';
  return 'execution';
};

const AGENT_STATUS_NAMES = Object.freeze({
  planner_agent: '任务规划智能体',
  route_agent: '任务规划智能体',
  memory_agent: '记忆管理智能体',
  diagnosis_agent: '学情诊断智能体',
  default_route_resolver: '学习路径解析器',
  learning_plan_service: '学习规划编译器',
  knowledge_base_agent: '知识库管理智能体',
  expert_agent: '专家智能体',
  paper_blueprint_agent: '试卷蓝图智能体',
  paper_assembly_agent: '试卷编排智能体',
  audit_agent: '审核智能体',
});

const agentStatusName = (agent = '') => AGENT_STATUS_NAMES[agent] || '模型';

function runtimeEventToTracePayload(event) {
  const name = event?.event || '';
  if (name === 'run_started') return { type: 'planning_start', text: '正在理解你的需求' };
  if (name === 'run_resumed' || name === 'graph_resumed') return { type: 'refine_start', text: '已从检查点恢复' };
  if (name === 'graph_compiled') return {
    type: 'planning_done',
    text: '执行路径已确定',
    plannedNodes: Array.isArray(event.nodes) ? event.nodes : [],
  };
  if (name === 'audit_revision_started') return {
    type: 'repair_event',
    kind: 'reaudit_started',
    text: '审核发现可局部修正的问题，正在安排返修',
    auditStepId: event.audit_step_id || event.trigger_step_id || 'audit',
    status: event.status || 'running',
  };
  if (name === 'audit_revision_completed') return {
    type: 'repair_event',
    kind: 'completed',
    text: event.status === 'needs_human_review' ? '修订复核完成，等待人工复核' : '修订复核已完成',
    auditStepId: event.audit_step_id || event.trigger_step_id || 'audit',
    status: event.status || 'completed',
  };
  if (name === 'run_completed') return { type: 'workflow_done', text: '处理完成' };
  if (name === 'run_cancelled') return { type: 'workflow_cancelled', text: '已停止生成' };
  if (name === 'run_waiting_human_review') {
    return { type: 'human_review_waiting', text: '内容正在等待人工复核' };
  }
  if (name === 'run_interrupted' || name === 'graph_interrupted') {
    return { type: 'workflow_interrupted', text: '等待用户补充信息' };
  }
  if (name === 'step_failed') {
    return {
      type: 'step_error',
      text: event.message || event.error_message || '执行失败',
      agent: event.agent || '',
      stepId: event.step_id || '',
    };
  }
  if (name === 'run_failed') {
    return {
      type: 'workflow_failed',
      text: event.message || event.error_message || '执行失败',
      agent: event.agent || '',
      stepId: event.step_id || event.failed_step || '',
    };
  }
  if (name === 'step_started' || name === 'step_completed') {
    const phase = agentPhase(event.agent);
    return {
      type: `${phase}_${name === 'step_started' ? 'start' : 'done'}`,
      text: event.agent ? `${event.agent}${name === 'step_started' ? '开始处理' : '处理完成'}` : '',
      agent: event.agent || '',
      stepId: event.step_id || '',
      ...(event.input_summary ? { inputSummary: event.input_summary } : {}),
      ...(Array.isArray(event.depends_on) && event.depends_on.length > 0
        ? { dependsOn: event.depends_on }
        : {}),
    };
  }
  if (name === 'system_output') {
    return {
      type: 'agent_progress',
      kind: 'artifact_ready',
      text: `${agentStatusName(event.agent)}已整理阶段产出`,
      agent: event.agent || '',
      stepId: event.step_id || '',
    };
  }
  if (name === 'agent_output_started') {
    return {
      type: 'agent_output_stream', kind: 'started', text: '',
      agent: event.agent || '', stepId: event.step_id || '',
      append: event.append === true,
      phase: event.output_phase || 'formal',
    };
  }
  if (name === 'agent_output_delta') {
    return {
      type: 'agent_output_stream', kind: 'delta', text: event.delta || '',
      agent: event.agent || '', stepId: event.step_id || '',
      phase: event.output_phase || 'formal',
    };
  }
  if (name === 'agent_output_committed') {
    return {
      type: 'agent_output_stream', kind: 'committed', text: '',
      agent: event.agent || '', stepId: event.step_id || '',
      phase: event.output_phase || 'formal',
    };
  }
  if (name === 'agent_output_replaced') {
    return {
      type: 'agent_output_stream', kind: 'replaced',
      text: event.public_output || '',
      agent: event.agent || '', stepId: event.step_id || '',
      phase: event.output_phase || 'formal',
    };
  }
  if (name === 'agent_reasoning_started') {
    return {
      type: 'reasoning_stream', kind: 'started', text: '',
      agent: event.agent || '', stepId: event.step_id || '',
    };
  }
  if (name === 'agent_reasoning_delta') {
    return {
      type: 'reasoning_stream', kind: 'delta', text: event.delta || '',
      agent: event.agent || '', stepId: event.step_id || '',
    };
  }
  if (name === 'agent_reasoning_committed') {
    return {
      type: 'reasoning_stream', kind: 'committed', text: '',
      agent: event.agent || '', stepId: event.step_id || '',
    };
  }
  if (name === 'handoff_prepared' || name === 'handoff_consumed') {
    return {
      type: 'agent_progress',
      kind: name,
      text: name === 'handoff_prepared'
        ? `已整理交接信息：${Number(event.fact_count || 0)} 项事实、${Number(event.evidence_count || 0)} 条证据`
        : '已接收上游智能体的有效信息',
      agent: event.agent || '',
      stepId: event.step_id || '',
    };
  }
  if (name === 'step_retrying') {
    return {
      type: 'agent_progress',
      kind: 'retrying',
      text: `本步骤响应异常，正在进行第 ${Number(event.attempt || 1) + 1} 次尝试`,
      agent: event.agent || '',
      stepId: event.step_id || '',
    };
  }
  if (name === 'repair_planned') {
    return {
      type: 'repair_event',
      kind: 'planned',
      text: '审核已定位问题，准备仅返修受影响的环节',
      auditStepId: event.trigger_step_id || '',
      targetStepIds: event.rerun_step_ids || [],
      preservedStepIds: event.preserved_step_ids || [],
      locations: event.location_labels || [],
      status: event.status || 'planned',
    };
  }
  if (name === 'repair_step_started' || name === 'repair_step_completed') {
    return {
      type: 'repair_event',
      kind: name === 'repair_step_started' ? 'step_started' : 'step_completed',
      text: name === 'repair_step_started' ? '正在局部返修目标环节' : '目标环节返修完成',
      stepId: event.step_id || '',
      targetStepIds: event.step_id ? [event.step_id] : [],
      status: event.status || (name === 'repair_step_completed' ? 'success' : 'running'),
    };
  }
  if (name === 'repair_reaudit_started') {
    return {
      type: 'repair_event',
      kind: 'reaudit_started',
      text: '局部返修完成，正在强制复审',
      auditStepId: event.trigger_step_id || '',
      status: event.status || 'running',
    };
  }
  if (name === 'repair_completed' || name === 'repair_stopped') {
    return {
      type: 'repair_event',
      kind: name === 'repair_completed' ? 'completed' : 'stopped',
      text: name === 'repair_completed'
        ? '复审通过，返修内容可以发布'
        : '返修后仍未通过，内容未自动发布',
      auditStepId: event.trigger_step_id || '',
      status: event.status || (name === 'repair_completed' ? 'pass' : 'failed'),
    };
  }
  if (name === 'web_search_status') {
    const resultCount = Number(event.result_count || 0);
    const resourceLabels = {
      video: '视频',
      question: '题目',
      reference: '参考资料',
      web: '网页',
    };
    const resourceLabel = resourceLabels[event.resource_type] || '资料';
    const provider = String(event.provider || '外部检索').toUpperCase();
    return {
      type: 'tool_event',
      name: 'web_search',
      agent: event.agent || 'knowledge_base_agent',
      stepId: event.step_id || '',
      status: ['success', 'completed'].includes(event.status)
        ? 'done'
        : event.status === 'failed' ? 'error' : 'running',
      args: {
        provider: event.provider || '',
        resourceType: event.resource_type || '',
        resultCount,
      },
      text: event.message || `${provider} · ${resourceLabel} · ${event.status === 'failed' ? '检索失败' : `${resultCount} 条结果`}`,
    };
  }
  if (name === 'model_input') {
    return {
      type: 'model_call',
      kind: 'input',
      text: `${agentStatusName(event.agent)}正在读取任务信息`,
      agent: event.agent || '',
      stepId: event.step_id || '',
      callId: event.call_id || '',
      input: event.raw_input,
    };
  }
  if (name === 'model_output') {
    return {
      type: 'model_call',
      kind: 'output',
      text: `${agentStatusName(event.agent)}已生成本轮结果`,
      agent: event.agent || '',
      stepId: event.step_id || '',
      callId: event.call_id || '',
      output: event.raw_output,
    };
  }
  if (name === 'model_transport') {
    return {
      type: 'model_call',
      kind: 'transport',
      text: `${agentStatusName(event.agent)}正在调用模型`,
      agent: event.agent || '',
      stepId: event.step_id || '',
      callId: event.call_id || '',
      requestPayload: event.request_payload,
      responseText: event.response_text,
    };
  }
  if (name === 'knowledge_retrieval') {
    // 知识库管理智能体的检索轨迹：每轮检索语句 + 证据列表 + 题目候选。
    // 同时用于实时 SSE（经 <<EV>> 嵌入）与持久化回执（刷新后回放）。
    return {
      type: 'knowledge_retrieval',
      agent: event.agent || 'knowledge_base_agent',
      stepId: event.step_id || '',
      kp_query: event.kp_query,
      question_query: event.question_query,
      retrieval_round: event.retrieval_round,
      evidence_items: event.evidence_items,
      question_candidates: event.question_candidates,
    };
  }
  if (name === 'knowledge_summary') {
    // 知识库管理智能体最终采用的总结：补充检索的中间轮次会被下一轮覆盖，
    // 只有最后一次确认的总结才会发出该事件，前端据此统计“份数”。
    return {
      type: 'knowledge_summary',
      agent: event.agent || 'knowledge_base_agent',
      summary: event.retrieval_summary || '',
      evidenceCount: event.evidence_count,
    };
  }
  // Answer chunks are rendered in the assistant bubble after the workflow's
  // publication gate. They are not execution-trace events and must never be
  // persisted as <<EV>> markers.
  if (name === 'answer_started' || name === 'answer_delta' || name === 'answer_committed') {
    return null;
  }
  return null;
}

export function runtimeEventToTrace(event) {
  const trace = runtimeEventToTracePayload(event);
  if (!trace) return trace;
  return {
    ...trace,
    ...(Number.isFinite(event?.ts) ? { ts: event.ts } : {}),
    ...(Number.isFinite(event?.seq) ? { seq: event.seq } : {}),
  };
}

async function responseError(response) {
  const payload = await readJsonResponse(response, {});
  const detail = payload?.detail;
  const error = new Error(
    (typeof detail === 'object' ? detail?.message : detail)
      || payload.message
      || `请求失败（HTTP ${response.status}）`,
  );
  error.code = detail?.code || payload?.code || '';
  error.activeThreadId = detail?.active_thread_id || payload?.active_thread_id || '';
  error.status = response.status;
  return error;
}

export function compactWorkflowHistoryContent(role, content = '') {
  let text = String(content || '');
  if (role !== 'assistant') return text;

  // Model traces and transport payloads are UI-only protocol data.  They can
  // be hundreds of kilobytes and must never re-enter the formal conversation
  // history on a later turn or regeneration.
  const rollbacks = [...text.matchAll(/<<ROLLBACK:.*?>>/gs)];
  const lastRollback = rollbacks.at(-1);
  if (lastRollback) text = text.slice((lastRollback.index || 0) + lastRollback[0].length);
  text = text.replace(/<think>[\s\S]*?<\/think>/g, '');
  text = text.replace(/<think>[\s\S]*$/g, '');
  text = removeTraceEventsFromContent(text);
  text = text.replace(
    /<<(?:STATUS|REFS|VIDEOS|PLAN|EXEC):(?:(?!<<(?:STATUS|REFS|VIDEOS|PLAN|EXEC):)[\s\S])*?(?:}>>|]>>)/g,
    '',
  );
  return text.trim();
}

export async function streamWorkflowTurn({
  conversationId,
  runId,
  answer,
  messages = [],
  availableMinutes = null,
  currentPage = null,
  examConstraints = null,
  conversationSurface = null,
  signal,
  onEvent,
  onEvents,
  resume = false,
  endpointOverride = '',
}) {
  const endpoint = resume
    ? `${MAIN_API_BASE}/review-cards/runs/${encodeURIComponent(runId)}/resume/stream`
    : (endpointOverride || `${MAIN_API_BASE}/review-cards/stream`);
  const body = resume
    ? { answer, ...(currentPage ? { current_page: currentPage } : {}) }
    : {
        thread_id: runId,
        conversation_id: conversationId,
        learner_id: 'authenticated-user',
        user_request: answer,
        ...(Number.isFinite(availableMinutes) ? { available_minutes: availableMinutes } : {}),
        ...(currentPage ? { current_page: currentPage } : {}),
        ...(examConstraints ? { exam_constraints: examConstraints } : {}),
        ...(conversationSurface ? { conversation_surface: conversationSurface } : {}),
        messages: messages
          .map(({ id, role, content }) => ({
            message_id: id || undefined,
            role,
            content: compactWorkflowHistoryContent(role, content),
          }))
          .filter(message => message.role === 'user' || message.content),
      };
  // 内部 controller：外部 signal 的 abort 语义（用户点“停止生成”）必须
  // 原样保留，因此将外部 signal 转发到内部。
  const innerController = new AbortController();
  const onExternalAbort = () => innerController.abort();
  if (signal) {
    if (signal.aborted) innerController.abort();
    else signal.addEventListener('abort', onExternalAbort, { once: true });
  }
  const response = await fetchWithAuth(endpoint, {
    method: 'POST',
    body: JSON.stringify(body),
    signal: innerController.signal,
  });
  if (!response.ok || !response.body?.getReader) throw await responseError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let terminal = null;
  let pendingEvents = [];
  let batchTimer = null;

  const flushPendingEvents = () => {
    if (batchTimer !== null) {
      clearTimeout(batchTimer);
      batchTimer = null;
    }
    if (pendingEvents.length === 0) return;
    const batch = pendingEvents;
    pendingEvents = [];
    onEvents?.(batch);
  };

  const scheduleEventBatch = () => {
    if (batchTimer !== null) return;
    batchTimer = setTimeout(() => {
      batchTimer = null;
      flushPendingEvents();
    }, STREAM_UI_BATCH_MS);
  };

  const deliverEvent = (event) => {
    const trace = runtimeEventToTrace(event);
    if (typeof onEvents === 'function') {
      pendingEvents.push([event, trace]);
      // Terminal events must be visible before streamWorkflowTurn resolves;
      // non-terminal bursts are deliberately coalesced into one UI commit.
      const isTerminal = event.event === 'run_completed'
        || event.event === 'run_interrupted'
        || event.event === 'run_waiting_human_review'
        || event.event === 'run_cancelled'
        || event.event === 'run_failed';
      if (isTerminal) flushPendingEvents();
      else scheduleEventBatch();
      return;
    }
    onEvent?.(event, trace);
  };

  const consumeFrame = (frame) => {
    const data = frame.split('\n').find(line => line.startsWith('data: '));
    if (!data) return;
    const event = JSON.parse(data.slice(6));
    deliverEvent(event);
    if (event.event === 'run_failed') throw new Error(event.message || '执行失败');
    if (
      event.event === 'run_completed'
      || event.event === 'run_interrupted'
      || event.event === 'run_waiting_human_review'
      || event.event === 'run_cancelled'
      || event.event === 'run_failed'
    ) terminal = event;
  };

  const consumeText = (text) => {
    buffer += text;
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';
    for (const frame of frames) consumeFrame(frame);
  };
  try {
    while (true) {
      // 用户主动停止：外部 signal 的 abort 需立即中断等待（真实浏览器中
      // fetch 会因此让流报错，但 mock/部分环境不会，故显式竞争）。
      const abortPromise = new Promise((_, reject) => {
        if (innerController.signal.aborted) {
          reject(new DOMException('Aborted', 'AbortError'));
        } else {
          innerController.signal.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'));
          }, { once: true });
        }
      });
      const { value, done } = await Promise.race([reader.read(), abortPromise]);
      if (done) break;
      consumeText(decoder.decode(value, { stream: true }));
    }
    // A compliant SSE producer normally ends frames with a blank line, but
    // accepting a final unterminated frame makes the reader robust to proxy or
    // server shutdown boundaries as well.
    consumeText(decoder.decode());
    if (buffer.trim()) consumeFrame(buffer);
    buffer = '';
  } finally {
    flushPendingEvents();
    signal?.removeEventListener('abort', onExternalAbort);
  }
  if (!terminal) {
    throw new Error('处理被中断，未收到完整结果。可稍后刷新会话查看，或重新发送。');
  }
  const terminalStatuses = {
    run_interrupted: 'interrupted',
    run_waiting_human_review: 'waiting_human_review',
    run_cancelled: 'cancelled',
  };
  return {
    status: terminalStatuses[terminal.event] || 'completed',
    result: terminal.result,
    message: terminal.assistant_message || '本次处理已完成。',
  };
}

export async function getWorkflowRun(runId) {
  const response = await fetchWithAuth(
    `${MAIN_API_BASE}/review-cards/runs/${encodeURIComponent(runId)}`,
  );
  if (response.status === 404) return null;
  if (!response.ok) throw await responseError(response);
  return readJsonResponse(response, {});
}

export async function cancelWorkflowRun(runId) {
  if (!runId) return { status: 'not_running', thread_id: null };
  const response = await fetchWithAuth(
    `${MAIN_API_BASE}/review-cards/runs/${encodeURIComponent(runId)}/cancel`,
    { method: 'POST' },
  );
  if (!response.ok) throw await responseError(response);
  return readJsonResponse(response, {});
}

export async function getWorkflowRunWithStartupGrace(runId) {
  for (let attempt = 0; attempt < RUN_STARTUP_LOOKUP_ATTEMPTS; attempt += 1) {
    const run = await getWorkflowRun(runId);
    if (run) return run;
    if (attempt < RUN_STARTUP_LOOKUP_ATTEMPTS - 1) {
      await new Promise(resolve => setTimeout(resolve, RUN_STARTUP_LOOKUP_DELAY_MS));
    }
  }
  return null;
}

export async function getResumableWorkflowRunId(runId) {
  if (!runId) return null;
  const run = await getWorkflowRun(runId);
  return run?.status === 'interrupted' ? runId : null;
}
