import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from './utils/api';

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

export function runtimeEventToTrace(event) {
  const name = event?.event || '';
  if (name === 'run_started') return { type: 'planning_start', text: '正在理解你的需求' };
  if (name === 'run_resumed' || name === 'graph_resumed') return { type: 'refine_start', text: '已从检查点恢复' };
  if (name === 'graph_compiled') return {
    type: 'planning_done',
    text: '执行路径已确定',
    plannedNodes: Array.isArray(event.nodes) ? event.nodes : [],
  };
  if (name === 'audit_revision_started') return { type: 'feedback_regenerate', text: '审核后正在修订' };
  if (name === 'audit_revision_completed') return {
    type: 'feedback_done', approved: event.status === 'pass', text: '修订复核已完成',
  };
  if (name === 'run_completed') return { type: 'workflow_done', text: '处理完成' };
  if (name === 'run_waiting_human_review') {
    return { type: 'human_review_waiting', text: '内容正在等待人工复核' };
  }
  if (name === 'run_interrupted' || name === 'graph_interrupted') {
    return { type: 'execution_done', text: '等待用户补充信息' };
  }
  if (name === 'run_failed' || name === 'step_failed') {
    return { type: 'feedback_done', approved: false, text: event.message || event.error_message || '执行失败' };
  }
  if (name === 'step_started' || name === 'step_completed') {
    const phase = agentPhase(event.agent);
    return {
      type: `${phase}_${name === 'step_started' ? 'start' : 'done'}`,
      text: event.agent ? `${event.agent}${name === 'step_started' ? '开始处理' : '处理完成'}` : '',
      agent: event.agent || '',
      stepId: event.step_id || '',
    };
  }
  if (name === 'web_search_status') {
    return {
      type: event.status === 'completed' ? 'tool_done' : 'tool_start',
      name: 'web_search',
      agent: event.agent || 'knowledge_base_agent',
      query: event.query || '',
      text: event.message || '',
    };
  }
  if (name === 'model_input') {
    return {
      type: 'model_call',
      kind: 'input',
      text: `${event.agent || 'model'} 模型输入`,
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
      text: `${event.agent || 'model'} 模型输出`,
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
      text: `${event.agent || 'model'} 模型传输`,
      agent: event.agent || '',
      stepId: event.step_id || '',
      callId: event.call_id || '',
      requestPayload: event.request_payload,
      responseText: event.response_text,
    };
  }
  return null;
}

async function responseError(response) {
  const payload = await readJsonResponse(response, {});
  return new Error(payload.detail || payload.message || `请求失败（HTTP ${response.status}）`);
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
  // EV tags embed JSON.stringify(traceEvent) which may itself contain `>>`
  // inside model payloads. A tag always ends with its JSON structure closing
  // brace (`}>>` for objects, `]>>` for arrays), so match to that instead of
  // the first bare `>>` — otherwise a stray `>>` inside JSON would truncate
  // the strip and leak raw JSON into the formal history.
  text = text.replace(
    /<<(?:STATUS|EV|REFS|VIDEOS|PLAN|EXEC):(?:(?!<<(?:STATUS|EV|REFS|VIDEOS|PLAN|EXEC):)[\s\S])*?(?:}>>|]>>)/g,
    '',
  );
  return text.trim();
}

export async function streamWorkflowTurn({
  conversationId,
  runId,
  answer,
  messages = [],
  availableMinutes = 60,
  currentPage = null,
  signal,
  onEvent,
  resume = false,
}) {
  const endpoint = resume
    ? `${MAIN_API_BASE}/review-cards/runs/${encodeURIComponent(runId)}/resume/stream`
    : `${MAIN_API_BASE}/review-cards/stream`;
  const body = resume
    ? { answer, ...(currentPage ? { current_page: currentPage } : {}) }
    : {
        thread_id: runId,
        conversation_id: conversationId,
        learner_id: 'authenticated-user',
        user_request: answer,
        available_minutes: availableMinutes,
        ...(currentPage ? { current_page: currentPage } : {}),
        messages: messages
          .map(({ id, role, content }) => ({
            message_id: id || undefined,
            role,
            content: compactWorkflowHistoryContent(role, content),
          }))
          .filter(message => message.role === 'user' || message.content),
      };
  const response = await fetchWithAuth(endpoint, {
    method: 'POST',
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body?.getReader) throw await responseError(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let terminal = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() || '';
    for (const frame of frames) {
      const data = frame.split('\n').find(line => line.startsWith('data: '));
      if (!data) continue;
      const event = JSON.parse(data.slice(6));
      onEvent?.(event, runtimeEventToTrace(event));
      if (event.event === 'run_failed') throw new Error(event.message || '执行失败');
      if (
        event.event === 'run_completed'
        || event.event === 'run_interrupted'
        || event.event === 'run_waiting_human_review'
      ) terminal = event;
    }
  }
  if (!terminal) throw new Error('连接已结束，但没有收到完整结果');
  const terminalStatuses = {
    run_interrupted: 'interrupted',
    run_waiting_human_review: 'waiting_human_review',
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

export async function getResumableWorkflowRunId(runId) {
  if (!runId) return null;
  const run = await getWorkflowRun(runId);
  return run?.status === 'interrupted' ? runId : null;
}
