const planningPaths = ['/agent/plan/summary', '/training/plan/summary'];
const reportPaths = ['/agent/diagnosis/report', '/training/report'];
const briefPath = ['/agent/context/brief'];
const tracePath = ['/agent/trace/recent'];

export const emptyPlan = {
  plan_summary: {},
  weekly_plan: { evidence: [] },
  daily_tasks: [],
  constraints: {},
  agent_trace: [],
};

export const emptyReport = {
  learner_overview: {},
  mastery_radar: [],
  weak_points: [],
  mistake_summary: {},
  resource_match: {},
  t_stage: { evidence: [] },
  next_actions: [],
  agent_trace: [],
};

export const emptyTrainingWorkspace = {
  schema_version: '1.0',
  default_module: '',
  default_task_type: '',
  modules: [],
};

export const emptyKnowledgeCardPage = {
  schema_version: '1.0',
  items: [],
  total: 0,
  offset: 0,
  limit: 50,
};

export const emptyTrainingTaskResult = {
  task_id: '',
  task_type: '',
  status: '',
  title: '',
  summary: '',
  artifact: {},
  evidence_pack: {},
  audit: {},
  trace: [],
  learning_updates: {},
  next_actions: [],
};

export const emptyCaseSession = {
  session_id: '',
  case_version_id: '',
  title: '',
  mode: '',
  status: '',
  learner_messages: 0,
  scoring_enabled: true,
  help_used: false,
  visible_context: {},
  messages: [],
};

export const emptyCaseTypes = {
  types: [],
  modes: [],
};

export const emptyVariationSources = {
  items: [],
};

export const emptyPaper = {
  paper_id: '',
  title: '',
  status: '',
  items: [],
  timing: null,
};

export const emptyPaperPage = {
  schema_version: '1.0',
  items: [],
  total: 0,
  offset: 0,
  limit: 50,
};

const createEmptyTrainingWorkspace = () => ({
  ...emptyTrainingWorkspace,
  modules: [],
});

const createEmptyTrainingTaskResult = () => ({
  ...emptyTrainingTaskResult,
  artifact: {},
  evidence_pack: {},
  audit: {},
  trace: [],
  learning_updates: {},
  next_actions: [],
});

const hasItemsArray = (value) => Array.isArray(value);
const hasNonEmptyText = (value) => typeof value === 'string' && value.trim().length > 0;

export const isPlanPayloadValid = (data) => {
  if (!data || typeof data !== 'object') {
    return false;
  }
  if (!data.plan_summary || typeof data.plan_summary !== 'object') {
    return false;
  }
  if (!data.weekly_plan || typeof data.weekly_plan !== 'object') {
    return false;
  }
  if (!hasItemsArray(data.daily_tasks)) {
    return false;
  }
  return hasNonEmptyText(data.plan_summary.goal)
    || hasNonEmptyText(data.weekly_plan.focus)
    || data.daily_tasks.length > 0;
};

export const isReportPayloadValid = (data) => {
  if (!data || typeof data !== 'object') {
    return false;
  }
  if (!data.learner_overview || typeof data.learner_overview !== 'object') {
    return false;
  }
  if (!hasItemsArray(data.mastery_radar) || !hasItemsArray(data.weak_points) || !hasItemsArray(data.next_actions)) {
    return false;
  }
  return hasNonEmptyText(data.learner_overview.goal)
    || hasNonEmptyText(data.learner_overview.current_focus)
    || data.mastery_radar.length > 0
    || data.weak_points.length > 0
    || data.next_actions.length > 0;
};

export const isTrainingModulesPayloadValid = (data) => {
  const defaultKey = data?.default_module || data?.default_task_type;
  if (!data || typeof data !== 'object' || !hasNonEmptyText(defaultKey) || !hasItemsArray(data.modules)) {
    return false;
  }
  return data.modules.length > 0
    && data.modules.some((module) => module?.key === defaultKey)
    && data.modules.every((module) => (
    module
    && typeof module === 'object'
    && hasNonEmptyText(module.key)
    && hasNonEmptyText(module.label)
    && hasNonEmptyText(module.description)
    && typeof module.enabled === 'boolean'
    && typeof module.recommended === 'boolean'
    && hasNonEmptyText(module.badge)
  ));
};

