import { test } from 'vitest';
import assert from 'node:assert/strict';

import { PAGE_TITLES, getAppShellConfig } from './appShell.js';

test('defaults authenticated users to dashboard and exposes top-level training navigation', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'dashboard',
  });

  assert.equal(config.defaultPage, 'dashboard');
  assert.deepEqual(config.primaryNav.map(({ key, label }) => ({ key, label })), [
    { key: 'learning-target', label: '考试类别' },
    { key: 'learning-path', label: '学习路径' },
    { key: 'practice', label: '教学资源' },
    { key: 'training-workshop', label: '训练工坊' },
    { key: 'personalization', label: '个性数据' },
  ]);
  assert.equal(config.currentPage, 'dashboard');
  assert.equal(config.pageTitle, '培训助手首页');
  assert.equal(config.homeAction, null);
});

test('allows the dedicated learning path page while keeping dashboard as the default', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'learning-path',
  });

  assert.equal(config.defaultPage, 'dashboard');
  assert.equal(config.currentPage, 'learning-path');
  assert.equal(config.pageTitle, '学习路径');
});

test('allows internal platform capability detail pages without adding a navigation item', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'capability-detail',
  });

  assert.equal(config.currentPage, 'capability-detail');
  assert.equal(config.pageTitle, '平台核心能力');
  assert.equal(config.primaryNav.some((item) => item.key === 'capability-detail'), false);
});

test('keeps the learning target dropdown out of page routing', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'learning-target',
  });

  assert.equal(config.currentPage, 'dashboard');
  assert.equal(config.primaryNav[0].kind, 'learning-target');
});

test('keeps dashboard as the default without exposing it in the primary navigation', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'personalization',
  });

  assert.equal(config.defaultPage, 'dashboard');
  assert.equal(config.primaryNav.some((item) => item.key === 'dashboard'), false);
  assert.deepEqual(config.homeAction, { key: 'dashboard', label: '返回主页' });
});

test('uses the learning path as a direct link without dropdown options', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'dashboard',
  });
  const learningPath = config.primaryNav.find((item) => item.key === 'learning-path');

  assert.deepEqual(learningPath.intent, { page: 'learning-path', params: {} });
  assert.equal('children' in learningPath, false);
});

test('keeps admin entry out of standard learner navigation and returns it for administrators', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'admin', role: 'admin' },
    currentPage: 'assistant',
  });

  assert.equal(config.primaryNav.some((item) => item.key === 'admin-feedback'), true);
  assert.equal(config.supportNav.some((item) => item.key === 'admin-feedback'), true);
  assert.equal(config.supportNav.some((item) => item.key === 'admin-knowledge'), false);
  assert.equal(config.pageTitle, '智能助教');
  assert.deepEqual(config.homeAction, { key: 'dashboard', label: '返回主页' });
});

test('defines dropdown destinations as explicit navigation intents', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'dashboard',
  });

  assert.deepEqual(
    config.primaryNav.find((item) => item.key === 'training-workshop').children,
    [
      { label: '题目训练', intent: { page: 'training-workshop', params: { taskType: 'question_training' } } },
      { label: 'AI 病患模拟', intent: { page: 'training-workshop', params: { taskType: 'simulated_patient' } } },
      { label: '错题变式', intent: { page: 'training-workshop', params: { taskType: 'mistake_variation' } } },
      { label: '试卷生成', intent: { page: 'training-workshop', params: { taskType: 'paper_generation' } } },
    ],
  );
  assert.deepEqual(config.primaryNav.find((item) => item.key === 'practice').intent, { page: 'practice', params: {} });
  assert.equal('children' in config.primaryNav.find((item) => item.key === 'practice'), false);
  assert.equal(config.primaryNav.some((item) => item.key === 'settings'), false);
});

test('uses personalization as a direct user-profile link without dropdown options', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'dashboard',
  });
  const personalization = config.primaryNav.find((item) => item.key === 'personalization');

  assert.deepEqual(personalization.intent, {
    page: 'personalization',
    params: { view: 'user-profile' },
  });
  assert.equal('children' in personalization, false);
});

