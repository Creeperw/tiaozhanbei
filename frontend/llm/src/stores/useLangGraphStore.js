import { create } from 'zustand';

/** @typedef {'idle'|'running'|'done'|'error'|'rollingBack'|'archived'} NodeStatus */
/** @typedef {{ id:string, name:string, args?:object, resultSnippet?:string, status:NodeStatus, startTime:number, endTime?:number }} ToolCall */
/** @typedef {{ id:string, label:string, intent:string, status:NodeStatus, startTime:number, endTime?:number, resultSnippet?:string }} IntentCall */
/** @typedef {{ id:string, callId?:string, stepId?:string, kind:'input'|'output'|'transport', agent?:string, input?:any, output?:any, requestPayload?:any, responseText?:string, ts:number }} ModelCall */
/** @typedef {{ id:string, name:string, agent?:string, stepId?:string, status:NodeStatus, startTime:number, endTime?:number, logs:string[], tools:ToolCall[], intents:IntentCall[], modelCalls:ModelCall[], outputSnippet?:string, error?:string, archived?:boolean }} ExecutionNode */
/** @typedef {{ type:string, title?:string, text?:string, name?:string, query?:string, intent?:string, approved?:boolean, kind?:string, input?:any, output?:any, requestPayload?:any, responseText?:string }} LangGraphEvent */

const NODE_MAP = {
  context: 'InfoManager',
  memory: 'InfoManager',
  planning: 'Planner',
  intent: 'Planner',
  tool: 'Planner',
  refine: 'InfoManager',
  execution: 'Executor',
  feedback: 'Feedback',
};

const now = () => Date.now();
const eventTime = (ev) => Number.isFinite(ev?.ts) ? ev.ts : now();
const newNode = (id, name, text = '', ts = now(), agent = '') => ({
  id,
  name,
  agent: agent || name,
  stepId: id,
  status: 'running',
  startTime: ts,
  logs: text ? [text] : [],
  tools: [],
  intents: [],
  modelCalls: [],
  retrievals: [],
  activities: [],
  auditEvents: [],
  workingOutput: '',
  workingOutputStreaming: false,
  formalOutput: '',
  formalOutputStreaming: false,
  formalOutputReady: false,
  // Compatibility projection for the existing side-panel presentation. New
  // conversation UI reads the phase-specific fields above.
  publicOutput: '',
  publicOutputStreaming: false,
  inputSummary: null,
  dependsOn: [],
});

export const useLangGraphStore = create((set, get) => ({
  nodes: [],
  currentActiveNodeId: null,
  isRollingBack: false,
  finalAnswerId: null,
  finalAnswerContent: '',
  references: [],
  showPreliminaryAnswer: true,
  workflowTerminal: false,

  resetWorkflow: () => set({ nodes: [], currentActiveNodeId: null, isRollingBack: false, workflowTerminal: false, finalAnswerId: null, finalAnswerContent: '', references: [] }),
  setReferences: (references) => set({ references: references || [] }),
  appendAnswer: (delta) => set(state => ({ finalAnswerContent: state.finalAnswerContent + delta })),

  dispatchEvent: (event) => set(state => reduceLangGraphEvent(state, event)),
  dispatchEvents: (events) => set(state => (
    (Array.isArray(events) ? events : []).reduce(reduceLangGraphEvent, state)
  )),

  mockPlannerTools: () => {
    get().resetWorkflow();
    ['context_start','context_done','planning_start'].forEach(type => get().dispatchEvent({ type, text: type }));
    get().dispatchEvent({ type: 'tool_start', name: 'search_rag', query: '高血压饮食' });
    get().dispatchEvent({ type: 'tool_done', name: 'search_rag', query: '高血压饮食', text: '低盐、DASH 饮食、规律运动。' });
    get().dispatchEvent({ type: 'tool_start', name: 'search_food_web', query: 'DASH diet' });
    get().dispatchEvent({ type: 'tool_done', name: 'search_food_web', query: 'DASH diet', text: '蔬果、全谷物、低脂乳制品。' });
    get().dispatchEvent({ type: 'planning_done', text: '信息足够。' });
  },
  mockFeedbackFail: () => {
    get().dispatchEvent({ type: 'feedback_start', text: '开始审核' });
    get().dispatchEvent({ type: 'feedback_done', approved: false, text: '包含过度承诺，触发回滚。' });
  },
  markNetworkInterrupted: (reason = '网络中断，事件流不完整。') => set(state => ({
    nodes: state.nodes.map(n => n.status === 'running' ? { ...n, status: 'error', endTime: now(), error: reason } : n),
    currentActiveNodeId: null,
  })),
}));

