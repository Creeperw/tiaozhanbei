import test from 'node:test';
import assert from 'node:assert/strict';

import { fetchJsonWithAuthFallback } from './utils/api.js';
import {
  emptyPlan,
  emptyReport,
  emptyTrainingTaskResult,
  emptyTrainingWorkspace,
  isCaseSessionPayloadValid,
  isCaseTypesPayloadValid,
  loadCaseSession,
  loadCaseTypes,
  loadPaper,
  loadPapers,
  loadVariationSources,
  requestCaseHelp,
  savePaperAnswers,
  sendCaseMessage,
  startCaseSession,
  submitPaper,
  submitCaseSession,
  isTrainingModulesPayloadValid,
  isTrainingTaskResultApproved,
  isTrainingTaskResultValid,
  loadPlanningData,
  loadPracticeAgentContext,
  loadReportsData,
  loadTrainingWorkspaceModules,
  loadTrainingWorkspaceTask,
  submitTrainingWorkspaceTask,
} from './pageDataLoaders.js';

function makeJsonResponse(status, body) {
  return {
    status,
    ok: status >= 200 && status < 300,
    text: async () => body,
  };
}

function snapshotDescriptor(target, key) {
  return Object.getOwnPropertyDescriptor(target, key);
}

function restoreDescriptor(target, key, descriptor) {
  if (descriptor) {
    Object.defineProperty(target, key, descriptor);
    return;
  }
  delete target[key];
}

function installBrowserStubs(responders, { token = 'token' } = {}) {
  const descriptors = {
    fetch: snapshotDescriptor(globalThis, 'fetch'),
    localStorage: snapshotDescriptor(globalThis, 'localStorage'),
    window: snapshotDescriptor(globalThis, 'window'),
  };

  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    enumerable: true,
    writable: true,
    value: {
      getItem: () => token,
      removeItem: () => {},
    },
  });

  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    enumerable: true,
    writable: true,
    value: {
      location: {
        reload: () => {},
      },
    },
  });

  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    enumerable: true,
    writable: true,
    value: async (url, options) => {
      const responder = responders.get(url);
      if (!responder) {
        throw new Error(`Unexpected fetch: ${url}`);
      }
      return responder(url, options);
    },
  });

  return () => {
    restoreDescriptor(globalThis, 'fetch', descriptors.fetch);
    restoreDescriptor(globalThis, 'localStorage', descriptors.localStorage);
    restoreDescriptor(globalThis, 'window', descriptors.window);
  };
}

async function withBrowserStubs(responders, callback, options = {}) {
  const restore = installBrowserStubs(responders, options);
  try {
    return await callback();
  } finally {
    restore();
  }
}

test('browser stub helpers restore fetch, localStorage, and window descriptors', { concurrency: false }, async () => {
  const originalDescriptors = {
    fetch: snapshotDescriptor(globalThis, 'fetch'),
    localStorage: snapshotDescriptor(globalThis, 'localStorage'),
    window: snapshotDescriptor(globalThis, 'window'),
  };
  const originalPresence = {
    fetch: Object.hasOwn(globalThis, 'fetch'),
    localStorage: Object.hasOwn(globalThis, 'localStorage'),
    window: Object.hasOwn(globalThis, 'window'),
  };

  await withBrowserStubs(new Map([
    ['/api/agent/plan/summary', () => makeJsonResponse(200, '')],
    ['/api/training/plan/summary', () => makeJsonResponse(200, JSON.stringify({
      plan_summary: { goal: '旧接口目标' },
      weekly_plan: { focus: '旧接口重点', evidence: [] },
      daily_tasks: [{ key: 'task-1', title: '任务', reason: '原因', duration_min: 20 }],
      constraints: { daily_available_minutes: 20 },
    }))],
  ]), async () => {
    assert.notDeepEqual(snapshotDescriptor(globalThis, 'fetch'), originalDescriptors.fetch);
    assert.notDeepEqual(snapshotDescriptor(globalThis, 'window'), originalDescriptors.window);
    assert.notDeepEqual(snapshotDescriptor(globalThis, 'localStorage'), originalDescriptors.localStorage);
  });

  assert.deepEqual(snapshotDescriptor(globalThis, 'fetch'), originalDescriptors.fetch);
  assert.deepEqual(snapshotDescriptor(globalThis, 'localStorage'), originalDescriptors.localStorage);
  assert.deepEqual(snapshotDescriptor(globalThis, 'window'), originalDescriptors.window);
  assert.equal(Object.hasOwn(globalThis, 'fetch'), originalPresence.fetch);
  assert.equal(Object.hasOwn(globalThis, 'localStorage'), originalPresence.localStorage);
  assert.equal(Object.hasOwn(globalThis, 'window'), originalPresence.window);
});

