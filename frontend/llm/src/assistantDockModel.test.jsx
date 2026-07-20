import { describe, expect, it } from 'vitest';

import {
  buildAssistantGreeting,
  createNewAssistantState,
} from './assistantDockModel';

describe('assistant dock model', () => {
  it('builds a contextual greeting from the learner and today plan', () => {
    expect(buildAssistantGreeting({
      username: 'admin',
      goal: '完成方剂学第3章',
      focus: '重点掌握方剂证型',
    })).toBe('你好，admin！今天的学习目标是完成方剂学第3章，重点掌握方剂证型。有什么问题可以随时问我。');
  });

  it('builds a concise fallback without leaking missing values', () => {
    const greeting = buildAssistantGreeting({ username: 'admin' });

    expect(greeting).toBe('你好，admin！今天可以继续完成你的学习计划。有什么问题可以随时问我。');
    expect(greeting).not.toContain('undefined');
    expect(greeting).not.toContain('null');
  });

  it('creates a fresh unpersisted conversation state', () => {
    expect(createNewAssistantState()).toEqual({
      sessionId: null,
      messages: [],
      mode: 'new',
    });
  });
});