export const isTrainingTaskResultApproved = (taskResult) => {
  const status = taskResult?.status;
  const decision = taskResult?.audit?.decision;
  return hasNonEmptyText(status)
    && hasNonEmptyText(decision)
    && status.trim().toLowerCase() === 'completed'
    && decision.trim().toLowerCase() === 'pass';
};

export const isTrainingTaskResultValid = (data) => {
  if (!data || typeof data !== 'object') {
    return false;
  }
  if (!hasNonEmptyText(data.task_id)
    || !hasNonEmptyText(data.task_type)
    || !hasNonEmptyText(data.status)
    || !hasNonEmptyText(data.title)
    || !hasNonEmptyText(data.summary)) {
    return false;
  }
  if (!data.artifact || typeof data.artifact !== 'object'
    || !hasNonEmptyText(data.artifact.artifact_type)
    || !hasNonEmptyText(data.artifact.title)
    || !Object.hasOwn(data.artifact, 'content')) {
    return false;
  }
  return data.evidence_pack && typeof data.evidence_pack === 'object'
    && data.audit && typeof data.audit === 'object'
    && hasItemsArray(data.trace)
    && data.learning_updates && typeof data.learning_updates === 'object'
    && hasItemsArray(data.next_actions);
};

export const isCaseTypesPayloadValid = (data) => (
  data && typeof data === 'object'
  && hasItemsArray(data.types)
  && hasItemsArray(data.modes)
  && data.types.every(hasNonEmptyText)
  && data.modes.every((mode) => mode === 'full' || mode === 'diagnosis_only')
);

export const isCaseSessionPayloadValid = (data) => (
  data && typeof data === 'object'
  && hasNonEmptyText(data.session_id)
  && hasNonEmptyText(data.case_version_id)
  && hasNonEmptyText(data.title)
  && (data.mode === 'full' || data.mode === 'diagnosis_only')
  && hasNonEmptyText(data.status)
  && Number.isInteger(data.learner_messages)
  && typeof data.scoring_enabled === 'boolean'
  && typeof data.help_used === 'boolean'
  && data.visible_context && typeof data.visible_context === 'object'
  && hasItemsArray(data.messages)
  && data.messages.every((message) => (
    message && typeof message === 'object'
    && (message.role === 'learner' || message.role === 'patient')
    && Number.isInteger(message.sequence)
    && hasNonEmptyText(message.content)
  ))
);

export const isVariationSourcesPayloadValid = (data) => (
  data && typeof data === 'object'
  && hasItemsArray(data.items)
  && data.items.every((item) => (
    item && typeof item === 'object'
    && Number.isInteger(item.mistake_id) && item.mistake_id > 0
    && hasNonEmptyText(item.question_version_id)
    && hasNonEmptyText(item.stem)
    && hasNonEmptyText(item.question_type)
    && Number.isInteger(item.difficulty)
    && hasItemsArray(item.kp_ids) && item.kp_ids.every(hasNonEmptyText)
  ))
);

export const isPaperPayloadValid = (data) => (
  data && typeof data === 'object'
  && hasNonEmptyText(data.paper_id)
  && hasNonEmptyText(data.title)
  && hasNonEmptyText(data.status)
  && hasItemsArray(data.items)
  && data.items.every((item) => (
    item && typeof item === 'object'
    && hasNonEmptyText(item.paper_item_id)
    && Number.isInteger(item.position)
    && hasNonEmptyText(item.question_version_id)
    && hasNonEmptyText(item.question_type)
    && hasNonEmptyText(item.stem)
    && hasItemsArray(item.options)
    && hasItemsArray(item.kp_ids)
    && Number.isInteger(item.difficulty)
    && typeof item.answer === 'string'
  ))
);

export const isPaperPagePayloadValid = (data) => (
  data && typeof data === 'object'
  && data.schema_version === '1.0'
  && hasItemsArray(data.items)
  && Number.isInteger(data.total)
  && data.items.every((item) => (
    item && typeof item === 'object'
    && hasNonEmptyText(item.paper_id)
    && hasNonEmptyText(item.title)
    && hasNonEmptyText(item.status)
    && Number.isInteger(item.duration_minutes)
  ))
);

export const isKnowledgeCardPageValid = (data) => (
  data && typeof data === 'object'
  && data.schema_version === '1.0'
  && hasItemsArray(data.items)
  && Number.isInteger(data.total)
  && data.items.every((item) => (
    item && typeof item === 'object'
    && hasNonEmptyText(item.card_id)
    && hasNonEmptyText(item.kp_id)
    && hasNonEmptyText(item.title)
    && item.learning_status === 'learned'
  ))
);