test('hides support navigation for standard learners', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'practice',
  });

  assert.deepEqual(config.supportNav, []);
  assert.equal(config.primaryNav.some((item) => item.key === 'admin-feedback'), false);
  assert.equal(config.pageTitle, '教学资源');
  assert.deepEqual(config.homeAction, { key: 'dashboard', label: '返回主页' });
});

test('falls back to dashboard when page is unknown', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'unknown-page',
  });

  assert.equal(config.currentPage, 'dashboard');
  assert.equal(config.pageTitle, '培训助手首页');
});

test('creates assistant navigation state that preserves a selected continue-learning session id', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'assistant',
    selectedSessionId: 'session-42',
  });

  assert.equal(config.currentPage, 'assistant');
  assert.equal(config.selectedSessionId, 'session-42');
});

test('uses separate learning and training workshop labels and page titles', () => {
  assert.equal(PAGE_TITLES.practice, '教学资源');
  assert.equal(PAGE_TITLES['training-workshop'], '训练工坊');
});

test('redirects retired question and governance entries into knowledge workspace tabs', () => {
  const questions = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'question-workspace',
  });
  const governance = getAppShellConfig({
    currentUser: { username: 'admin', role: 'admin' },
    currentPage: 'admin-knowledge',
  });

  assert.equal(questions.currentPage, 'knowledge');
  assert.equal(questions.knowledgeView, 'questions');
  assert.equal(governance.currentPage, 'knowledge');
  assert.equal(governance.knowledgeView, 'personal');
});

test('exposes Phase 4 training module route bindings for real pages', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'practice',
  });

  assert.equal(config.moduleRoutes.practiceWorkspace.endpoint, '/training/workspace/tasks');
  assert.equal(config.moduleRoutes.practice.endpoint, '/training/practice/grade');
  assert.equal(config.moduleRoutes.planning.endpoint, '/training/plan/summary');
  assert.equal(config.moduleRoutes.reports.endpoint, '/training/report');
});

test('uses updated page titles for knowledge, personalization, and settings modules', () => {
  assert.equal(
    getAppShellConfig({ currentUser: { username: 'alice', role: 'user' }, currentPage: 'personalization' }).pageTitle,
    '个性数据',
  );
  assert.equal(
    getAppShellConfig({ currentUser: { username: 'alice', role: 'user' }, currentPage: 'knowledge' }).pageTitle,
    '知识库',
  );
  assert.equal(
    getAppShellConfig({ currentUser: { username: 'alice', role: 'user' }, currentPage: 'settings' }).pageTitle,
    '用户设置',
  );
});

test('exposes a return-home action for assistant empty state without a current session', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'assistant',
  });

  assert.deepEqual(config.homeAction, { key: 'dashboard', label: '返回主页' });
  assert.equal(config.assistantHomeAction.label, '返回主页');
  assert.equal(config.assistantHomeAction.showWhenSessionMissing, true);
});

test('uses a full-width workspace shell for assistant, both workshops, and knowledge', () => {
  const assistant = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'assistant',
  });
  const practice = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'practice',
  });
  const trainingWorkshop = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'training-workshop',
  });
  const knowledge = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'knowledge',
  });

  assert.equal(assistant.shellMode, 'workspace');
  assert.equal(practice.shellMode, 'workspace');
  assert.equal(trainingWorkshop.shellMode, 'workspace');
  assert.equal(knowledge.shellMode, 'workspace');
  assert.equal(knowledge.primaryNav.some((item) => item.key === 'knowledge'), false);
});

test('keeps dashboard, personalization, settings and admin in the standard shell', () => {
  for (const currentPage of ['dashboard', 'personalization', 'settings']) {
    const config = getAppShellConfig({
      currentUser: { username: 'alice', role: 'user' },
      currentPage,
    });
    assert.equal(config.shellMode, 'standard');
  }

  const admin = getAppShellConfig({
    currentUser: { username: 'root', role: 'admin' },
    currentPage: 'admin-feedback',
  });
  assert.equal(admin.shellMode, 'standard');
});