test('loadPlanningData falls back to legacy summary when agent payload is invalid', { concurrency: false }, async () => {
  await withBrowserStubs(new Map([
    ['/api/agent/plan/summary', () => makeJsonResponse(200, '')],
    ['/api/training/plan/summary', () => makeJsonResponse(200, JSON.stringify({
      plan_summary: { goal: '旧接口目标' },
      weekly_plan: { focus: '旧接口重点', evidence: [] },
      daily_tasks: [{ key: 'task-1', title: '任务', reason: '原因', duration_min: 20 }],
      constraints: { daily_available_minutes: 20 },
    }))],
  ]), async () => {
    const result = await loadPlanningData({ fetcher: fetchJsonWithAuthFallback });
    assert.equal(result.plan.plan_summary.goal, '旧接口目标');
    assert.equal(result.error, '');
    assert.equal(result.source, '/training/plan/summary');
  });
});

test('loadReportsData falls back to legacy report when agent payload is invalid', { concurrency: false }, async () => {
  await withBrowserStubs(new Map([
    ['/api/agent/diagnosis/report', () => makeJsonResponse(200, 'null')],
    ['/api/training/report', () => makeJsonResponse(200, JSON.stringify({
      learner_overview: { goal: '旧接口报告', learner_group: '进阶群体', current_focus: '辨证' },
      mastery_radar: [{ name: '辨证', value: 0.6 }],
      weak_points: [],
      mistake_summary: { total_mistakes: 1, top_error_type: '证型混淆' },
      t_stage: { stage_name: 'T2' },
      resource_match: { recommended_difficulty: '中', difficulty_match: 0.8 },
      next_actions: ['复盘错题'],
    }))],
  ]), async () => {
    const result = await loadReportsData({ fetcher: fetchJsonWithAuthFallback });
    assert.equal(result.report.learner_overview.goal, '旧接口报告');
    assert.equal(result.error, '');
    assert.equal(result.source, '/training/report');
  });
});

test('loadPracticeAgentContext degrades locally when trace request fails', async () => {
  const result = await loadPracticeAgentContext({
    fetcher: async ({ paths }) => {
      const key = paths[0];
      if (key === '/agent/trace/recent') {
        throw new Error('trace unavailable');
      }
      return { data: { goal: '保持练习节奏' }, source: key };
    },
  });

  assert.equal(result.contextBrief.goal, '保持练习节奏');
  assert.deepEqual(result.recentTrace, []);
});

test('loadPracticeAgentContext degrades locally when brief request fails', async () => {
  const result = await loadPracticeAgentContext({
    fetcher: async ({ paths }) => {
      const key = paths[0];
      if (key === '/agent/context/brief') {
        throw new Error('brief unavailable');
      }
      return { data: { items: [{ output_summary: '最近执行了复盘' }] }, source: key };
    },
  });

  assert.equal(result.contextBrief, null);
  assert.deepEqual(result.recentTrace, [{ output_summary: '最近执行了复盘' }]);
});

test('loadPlanningData returns empty plan and error when all sources fail', async () => {
  const result = await loadPlanningData({
    fetcher: async () => {
      throw new Error('network down');
    },
  });

  assert.deepEqual(result.plan, emptyPlan);
  assert.equal(result.error, 'network down');
  assert.equal(result.source, null);
});

test('loadReportsData returns empty report and error when all sources fail', async () => {
  const result = await loadReportsData({
    fetcher: async () => {
      throw new Error('network down');
    },
  });

  assert.deepEqual(result.report, emptyReport);
  assert.equal(result.error, 'network down');
  assert.equal(result.source, null);
});