export const isKnowledgeCardDetailValid = (data) => (
  data && typeof data === 'object'
  && data.schema_version === '1.0'
  && hasNonEmptyText(data.card_id)
  && hasNonEmptyText(data.kp_id)
  && data.resource_bundle && typeof data.resource_bundle === 'object'
  && data.resource_bundle.schema_version === '1.0'
);

export const isPaperSubmissionPayloadValid = (data) => (
  data && typeof data === 'object'
  && hasNonEmptyText(data.paper_id)
  && data.status === 'completed'
  && Number.isFinite(data.score)
  && Number.isFinite(data.max_score)
  && hasItemsArray(data.items)
);

export async function loadPlanningData({ fetcher }) {
  try {
    const { data, source } = await fetcher({
      paths: planningPaths,
      fallback: emptyPlan,
      validator: isPlanPayloadValid,
    });
    return {
      plan: { ...emptyPlan, ...data },
      error: '',
      source,
    };
  } catch (error) {
    return {
      plan: emptyPlan,
      error: error.message || '学习规划加载失败',
      source: null,
    };
  }
}

export async function loadReportsData({ fetcher }) {
  try {
    const { data, source } = await fetcher({
      paths: reportPaths,
      fallback: emptyReport,
      validator: isReportPayloadValid,
    });
    return {
      report: { ...emptyReport, ...data },
      error: '',
      source,
    };
  } catch (error) {
    return {
      report: emptyReport,
      error: error.message || '学情报告加载失败',
      source: null,
    };
  }
}

async function requestTrainingTask({ fetcher, paths, options }) {
  try {
    const { data, source } = await fetcher({
      paths,
      fallback: createEmptyTrainingTaskResult(),
      options,
      validator: isTrainingTaskResultValid,
    });
    return {
      taskResult: { ...emptyTrainingTaskResult, ...data },
      error: '',
      source,
    };
  } catch (error) {
    return {
      taskResult: createEmptyTrainingTaskResult(),
      error: error.message || '训练任务请求失败',
      source: null,
    };
  }
}

export async function loadTrainingWorkspaceModules({ fetcher }) {
  try {
    const { data, source } = await fetcher({
      paths: ['/v1/workshop', '/training/workspace/modules'],
      fallback: createEmptyTrainingWorkspace(),
      validator: isTrainingModulesPayloadValid,
    });
    return {
      workspace: {
        ...emptyTrainingWorkspace,
        ...data,
        default_task_type: data.default_task_type || data.default_module,
      },
      error: '',
      source,
    };
  } catch (error) {
    return {
      workspace: createEmptyTrainingWorkspace(),
      error: error.message || '训练工坊模块加载失败',
      source: null,
    };
  }
}

export async function loadKnowledgeCards({ fetcher, offset = 0, limit = 50 }) {
  try {
    const { data, source } = await fetcher({
      paths: [`/v1/workshop/knowledge-cards?offset=${offset}&limit=${limit}`],
      fallback: emptyKnowledgeCardPage,
      validator: isKnowledgeCardPageValid,
    });
    return { cards: { ...emptyKnowledgeCardPage, ...data }, error: '', source };
  } catch (error) {
    return { cards: { ...emptyKnowledgeCardPage }, error: error.message || '知识卡片加载失败', source: null };
  }
}

export async function loadKnowledgeCard({ fetcher, cardId }) {
  if (!hasNonEmptyText(cardId)) return { card: null, error: '知识卡 ID 不能为空', source: null };
  try {
    const { data, source } = await fetcher({
      paths: [`/v1/workshop/knowledge-cards/${encodeURIComponent(cardId.trim())}`],
      fallback: null,
      validator: isKnowledgeCardDetailValid,
    });
    return { card: data, error: '', source };
  } catch (error) {
    return { card: null, error: error.message || '知识卡片加载失败', source: null };
  }
}

export async function resolveKnowledgeCard({ fetcher, kpId, sourceExecutionId = '' }) {
  if (!hasNonEmptyText(kpId)) return { card: null, error: '知识点 ID 不能为空', source: null };
  try {
    const { data, source } = await fetcher({
      paths: ['/v1/workshop/knowledge-cards/resolve'],
      fallback: null,
      options: {
        method: 'POST',
        body: JSON.stringify({ kp_id: kpId.trim(), source_execution_id: sourceExecutionId }),
      },
      validator: isKnowledgeCardDetailValid,
    });
    return { card: data, error: '', source };
  } catch (error) {
    return { card: null, error: error.message || '知识卡片生成失败', source: null };
  }
}

