import test from 'node:test';
import assert from 'node:assert/strict';

import { buildProfileConflictSections } from './profileConflictList.js';

test('builds conflict sections from duplicate active memories and pending candidates', () => {
  const sections = buildProfileConflictSections({
    memories: [
      { id: 1, is_active: true, category: 'preference', title: '午休学习', content: '偏好午休' },
      { id: 2, is_active: true, category: 'preference', title: '午休学习', content: '偏好晚间' },
      { id: 3, is_active: false, category: 'preference', title: '午休学习', content: '已停用' },
      { id: 4, is_active: true, category: 'goal', title: '方剂复习', content: '保持' },
    ],
    candidates: [
      { id: 10, status: 'pending', title: '新偏好', content: '更喜欢案例训练' },
      { id: 11, status: 'promoted', title: '旧建议', content: '已处理' },
    ],
  });

  assert.equal(sections.conflicts.length, 1);
  assert.equal(sections.conflicts[0].items.length, 2);
  assert.equal(sections.pendingCandidates.length, 1);
  assert.equal(sections.hasActionableItems, true);
});

test('reports empty state when no conflicts or pending candidates exist', () => {
  const sections = buildProfileConflictSections({
    memories: [{ id: 1, is_active: true, category: 'goal', title: '方剂复习', content: '保持' }],
    candidates: [{ id: 11, status: 'ignored', title: '旧建议' }],
  });

  assert.deepEqual(sections.conflicts, []);
  assert.deepEqual(sections.pendingCandidates, []);
  assert.equal(sections.hasActionableItems, false);
});

test('degrades to empty sections when API payloads are not arrays', () => {
  const sections = buildProfileConflictSections({
    memories: { detail: 'memory endpoint failed' },
    candidates: { detail: 'candidate endpoint failed' },
  });

  assert.deepEqual(sections.conflicts, []);
  assert.deepEqual(sections.pendingCandidates, []);
  assert.equal(sections.hasActionableItems, false);
});
