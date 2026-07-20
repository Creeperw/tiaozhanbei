import { describe, expect, it } from 'vitest';
import {
  assistantDraftFromContext,
  knowledgeQueryFromContext,
  practiceContextFromIntent,
} from './examAtlasPageContext';

describe('exam atlas page context', () => {
  const context = {
    trackId: 'track-a',
    membershipId: 'membership-a',
    kpId: 'kp-yinyang',
    kpName: '阴阳学说',
    query: '阴阳学说',
    context: '请围绕知识点“阴阳学说”进行讲解。考纲路径：中医学 / 阴阳学说',
  };

  it('normalizes the selected KP for training without inventing a question', () => {
    expect(practiceContextFromIntent(context)).toEqual({
      trackId: 'track-a',
      membershipId: 'membership-a',
      kpId: 'kp-yinyang',
      kpName: '阴阳学说',
    });
    expect(practiceContextFromIntent({})).toBeNull();
  });

  it('prefills knowledge retrieval from the readable KP name', () => {
    expect(knowledgeQueryFromContext(context)).toBe('阴阳学说');
    expect(knowledgeQueryFromContext({ kpName: '方剂学' })).toBe('方剂学');
  });

  it('prefills assistant only when a readable context exists', () => {
    expect(assistantDraftFromContext(context)).toContain('中医学 / 阴阳学说');
    expect(assistantDraftFromContext({})).toBe('');
  });
});
