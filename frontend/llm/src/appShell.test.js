import test from 'node:test';
import assert from 'node:assert/strict';

import { PAGE_TITLES, getAppShellConfig } from './appShell.js';

test('defaults authenticated users to dashboard and exposes top-level training navigation', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'dashboard',
  });

  assert.equal(config.defaultPage, 'dashboard');
  assert.deepEqual(config.primaryNav, [
    { key: 'dashboard', label: '平台首页' },
    { key: 'assistant', label: '智能助教' },
    { key: 'practice', label: '学习工坊' },
    { key: 'knowledge', label: '知识仓库' },
    { key: 'personalization', label: '画像与记忆' },
    { key: 'settings', label: '用户设置' },
  ]);
  assert.equal(config.currentPage, 'dashboard');
  assert.equal(config.pageTitle, '培训助手首页');
  assert.equal(config.homeAction, null);
});

test('keeps admin entry out of primary navigation while preserving support access', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'admin', role: 'admin' },
    currentPage: 'assistant',
  });

  assert.equal(config.primaryNav.some((item) => item.key === 'admin-feedback'), false);
  assert.equal(config.supportNav.some((item) => item.key === 'admin-feedback'), true);
  assert.equal(config.supportNav.some((item) => item.key === 'admin-knowledge'), false);
  assert.equal(config.pageTitle, '智能助教');
  assert.deepEqual(config.homeAction, { key: 'dashboard', label: '返回主页' });
});

test('hides support navigation for standard learners', () => {
  const config = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'practice',
  });

  assert.deepEqual(config.supportNav, []);
  assert.equal(config.pageTitle, '训练工坊');
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

test('uses training workshop label and page title for practice navigation', () => {
  assert.equal(PAGE_TITLES.practice, '训练工坊');
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
    '学习画像与记忆',
  );
  assert.equal(
    getAppShellConfig({ currentUser: { username: 'alice', role: 'user' }, currentPage: 'knowledge' }).pageTitle,
    '知识库',
  );
  assert.equal(
    getAppShellConfig({ currentUser: { username: 'alice', role: 'user' }, currentPage: 'settings' }).pageTitle,
    '设置',
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

test('uses a full-width workspace shell for assistant, training workshop, and knowledge', () => {
  const assistant = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'assistant',
  });
  const practice = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'practice',
  });
  const knowledge = getAppShellConfig({
    currentUser: { username: 'alice', role: 'user' },
    currentPage: 'knowledge',
  });

  assert.equal(assistant.shellMode, 'workspace');
  assert.equal(practice.shellMode, 'workspace');
  assert.equal(knowledge.shellMode, 'workspace');
  assert.equal(knowledge.primaryNav.find((item) => item.key === 'knowledge').label, '知识仓库');
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