function finishNode(nodes, id, patch = {}, ts = now()) {
  return nodes.map(n => n.id === id ? { ...n, ...patch, status: patch.status || 'done', endTime: n.endTime || ts } : n);
}

function upsertNode(nodes, id, name, text, status = 'running', ts = now(), agent = '') {
  const idx = nodes.findIndex(n => n.id === id);
  if (idx < 0) return [...nodes, { ...newNode(id, name, text, ts, agent), status }];
  const node = nodes[idx];
  const logs = text && node.logs.at(-1) !== text ? [...node.logs, text] : node.logs;
  const next = [...nodes];
  next[idx] = {
    ...node,
    agent: agent || node.agent,
    status,
    logs,
    endTime: status === 'done' ? ts : node.endTime,
  };
  return next;
}

function eventNode(ev, fallbackId, fallbackAgent, fallbackName) {
  const agent = ev.agent || fallbackAgent;
  return {
    id: ev.stepId || agent || fallbackId,
    agent,
    name: agent || fallbackName,
  };
}

function plannerNode(nodes, ev) {
  const requested = eventNode(ev, 'planner', 'planner_agent', NODE_MAP.planning);
  const matching = nodes.find((node) => node.id === requested.id)
    || nodes.find((node) => node.agent === requested.agent);
  return matching ? { ...requested, id: matching.id } : requested;
}

function addTool(nodes, nodeId, descriptor) {
  return nodes.map((node) => {
    if (node.id !== nodeId) return node;
    const signature = JSON.stringify([descriptor.name, descriptor.args || {}]);
    const duplicate = node.tools.some((tool) => (
      tool.status === 'running'
      && JSON.stringify([tool.name, tool.args || {}]) === signature
    ));
    return duplicate ? node : { ...node, tools: [...node.tools, descriptor] };
  });
}

function appendActivity(nodes, nodeId, activity) {
  return nodes.map((node) => {
    if (node.id !== nodeId) return node;
    const activities = node.activities || [];
    const signature = `${activity.kind || ''}|${activity.text || ''}|${activity.ts || ''}`;
    if (activities.some((item) => `${item.kind || ''}|${item.text || ''}|${item.ts || ''}` === signature)) {
      return node;
    }
    return { ...node, activities: [...activities, activity] };
  });
}