const validTrainingModules = {
  default_task_type: 'practice_grading',
  modules: [
    {
      key: 'practice_grading',
      label: '练习批改',
      description: '提交练习并获得批改与复盘建议。',
      enabled: true,
      badge: 'MVP',
      recommended: true,
    },
    {
      key: 'handout_generation',
      label: '讲义生成',
      description: '根据学习目标生成培训讲义。',
      enabled: true,
      badge: 'MVP',
      recommended: false,
    },
    {
      key: 'knowledge_card_generation',
      label: '知识卡生成',
      description: '根据知识点生成便于复习的知识卡。',
      enabled: true,
      badge: 'MVP',
      recommended: false,
    },
    {
      key: 'paper_generation',
      label: '试卷生成',
      description: '按训练目标生成综合试卷。',
      enabled: false,
      badge: '增强功能',
      recommended: false,
    },
    {
      key: 'case_training',
      label: '案例训练',
      description: '围绕案例开展情境化训练。',
      enabled: false,
      badge: '增强功能',
      recommended: false,
    },
    {
      key: 'mistake_variation',
      label: '错题变式',
      description: '根据错题生成变式练习。',
      enabled: false,
      badge: '增强功能',
      recommended: false,
    },
  ],
};

const validPaper = {
  paper_id: 'PAPER_1',
  title: '训练试卷',
  status: 'published',
  timing: null,
  items: [{
    paper_item_id: 'PI_1',
    position: 1,
    question_version_id: 'QV_1',
    question_type: 'short_answer',
    stem: '四君子汤主治什么证型？',
    options: [],
    kp_ids: ['KP_1'],
    difficulty: 2,
    answer: '',
  }],
};

const validCaseSession = {
  session_id: 'CS_abc123',
  case_version_id: 'CASEV_001',
  title: '虚劳案例',
  mode: 'diagnosis_only',
  status: 'active',
  learner_messages: 0,
  scoring_enabled: true,
  help_used: false,
  visible_context: { chief_complaint: '乏力纳差' },
  messages: [],
};

const validTrainingTaskResult = {
  task_id: 'TT_20260711_abc12345',
  task_type: 'handout_generation',
  status: 'completed',
  title: '脾胃学说讲义',
  summary: '培训资料已生成。',
  artifact: {
    artifact_type: 'handout',
    title: '脾胃学说讲义',
    content: { sections: [{ heading: '概述' }] },
  },
  evidence_pack: { pack_id: 'EP_TT_20260711_abc12345', items: [] },
  audit: { decision: 'pass' },
  trace: [],
  learning_updates: { activity_recorded: true },
  next_actions: [],
};

test('loadTrainingWorkspaceModules requests and returns validated workspace modules', async () => {
  let received;
  const result = await loadTrainingWorkspaceModules({
    fetcher: async (request) => {
      received = request;
      return { data: { ...validTrainingModules, future_field: true }, source: request.paths[0] };
    },
  });

  assert.deepEqual(received.paths, ['/v1/workshop', '/training/workspace/modules']);
  assert.deepEqual(result.workspace, { ...emptyTrainingWorkspace, ...validTrainingModules, future_field: true });
  assert.equal(result.error, '');
  assert.equal(result.source, '/v1/workshop');
});

test('isTrainingModulesPayloadValid accepts the six-module fixture and an additional valid module', () => {
  const sevenModuleWorkspace = {
    ...validTrainingModules,
    modules: [
      ...validTrainingModules.modules,
      {
        key: 'oral_assessment',
        label: '口述考核',
        description: '根据训练主题进行口述考核。',
        enabled: false,
        badge: '增强功能',
        recommended: false,
      },
    ],
  };

  assert.equal(isTrainingModulesPayloadValid(validTrainingModules), true);
  assert.equal(isTrainingModulesPayloadValid(sevenModuleWorkspace), true);
  assert.equal(isTrainingModulesPayloadValid({
    ...sevenModuleWorkspace,
    default_task_type: 'missing_task_type',
  }), false);
});

test('loadTrainingWorkspaceModules returns empty workspace and error for invalid payload', { concurrency: false }, async () => {
  await withBrowserStubs(new Map([
    ['/api/training/workspace/modules', () => makeJsonResponse(200, JSON.stringify({
      default_task_type: 'practice_grading',
      modules: [],
    }))],
  ]), async () => {
    const result = await loadTrainingWorkspaceModules({ fetcher: fetchJsonWithAuthFallback });
    assert.deepEqual(result.workspace, emptyTrainingWorkspace);
    assert.equal(result.error, 'Invalid payload for /training/workspace/modules');
    assert.equal(result.source, null);
  });
});