export async function submitTrainingWorkspaceTask({ fetcher, task }) {
  return requestTrainingTask({
    fetcher,
    paths: ['/training/workspace/tasks'],
    options: {
      method: 'POST',
      body: JSON.stringify(task),
    },
  });
}

export async function loadTrainingWorkspaceTask({ fetcher, taskId }) {
  if (!hasNonEmptyText(taskId)) {
    return {
      taskResult: createEmptyTrainingTaskResult(),
      error: '训练任务 ID 不能为空',
      source: null,
    };
  }
  return requestTrainingTask({
    fetcher,
    paths: [`/training/workspace/tasks/${encodeURIComponent(taskId.trim())}`],
  });
}

export async function loadPracticeAgentContext({ fetcher }) {
  const [briefResult, traceResult] = await Promise.allSettled([
    fetcher({ paths: briefPath, fallback: null }),
    fetcher({ paths: tracePath, fallback: { items: [] } }),
  ]);

  const contextBrief = briefResult.status === 'fulfilled' ? briefResult.value.data : null;
  const traceData = traceResult.status === 'fulfilled' ? traceResult.value.data : { items: [] };

  return {
    contextBrief,
    recentTrace: Array.isArray(traceData?.items) ? traceData.items : [],
  };
}

async function requestCaseSession({ fetcher, paths, options }) {
  try {
    const { data, source } = await fetcher({
      paths,
      fallback: emptyCaseSession,
      options,
      validator: isCaseSessionPayloadValid,
    });
    return { session: { ...emptyCaseSession, ...data }, error: '', source };
  } catch (error) {
    return { session: { ...emptyCaseSession }, error: error.message || '案例训练请求失败', source: null };
  }
}

async function requestCaseAction({ fetcher, paths, options }) {
  try {
    const { data, source } = await fetcher({ paths, fallback: {}, options });
    return { result: data, error: '', source };
  } catch (error) {
    return { result: {}, error: error.message || '案例训练请求失败', source: null };
  }
}

export async function loadPaper({ fetcher, paperId }) {
  if (!hasNonEmptyText(paperId)) return { paper: { ...emptyPaper }, error: '试卷 ID 不能为空', source: null };
  try {
    const { data, source } = await fetcher({
      paths: [
        `/v1/workshop/papers/${encodeURIComponent(paperId.trim())}`,
        `/training/workspace/papers/${encodeURIComponent(paperId.trim())}`,
      ],
      fallback: emptyPaper,
      validator: isPaperPayloadValid,
    });
    return { paper: { ...emptyPaper, ...data }, error: '', source };
  } catch (error) {
    return { paper: { ...emptyPaper }, error: error.message || '试卷加载失败', source: null };
  }
}

export async function loadPapers({ fetcher, offset = 0, limit = 50 }) {
  try {
    const { data, source } = await fetcher({
      paths: [`/v1/workshop/papers?offset=${offset}&limit=${limit}`],
      fallback: emptyPaperPage,
      validator: isPaperPagePayloadValid,
    });
    return { papers: { ...emptyPaperPage, ...data }, error: '', source };
  } catch (error) {
    return { papers: { ...emptyPaperPage }, error: error.message || '试卷列表加载失败', source: null };
  }
}

export async function savePaperAnswers({ fetcher, paperId, answers }) {
  if (!hasNonEmptyText(paperId) || !answers || typeof answers !== 'object' || Array.isArray(answers)) return { paper: { ...emptyPaper }, error: '试卷答案无效', source: null };
  try {
    const { data, source } = await fetcher({
      paths: [
        `/v1/workshop/papers/${encodeURIComponent(paperId.trim())}/answers`,
        `/training/workspace/papers/${encodeURIComponent(paperId.trim())}/answers`,
      ],
      fallback: emptyPaper,
      options: { method: 'PUT', body: JSON.stringify({ answers }) },
      validator: isPaperPayloadValid,
    });
    return { paper: { ...emptyPaper, ...data }, error: '', source };
  } catch (error) {
    return { paper: { ...emptyPaper }, error: error.message || '答案保存失败', source: null };
  }
}

