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

  it('loads the five supported qualification exams and saves the trusted target', async () => {
    const requests = [];
    const targets = [
      ['tcm_physician', '中医执业医师资格考试', 'EXAM_2025_TCM_PHYSICIAN', 'textbook_tcm_physician'],
      ['tcm_assistant', '中医执业助理医师资格考试', 'EXAM_2025_TCM_ASSISTANT', 'textbook_tcm_physician'],
      ['integrated_physician', '中西医结合执业医师资格考试', 'EXAM_2025_INTEGRATED_PHYSICIAN', 'textbook_integrated_clinical'],
      ['integrated_assistant', '中西医结合执业助理医师资格考试', 'EXAM_2025_INTEGRATED_ASSISTANT', 'textbook_integrated_clinical'],
      ['licensed_pharmacist_tcm', '执业药师职业资格考试（中药学类）', 'EXAM_TCM_LICENSED_PHARMACIST', 'textbook_tcm_pharmacy'],
    ].map(([target_id, official_name, exam_track_id, textbook_route_id]) => ({
      target_id,
      official_name,
      exam_track_id,
      textbook_route_id,
      textbook_route_version: 1,
      target_type: 'certification',
    }));
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      requests.push({ url, options });
      if (url.endsWith('/training/onboarding/group-templates')) {
        return jsonResponse({ groups: [], questions: [], required_fields: ['learner_group'] });
      }
      if (url.endsWith('/api/v1/qualification-targets')) {
        return jsonResponse({
          schema_version: '1.0',
          items: targets,
          total: 5,
        });
      }
      if (url.endsWith('/training/onboarding/status')) {
        return jsonResponse({ status: 'pending', survey_answers: {} });
      }
      if (url.endsWith('/training/onboarding/survey')) {
        return jsonResponse({ status: 'onboarding_completed' });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    const onSaved = vi.fn();
    render(<OnboardingSurveyPanel onSaved={onSaved} />);

    for (const [, label] of targets.map((item) => [item.target_id, item.official_name])) {
      expect(await screen.findByRole('option', { name: label })).toBeInTheDocument();
    }
    fireEvent.change(screen.getByLabelText('所属用户群体'), {
      target: { value: 'academic' },
    });
    fireEvent.change(screen.getByLabelText('学习/考试方向'), {
      target: { value: 'tcm_physician' },
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
      goals: {
        target_exam_or_course: '中医执业医师资格考试',
        textbook_route_id: 'textbook_tcm_physician',
        textbook_route_version: 1,
      },
      target_type: 'certification',
      exam_track_id: 'EXAM_2025_TCM_PHYSICIAN',
    });
    expect(requests.some(({ url }) => url.endsWith('/personalization/learning-target'))).toBe(false);
    expect(screen.queryByLabelText('长期目标')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('短期目标')).not.toBeInTheDocument();
    expect(screen.queryByText('规划输入')).not.toBeInTheDocument();
  });

  it('requires registration basics and completes the auth gate after saving', async () => {
    const requests = [];
    vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
      requests.push({ url, options });
      if (url.endsWith('/training/onboarding/group-templates')) {
        return jsonResponse({ groups: [], questions: [], required_fields: ['learner_group'] });
      }
      if (url.endsWith('/api/v1/qualification-targets')) {
        return jsonResponse({
          schema_version: '1.0',
          items: [{
            target_id: 'tcm_physician',
            official_name: '中医执业医师资格考试',
            target_type: 'certification',
            exam_track_id: 'EXAM_2025_TCM_PHYSICIAN',
            textbook_route_id: 'textbook_tcm_physician',
            textbook_route_version: 1,
          }],
        });
      }
      if (url.endsWith('/training/onboarding/status')) {
        return jsonResponse({ status: 'pending', survey_answers: {} });
      }
      if (url.endsWith('/training/onboarding/survey')) {
        return jsonResponse({ status: 'onboarding_completed' });
      }
      if (url.endsWith('/api/v1/auth/onboarding/complete')) {
        return jsonResponse({
          user: { username: 'new-user', onboarding_required: false },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));
    const onSaved = vi.fn();
    render(<OnboardingSurveyPanel required onSaved={onSaved} />);

    await screen.findByRole('option', { name: '中医执业医师资格考试' });
    fireEvent.change(screen.getByLabelText('所属用户群体'), { target: { value: 'academic' } });
    fireEvent.change(screen.getByLabelText('学习/考试方向'), { target: { value: 'tcm_physician' } });
    fireEvent.change(screen.getByLabelText('学历/专业'), { target: { value: '非医学专业' } });
    fireEvent.change(screen.getByLabelText('基础水平'), { target: { value: '零基础' } });
    fireEvent.change(screen.getByLabelText('每日可投入时长'), { target: { value: '45' } });
    fireEvent.click(screen.getByRole('button', { name: '保存学情调查' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({
      user: { username: 'new-user', onboarding_required: false },
    }));
    expect(requests.some(({ url }) => url.endsWith('/api/v1/auth/onboarding/complete'))).toBe(true);
  });

  it('restores the saved registration survey when the learner revisits the survey tab', async () => {
    vi.stubGlobal('fetch', vi.fn((url) => {
      if (url.endsWith('/training/onboarding/group-templates')) {
        return jsonResponse({
          groups: [
            {
              key: 'academic',
              title: '学历教育群体',
              default_profile: { learning_goal: '课程达标', resource_preference: ['教材'] },
            },
            {
              key: 'cross_professional',
              title: '跨专业进阶群体',
              default_profile: { learning_goal: '能力进阶', resource_preference: ['知识卡片'] },
            },
          ],
          questions: [],
          required_fields: ['learner_group'],
        });
      }
      if (url.endsWith('/api/v1/qualification-targets')) {
        return jsonResponse({
          items: [{
            target_id: 'tcm_physician',
            official_name: '中医执业医师资格考试',
            target_type: 'certification',
            exam_track_id: 'EXAM_2025_TCM_PHYSICIAN',
            textbook_route_id: 'textbook_tcm_physician',
            textbook_route_version: 1,
          }],
        });
      }
      if (url.endsWith('/training/onboarding/status')) {
        return jsonResponse({
          status: 'onboarding_completed',
          survey_answers: {
            learner_group: 'academic',
            major_or_role: '非医学专业',
            tcm_foundation: '零基础',
            target_exam_or_course: '中医执业医师资格考试',
            qualification_target_id: 'tcm_physician',
            textbook_route_id: 'textbook_tcm_physician',
            textbook_route_version: 1,
            daily_available_minutes: 45,
            preferred_time_slot: '晚间',
            resource_preference: ['知识卡片'],
          },
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    }));

    render(<OnboardingSurveyPanel />);

    expect(await screen.findByLabelText('所属用户群体')).toHaveValue('academic');
    expect(screen.getByLabelText('学习/考试方向')).toHaveValue('tcm_physician');
    expect(screen.getByLabelText('学历/专业')).toHaveValue('非医学专业');
    expect(screen.getByLabelText('基础水平')).toHaveValue('零基础');
    expect(screen.queryByLabelText('长期目标')).not.toBeInTheDocument();
    expect(screen.getByLabelText('每日可投入时长')).toHaveValue('45');
    expect(screen.getByLabelText('偏好学习时段')).toHaveValue('晚间');
    expect(screen.getByLabelText('偏好资源类型')).toHaveValue('知识卡片');
  });
});
