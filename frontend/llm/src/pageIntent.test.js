import { describe, expect, it } from 'vitest';

import {
  createPageIntent,
  getIntentPage,
  mergePageIntent,
  workshopActionIntent,
} from './pageIntent';

describe('page intents', () => {
  it('normalizes legacy string navigation into an intent', () => {
    expect(createPageIntent('practice')).toEqual({ page: 'practice', params: {} });
  });

  it('keeps exam and knowledge point context immutable', () => {
    const current = createPageIntent('dashboard', {
      trackId: 'track-1',
      membershipId: 'membership-2',
    });
    const next = mergePageIntent(current, {
      page: 'knowledge',
      params: { kpId: 'kp-3' },
    });

    expect(next).toEqual({
      page: 'knowledge',
      params: {
        trackId: 'track-1',
        membershipId: 'membership-2',
        kpId: 'kp-3',
      },
    });
    expect(current).toEqual({
      page: 'dashboard',
      params: { trackId: 'track-1', membershipId: 'membership-2' },
    });
  });

  it('reads the page from both legacy strings and intents', () => {
    expect(getIntentPage('assistant')).toBe('assistant');
    expect(getIntentPage({ page: 'knowledge', params: { kpId: 'kp-3' } })).toBe('knowledge');
  });

  it('normalizes persisted workshop actions without losing the caller return path', () => {
    expect(workshopActionIntent({
      destination: 'workshop.paper',
      params: { paper_id: 'PAPER_1' },
    }, {
      returnTo: { page: 'assistant', params: { sessionId: 'SESSION_1' } },
    })).toEqual({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'paper_workspace',
        paper_id: 'PAPER_1',
        paperId: 'PAPER_1',
        returnTo: { page: 'assistant', params: { sessionId: 'SESSION_1' } },
      },
    });
  });

  it('routes daily videos to the knowledge card video resource', () => {
    const video = { title: '章节精讲', url: 'https://example.test/video.mp4' };
    expect(workshopActionIntent({
      destination: 'workshop.knowledge_video',
      params: { taskItemId: 'ITEM_VIDEO', video },
    })).toEqual({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'knowledge_cards',
        resourceView: 'videos',
        taskItemId: 'ITEM_VIDEO',
        video,
        directVideo: video,
      },
    });
  });

  it('returns null for unknown destinations instead of opening the wrong workspace', () => {
    expect(workshopActionIntent({ destination: 'external.unknown' })).toBeNull();
  });
});