test('training task approval requires completed status and pass audit decision', () => {
  assert.equal(isTrainingTaskResultApproved({ status: 'completed', audit: { decision: 'pass' } }), true);
  assert.equal(isTrainingTaskResultApproved({ status: ' completed ', audit: { decision: ' PASS ' } }), true);

  for (const decision of ['reject', 'human_review', 'needs_human_review', 'revise', 'needs_review']) {
    assert.equal(
      isTrainingTaskResultApproved({ status: 'completed', audit: { decision } }),
      false,
      decision,
    );
  }

  assert.equal(isTrainingTaskResultApproved({ status: 'failed', audit: { decision: 'pass' } }), false);
  assert.equal(isTrainingTaskResultApproved({ status: 'completed', audit: {} }), false);
  assert.equal(isTrainingTaskResultApproved({ status: 1, audit: { decision: [] } }), false);
  assert.equal(isTrainingTaskResultApproved(null), false);
});

test('paper loaders use owned read, answer save, and idempotent submit contracts', async () => {
  const requests = [];
  const fetcher = async (request) => {
    requests.push(request);
    if (request.paths[0].endsWith('/submit')) {
      return { data: { paper_id: 'PAPER_1', status: 'completed', score: 100, max_score: 100, items: [] }, source: request.paths[0] };
    }
    return { data: validPaper, source: request.paths[0] };
  };
  const loaded = await loadPaper({ fetcher, paperId: 'PAPER_1' });
  const saved = await savePaperAnswers({ fetcher, paperId: 'PAPER_1', answers: { PI_1: '脾胃气虚证' } });
  const submitted = await submitPaper({ fetcher, paperId: 'PAPER_1', requestId: 'submit-1' });

  assert.deepEqual(loaded.paper, validPaper);
  assert.deepEqual(saved.paper, validPaper);
  assert.equal(submitted.result.score, 100);
  assert.deepEqual(requests[1].options, { method: 'PUT', body: JSON.stringify({ answers: { PI_1: '脾胃气虚证' } }) });
  assert.deepEqual(requests[2].options, { method: 'POST', body: JSON.stringify({ request_id: 'submit-1' }) });
  assert.doesNotMatch(JSON.stringify(requests), /standard_answer/);
});

test('paper library loader uses the stable user-scoped workshop contract', async () => {
  let received;
  const payload = {
    schema_version: '1.0',
    items: [{
      paper_id: 'PAPER_1',
      title: '四君子汤训练卷',
      status: 'published',
      duration_minutes: 45,
      created_at: null,
    }],
    total: 1,
    offset: 0,
    limit: 50,
  };
  const result = await loadPapers({
    fetcher: async (request) => {
      received = request;
      return { data: payload, source: request.paths[0] };
    },
  });

  assert.deepEqual(received.paths, ['/v1/workshop/papers?offset=0&limit=50']);
  assert.deepEqual(result.papers, payload);
  assert.equal(result.error, '');
});

test('paper submission loader rejects a malformed successful response', async () => {
  const submitted = await submitPaper({
    fetcher: async (request) => {
      assert.equal(request.validator({ status: 'completed' }), false);
      throw new Error('响应格式无效');
    },
    paperId: 'PAPER_1',
    requestId: 'submit-1',
  });

  assert.deepEqual(submitted.result, {});
  assert.equal(submitted.error, '响应格式无效');
});

test('variation source loader returns only validated source projections', async () => {
  const payload = {
    items: [{
      mistake_id: 91,
      question_version_id: 'QV_SOURCE_1',
      stem: '原题题干',
      question_type: 'short_answer',
      difficulty: 2,
      kp_ids: ['KP_1'],
    }],
  };
  let received;
  const result = await loadVariationSources({
    fetcher: async (request) => {
      received = request;
      return { data: payload, source: request.paths[0] };
    },
  });

  assert.deepEqual(received.paths, ['/training/workspace/mistake-variations/sources']);
  assert.deepEqual(result.sources, payload);
  assert.equal(result.error, '');
});

