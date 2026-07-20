import { describe, expect, it } from 'vitest';

import {
  createPageIntent,
  getIntentPage,
  mergePageIntent,
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
});
