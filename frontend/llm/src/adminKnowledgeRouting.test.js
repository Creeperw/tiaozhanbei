import test from 'node:test';
import assert from 'node:assert/strict';
import { getAppShellConfig } from './appShell.js';

test('管理员菜单提供知识治理入口，旧地址迁移到知识库个人视图', () => {
  const admin = getAppShellConfig({ currentUser: { role: 'admin' }, currentPage: 'admin-knowledge' });
  const learner = getAppShellConfig({ currentUser: { role: 'user' }, currentPage: 'dashboard' });

  assert.equal(admin.currentPage, 'knowledge');
  assert.equal(admin.knowledgeView, 'personal');
  assert.ok(admin.supportNav.some((item) => item.key === 'admin-feedback'
    && item.children.some((child) => child.intent.page === 'knowledge'
      && child.intent.params.view === 'personal')));
  assert.ok(!learner.supportNav.some((item) => item.key === 'admin-feedback'));
  const legacy = getAppShellConfig({ currentUser: { role: 'user' }, currentPage: 'admin-knowledge' });
  assert.equal(legacy.currentPage, 'knowledge');
  assert.equal(legacy.knowledgeView, 'personal');
});
