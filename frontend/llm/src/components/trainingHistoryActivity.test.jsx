import { describe, expect, it } from 'vitest';
import {
  categoryForActivity,
  isVerifiedPracticeActivity,
  patientModeForActivity,
} from './trainingHistoryActivity';

describe('training history activity contract', () => {
  it('maps the legacy simulated-patient projection by exact activity fields', () => {
    const activity = {
      activity_type: 'case_training',
      resource_type: 'simulated_patient_session',
      completion_status: 'completed',
      payload: { practice_mode: 'acupuncture' },
    };

    expect(categoryForActivity(activity)).toBe('ai_patient_simulation');
    expect(patientModeForActivity(activity)).toBe('acupuncture');
    expect(isVerifiedPracticeActivity(activity)).toBe(true);
  });

  it('does not classify arbitrary text by keywords', () => {
    const activity = {
      activity_type: 'login',
      resource_type: 'session',
      completion_status: 'completed',
      title: '完成模拟病患和专项特训',
    };

    expect(categoryForActivity(activity)).toBeNull();
    expect(isVerifiedPracticeActivity(activity)).toBe(false);
  });

  it('requires a verified completion status', () => {
    const activity = {
      activity_type: 'case_training',
      resource_type: 'simulated_patient_session',
      completion_status: 'started',
    };

    expect(categoryForActivity(activity)).toBe('ai_patient_simulation');
    expect(isVerifiedPracticeActivity(activity)).toBe(false);
  });
});