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
        resource_preference: ['知识卡片'],
      },
    },
  ],
  questions: [],
  required_fields: ['learner_group'],
};

function installRequests({ savedSurvey = {}, completionUser = null, groupTemplate = template } = {}) {
  const requests = [];
  vi.stubGlobal('fetch', vi.fn((url, options = {}) => {
    requests.push({ url, options });
    if (url.endsWith('/training/onboarding/group-templates')) return jsonResponse(groupTemplate);
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
  fireEvent.click(screen.queryByRole('radio', { name: new RegExp(name) }) || screen.getByRole('checkbox', { name: new RegExp(name) }));
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

  it('offers exactly three baseline classes and submits the selected background', async () => {
    const groups = [
      ['low', '低（low）', '非医学专业，无系统中医学学习经历', '零基础非医学专业'],
      ['medium', '中（medium）', '医学相关专业，接触过中医基础课程', '一定基础医学专业'],
      ['high', '高（high）', '中医学专业，接受过系统课程训练', '有基础中医药专业'],
    ].map(([key, title, description, readiness]) => ({ key, title, description, readiness,
      baseline_background: { education_major: description, foundation_level: readiness },
      default_profile: { resource_preference: [] },
    }));
    const requests = installRequests({ groupTemplate: { groups }, savedSurvey: { learner_group: 'academic' } });
    const onSaved = vi.fn();
    render(<OnboardingSurveyPanel required lockedTarget={target} onSaved={onSaved} />);
    await screen.findByRole('radio', { name: /低（low）/ });
    expect(screen.getAllByRole('radio')).toHaveLength(3);
    expect(screen.queryByRole('radio', { name: /学历教育/ })).not.toBeInTheDocument();
    expect(screen.getAllByRole('radio').every(option => option.getAttribute('aria-checked') === 'false')).toBe(true);
    choose('中（medium）');
    continueStep();
    expect(screen.getByRole('heading', { name: '你每天大约能投入多少时间？' })).toBeInTheDocument();
    choose('30–60 分钟');
    continueStep();
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));
    choose('案例训练');
    choose('视频');
    continueStep();
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    const request = requests.find(({ url }) => url.endsWith('/training/onboarding/survey'));
    expect(JSON.parse(request.options.body)).toMatchObject({
      learner_group: 'medium',
      background: { education_major: groups[1].description, foundation_level: groups[1].readiness },
      preferences: { resource_preference: ['案例训练', '视频'] },
    });
  });

  it('restores every saved preference including an older unlisted value', async () => {
    const groups = [{ key: 'low', title: '低（low）', baseline_background: { education_major: '非医学专业', foundation_level: '零基础' } }];
    installRequests({ groupTemplate: { groups }, savedSurvey: {
      learner_group: 'low', daily_available_minutes: 45, resource_preference: ['视频', '案例训练', '历史自定义偏好'],
    } });
    render(<OnboardingSurveyPanel lockedTarget={target} />);
    await screen.findByRole('radio', { name: /低（low）/ });
    continueStep();
    continueStep();
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));
    for (const name of ['视频', '案例训练', '历史自定义偏好']) {
      expect(screen.getByRole('checkbox', { name: new RegExp(name) })).toHaveAttribute('aria-checked', 'true');
    }
  });

  it('shows one question at a time, saves the trusted target, and allows optional skips', async () => {
    const requests = installRequests();
    const onSaved = vi.fn();
    render(<OnboardingSurveyPanel required stepOffset={1} onSaved={onSaved} onBackToAccount={vi.fn()} />);

    expect(await screen.findByRole('radio', { name: /学历教育群体/ })).toBeInTheDocument();
    await completeRequiredSteps();
    expect(screen.getByRole('heading', { name: '你通常喜欢在什么时候学习？' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));
    expect(screen.getByRole('heading', { name: '你更喜欢哪些学习资源？' })).toBeInTheDocument();
    choose('知识卡片');
    continueStep();
    expect(screen.getByRole('heading', { name: '你还有什么特别的学习需求或偏好？' })).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: '希望侧重方剂背诵，每天只学 30 分钟' } });
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
        resource_preference: ['知识卡片'],
      },
      goals: {
        target_exam_or_course: target.official_name,
        textbook_route_id: target.textbook_route_id,
        textbook_route_version: 1,
      },
      target_type: 'certification',
      exam_track_id: target.exam_track_id,
      custom_requirements: '希望侧重方剂背诵，每天只学 30 分钟',
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
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith({ user: completionUser }, ''));
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
        resource_preference: ['知识卡片'],
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

  it('restores and saves free-text custom requirements', async () => {
    const requests = installRequests({
      savedSurvey: {
        learner_group: 'academic',
        custom_requirements: '希望侧重方剂背诵',
      },
    });
    const onSaved = vi.fn();
    render(<OnboardingSurveyPanel lockedTarget={target} onSaved={onSaved} />);

    await screen.findByRole('radio', { name: /学历教育群体/ });
    choose('学历教育群体');
    continueStep();
    choose('非医学专业');
    continueStep();
    choose('零基础');
    continueStep();
    choose('30–60 分钟');
    continueStep();
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));

    expect(screen.getByRole('heading', { name: '你还有什么特别的学习需求或偏好？' })).toBeInTheDocument();
    const input = screen.getByRole('textbox');
    expect(input.value).toBe('希望侧重方剂背诵');
    fireEvent.change(input, { target: { value: '希望侧重方剂背诵，每天只学 30 分钟' } });
    fireEvent.click(screen.getByRole('button', { name: '完成并进入学习' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    const surveyRequest = requests.find(({ url }) => url.endsWith('/training/onboarding/survey'));
    expect(JSON.parse(surveyRequest.options.body).custom_requirements).toBe('希望侧重方剂背诵，每天只学 30 分钟');
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

  it('uses the current exam target without asking the learner to choose it again', async () => {
    const requests = installRequests();
    const onSaved = vi.fn();
    render(<OnboardingSurveyPanel lockedTarget={target} onSaved={onSaved} />);

    await screen.findByRole('radio', { name: /学历教育群体/ });
    choose('学历教育群体');
    continueStep();
    expect(screen.queryByRole('heading', { name: '你准备学习或参加哪项考试？' })).not.toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '你的学历或专业背景是？' })).toBeInTheDocument();

    choose('非医学专业');
    continueStep();
    choose('零基础');
    continueStep();
    choose('30–60 分钟');
    continueStep();
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));
    fireEvent.click(screen.getByRole('button', { name: '暂时跳过' }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    const surveyRequest = requests.find(({ url }) => url.endsWith('/training/onboarding/survey'));
    expect(JSON.parse(surveyRequest.options.body)).toMatchObject({
      exam_track_id: target.exam_track_id,
      goals: { target_exam_or_course: target.official_name },
    });
  });
});
