import { describe, expect, it } from 'vitest';
import { intentToPath, pathToIntent } from './urlRouting';

describe('intentToPath', () => {
  it('maps dashboard to root path', () => {
    expect(intentToPath({ page: 'dashboard', params: {} })).toBe('/dashboard');
  });

  it('maps training-workshop overview to /practice', () => {
    expect(intentToPath({ page: 'training-workshop', params: {} })).toBe('/practice');
  });

  it('maps practice workspace modules to /practice/<slug>', () => {
    expect(intentToPath({ page: 'practice', params: { view: 'workspace', taskType: 'special_training' } })).toBe('/practice/special-training');
    expect(intentToPath({ page: 'practice', params: { view: 'workspace', taskType: 'topic_training' } })).toBe('/practice/topic-training');
    expect(intentToPath({ page: 'practice', params: { view: 'workspace', taskType: 'mistake_redo' } })).toBe('/practice/mistake-redo');
    expect(intentToPath({ page: 'practice', params: { view: 'workspace', taskType: 'question_training' } })).toBe('/practice/comprehensive');
    expect(intentToPath({ page: 'practice', params: { view: 'workspace', taskType: 'paper_workspace' } })).toBe('/practice/smart-paper');
    expect(intentToPath({ page: 'practice', params: { view: 'workspace', taskType: 'ai_patient_simulation' } })).toBe('/practice/patient-simulation');
    expect(intentToPath({ page: 'practice', params: { view: 'workspace', taskType: 'training_history' } })).toBe('/practice/history');
    expect(intentToPath({ page: 'practice', params: { view: 'workspace', taskType: 'question_favorites' } })).toBe('/practice/favorites');
  });

  it('falls back to /practice when workspace task type is unknown', () => {
    expect(intentToPath({ page: 'practice', params: { view: 'workspace', taskType: 'unknown_module' } })).toBe('/practice');
  });

  it('maps teaching resources (practice default view) to /resources', () => {
    expect(intentToPath({ page: 'practice', params: {} })).toBe('/resources');
  });

  it('maps personalization views', () => {
    expect(intentToPath({ page: 'personalization', params: {} })).toBe('/personalization');
    expect(intentToPath({ page: 'personalization', params: { view: 'user-profile' } })).toBe('/personalization/profile');
    expect(intentToPath({ page: 'personalization', params: { view: 'memory' } })).toBe('/personalization/memory');
  });

  it('maps top-level pages', () => {
    expect(intentToPath({ page: 'learning-path', params: {} })).toBe('/learning-path');
    expect(intentToPath({ page: 'assistant', params: {} })).toBe('/assistant');
    expect(intentToPath({ page: 'knowledge', params: {} })).toBe('/knowledge');
    expect(intentToPath({ page: 'settings', params: {} })).toBe('/settings');
  });

  it('returns null for unmappable intents', () => {
    expect(intentToPath({ page: 'admin-feedback', params: {} })).toBeNull();
    expect(intentToPath({ page: 'qualification-route', params: {} })).toBeNull();
  });
});

describe('pathToIntent', () => {
  it('maps root path to dashboard', () => {
    expect(pathToIntent('/')).toEqual({ page: 'dashboard', params: {} });
    expect(pathToIntent('')).toEqual({ page: 'dashboard', params: {} });
  });

  it('maps /practice to training-workshop overview', () => {
    expect(pathToIntent('/practice')).toEqual({ page: 'training-workshop', params: {} });
  });

  it('maps /practice/<slug> to workspace module intents', () => {
    expect(pathToIntent('/practice/special-training')).toEqual({
      page: 'practice',
      params: { view: 'workspace', taskType: 'special_training', initialMode: 'case_training' },
    });
    expect(pathToIntent('/practice/mistake-redo')).toEqual({
      page: 'practice',
      params: { view: 'workspace', taskType: 'mistake_redo' },
    });
  });

  it('maps unknown /practice/<slug> back to overview', () => {
    expect(pathToIntent('/practice/not-a-module')).toEqual({ page: 'training-workshop', params: {} });
  });

  it('maps personalization sub-views', () => {
    expect(pathToIntent('/personalization')).toEqual({ page: 'personalization', params: { view: 'reports' } });
    expect(pathToIntent('/personalization/profile')).toEqual({ page: 'personalization', params: { view: 'user-profile' } });
    expect(pathToIntent('/personalization/unknown')).toEqual({ page: 'personalization', params: { view: 'reports' } });
  });

  it('maps top-level pages', () => {
    expect(pathToIntent('/dashboard')).toEqual({ page: 'dashboard', params: {} });
    expect(pathToIntent('/learning-path')).toEqual({ page: 'learning-path', params: {} });
    expect(pathToIntent('/assistant')).toEqual({ page: 'assistant', params: {} });
    expect(pathToIntent('/knowledge')).toEqual({ page: 'knowledge', params: {} });
    expect(pathToIntent('/settings')).toEqual({ page: 'settings', params: {} });
    expect(pathToIntent('/resources')).toEqual({ page: 'practice', params: {} });
  });

  it('returns null for unknown paths', () => {
    expect(pathToIntent('/no-such-page')).toBeNull();
    expect(pathToIntent('/practice')).not.toBeNull();
  });
});

describe('round-trip', () => {
  it('converts intent → path → intent for workshop modules', () => {
    const intents = [
      { page: 'practice', params: { view: 'workspace', taskType: 'special_training' } },
      { page: 'practice', params: { view: 'workspace', taskType: 'ai_patient_simulation' } },
      { page: 'training-workshop', params: {} },
      { page: 'personalization', params: { view: 'memory' } },
      { page: 'dashboard', params: {} },
    ];
    for (const intent of intents) {
      const path = intentToPath(intent);
      const restored = pathToIntent(path);
      expect(restored.page).toBe(intent.page);
      expect(restored.params.taskType).toBe(intent.params.taskType);
      expect(restored.params.view).toBe(intent.params.view === 'workspace' ? 'workspace' : intent.params.view);
    }
  });
});