test('case training loaders use the independent session API contracts', async () => {
  const requests = [];
  const fetcher = async (request) => {
    requests.push(request);
    if (request.paths[0] === '/training/cases/types') {
      return { data: { types: ['internal'], modes: ['full', 'diagnosis_only'] }, source: request.paths[0] };
    }
    if (request.paths[0] === '/training/case-sessions') {
      return { data: validCaseSession, source: request.paths[0] };
    }
    if (request.paths[0] === '/training/case-sessions/CS_abc123') {
      return { data: validCaseSession, source: request.paths[0] };
    }
    return { data: { status: 'active' }, source: request.paths[0] };
  };

  const types = await loadCaseTypes({ fetcher });
  const started = await startCaseSession({ fetcher, selection: 'by_type', caseType: 'internal', mode: 'diagnosis_only' });
  const restored = await loadCaseSession({ fetcher, sessionId: 'CS_abc123' });
  const message = await sendCaseMessage({ fetcher, sessionId: 'CS_abc123', message: '请描述食欲情况' });
  const help = await requestCaseHelp({ fetcher, sessionId: 'CS_abc123', helpType: 'hint' });
  const submitted = await submitCaseSession({ fetcher, sessionId: 'CS_abc123', answer: { syndrome: '脾胃气虚' } });

  assert.deepEqual(types.caseTypes, { types: ['internal'], modes: ['full', 'diagnosis_only'] });
  assert.deepEqual(started.session, validCaseSession);
  assert.deepEqual(restored.session, validCaseSession);
  assert.equal(message.result.status, 'active');
  assert.equal(help.result.status, 'active');
  assert.equal(submitted.result.status, 'active');
  assert.deepEqual(requests[1].options, { method: 'POST', body: JSON.stringify({ selection: 'by_type', mode: 'diagnosis_only', case_type: 'internal' }) });
  assert.deepEqual(requests[3].options, { method: 'POST', body: JSON.stringify({ message: '请描述食欲情况' }) });
  assert.deepEqual(requests[4].options, { method: 'POST', body: JSON.stringify({ help_type: 'hint' }) });
  assert.deepEqual(requests[5].options, { method: 'POST', body: JSON.stringify({ answer: { syndrome: '脾胃气虚' } }) });
});

test('case training loaders reject invalid local arguments and invalid session payloads', async () => {
  assert.equal(isCaseTypesPayloadValid({ types: ['internal'], modes: ['full', 'diagnosis_only'] }), true);
  assert.equal(isCaseTypesPayloadValid({ types: ['internal'], modes: ['other'] }), false);
  assert.equal(isCaseSessionPayloadValid(validCaseSession), true);
  assert.equal(isCaseSessionPayloadValid({ ...validCaseSession, messages: {} }), false);
  assert.equal(isCaseSessionPayloadValid({ ...validCaseSession, messages: [null] }), false);
  assert.equal(isCaseSessionPayloadValid({ ...validCaseSession, messages: [{ role: 'patient', sequence: 1 }] }), false);

  const invalidStart = await startCaseSession({ fetcher: async () => { throw new Error('must not request'); }, selection: 'by_type', mode: 'full' });
  const invalidMessage = await sendCaseMessage({ fetcher: async () => { throw new Error('must not request'); }, sessionId: 'CS_1', message: ' ' });
  const emptySession = await loadCaseSession({ fetcher: async () => { throw new Error('must not request'); }, sessionId: ' ' });

  assert.equal(invalidStart.error, '案例训练参数无效');
  assert.equal(invalidMessage.error, '请填写问诊内容');
  assert.equal(emptySession.error, '案例会话 ID 不能为空');
});

test('submitTrainingWorkspaceTask posts the task and returns a valid task result', async () => {
  const task = { task_type: 'handout_generation', title: '脾胃学说讲义', query: '脾胃学说' };
  let received;
  const result = await submitTrainingWorkspaceTask({
    fetcher: async (request) => {
      received = request;
      return { data: validTrainingTaskResult, source: request.paths[0] };
    },
    task,
  });

  assert.deepEqual(received.paths, ['/training/workspace/tasks']);
  assert.deepEqual(received.options, { method: 'POST', body: JSON.stringify(task) });
  assert.deepEqual(result.taskResult, validTrainingTaskResult);
  assert.equal(result.error, '');
  assert.equal(result.source, '/training/workspace/tasks');
});

