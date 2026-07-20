import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import OnboardingSurveyPanel from './OnboardingSurveyPanel';

function jsonResponse(payload, ok = true) {
  return Promise.resolve({
    ok,
    status: ok ? 200 : 422,
    text: async () => JSON.stringify(payload),
  });
}

describe('OnboardingSurveyPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('saves a numeric survey duration and a selected official exam target', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      requests.push({ url, options });
      if (url.endsWith('/training/onboarding/group-templates')) {
        return jsonResponse({ groups: [], questions: [], required_fields: ['learner_group'] });
      }
      if (url.endsWith('/exam-learning/tracks')) {
        return jsonResponse({
          items: [
            {
              track_id: 'EXAM_2025_TCM_PHYSICIAN',
              title_normalized: '2025 中医执业医师资格考试',
            },
          ],
          total: 1,
          version: '2.0.0',
        });
      }
      if (url.endsWith('/training/onboarding/survey')) {
        return jsonResponse({
          status: 'onboarding_completed',
          learning_target: { exam_track_id: 'EXAM_2025_TCM_PHYSICIAN' },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    const onSaved = vi.fn();
    render(<OnboardingSurveyPanel onSaved={onSaved} />);

    await screen.findByRole('option', { name: '2025 中医执业医师资格考试' });
    fireEvent.change(screen.getByLabelText('所属用户群体'), {
      target: { value: 'academic' },
    });
    fireEvent.change(screen.getByLabelText('具体考试目标'), {
      target: { value: 'EXAM_2025_TCM_PHYSICIAN' },
    });
    fireEvent.change(screen.getByLabelText('每日可投入时长'), {
      target: { value: '45' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存学情调查' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    const surveyRequests = requests.filter(({ url }) => url.endsWith('/training/onboarding/survey'));
    expect(surveyRequests).toHaveLength(1);
    expect(JSON.parse(surveyRequests[0].options.body)).toMatchObject({
      learner_group: 'academic',
      preferences: { daily_available_minutes: 45 },
      target_type: 'certification',
      exam_track_id: 'EXAM_2025_TCM_PHYSICIAN',
      is_locked: true,
    });
    expect(requests.some(({ url }) => url.endsWith('/personalization/learning-target'))).toBe(false);
  });
});