export async function submitPaper({ fetcher, paperId, requestId }) {
  if (!hasNonEmptyText(paperId) || !hasNonEmptyText(requestId)) return { result: {}, error: '交卷请求无效', source: null };
  try {
    const { data, source } = await fetcher({
      paths: [
        `/v1/workshop/papers/${encodeURIComponent(paperId.trim())}/submit`,
        `/training/workspace/papers/${encodeURIComponent(paperId.trim())}/submit`,
      ],
      fallback: {},
      options: { method: 'POST', body: JSON.stringify({ request_id: requestId.trim() }) },
      validator: isPaperSubmissionPayloadValid,
    });
    return { result: data, error: '', source };
  } catch (error) {
    return { result: {}, error: error.message || '交卷失败', source: null };
  }
}

export async function loadVariationSources({ fetcher }) {
  try {
    const { data, source } = await fetcher({
      paths: ['/training/workspace/mistake-variations/sources'],
      fallback: emptyVariationSources,
      validator: isVariationSourcesPayloadValid,
    });
    return { sources: { ...emptyVariationSources, ...data }, error: '', source };
  } catch (error) {
    return { sources: { ...emptyVariationSources }, error: error.message || '错题列表加载失败', source: null };
  }
}

export async function loadCaseTypes({ fetcher }) {
  try {
    const { data, source } = await fetcher({
      paths: ['/training/cases/types'],
      fallback: emptyCaseTypes,
      validator: isCaseTypesPayloadValid,
    });
    return { caseTypes: { ...emptyCaseTypes, ...data }, error: '', source };
  } catch (error) {
    return { caseTypes: { ...emptyCaseTypes }, error: error.message || '案例类型加载失败', source: null };
  }
}

export async function startCaseSession({ fetcher, selection = 'random', caseType, caseVersionId, mode = 'full' }) {
  const body = { selection, mode };
  if (selection === 'by_type' && hasNonEmptyText(caseType)) body.case_type = caseType.trim();
  if (selection === 'by_version' && hasNonEmptyText(caseVersionId)) body.case_version_id = caseVersionId.trim();
  if ((selection === 'by_type' && !body.case_type)
    || (selection === 'by_version' && !body.case_version_id)
    || !['random', 'by_type', 'by_version'].includes(selection)
    || !['full', 'diagnosis_only'].includes(mode)) {
    return { session: { ...emptyCaseSession }, error: '案例训练参数无效', source: null };
  }
  return requestCaseSession({
    fetcher,
    paths: ['/training/case-sessions'],
    options: { method: 'POST', body: JSON.stringify(body) },
  });
}

export async function loadCaseSession({ fetcher, sessionId }) {
  if (!hasNonEmptyText(sessionId)) return { session: { ...emptyCaseSession }, error: '案例会话 ID 不能为空', source: null };
  return requestCaseSession({
    fetcher,
    paths: [`/training/case-sessions/${encodeURIComponent(sessionId.trim())}`],
  });
}

export async function sendCaseMessage({ fetcher, sessionId, message }) {
  if (!hasNonEmptyText(sessionId) || !hasNonEmptyText(message)) return { result: {}, error: '请填写问诊内容', source: null };
  return requestCaseAction({
    fetcher,
    paths: [`/training/case-sessions/${encodeURIComponent(sessionId.trim())}/messages`],
    options: { method: 'POST', body: JSON.stringify({ message: message.trim() }) },
  });
}

export async function requestCaseHelp({ fetcher, sessionId, helpType }) {
  if (!hasNonEmptyText(sessionId) || !['hint', 'answer'].includes(helpType)) return { result: {}, error: '案例帮助参数无效', source: null };
  return requestCaseAction({
    fetcher,
    paths: [`/training/case-sessions/${encodeURIComponent(sessionId.trim())}/help`],
    options: { method: 'POST', body: JSON.stringify({ help_type: helpType }) },
  });
}

export async function submitCaseSession({ fetcher, sessionId, answer }) {
  if (!hasNonEmptyText(sessionId) || !answer || typeof answer !== 'object' || Array.isArray(answer)) return { result: {}, error: '请填写案例答案', source: null };
  return requestCaseAction({
    fetcher,
    paths: [`/training/case-sessions/${encodeURIComponent(sessionId.trim())}/submit`],
    options: { method: 'POST', body: JSON.stringify({ answer }) },
  });
}
