export const AGENT_ROLES = Object.freeze([
  { key: 'planner', label: '任务规划', description: '理解需求并安排执行路径。' },
  { key: 'memory', label: '记忆管理', description: '读取会话、偏好和有效历史。' },
  { key: 'diagnosis', label: '学情诊断', description: '判断掌握状态和学习节奏。' },
  { key: 'knowledge', label: '知识库管理', description: '检索教材、题目、视频和证据。' },
  { key: 'expert', label: '专家', description: '生成计划、讲解、题目或试卷。' },
  { key: 'audit', label: '审核裁判', description: '检查事实、质量与发布条件。' },
]);

const PAPER_STAGE_LABELS = Object.freeze({
  paper_blueprint: '试卷蓝图设计',
  question_pool: '题目与证据检索',
  paper_assembly: '试卷编排',
  audit: '试卷质量审核',
});

const PAPER_STAGE_IDS = new Set([
  'paper_blueprint',
  'question_pool',
  'paper_assembly',
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
  waiting_human_review: '等待人工复核',
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

export const STATUS_PRIORITY = Object.freeze({
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
    if (!String(tool?.name || '').trim()) return false;
    // Blank repeated tool calls carry no information; keep only calls with a
    // meaningful argument or a returned snippet.
    if (!hasMeaningfulValue(tool.args) && !hasMeaningfulValue(tool.resultSnippet)) return false;
    const signature = [
      tool?.name || '',
      tool?.args?.resourceType || tool?.args?.resource_type || '',
    ].join(':');
    if (seen.has(signature)) return false;
    seen.add(signature);
    return true;
  });
}

/**
 * A single model boundary produces three runtime events sharing one callId:
 * model_input (structured agent context), model_transport (the real HTTP
 * messages sent to the provider plus the raw response text) and model_output
 * (the parsed result).  Merge them per callId so the sidebar can prefer the
 * real natural-language request/response over the structured payload.
 */
export function mergeModelCalls(modelCalls = []) {
  const grouped = new Map();
  for (const call of modelCalls.filter((item) => (
    item?.callId
    || hasMeaningfulValue(item?.input)
    || hasMeaningfulValue(item?.output)
    || hasMeaningfulValue(item?.requestPayload)
    || hasMeaningfulValue(item?.responseText)
  ))) {
    const key = call.callId || call.id || `${call.agent || 'model'}:${call.ts || 0}`;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(call);
  }
  const merged = [];
  for (const group of grouped.values()) {
    const transport = group.find(
      (call) => call.kind === 'transport'
        && (hasMeaningfulValue(call.requestPayload) || hasMeaningfulValue(call.responseText))
    );
    const input = group.find((call) => call.kind === 'input');
    const output = group.find((call) => call.kind === 'output');
    const representative = transport || output || input || group.at(-1);
    merged.push({
      ...representative,
      kind: output ? 'output' : transport ? 'transport' : representative.kind,
      input: input?.input,
      output: output?.output,
      requestPayload: transport?.requestPayload,
      responseText: transport?.responseText,
    });
  }
  return merged;
}

function displayStatus(nodes) {
  if (!nodes.length) return 'skipped';
  return nodes.reduce((selected, node) => (
    (STATUS_PRIORITY[node.status] ?? 0) > (STATUS_PRIORITY[selected] ?? 0)
      ? node.status
      : selected
  ), nodes[0]?.status || 'pending');
}

function collectContextSignals(nodes = []) {
  const summaries = nodes
    .map((node) => node.inputSummary)
    .filter((summary) => summary && typeof summary === 'object');
  const signals = [];
  if (summaries.some((summary) => summary.user_request || summary.original_user_request)) {
    signals.push('当前消息');
  }
  if (summaries.some((summary) => summary.has_user_profile)) signals.push('用户画像');
  if (summaries.some((summary) => summary.has_compressed_history)) signals.push('压缩历史对话');
  if (summaries.some((summary) => Number(summary.recent_message_count || 0) > 0)) signals.push('近期历史对话');
  if (summaries.some((summary) => summary.has_external_information)) signals.push('外部信息');
  if (summaries.some((summary) => summary.has_learning_monitoring)) signals.push('学习状态');
  if (summaries.some((summary) => summary.has_existing_long_term_plan)) signals.push('长期规划');
  if (summaries.some((summary) => summary.has_existing_short_term_plan)) signals.push('短期计划');
  return unique(signals);
}

export function buildAgentPresentation(nodes = []) {
  // Each execution node becomes its own presentation seat, ordered by when it
  // actually started. The same agent invoked multiple times therefore shows
  // up as separate stages in real execution order instead of being merged
  // into one crowded seat. Internal/system-only nodes are not user-facing.
  // `audit` is a shared step id across knowledge explanations, plans and
  // papers. Apply paper-specific labels only when the graph contains an
  // unambiguous paper stage; otherwise retain the formal role label.
  const isPaperWorkflow = nodes.some((node) => (
    PAPER_STAGE_IDS.has(node.stepId) || PAPER_STAGE_IDS.has(node.id)
  ));
  return [...nodes]
    .sort((left, right) => (left.startTime || 0) - (right.startTime || 0))
    .filter((node) => resolveAgentRole(node.agent || node.name) !== 'system')
    .map((node) => {
      const roleKey = resolveAgentRole(node.agent || node.name);
      const role = AGENT_ROLES.find((item) => item.key === roleKey)
        || { key: roleKey, label: node.agent || roleKey, description: '' };
      const stageLabel = isPaperWorkflow
        ? PAPER_STAGE_LABELS[node.stepId] || PAPER_STAGE_LABELS[node.id]
        : undefined;
      const roleNodes = [node];
      const status = displayStatus(roleNodes);
      const details = unique(roleNodes.flatMap((item) => (
        item.logs || []
      )).map(sanitizeAgentLog));
      const tools = meaningfulTools(roleNodes.flatMap((item) => item.tools || []));
      const modelCalls = mergeModelCalls(roleNodes.flatMap((item) => item.modelCalls || []));
      const retrievals = roleNodes.flatMap((item) => (
        (item.retrievals || []).map((retrieval) => ({
          ...retrieval,
          kp_query: String(retrieval.kp_query || '').trim(),
          question_query: String(retrieval.question_query || '').trim(),
        }))
      )).filter((retrieval) => retrieval.kp_query || retrieval.question_query);
      const auditEvents = roleNodes.flatMap((item) => item.auditEvents || []);
      const activities = roleNodes.flatMap((item) => item.activities || []);
      const reasoning = roleNodes
        .map((item) => String(item.reasoning || '').trim())
        .filter(Boolean)
        .join('\n\n');
      const reasoningStreaming = roleNodes.some((item) => item.reasoningStreaming);
      const workingOutput = roleNodes
        .map((item) => String(item.workingOutput || '').trim())
        .filter(Boolean)
        .join('\n\n');
      const workingOutputStreaming = roleNodes.some((item) => item.workingOutputStreaming);
      const formalOutput = roleNodes
        .map((item) => String(item.formalOutput || '').trim())
        .filter(Boolean)
        .join('\n\n');
      const formalOutputStreaming = roleNodes.some((item) => item.formalOutputStreaming);
      const formalOutputReady = roleNodes.some((item) => item.formalOutputReady) || Boolean(formalOutput);
      // Old traces have only publicOutput. Treat those persisted allow-listed
      // stage artifacts as formal output rather than as live working prose.
      const legacyPublicOutput = roleNodes
        .filter((item) => !item.workingOutput && !item.formalOutput)
        .map((item) => String(item.publicOutput || '').trim())
        .filter(Boolean)
        .join('\n\n');
      const resolvedFormalOutput = formalOutput || legacyPublicOutput;
      const publicOutput = resolvedFormalOutput || workingOutput;
      const publicOutputStreaming = formalOutputStreaming || workingOutputStreaming;
      const contextSignals = collectContextSignals(roleNodes);
      const dependencies = unique(roleNodes.flatMap((item) => item.dependsOn || []));
      const startedAt = roleNodes.length
        ? Math.min(...roleNodes.map((item) => item.startTime || Number.MAX_SAFE_INTEGER))
        : null;
      const endedValues = roleNodes.map((item) => item.endTime).filter(Number.isFinite);
      const endedAt = endedValues.length ? Math.max(...endedValues) : null;

      return {
        ...role,
        label: stageLabel || role.label,
        description: stageLabel ? `${stageLabel}阶段由${role.label}负责。` : role.description,
        status,
        statusLabel: agentStatusLabel(status),
        summary: details.at(-1) || (status === 'skipped' ? '本次任务不需要这个智能体参与。' : role.description),
        details,
        tools,
        modelCalls,
        retrievals,
        auditEvents,
        activities,
        reasoning,
        reasoningStreaming,
        publicOutput,
        publicOutputStreaming,
        workingOutput,
        workingOutputStreaming,
        formalOutput: resolvedFormalOutput,
        formalOutputStreaming,
        formalOutputReady: formalOutputReady || Boolean(legacyPublicOutput),
        contextSignals,
        dependencies,
        nodes: roleNodes,
        startedAt: startedAt === Number.MAX_SAFE_INTEGER ? null : startedAt,
        endedAt,
      };
    });
}
