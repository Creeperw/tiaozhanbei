import test from 'node:test';
import assert from 'node:assert/strict';

import { getKnowledgeScopeNotice, getSearchFeedback } from './knowledgePageState.js';

test('describes personal and public knowledge boundaries', () => {
  assert.match(getKnowledgeScopeNotice('personal', false), /仅当前用户可见/);
  assert.match(getKnowledgeScopeNotice('public', false), /管理员维护/);
  assert.match(getKnowledgeScopeNotice('public', true), /所有用户可检索/);
});

test('prioritizes search loading and errors before empty results', () => {
  assert.deepEqual(getSearchFeedback({ isSearching: true, error: '', hasQueried: true, resultCount: 0 }), { tone: 'loading', text: '正在检索公共与个人知识…' });
  assert.deepEqual(getSearchFeedback({ isSearching: false, error: '服务暂不可用', hasQueried: true, resultCount: 0 }), { tone: 'error', text: '服务暂不可用' });
  assert.deepEqual(getSearchFeedback({ isSearching: false, error: '', hasQueried: true, resultCount: 0 }), { tone: 'empty', text: '未找到相关内容' });
  assert.equal(getSearchFeedback({ isSearching: false, error: '', hasQueried: false, resultCount: 0 }), null);
});