export function reduceLangGraphEvent(state, ev) {
  const terminalEventTypes = new Set([
    'workflow_done',
    'workflow_failed',
    'workflow_interrupted',
    'human_review_waiting',
  ]);
  if (state.workflowTerminal && !terminalEventTypes.has(ev?.type)) return state;
  let nodes = state.nodes;
  let currentActiveNodeId = state.currentActiveNodeId;
  let isRollingBack = state.isRollingBack;
  const ts = eventTime(ev);

  if (ev.type === 'context_start') {
    const target = eventNode(ev, 'context', 'memory_agent', NODE_MAP.context);
    nodes = upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent);
    currentActiveNodeId = target.id;
  }
  else if (ev.type === 'context_done') {
    const target = eventNode(ev, 'context', 'memory_agent', NODE_MAP.context);
    nodes = finishNode(upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent), target.id, {}, ts);
  }
  else if (ev.type === 'memory_start') {
    const target = eventNode(ev, 'memory', 'memory_agent', NODE_MAP.memory);
    nodes = upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent);
    currentActiveNodeId = target.id;
  }
  else if (ev.type === 'memory_done') {
    const target = eventNode(ev, 'memory', 'memory_agent', NODE_MAP.memory);
    nodes = finishNode(upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent), target.id, {}, ts);
  }
  else if (ev.type === 'planning_start') {
    const target = plannerNode(nodes, ev);
    nodes = upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent);
    currentActiveNodeId = target.id;
  }
  else if (ev.type === 'planning_delta') {
    const target = plannerNode(nodes, ev);
    nodes = upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent);
    currentActiveNodeId = target.id;
  }
  else if (ev.type === 'intent') {
    const target = plannerNode(nodes, ev);
    nodes = upsertNode(nodes, target.id, target.name, '', 'running', ts, target.agent);
    nodes = nodes.map(n => n.id === target.id ? {
      ...n,
      intents: [...(n.intents || []), {
        id: `intent-${(n.intents || []).length}-${ts}`,
        label: 'IntentClassifier',
        intent: ev.intent || '其他',
        status: 'done',
        startTime: ts,
        endTime: ts,
        resultSnippet: ev.text || `识别意图：${ev.intent || '其他'}`,
      }],
    } : n);
    currentActiveNodeId = target.id;
  }
  else if (ev.type === 'tool_start') {
    const requestedTarget = eventNode(ev, 'knowledge', 'knowledge_base_agent', NODE_MAP.tool);
    const matchingNode = nodes.find((node) => node.agent === requestedTarget.agent);
    const target = matchingNode ? { ...requestedTarget, id: matchingNode.id } : requestedTarget;
    if (!matchingNode) {
      nodes = upsertNode(nodes, target.id, target.name, '', 'pending', ts, target.agent);
    }
    const existing = nodes.find((node) => node.id === target.id);
    nodes = addTool(nodes, target.id, {
      id: `${ev.name || 'tool'}-${existing?.tools?.length || 0}-${ts}`,
      name: ev.name || 'tool',
      args: ev.args || { query: ev.query || '' },
      status: 'running',
      startTime: ts,
    });
  } else if (ev.type === 'tool_event') {
    const requestedTarget = eventNode(ev, 'knowledge', 'knowledge_base_agent', NODE_MAP.tool);
    const matchingNode = nodes.find((node) => node.agent === requestedTarget.agent);
    const target = matchingNode ? { ...requestedTarget, id: matchingNode.id } : requestedTarget;
    if (!matchingNode) {
      nodes = upsertNode(nodes, target.id, target.name, '', 'pending', ts, target.agent);
    }
    const existing = nodes.find((node) => node.id === target.id);
    nodes = addTool(nodes, target.id, {
      id: `${ev.name || 'tool'}-${existing?.tools?.length || 0}-${ts}`,
      name: ev.name || 'tool',
      args: ev.args || {},
      status: ev.status || 'done',
      startTime: ts,
      endTime: ev.status === 'running' ? undefined : ts,
      resultSnippet: ev.text || '',
    });
  } else if (ev.type === 'tool_done') {
    const requestedTarget = eventNode(ev, 'knowledge', 'knowledge_base_agent', NODE_MAP.tool);
    const matchingNode = nodes.find((node) => node.agent === requestedTarget.agent);
    const target = matchingNode ? { ...requestedTarget, id: matchingNode.id } : requestedTarget;
    nodes = nodes.map(n => {
      if (n.id !== target.id) return n;
      const runningIndex = n.tools.findIndex((tool) => tool.status === 'running' && (!ev.name || tool.name === ev.name));
      return {
        ...n,
        tools: n.tools.map((tool, idx) => idx === runningIndex ? { ...tool, status: 'done', endTime: ts, resultSnippet: ev.text || '' } : tool),
      };
    });
  } else if (ev.type === 'planning_done') {
    const target = plannerNode(nodes, ev);
    nodes = finishNode(upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent), target.id, {}, ts);
    for (const planned of ev.plannedNodes || []) {
      const stepId = planned?.step_id || planned?.id;
      const agent = planned?.agent;
      if (!stepId || !agent || stepId === target.id || agent === 'planner_agent') continue;
      nodes = upsertNode(nodes, stepId, agent, '已列入本次执行路径。', 'pending', ts, agent);
      nodes = nodes.map((node) => node.id === stepId ? {
        ...node,
        dependsOn: Array.isArray(planned?.depends_on) ? planned.depends_on : [],
      } : node);
    }
  }
  else if (ev.type === 'refine_start') { nodes = upsertNode(nodes, 'refine', NODE_MAP.refine, ev.text, 'running', ts); currentActiveNodeId = 'refine'; }
  else if (ev.type === 'refine_done') { nodes = finishNode(upsertNode(nodes, 'refine', NODE_MAP.refine, ev.text, 'running', ts), 'refine', {}, ts); }
  else if (ev.type === 'execution_start') {
    const target = eventNode(ev, 'executor', 'expert_agent', NODE_MAP.execution);
    nodes = upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent);
    currentActiveNodeId = target.id;
  }
  else if (ev.type === 'execution_delta') {
    const target = eventNode(ev, 'executor', 'expert_agent', NODE_MAP.execution);
    nodes = upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent);
  }
  else if (ev.type === 'execution_done') {
    const target = eventNode(ev, 'executor', 'expert_agent', NODE_MAP.execution);
    nodes = finishNode(upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent), target.id, {}, ts);
  }
  else if (ev.type === 'feedback_start') {
    const target = eventNode(ev, 'feedback', 'audit_agent', NODE_MAP.feedback);
    // Entering Audit is an authoritative stage boundary. Some upstream
    // streams can finish without a matching terminal chunk (for example a
    // reasoning stream emitted around paper retrieval), leaving an earlier
    // node marked as running even though the backend has already moved on.
    // Close those stale states before activating Audit so the trace header and
    // stage cards describe the real workflow position.
    nodes = nodes.map((node) => {
      if (node.agent === target.agent || !['running', 'rollingBack'].includes(node.status)) {
        return node;
      }
      return {
        ...node,
        status: 'done',
        endTime: node.endTime || ts,
        tools: node.tools.map((tool) => tool.status === 'running'
          ? { ...tool, status: 'done', endTime: tool.endTime || ts }
          : tool),
        intents: (node.intents || []).map((intent) => intent.status === 'running'
          ? { ...intent, status: 'done', endTime: intent.endTime || ts }
          : intent),
        reasoningStreaming: false,
        workingOutputStreaming: false,
        formalOutputStreaming: false,
        publicOutputStreaming: false,
      };
    });
    nodes = upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent);
    currentActiveNodeId = target.id;
  }
  else if (ev.type === 'feedback_regenerate') {
    isRollingBack = true;
    nodes = nodes.map(n => n.id === 'executor' ? { ...n, archived: true, status: 'archived' } : n);
    nodes = upsertNode(nodes, `feedback-${ts}`, NODE_MAP.feedback, ev.text, 'rollingBack', ts);
  } else if (ev.type === 'feedback_done') {
    const target = eventNode(ev, 'feedback', 'audit_agent', NODE_MAP.feedback);
    if (ev.approved === false) {
      isRollingBack = true;
      nodes = upsertNode(nodes, target.id, target.name, ev.text, 'error', ts, target.agent);
      nodes = nodes.map(n => n.id === target.id ? { ...n, error: ev.text || '审核失败' } : n);
    } else {
      nodes = finishNode(upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent), target.id, {}, ts);
      // A successful forced re-audit supersedes the earlier audit failure
      // that triggered local repair. Keeping that recoverable error terminal
      // makes a successful conversation misleadingly appear to have failed.
      nodes = nodes.map((node) => (
        node.agent === 'audit_agent' || node.name === NODE_MAP.feedback
          ? {
            ...node,
            status: 'done',
            endTime: node.endTime || ts,
            error: undefined,
          }
          : node
      ));
      isRollingBack = false;
    }
  } else if (ev.type === 'step_error') {
    const target = eventNode(ev, ev.stepId || 'failed-step', ev.agent || '', ev.agent || '执行节点');
    nodes = upsertNode(nodes, target.id, target.name, ev.text, 'error', ts, target.agent);
    nodes = nodes.map((node) => (
      node.id === target.id
        ? { ...node, status: 'error', endTime: ts, error: ev.text || '执行失败' }
        : node
    ));
    currentActiveNodeId = null;
  } else if (ev.type === 'workflow_failed') {
    const matchingNode = nodes.find((node) => (
      (ev.stepId && node.id === ev.stepId) || (ev.agent && node.agent === ev.agent)
    ));
    nodes = nodes.map((node) => {
      const failed = matchingNode ? node.id === matchingNode.id : node.status === 'running';
      return failed
        ? { ...node, status: 'error', endTime: ts, error: ev.text || '执行失败' }
        : node;
    });
    currentActiveNodeId = null;
  } else if (ev.type === 'model_call') {
    const agent = ev.agent || '';
    const stepId = ev.stepId || '';
    const matchingNode = nodes.find((node) => (
      (stepId && node.id === stepId) || (agent && node.agent === agent)
    ));
    const targetId = matchingNode?.id || (agent ? `model-${agent}-${ts}` : `model-${ts}`);
    if (!matchingNode) {
      nodes = upsertNode(nodes, targetId, agent || 'expert_agent', '', 'pending', ts, agent || 'expert_agent');
    }
    const call = {
      id: `${ev.callId || 'model_call'}-${ev.kind || 'call'}-${ts}`,
      callId: ev.callId || '',
      stepId,
      kind: ev.kind || 'call',
      agent,
      input: ev.input,
      output: ev.output,
      requestPayload: ev.requestPayload,
      responseText: ev.responseText,
      ts,
    };
    nodes = nodes.map((node) => {
      if (node.id !== targetId) return node;
      return {
        ...node,
        modelCalls: [...(node.modelCalls || []), call],
      };
    });
    if (ev.text) {
      nodes = appendActivity(nodes, targetId, {
        kind: `model_${ev.kind || 'call'}`,
        text: ev.text,
        ts,
      });
    }
  } else if (ev.type === 'knowledge_retrieval') {
    // 知识库管理智能体的每轮检索语句：kp_query（知识点检索语句）与
    // question_query（题目检索语句）记录到 knowledge 节点，供协作侧边栏展示。
    const requestedTarget = eventNode(ev, 'knowledge', 'knowledge_base_agent', NODE_MAP.tool);
    const matchingNode = nodes.find((node) => node.agent === requestedTarget.agent);
    const target = matchingNode ? { ...requestedTarget, id: matchingNode.id } : requestedTarget;
    if (!matchingNode) {
      nodes = upsertNode(nodes, target.id, target.name, '', 'pending', ts, target.agent);
    }
    nodes = nodes.map((node) => {
      if (node.id !== target.id) return node;
      const retrievals = [...(node.retrievals || [])];
      const existingIndex = retrievals.findIndex((retrieval) => (
        retrieval.kp_query === ev.kp_query && retrieval.question_query === ev.question_query
      ));
      const retrieval = {
        kp_query: ev.kp_query || '',
        question_query: ev.question_query || '',
        retrieval_round: ev.retrieval_round,
        evidence_items: Array.isArray(ev.evidence_items) ? ev.evidence_items : [],
        question_candidates: Array.isArray(ev.question_candidates) ? ev.question_candidates : [],
        ts,
      };
      if (existingIndex >= 0) {
        retrievals[existingIndex] = retrieval;
      } else {
        retrievals.push(retrieval);
      }
      return { ...node, retrievals };
    });
  } else if (ev.type === 'agent_progress') {
    const requestedTarget = eventNode(ev, ev.stepId || 'progress', ev.agent || '', ev.agent || '执行节点');
    const matchingNode = nodes.find((node) => (
      (requestedTarget.id && node.id === requestedTarget.id)
      || (requestedTarget.agent && node.agent === requestedTarget.agent)
    ));
    const target = matchingNode ? { ...requestedTarget, id: matchingNode.id } : requestedTarget;
    if (!matchingNode) {
      nodes = upsertNode(nodes, target.id, target.name, '', 'running', ts, target.agent);
    }
    nodes = appendActivity(nodes, target.id, {
      kind: ev.kind || 'progress',
      text: ev.text || '正在处理',
      ts,
      detail: ev.detail || null,
    });
  } else if (ev.type === 'agent_output_stream') {
    const requestedTarget = eventNode(ev, ev.stepId || 'agent-output', ev.agent || '', ev.agent || '执行节点');
    const matchingNode = nodes.find((node) => (
      (requestedTarget.id && node.id === requestedTarget.id)
      || (requestedTarget.agent && node.agent === requestedTarget.agent)
    ));
    const target = matchingNode ? { ...requestedTarget, id: matchingNode.id } : requestedTarget;
    if (!matchingNode) {
      nodes = upsertNode(nodes, target.id, target.name, '', 'running', ts, target.agent);
    }
    nodes = nodes.map((node) => {
      if (node.id !== target.id) return node;
      const phase = ev.phase === 'working' ? 'working' : 'formal';
      const textKey = phase === 'working' ? 'workingOutput' : 'formalOutput';
      const streamingKey = phase === 'working' ? 'workingOutputStreaming' : 'formalOutputStreaming';
      const currentText = node[textKey] || '';
      const withCompatibilityProjection = (patch) => {
        const next = { ...node, ...patch };
        return {
          ...next,
          publicOutput: next.formalOutput || next.workingOutput || '',
          publicOutputStreaming: Boolean(
            next.formalOutputStreaming || next.workingOutputStreaming
          ),
        };
      };
      if (ev.kind === 'started') {
        return withCompatibilityProjection({
          [textKey]: ev.append ? currentText : '',
          [streamingKey]: true,
          ...(phase === 'formal' ? { formalOutputReady: false } : {}),
        });
      }
      if (ev.kind === 'delta') {
        return withCompatibilityProjection({
          [textKey]: `${currentText}${ev.text || ''}`,
          [streamingKey]: true,
        });
      }
      if (ev.kind === 'replaced') {
        return withCompatibilityProjection({
          [textKey]: ev.text || '',
          [streamingKey]: false,
          ...(phase === 'formal' ? {
            formalOutputReady: Boolean(ev.text),
            workingOutputStreaming: false,
          } : {}),
        });
      }
      return withCompatibilityProjection({
        [streamingKey]: false,
        ...(phase === 'formal' ? {
          formalOutputReady: Boolean(currentText),
          workingOutputStreaming: false,
        } : {}),
      });
    });
  } else if (ev.type === 'reasoning_stream') {
    const requestedTarget = eventNode(ev, ev.stepId || 'reasoning', ev.agent || '', ev.agent || '思考节点');
    const matchingNode = nodes.find((node) => (
      (requestedTarget.id && node.id === requestedTarget.id)
      || (requestedTarget.agent && node.agent === requestedTarget.agent)
    ));
    const target = matchingNode ? { ...requestedTarget, id: matchingNode.id } : requestedTarget;
    if (!matchingNode) {
      nodes = upsertNode(nodes, target.id, target.name, '', 'running', ts, target.agent);
    }
    nodes = nodes.map((node) => {
      if (node.id !== target.id) return node;
      const currentText = node.reasoning || '';
      if (ev.kind === 'started') {
        return { ...node, reasoning: '', reasoningStreaming: true };
      }
      if (ev.kind === 'delta') {
        return { ...node, reasoning: `${currentText}${ev.text || ''}`, reasoningStreaming: true };
      }
      return { ...node, reasoningStreaming: false };
    });
  } else if (ev.type === 'repair_event') {
    if (['planned', 'step_started', 'reaudit_started'].includes(ev.kind)) isRollingBack = true;
    if (['completed', 'stopped'].includes(ev.kind)) isRollingBack = false;
    const auditNode = nodes.find((node) => node.agent === 'audit_agent');
    const targetId = auditNode?.id || ev.auditStepId || 'audit';
    if (!auditNode) {
      nodes = upsertNode(nodes, targetId, 'audit_agent', '', 'running', ts, 'audit_agent');
    }
    nodes = nodes.map((node) => node.id === targetId ? {
      ...node,
      status: ev.status === 'failed'
        ? 'error'
        : (ev.status === 'needs_human_review'
          ? 'waiting_human_review'
          : (['pass', 'completed', 'success'].includes(ev.status) ? 'done' : node.status)),
      endTime: ['needs_human_review', 'failed', 'pass', 'completed', 'success'].includes(ev.status)
        ? (node.endTime || ts)
        : node.endTime,
      error: ev.status === 'failed' ? (ev.text || '返修后审核失败') : undefined,
      auditEvents: [...(node.auditEvents || []), {
        kind: ev.kind || 'repair',
        text: ev.text || '',
        status: ev.status || '',
        targetStepIds: ev.targetStepIds || [],
        preservedStepIds: ev.preservedStepIds || [],
        locations: ev.locations || [],
        ts,
      }],
    } : node);
  } else if (ev.type === 'human_review_waiting') {
    const requestedTarget = eventNode(ev, 'feedback', 'audit_agent', NODE_MAP.feedback);
    const matchingNode = nodes.find((node) => node.agent === requestedTarget.agent);
    const target = matchingNode ? { ...requestedTarget, id: matchingNode.id } : requestedTarget;
    nodes = upsertNode(
      nodes,
      target.id,
      target.name,
      ev.text,
      'waiting_human_review',
      ts,
      target.agent,
    );
    currentActiveNodeId = null;
    isRollingBack = false;
  } else if (ev.type === 'workflow_interrupted') {
    // A compiled graph may contain later optional gates that never actually
    // start (for example Audit on a lightweight conversational response).
    // Once the run reaches a terminal interruption, those planned-only nodes
    // are no longer participants and must not remain visible as "waiting".
    nodes = nodes.map((node) => (
      node.status === 'pending'
        ? { ...node, status: 'skipped', endTime: ts }
        : node
    ));
    currentActiveNodeId = null;
    isRollingBack = false;
  } else if (ev.type === 'workflow_done') {
    nodes = nodes.map((node) => {
      if (node.status === 'pending') return { ...node, status: 'skipped', endTime: ts };
      if (node.status !== 'running') return node;
      return {
        ...node,
        status: 'done',
        endTime: ts,
        tools: node.tools.map(tool => tool.status === 'running' ? { ...tool, status: 'done', endTime: ts } : tool),
        intents: (node.intents || []).map(intent => intent.status === 'running' ? { ...intent, status: 'done', endTime: ts } : intent),
        // Clear any leftover streaming flags so no section keeps a blinking
        // caret after the workflow has completed (fixes "输出完了还有光标闪").
        reasoningStreaming: false,
        workingOutputStreaming: false,
        formalOutputStreaming: false,
        publicOutputStreaming: false,
      };
    });
    currentActiveNodeId = null;
  }
  if (ev.stepId || ev.agent) {
    const matchingNode = nodes.find((node) => (
      (ev.stepId && node.id === ev.stepId) || (ev.agent && node.agent === ev.agent)
    ));
    if (matchingNode) {
      nodes = nodes.map((node) => node.id === matchingNode.id ? {
        ...node,
        inputSummary: ev.inputSummary || node.inputSummary,
        dependsOn: Array.isArray(ev.dependsOn) ? ev.dependsOn : node.dependsOn,
      } : node);
    }
  }
  return {
    ...state,
    nodes,
    currentActiveNodeId,
    isRollingBack,
    workflowTerminal: state.workflowTerminal || terminalEventTypes.has(ev?.type),
  };
}

