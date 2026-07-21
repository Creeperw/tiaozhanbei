export const AGENT_ROLES = Object.freeze([
  { key: 'planner', label: '任务规划', description: '理解需求并安排执行路径。' },
  { key: 'memory', label: '记忆管理', description: '读取会话、偏好和有效历史。' },
  { key: 'diagnosis', label: '学情诊断', description: '判断掌握状态和学习节奏。' },
  { key: 'knowledge', label: '知识库管理', description: '检索教材、题目、视频和证据。' },
  { key: 'expert', label: '专家', description: '生成计划、讲解、题目或试卷。' },
  { key: 'audit', label: '审核裁判', description: '检查事实、质量与发布条件。' },
]);

const ROLE_BY_RUNTIME_NAME = Object.freeze({
  planner_agent: 'planner',
  route_agent: 'planner',
  Planner: 'planner',
  memory_agent: 'memory',
  InfoManager: 'memory',
  diagnosis_agent: 'diagnosis',
  default_route_resolver: 'diagnosis',
  learning_plan_service: 'diagnosis',
  knowledge_base_agent: 'knowledge',
  expert_agent: 'expert',
  paper_blueprint_agent: 'expert',
  paper_assembly_agent: 'expert',
  knowledge_explanation_agent: 'expert',
  Executor: 'expert',
  audit_agent: 'audit',
  Feedback: 'audit',
  review_scheduler: 'system',
});

const STATUS_LABELS = Object.freeze({
  idle: '等待执行',
  pending: '等待执行',
  running: '执行中',
  done: '已完成',
  success: '已完成',
  completed: '已完成',
  error: '执行失败',
  failed: '执行失败',
  retrying: '正在重试',
  rollingBack: '正在复核',
  waiting_human_review: '等待补充',
  interrupted: '等待补充',
  archived: '已重新生成',
  skipped: '本次无需参与',
});

const LOG_REPLACEMENTS = [
  [/^planner_agent开始处理。?$/, '开始理解需求并安排执行路径。'],
  [/^planner_agent处理完成。?$/, '需求分析与执行路径安排完成。'],
  [/^memory_agent开始处理。?$/, '开始读取本次任务相关的会话与偏好。'],
  [/^memory_agent处理完成。?$/, '相关会话与偏好读取完成。'],
  [/^(diagnosis_agent|default_route_resolver|learning_plan_service)开始处理。?$/, '开始分析学习状态与计划衔接。'],
  [/^(diagnosis_agent|default_route_resolver|learning_plan_service)处理完成。?$/, '学习状态与计划衔接分析完成。'],
  [/^knowledge_base_agent开始处理。?$/, '开始查找教材、题目与相关资料。'],
  [/^knowledge_base_agent处理完成。?$/, '所需学习资料查找完成。'],
  [/^(expert_agent|paper_blueprint_agent|paper_assembly_agent|knowledge_explanation_agent)开始处理。?$/, '开始生成本次学习内容。'],
  [/^(expert_agent|paper_blueprint_agent|paper_assembly_agent|knowledge_explanation_agent)处理完成。?$/, '本次学习内容生成完成。'],
  [/^audit_agent开始处理。?$/, '开始检查内容质量与事实依据。'],
  [/^audit_agent处理完成。?$/, '内容质量检查完成。'],
  [/^发起工具调用。?$/, ''],
  [/^工具返回：?.*$/, ''],
];

const STATUS_PRIORITY = Object.freeze({
  error: 7,
  failed: 7,
  waiting_human_review: 6,
  interrupted: 6,
  retrying: 5,
  rollingBack: 4,
  running: 3,
  done: 2,
  success: 2,
  completed: 2,
  archived: 1,
  pending: 0,
  idle: 0,
  skipped: -1,
});

export function resolveAgentRole(agent = '') {
  return ROLE_BY_RUNTIME_NAME[String(agent || '').trim()] || 'system';
}

export function agentStatusLabel(status = 'idle') {
  return STATUS_LABELS[status] || STATUS_LABELS.idle;
}

export function sanitizeAgentLog(log = '') {
  const normalized = String(log || '').replace(/\s+/g, ' ').trim();
  if (!normalized) return '';
  for (const [pattern, replacement] of LOG_REPLACEMENTS) {
    if (pattern.test(normalized)) return replacement;
  }
  return normalized;
}

function unique(values = []) {
  return [...new Set(values.filter(Boolean))];
}

function hasMeaningfulValue(value) {
  if (value == null) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.some(hasMeaningfulValue);
  if (typeof value === 'object') return Object.values(value).some(hasMeaningfulValue);
  return true;
}

function meaningfulTools(tools = []) {
  const seen = new Set();
  return tools.filter((tool) => {
    if (!hasMeaningfulValue(tool?.args) && !hasMeaningfulValue(tool?.resultSnippet)) return false;
    const signature = JSON.stringify([tool?.name || '', tool?.args || {}, tool?.resultSnippet || '']);
    if (seen.has(signature)) return false;
    seen.add(signature);
    return true;
  });
}

function displayStatus(nodes) {
  if (!nodes.length) return 'skipped';
  return nodes.reduce((selected, node) => (
    (STATUS_PRIORITY[node.status] ?? 0) > (STATUS_PRIORITY[selected] ?? 0)
      ? node.status
      : selected
  ), nodes[0]?.status || 'pending');
}

export function buildAgentPresentation(nodes = []) {
  const grouped = new Map(AGENT_ROLES.map((role) => [role.key, []]));
  [...nodes]
    .sort((left, right) => (left.startTime || 0) - (right.startTime || 0))
    .forEach((node) => {
      const role = resolveAgentRole(node.agent || node.name);
      if (grouped.has(role)) grouped.get(role).push(node);
    });

  return AGENT_ROLES.map((role) => {
    const roleNodes = grouped.get(role.key) || [];
    const status = displayStatus(roleNodes);
    const details = unique(roleNodes.flatMap((node) => (
      node.logs || []
    )).map(sanitizeAgentLog));
    const tools = meaningfulTools(roleNodes.flatMap((node) => node.tools || []));
    const startedAt = roleNodes.length
      ? Math.min(...roleNodes.map((node) => node.startTime || Number.MAX_SAFE_INTEGER))
      : null;
    const endedValues = roleNodes.map((node) => node.endTime).filter(Number.isFinite);
    const endedAt = endedValues.length ? Math.max(...endedValues) : null;

    return {
      ...role,
      status,
      statusLabel: agentStatusLabel(status),
      summary: details.at(-1) || (status === 'skipped' ? '本次任务不需要这个智能体参与。' : role.description),
      details,
      tools,
      nodes: roleNodes,
      startedAt: startedAt === Number.MAX_SAFE_INTEGER ? null : startedAt,
      endedAt,
    };
  });
}
