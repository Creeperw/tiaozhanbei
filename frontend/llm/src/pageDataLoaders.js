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
  long_term_plan_content: '',
  long_term_plan_stages: [],
  short_term_plan_content: '',
};

export const learningTaskToDailyTasks = (learningTask) => {
  if (!learningTask || typeof learningTask !== 'object'
    || typeof learningTask.task_content !== 'string'
    || !learningTask.task_content.trim()) {
    return [];
  }
  return [{
    key: learningTask.task_id || `learning-task-v${learningTask.version || 1}`,
    title: learningTask.task_content,
    reason: learningTask.completion_criteria
      ? `验收标准：${learningTask.completion_criteria}`
      : (learningTask.expected_output ? `学习产出：${learningTask.expected_output}` : '完成后记录本次学习结果。'),
    duration_min: Number.isInteger(learningTask.estimated_minutes) ? learningTask.estimated_minutes : 0,
    expected_output: learningTask.expected_output || '',
    status: learningTask.status || '',
    source: 'learning_task',
  }];
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

export const emptyMistakePage = {
  schema_version: '1.0',
  items: [],
  total: 0,
  offset: 0,
  limit: 50,
  has_more: false,
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

export const emptyReviewDashboard = {
  schema_version: '1.0',
  summary: { knowledge_point_count: 0, average_mastery: null, due_count: 0, active_task_count: 0, history_count: 0 },
  queue: { entries: [], due_count: 0, active_task_count: 0, awaiting_resource_count: 0 },
  mastery: [],
  mastery_history: [],
  review_states: [],
  review_tasks: [],
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

export const isReviewDashboardPayloadValid = (data) => (
  data && typeof data === 'object'
  && data.summary && typeof data.summary === 'object'
  && data.queue && Array.isArray(data.queue.entries)
  && Array.isArray(data.mastery)
  && Array.isArray(data.mastery_history)
  && Array.isArray(data.review_states)
);

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

export const isPracticeQuestionPayloadValid = (data) => (
  data && typeof data === 'object'
  && typeof data.available === 'boolean'
  && (data.question === null || (
    data.question && typeof data.question === 'object'
    && hasNonEmptyText(data.question.question_id)
    && hasNonEmptyText(data.question.question_type)
    && hasNonEmptyText(data.question.stem)
    && hasItemsArray(data.question.options)
    && hasItemsArray(data.question.kp_ids)
    && Number.isInteger(data.question.difficulty)
    && hasNonEmptyText(data.question.request_id)
  ))
);

export const isPracticeGradePayloadValid = (data) => (
  data && typeof data === 'object'
  && data.grading && typeof data.grading === 'object'
  && Number.isFinite(data.grading.score)
  && typeof data.grading.is_correct === 'boolean'
  && data.writeback && typeof data.writeback === 'object'
);

export const isMistakePagePayloadValid = (data) => (
  data && typeof data === 'object'
  && data.schema_version === '1.0'
  && hasItemsArray(data.items)
  && Number.isInteger(data.total)
  && Number.isInteger(data.offset)
  && Number.isInteger(data.limit)
  && typeof data.has_more === 'boolean'
  && data.items.every((item) => (
    item && typeof item === 'object'
    && Number.isInteger(item.mistake_id)
    && hasNonEmptyText(item.question_id)
    && hasNonEmptyText(item.stem)
    && hasItemsArray(item.kp_ids)
    && typeof item.variation_available === 'boolean'
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
    const [summaryResult, contextResult] = await Promise.allSettled([
      fetcher({ paths: planningPaths, fallback: emptyPlan, validator: isPlanPayloadValid }),
      fetcher({
        paths: ['/v1/learning-context'],
        fallback: {},
        validator: (data) => data && typeof data === 'object',
      }),
    ]);
    if (summaryResult.status !== 'fulfilled' && contextResult.status !== 'fulfilled') {
      throw summaryResult.reason || contextResult.reason || new Error('学习规划加载失败');
    }
    const summary = summaryResult.status === 'fulfilled' ? summaryResult.value : { data: emptyPlan, source: null };
    const learningContext = contextResult.status === 'fulfilled' ? contextResult.value.data : {};
    const data = {
      ...emptyPlan,
      ...summary.data,
      long_term_plan_content: String(learningContext.long_term_plan?.content || ''),
      long_term_plan_stages: Array.isArray(learningContext.long_term_plan?.stages)
        ? learningContext.long_term_plan.stages
        : [],
      short_term_plan_content: String(learningContext.short_term_plan?.content || ''),
      daily_tasks: learningTaskToDailyTasks(learningContext.learning_task),
    };
    return {
      plan: data,
      error: '',
      source: summary.source || contextResult.value?.source || null,
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

export async function generateWorkshopPaperWithAgents({ fetcher, topic, distribution }) {
  const activeDistribution = Object.fromEntries(
    Object.entries(distribution || {}).filter(([, count]) => Number.isInteger(count) && count > 0),
  );
  const questionCount = Object.values(activeDistribution).reduce((total, count) => total + count, 0);
  if (!hasNonEmptyText(topic) || questionCount < 1 || questionCount > 50) {
    return { paperId: '', result: null, error: '请填写主题，并设置 1 至 50 道题的题型分布。', source: null };
  }
  const typeLabels = {
    single_choice: '单选题',
    multiple_choice: '多选题',
    fill_blank: '填空题',
    short_answer: '简答题',
    case_quiz: '案例分析题',
  };
  const typeRequirement = Object.entries(activeDistribution)
    .map(([type, count]) => `${typeLabels[type] || type}${count}题`)
    .join('、');
  try {
    const { data, source } = await fetcher({
      paths: ['/v1/review-cards'],
      fallback: null,
      options: {
        method: 'POST',
        body: JSON.stringify({
          learner_id: 'authenticated-user',
          user_request: `请围绕“${topic.trim()}”生成一份练习试卷，共${questionCount}题，其中${typeRequirement}。完成审核后发布到学习工坊，不要在对话中展开试卷正文。`,
          available_minutes: 60,
          exam_constraints: {
            question_count: questionCount,
            question_types: Object.keys(activeDistribution).map((type) => typeLabels[type] || type),
            question_type_distribution: activeDistribution,
          },
        }),
      },
      validator: (value) => value && typeof value === 'object'
        && value.status === 'success'
        && value.task_type === 'paper_generation'
        && Array.isArray(value.ui_actions),
    });
    const action = data.ui_actions.find((item) => item?.destination === 'workshop.paper');
    const paperId = action?.params?.paper_id || action?.params?.paperId || '';
    if (!hasNonEmptyText(paperId)) {
      return { paperId: '', result: data, error: '试卷已生成，但未能发布到答题工作区。', source };
    }
    return { paperId, result: data, error: '', source };
  } catch (error) {
    return { paperId: '', result: null, error: error.message || '试卷生成失败', source: null };
  }
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

export async function setPaperTimerPaused({ fetcher, paperId, paused }) {
  if (!hasNonEmptyText(paperId)) return { paper: { ...emptyPaper }, error: '试卷 ID 不能为空', source: null };
  try {
    const action = paused ? 'pause' : 'resume';
    const { data, source } = await fetcher({
      paths: [
        `/v1/workshop/papers/${encodeURIComponent(paperId.trim())}/timer/${action}`,
        `/training/workspace/papers/${encodeURIComponent(paperId.trim())}/timer/${action}`,
      ],
      fallback: emptyPaper,
      options: { method: 'POST' },
      validator: isPaperPayloadValid,
    });
    return { paper: { ...emptyPaper, ...data }, error: '', source };
  } catch (error) {
    return { paper: { ...emptyPaper }, error: error.message || '计时状态更新失败', source: null };
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

export async function loadPracticeQuestion({ fetcher, mode = 'objective', kpId = '', topic = '', scope = 'public' }) {
  const params = new URLSearchParams({ mode, scope });
  if (hasNonEmptyText(kpId)) params.set('kp_id', kpId.trim());
  if (hasNonEmptyText(topic)) params.set('topic', topic.trim());
  const legacyParams = new URLSearchParams();
  if (hasNonEmptyText(kpId)) legacyParams.set('kp_id', kpId.trim());
  legacyParams.set('scope', scope);
  legacyParams.set('mode', mode);
  try {
    const { data, source } = await fetcher({
      paths: [`/v1/workshop/practice/next?${params.toString()}`, `/training/practice/next?${legacyParams.toString()}`],
      fallback: { available: false, kp_id: kpId || null, question: null },
      validator: isPracticeQuestionPayloadValid,
    });
    return { practice: data, error: '', source };
  } catch (error) {
    return { practice: { available: false, kp_id: kpId || null, question: null }, error: error.message || '练习题加载失败', source: null };
  }
}

export async function submitPracticeAnswer({ fetcher, question, answer }) {
  if (!question || typeof question !== 'object' || !hasNonEmptyText(answer)) {
    return { result: null, error: '请先完成作答', source: null };
  }
  try {
    const { data, source } = await fetcher({
      paths: ['/v1/workshop/practice/grade', '/training/practice/grade'],
      fallback: null,
      options: {
        method: 'POST',
        body: JSON.stringify({
          question_id: question.question_id,
          question_type: question.question_type,
          stem: question.stem,
          student_answer: answer.trim(),
          knowledge_points: question.kp_ids,
          knowledge_point_names: question.kp_names || [],
          difficulty: question.difficulty,
          request_id: question.request_id,
        }),
      },
      validator: isPracticeGradePayloadValid,
    });
    return { result: data, error: '', source };
  } catch (error) {
    return { result: null, error: error.message || '答案提交失败', source: null };
  }
}

export async function loadMistakes({ fetcher, status = 'all', offset = 0, limit = 50 }) {
  const params = new URLSearchParams({ status, offset: String(offset), limit: String(limit) });
  try {
    const { data, source } = await fetcher({
      paths: [`/v1/workshop/practice/mistakes?${params.toString()}`, `/training/workspace/mistakes?${params.toString()}`],
      fallback: emptyMistakePage,
      validator: isMistakePagePayloadValid,
    });
    return { mistakes: { ...emptyMistakePage, ...data }, error: '', source };
  } catch (error) {
    return { mistakes: { ...emptyMistakePage }, error: error.message || '错题列表加载失败', source: null };
  }
}

export async function loadReviewDashboard({ fetcher, limit = 50, historyLimit = 100 }) {
  try {
    const params = new URLSearchParams({ limit: String(limit), history_limit: String(historyLimit) });
    const { data, source } = await fetcher({
      paths: [`/v1/review-dashboard?${params.toString()}`],
      fallback: emptyReviewDashboard,
      validator: isReviewDashboardPayloadValid,
    });
    return {
      dashboard: {
        ...emptyReviewDashboard,
        ...data,
        summary: { ...emptyReviewDashboard.summary, ...(data.summary || {}) },
        queue: { ...emptyReviewDashboard.queue, ...(data.queue || {}) },
      },
      error: '',
      source,
    };
  } catch (error) {
    return { dashboard: { ...emptyReviewDashboard }, error: error.message || '复习数据加载失败', source: null };
  }
}

export async function submitMistakeAnswerContext({ fetcher, mistakeId, answerState, reason, notes = '' }) {
  if (!Number.isInteger(mistakeId) || mistakeId <= 0 || !hasNonEmptyText(answerState) || !hasNonEmptyText(reason)) {
    return { mistake: null, error: '请完整填写当时的作答情况', source: null };
  }
  try {
    const { data, source } = await fetcher({
      paths: [`/v1/workshop/practice/mistakes/${mistakeId}/answer-context`],
      fallback: null,
      options: {
        method: 'POST',
        body: JSON.stringify({ answer_state: answerState, reason, notes: notes.trim() }),
      },
      validator: (value) => value && typeof value === 'object' && value.mistake?.mistake_id === mistakeId,
    });
    return { mistake: data.mistake, error: '', source };
  } catch (error) {
    return { mistake: null, error: error.message || '作答情况保存失败', source: null };
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