/** Convert a persisted event list into message-level execution nodes. */
export function buildTraceFromEvents(events = [], options = {}) {
  const state = events.reduce((acc, event, idx) => reduceLangGraphEvent(acc, event.ts ? event : { ...event, ts: now() - (events.length - idx) * 120 }), {
    nodes: [],
    currentActiveNodeId: null,
    isRollingBack: false,
    finalAnswerId: null,
    finalAnswerContent: '',
    references: [],
    showPreliminaryAnswer: true,
    workflowTerminal: false,
  });
  if (!options.historical) return state.nodes;
  const endTs = Math.max(...state.nodes.map(n => n.endTime || n.startTime || now()), now());
  return state.nodes.map((n) => {
    // Historical messages are, by definition, already finished. Clear any
    // leftover streaming flags unconditionally so no section keeps a blinking
    // caret (e.g. when a final reasoning_committed event did not match its
    // node during replay).
    const finished = n.status === 'running'
      ? { ...n, status: 'done', endTime: n.endTime || endTs,
          tools: n.tools.map(t => t.status === 'running' ? { ...t, status: 'done', endTime: t.endTime || endTs } : t),
          intents: (n.intents || []).map(t => t.status === 'running' ? { ...t, status: 'done', endTime: t.endTime || endTs } : t) }
      : n;
    return {
      ...finished,
      reasoningStreaming: false,
      workingOutputStreaming: false,
      formalOutputStreaming: false,
      publicOutputStreaming: false,
    };
  });
}