test('submitTrainingWorkspaceTask accepts a failed task result through its validator and loader', async () => {
  const failedTaskResult = { ...validTrainingTaskResult, status: 'failed' };
  let received;

  const result = await submitTrainingWorkspaceTask({
    fetcher: async (request) => {
      received = request;
      return { data: failedTaskResult, source: request.paths[0] };
    },
    task: { task_type: 'handout_generation' },
  });

  assert.equal(received.validator(failedTaskResult), true);
  assert.equal(isTrainingTaskResultValid(failedTaskResult), true);
  assert.deepEqual(result.taskResult, failedTaskResult);
  assert.equal(result.error, '');
});

test('submitTrainingWorkspaceTask returns empty result and error for invalid payload or request failure', { concurrency: false }, async () => {
  await withBrowserStubs(new Map([
    ['/api/training/workspace/tasks', () => makeJsonResponse(200, JSON.stringify({
      task_id: 'TT_invalid',
      task_type: 'practice_grading',
    }))],
  ]), async () => {
    const invalidPayloadResult = await submitTrainingWorkspaceTask({
      fetcher: fetchJsonWithAuthFallback,
      task: { task_type: 'practice_grading' },
    });
    const failedRequestResult = await submitTrainingWorkspaceTask({
      fetcher: async () => {
        throw new Error('network down');
      },
      task: { task_type: 'practice_grading' },
    });

    assert.deepEqual(invalidPayloadResult.taskResult, emptyTrainingTaskResult);
    assert.equal(invalidPayloadResult.error, 'Invalid payload for /training/workspace/tasks');
    assert.equal(invalidPayloadResult.source, null);
    assert.deepEqual(failedRequestResult.taskResult, emptyTrainingTaskResult);
    assert.equal(failedRequestResult.error, 'network down');
    assert.equal(failedRequestResult.source, null);
  });
});

test('loadTrainingWorkspaceTask encodes task IDs and rejects an empty ID locally', async () => {
  let received;
  const loadedResult = await loadTrainingWorkspaceTask({
    fetcher: async (request) => {
      received = request;
      return { data: validTrainingTaskResult, source: request.paths[0] };
    },
    taskId: 'TT 1/2',
  });
  const emptyIdResult = await loadTrainingWorkspaceTask({
    fetcher: async () => {
      throw new Error('must not request');
    },
    taskId: ' ',
  });

  assert.deepEqual(received.paths, ['/training/workspace/tasks/TT%201%2F2']);
  assert.deepEqual(loadedResult.taskResult, validTrainingTaskResult);
  assert.equal(loadedResult.error, '');
  assert.deepEqual(emptyIdResult.taskResult, emptyTrainingTaskResult);
  assert.equal(emptyIdResult.error, '训练任务 ID 不能为空');
  assert.equal(emptyIdResult.source, null);
});

