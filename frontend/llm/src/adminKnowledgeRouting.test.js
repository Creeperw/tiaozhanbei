import test from 'node:test';
import assert from 'node:assert/strict';
import { getAppShellConfig } from './appShell.js';

test('管理员可见知识库治理入口，普通用户不可见', () => {
  const admin = getAppShellConfig({ currentUser: { role: 'admin' }, currentPage: 'admin-knowledge' });
  const learner = getAppShellConfig({ currentUser: { role: 'user' }, currentPage: 'dashboard' });

  assert.equal(admin.currentPage, 'admin-knowledge');
  assert.ok(admin.supportNav.some((item) => item.key === 'admin-knowledge'));
  assert.ok(!learner.supportNav.some((item) => item.key === 'admin-knowledge'));
  assert.equal(getAppShellConfig({ currentUser: { role: 'user' }, currentPage: 'admin-knowledge' }).currentPage, 'dashboard');
});
