import { create } from 'zustand';

/** @typedef {'idle'|'running'|'done'|'error'|'rollingBack'|'archived'} NodeStatus */
/** @typedef {{ id:string, name:string, args?:object, resultSnippet?:string, status:NodeStatus, startTime:number, endTime?:number }} ToolCall */
/** @typedef {{ id:string, label:string, intent:string, status:NodeStatus, startTime:number, endTime?:number, resultSnippet?:string }} IntentCall */
/** @typedef {{ id:string, name:string, agent?:string, stepId?:string, status:NodeStatus, startTime:number, endTime?:number, logs:string[], tools:ToolCall[], intents:IntentCall[], outputSnippet?:string, error?:string, archived?:boolean }} ExecutionNode */
/** @typedef {{ type:string, title?:string, text?:string, name?:string, query?:string, intent?:string, approved?:boolean }} LangGraphEvent */

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
});

export const useLangGraphStore = create((set, get) => ({
  nodes: [],
  currentActiveNodeId: null,
  isRollingBack: false,
  finalAnswerId: null,
  finalAnswerContent: '',
  references: [],
  showPreliminaryAnswer: true,

  resetWorkflow: () => set({ nodes: [], currentActiveNodeId: null, isRollingBack: false, finalAnswerId: null, finalAnswerContent: '', references: [] }),
  setReferences: (references) => set({ references: references || [] }),
  appendAnswer: (delta) => set(state => ({ finalAnswerContent: state.finalAnswerContent + delta })),

  dispatchEvent: (event) => set(state => reduceLangGraphEvent(state, event)),

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

export function reduceLangGraphEvent(state, ev) {
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
    const target = eventNode(ev, 'planner', 'planner_agent', NODE_MAP.planning);
    nodes = upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent);
    currentActiveNodeId = target.id;
  }
  else if (ev.type === 'planning_delta') {
    const target = eventNode(ev, 'planner', 'planner_agent', NODE_MAP.planning);
    nodes = upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent);
    currentActiveNodeId = target.id;
  }
  else if (ev.type === 'intent') {
    const target = eventNode(ev, 'planner', 'planner_agent', NODE_MAP.planning);
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
    nodes = upsertNode(nodes, target.id, target.name, '', 'running', ts, target.agent);
    const existing = nodes.find((node) => node.id === target.id);
    nodes = addTool(nodes, target.id, {
      id: `${ev.name || 'tool'}-${existing?.tools?.length || 0}-${ts}`,
      name: ev.name || 'tool',
      args: ev.args || { query: ev.query || '' },
      status: 'running',
      startTime: ts,
    });
    currentActiveNodeId = target.id;
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
    const target = eventNode(ev, 'planner', 'planner_agent', NODE_MAP.planning);
    nodes = finishNode(upsertNode(nodes, target.id, target.name, ev.text, 'running', ts, target.agent), target.id, {}, ts);
    for (const planned of ev.plannedNodes || []) {
      const stepId = planned?.step_id || planned?.id;
      const agent = planned?.agent;
      if (!stepId || !agent || stepId === target.id || agent === 'planner_agent') continue;
      nodes = upsertNode(nodes, stepId, agent, '已列入本次执行路径。', 'pending', ts, agent);
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
      isRollingBack = false;
    }
  } else if (ev.type === 'workflow_done') {
    nodes = nodes.map(n => ['running', 'pending'].includes(n.status) ? { ...n, status: 'done', endTime: ts, tools: n.tools.map(t => t.status === 'running' ? { ...t, status: 'done', endTime: ts } : t), intents: (n.intents || []).map(t => t.status === 'running' ? { ...t, status: 'done', endTime: ts } : t) } : n);
    currentActiveNodeId = null;
  }
  return { ...state, nodes, currentActiveNodeId, isRollingBack };
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
  });
  if (!options.historical) return state.nodes;
  const endTs = Math.max(...state.nodes.map(n => n.endTime || n.startTime || now()), now());
  return state.nodes.map(n => n.status === 'running' ? { ...n, status: 'done', endTime: n.endTime || endTs, tools: n.tools.map(t => t.status === 'running' ? { ...t, status: 'done', endTime: t.endTime || endTs } : t), intents: (n.intents || []).map(t => t.status === 'running' ? { ...t, status: 'done', endTime: t.endTime || endTs } : t) } : n);
}