test('training workspace fallbacks return deep independent empty states', async () => {
  const invalidModulePayload = { default_task_type: 'practice_grading', modules: [] };
  const moduleFallbacks = await Promise.all([
    loadTrainingWorkspaceModules({ fetcher: async () => { throw new Error('modules unavailable'); } }),
    loadTrainingWorkspaceModules({ fetcher: async () => { throw new Error('modules unavailable'); } }),
    loadTrainingWorkspaceModules({
      fetcher: async (request) => {
        assert.equal(request.validator(invalidModulePayload), false);
        throw new Error('Invalid payload for /training/workspace/modules');
      },
    }),
  ]);
  moduleFallbacks[0].workspace.modules.push({ key: 'mutated' });

  const invalidTaskPayload = { task_id: 'TT_invalid', task_type: 'practice_grading' };
  const submitFallbacks = await Promise.all([
    submitTrainingWorkspaceTask({ fetcher: async () => { throw new Error('submit unavailable'); }, task: {} }),
    submitTrainingWorkspaceTask({
      fetcher: async (request) => {
        assert.equal(request.validator(invalidTaskPayload), false);
        throw new Error('Invalid payload for /training/workspace/tasks');
      },
      task: {},
    }),
  ]);
  submitFallbacks[0].taskResult.artifact.mutated = true;
  submitFallbacks[0].taskResult.trace.push('mutated');

  const detailFallbacks = await Promise.all([
    loadTrainingWorkspaceTask({ fetcher: async () => { throw new Error('must not request'); }, taskId: ' ' }),
    loadTrainingWorkspaceTask({ fetcher: async () => { throw new Error('detail unavailable'); }, taskId: 'TT_failed' }),
    loadTrainingWorkspaceTask({
      fetcher: async (request) => {
        assert.equal(request.validator(invalidTaskPayload), false);
        throw new Error('Invalid payload for /training/workspace/tasks/TT_invalid');
      },
      taskId: 'TT_invalid',
    }),
  ]);
  detailFallbacks[0].taskResult.evidence_pack.mutated = true;
  detailFallbacks[1].taskResult.next_actions.push('mutated');

  assert.deepEqual(moduleFallbacks[1].workspace, emptyTrainingWorkspace);
  assert.deepEqual(moduleFallbacks[2].workspace, emptyTrainingWorkspace);
  assert.notStrictEqual(moduleFallbacks[0].workspace, moduleFallbacks[1].workspace);
  assert.notStrictEqual(moduleFallbacks[0].workspace.modules, moduleFallbacks[1].workspace.modules);
  assert.deepEqual(emptyTrainingWorkspace, {
    schema_version: '1.0',
    default_module: '',
    default_task_type: '',
    modules: [],
  });

  assert.deepEqual(submitFallbacks[1].taskResult, emptyTrainingTaskResult);
  assert.notStrictEqual(submitFallbacks[0].taskResult, submitFallbacks[1].taskResult);
  assert.notStrictEqual(submitFallbacks[0].taskResult.artifact, submitFallbacks[1].taskResult.artifact);
  assert.notStrictEqual(submitFallbacks[0].taskResult.trace, submitFallbacks[1].taskResult.trace);

  assert.deepEqual(detailFallbacks[2].taskResult, emptyTrainingTaskResult);
  assert.notStrictEqual(detailFallbacks[0].taskResult, detailFallbacks[1].taskResult);
  assert.notStrictEqual(detailFallbacks[0].taskResult.evidence_pack, detailFallbacks[1].taskResult.evidence_pack);
  assert.notStrictEqual(detailFallbacks[1].taskResult.next_actions, detailFallbacks[2].taskResult.next_actions);
  assert.deepEqual(emptyTrainingTaskResult, {
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
  });
});

test('fetchJsonWithAuthFallback retains HTTP status and backend detail for failed requests', { concurrency: false }, async () => {
  await withBrowserStubs(new Map([
    ['/api/training/workspace/tasks', () => makeJsonResponse(422, JSON.stringify({ detail: '缺少正式训练数据' }))],
  ]), async () => {
    await assert.rejects(
      fetchJsonWithAuthFallback({ paths: ['/training/workspace/tasks'] }),
      { message: '422: 缺少正式训练数据' },
    );
  });
});

test('submitTrainingWorkspaceTask sends the POST contract through fetchJsonWithAuthFallback', { concurrency: false }, async () => {
  const task = { task_type: 'handout_generation', title: '脾胃学说讲义', query: '脾胃学说' };
  let receivedUrl;
  let receivedOptions;

  await withBrowserStubs(new Map([
    ['/api/training/workspace/tasks', (url, options) => {
      receivedUrl = url;
      receivedOptions = options;
      return makeJsonResponse(200, JSON.stringify(validTrainingTaskResult));
    }],
  ]), async () => {
    const result = await submitTrainingWorkspaceTask({ fetcher: fetchJsonWithAuthFallback, task });

    assert.equal(receivedUrl, '/api/training/workspace/tasks');
    assert.equal(receivedOptions.method, 'POST');
    assert.equal(receivedOptions.body, JSON.stringify(task));
    assert.equal(receivedOptions.headers['Content-Type'], 'application/json');
    assert.equal(receivedOptions.headers.Authorization, undefined);
    assert.equal(receivedOptions.credentials, 'include');
    assert.equal(result.error, '');
  }, { token: 'training-token' });
});
