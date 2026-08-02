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

const target = {
  target_id: 'tcm_physician',
  official_name: '中医执业医师资格考试',
  target_type: 'certification',
  exam_track_id: 'EXAM_2025_TCM_PHYSICIAN',
  textbook_route_id: 'textbook_tcm_physician',
  textbook_route_version: 1,
};

const template = {
  groups: [
    {
      key: 'academic',
      title: '学历教育群体',
      default_profile: {
        learning_goal: '课程达标',
        resource_preference: ['经典教材', '分阶测试题'],
      },
    },
    {
      key: 'cross_professional',
      title: '跨专业进阶群体',
      default_profile: {
        learning_goal: '能力进阶',
        resource_preference: ['讲义讲解'],
      },
    },
  ],
  questions: [],
  required_fields: ['learner_group'],
};

function installRequests({ savedSurvey = {}, completionUser = null } = {}) {
  const requests = [];
  vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
    requests.push({ url, options });
    if (url.endsWith('/training/onboarding/group-templates')) return jsonResponse(template);
    if (url.endsWith('/api/v1/qualification-targets')) return jsonResponse({ items: [target] });
    if (url.endsWith('/training/onboarding/status')) {
      return jsonResponse({
        status: Object.keys(savedSurvey).length ? 'onboarding_completed' : 'pending',
        survey_answers: savedSurvey,
      });
    }
    if (url.endsWith('/training/onboarding/survey')) {
      return jsonResponse({ status: 'onboarding_completed' });
    }
    if (url.endsWith('/api/v1/auth/onboarding/complete')) {
      return jsonResponse({
        user: completionUser || { username: 'new-user', onboarding_required: false },
      });
    }
    throw new Error(`Unexpected request: ${url}`);
  }));
  return requests;
}

function choose(name) {
  fireEvent.click(screen.getByRole('radio', { name: new RegExp(name) }));
}

function continueStep() {
  fireEvent.click(screen.getByRole('button', { name: '继续' }));
}

async function completeRequiredSteps() {
  choose('学历教育群体');
  continueStep();
  fireEvent.click(await screen.findByRole('radio', { name: new RegExp(target.official_name) }));
  continueStep();
  choose('非医学专业');
  continueStep();
  choose('零基础');
  continueStep();
  choose('30–60 分钟');
  continueStep();
}

describe('OnboardingSurveyPanel', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('shows one question at a time, saves the trusted target, and allows optional skips', async () => {
    const requests = installRequests();
    const onSaved = vi.fn();
    render(<OnboardingSurveyPanel required stepOffset={1} onSaved={onSaved} onBackToAccount={vi.fn()} />);

    expect(await screen.findByRole('radio', { name: /学历教育群体/ })).toBeInTheDocument();
    await completeRequiredSteps();
    expect(screen.getByRole('heading', { name: '你通常喜欢在什么时候学习？' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));
    expect(screen.getByRole('heading', { name: '你更喜欢哪一种学习资源？' })).toBeInTheDocument();
    choose('讲义讲解');
    fireEvent.click(screen.getByRole('button', { name: '完成并进入学习' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledTimes(1));
    const surveyRequest = requests.find(({ url }) => url.endsWith('/training/onboarding/survey'));
    expect(JSON.parse(surveyRequest.options.body)).toMatchObject({
      learner_group: 'academic',
      background: {
        education_major: '非医学专业',
        foundation_level: '零基础',
      },
      preferences: {
        daily_available_minutes: 45,
        preferred_time_slot: '',
        resource_preference: '讲义讲解',
      },
      goals: {
        target_exam_or_course: target.official_name,
        textbook_route_id: target.textbook_route_id,
        textbook_route_version: 1,
      },
      target_type: 'certification',
      exam_track_id: target.exam_track_id,
    });
  });

  it('completes the auth gate and supports going back on every survey step', async () => {
    const onBackToAccount = vi.fn();
    const completionUser = { username: 'new-user', onboarding_required: false };
    const requests = installRequests({ completionUser });
    const onSaved = vi.fn();
    render(
      <OnboardingSurveyPanel
        required
        stepOffset={1}
        onSaved={onSaved}
        onBackToAccount={onBackToAccount}
      />,
    );

    await screen.findByRole('radio', { name: /学历教育群体/ });
    fireEvent.click(screen.getByRole('button', { name: '上一步' }));
    expect(onBackToAccount).toHaveBeenCalledTimes(1);

    await completeRequiredSteps();
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ user: completionUser }));
    expect(requests.some(({ url }) => url.endsWith('/api/v1/auth/onboarding/complete'))).toBe(true);
  });

  it('restores saved choices and speaks the group recommendation as Li Shizhen', async () => {
    installRequests({
      savedSurvey: {
        learner_group: 'academic',
        major_or_role: '非医学专业',
        tcm_foundation: '零基础',
        target_exam_or_course: target.official_name,
        qualification_target_id: target.target_id,
        daily_available_minutes: 45,
        preferred_time_slot: '晚间',
        resource_preference: ['讲义讲解'],
      },
    });

    render(<OnboardingSurveyPanel stepOffset={1} onBackToAccount={vi.fn()} />);

    const selectedGroup = await screen.findByRole('radio', { name: /学历教育群体/ });
    expect(selectedGroup).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByText('好，根据你这一情况，我优先推荐你学习经典教材、分阶测试题。')).toBeInTheDocument();
    continueStep();
    expect(await screen.findByRole('radio', { name: new RegExp(target.official_name) })).toHaveAttribute('aria-checked', 'true');
    continueStep();
    expect(screen.getByRole('radio', { name: /非医学专业/ })).toHaveAttribute('aria-checked', 'true');
  });

  it('uses a page-specific exit label when embedded as an editable survey', async () => {
    installRequests();
    render(
      <OnboardingSurveyPanel
        exitLabel="退出调研"
        onExit={vi.fn()}
      />,
    );

    expect(await screen.findByRole('button', { name: '退出调研' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '退出注册' })).not.toBeInTheDocument();
  });
});
