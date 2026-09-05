import React, { useEffect, useMemo, useState } from 'react';
import { Check, Loader2 } from 'lucide-react';
import {
  API_BASE,
  AUTH_API_BASE,
  MAIN_API_BASE,
  fetchWithAuth,
  readJsonResponse,
} from '../utils/api';
import RegistrationJourneyFrame from './RegistrationJourneyFrame';

const emptyTemplate = { groups: [], questions: [], required_fields: ['learner_group'] };
const emptyAnswers = { preferences: {}, goals: {}, background: {}, special_requirements: {}, locked_fields: [] };

const fallbackGroups = [
  { key: 'low', title: '低（low）', description: '非医学专业，无系统中医学学习经历', readiness: '零基础非医学专业', baseline_background: { education_major: '非医学专业，无系统中医学学习经历', foundation_level: '零基础非医学专业' } },
  { key: 'medium', title: '中（medium）', description: '医学相关专业，接触过中医基础课程', readiness: '一定基础医学专业', baseline_background: { education_major: '医学相关专业，接触过中医基础课程', foundation_level: '一定基础医学专业' } },
  { key: 'high', title: '高（high）', description: '中医学专业，接受过系统课程训练', readiness: '有基础中医药专业', baseline_background: { education_major: '中医学专业，接受过系统课程训练', foundation_level: '有基础中医药专业' } },
];

const profileQuestions = [
  {
    bucket: 'background',
    key: 'education_major',
    eyebrow: '学习背景',
    title: '你的学历或专业背景是？',
    description: '这会帮助我判断知识讲解需要从哪里开始。',
    options: ['中医药相关专业', '西医/护理/康复相关专业', '非医学专业', '暂不确定'],
    requiredWhenRegistering: true,
    mascotMessage: '背景不同，适合的起点也不同。选最接近你的情况即可。',
  },
  {
    bucket: 'background',
    key: 'foundation_level',
    eyebrow: '当前基础',
    title: '你现在的中医药基础如何？',
    description: '不用担心选低了，真实情况比漂亮答案更有用。',
    options: ['零基础', '了解基础术语', '学过核心课程', '具备案例训练基础'],
    requiredWhenRegistering: true,
    mascotMessage: '如实选择就好，我会据此调整讲解深度和训练难度。',
  },
  {
    bucket: 'preferences',
    key: 'daily_available_minutes',
    eyebrow: '学习节奏',
    title: '你每天大约能投入多少时间？',
    description: '系统会据此控制每日任务量，避免计划过重。',
    options: [
      { value: 15, label: '15 分钟以内' },
      { value: 25, label: '15–30 分钟' },
      { value: 45, label: '30–60 分钟' },
      { value: 75, label: '60 分钟以上' },
    ],
    requiredWhenRegistering: true,
    mascotMessage: '稳定比一次学很久更重要。选择你大多数日子能做到的时长。',
  },
  {
    bucket: 'preferences',
    key: 'preferred_time_slot',
    eyebrow: '学习习惯 · 选填',
    title: '你通常喜欢在什么时候学习？',
    description: '这是选填项。之后也可以在个人画像中修改。',
    options: ['早晨', '午间', '晚间', '碎片时间'],
    mascotMessage: '固定一个顺手的时间段，更容易把学习坚持下来。',
  },
  {
    bucket: 'preferences',
    key: 'resource_preference',
    eyebrow: '内容偏好 · 选填',
    title: '你更喜欢哪些学习资源？',
    description: '这是最后一步。暂时不确定也可以直接跳过。',
    options: ['知识卡片', '讲义讲解', '分阶测试题', '案例训练', '视频'],
    mascotMessage: '最后一个问题。告诉我你更喜欢怎样学，我会优先安排同类内容。',
  },
];

const restoreSurveyAnswers = (survey = {}) => ({
  ...emptyAnswers,
  background: {
    education_major: survey.major_or_role || survey.education || '',
    foundation_level: survey.tcm_foundation || '',
  },
  goals: {},
  preferences: {
    daily_available_minutes: survey.daily_available_minutes || '',
    preferred_time_slot: survey.preferred_time_slot || '',
    resource_preference: Array.isArray(survey.resource_preference)
      ? survey.resource_preference : (survey.resource_preference ? [survey.resource_preference] : []),
  },
  special_requirements: {},
  locked_fields: Array.isArray(survey.locked_fields) ? survey.locked_fields : [],
});

function optionValue(option) {
  return typeof option === 'object' ? option.value : option;
}

function optionLabel(option) {
  return typeof option === 'object' ? option.label : option;
}

export default function OnboardingSurveyPanel({
  onSaved,
  lockedTarget = null,
  required = false,
  stepOffset = 0,
  onBackToAccount,
  onExit,
  exitLabel = '退出注册',
}) {
  const [template, setTemplate] = useState(emptyTemplate);
  const [routes, setRoutes] = useState([]);
  const [selectedRouteId, setSelectedRouteId] = useState('');
  const [selectedGroup, setSelectedGroup] = useState('');
  const [answers, setAnswers] = useState(emptyAnswers);
  const [step, setStep] = useState(0);
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [customRequirements, setCustomRequirements] = useState('');

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const [templateRes, routesRes, statusRes] = await Promise.all([
          fetchWithAuth(`${API_BASE}/training/onboarding/group-templates`),
          fetchWithAuth(`${MAIN_API_BASE}/qualification-targets`),
          fetchWithAuth(`${API_BASE}/training/onboarding/status`),
        ]);
        const [templateData, routesData, statusData] = await Promise.all([
          readJsonResponse(templateRes, emptyTemplate),
          readJsonResponse(routesRes, { items: [] }),
          readJsonResponse(statusRes, {}),
        ]);
        if (cancelled) return;
        setTemplate({ ...emptyTemplate, ...templateData });
        const targetItems = Array.isArray(routesData.items) ? routesData.items : [];
        setRoutes(targetItems);
        const savedSurvey = statusData.survey_answers || {};
        const lockedRoute = targetItems.find((item) => (
          item.target_id === lockedTarget?.target_id
          || item.exam_track_id === lockedTarget?.exam_track_id
        ));
        if (lockedRoute) setSelectedRouteId(lockedRoute.target_id);
        if (Object.keys(savedSurvey).length > 0) {
          const availableGroups = templateData.groups?.length ? templateData.groups : fallbackGroups;
          setSelectedGroup(availableGroups.some(group => group.key === savedSurvey.learner_group)
            ? savedSurvey.learner_group : '');
          const savedTarget = targetItems.find(
            (item) => item.target_id === savedSurvey.qualification_target_id
              || item.official_name === savedSurvey.target_exam_or_course,
          );
          if (!lockedRoute) setSelectedRouteId(savedTarget?.target_id || '');
          setAnswers(restoreSurveyAnswers(savedSurvey));
          setCustomRequirements(String(savedSurvey.custom_requirements || ''));
        }
      } catch (reason) {
        if (!cancelled) setError(reason.message || '学情调查模板加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [lockedTarget?.exam_track_id, lockedTarget?.target_id]);

  const groups = useMemo(() => (
    template.groups.length > 0
      ? template.groups.map((group) => ({
        key: group.key,
        title: group.title,
        description: group.description,
        readiness: group.readiness,
        baseline_background: group.baseline_background,
        resources: group.default_profile?.resource_preference || [],
      }))
      : fallbackGroups
  ), [template.groups]);

  const selectedTemplate = groups.find((item) => item.key === selectedGroup);
  const activeProfileQuestions = profileQuestions.filter(question => (
    !selectedTemplate?.baseline_background || question.bucket !== 'background'
  ));
  const surveyQuestions = [
    {
      type: 'group',
      eyebrow: '认识你',
      title: '请选择你的基线画像',
      description: '按专业背景与前置知识就绪度选择低、中、高三类；不代表当前学习成绩。',
      options: groups.map((group) => ({ value: group.key, label: group.title, description: group.description, readiness: group.readiness })),
      required: true,
      mascotMessage: selectedTemplate?.resources?.length
        ? `好，根据你这一情况，我优先推荐你学习${selectedTemplate.resources.join('、')}。`
        : '先告诉我你属于哪一类学习者，我会据此调整推荐内容。',
    },
    ...(!lockedTarget ? [{
      type: 'route',
      eyebrow: '确定目标',
      title: '你准备学习或参加哪项考试？',
      description: '学习目标由所选资格考试确定，这是必填项。',
      options: routes.map((route) => ({ value: route.target_id, label: route.official_name || route.target_id })),
      required: true,
      mascotMessage: '目标清楚，学习路线才不会绕远。请选择你当前最主要的考试方向。',
    }] : []),
    ...activeProfileQuestions.map((question) => ({
      ...question,
      type: 'profile',
      required: required && question.requiredWhenRegistering,
    })),
    {
      type: 'custom_requirements',
      eyebrow: '自定义需求 · 选填',
      title: '你还有什么特别的学习需求或偏好？',
      description: '选填。这些内容会作为后续制定学习规划的参考。',
      required: false,
      mascotMessage: '例如侧重方剂背诵、每天只学 30 分钟、需要大量重复练习、想同时准备其他考试等。',
    },
  ];
  const current = surveyQuestions[step];
  const totalSteps = stepOffset + surveyQuestions.length;
  const absoluteStep = Math.min(stepOffset + step + 1, totalSteps);

  const currentValue = current.type === 'group'
    ? selectedGroup
    : current.type === 'route'
      ? selectedRouteId
      : current.type === 'custom_requirements'
        ? customRequirements
        : answers[current.bucket]?.[current.key] || '';

  const setCurrentValue = (value) => {
    setError('');
    if (current.type === 'group') {
      setSelectedGroup(value);
      const background = groups.find(group => group.key === value)?.baseline_background;
      if (background) setAnswers(existing => ({ ...existing, background: { ...existing.background, ...background } }));
      return;
    }
    if (current.type === 'route') {
      setSelectedRouteId(value);
      return;
    }
    setAnswers((existing) => ({
      ...existing,
      [current.bucket]: {
        ...(existing[current.bucket] || {}),
        [current.key]: value,
      },
    }));
  };

  const submit = async (submittedAnswers = answers) => {
    setError('');
    if (!selectedGroup) {
      setError('请选择所属用户群体');
      return;
    }
    if (!selectedRouteId) {
      setError('请选择资格考试');
      return;
    }
    if (required) {
      const missing = activeProfileQuestions
        .filter((question) => question.requiredWhenRegistering)
        .find((question) => !submittedAnswers[question.bucket]?.[question.key]);
      if (missing) {
        setError(`请完成“${missing.title}”`);
        return;
      }
    }
    const selectedRoute = routes.find((route) => route.target_id === selectedRouteId);
    if (!selectedRoute) {
      setError('所选资格考试已不可用，请重新选择');
      return;
    }
    setSaving(true);
    try {
      const surveyRes = await fetchWithAuth(`${API_BASE}/training/onboarding/survey`, {
        method: 'POST',
        body: JSON.stringify({
          ...submittedAnswers,
          custom_requirements: customRequirements.trim(),
          goals: {
            ...(submittedAnswers.goals || {}),
            target_exam_or_course: selectedRoute.official_name,
            textbook_route_id: selectedRoute.textbook_route_id,
            textbook_route_version: selectedRoute.textbook_route_version,
          },
          learner_group: selectedGroup,
          target_type: selectedRoute.target_type,
          exam_track_id: selectedRoute.exam_track_id,
        }),
      });
      const surveyData = await readJsonResponse(surveyRes, {});
      if (!surveyRes.ok) throw new Error(surveyData.detail || '学情调查保存失败');
      let completionData = surveyData;
      if (required) {
        const completionRes = await fetchWithAuth(`${AUTH_API_BASE}/onboarding/complete`, { method: 'POST' });
        completionData = await readJsonResponse(completionRes, {});
        if (!completionRes.ok) {
          throw new Error(completionData.detail || '学情调查已保存，但注册初始化未完成');
        }
      }
      onSaved?.(completionData, customRequirements.trim());
    } catch (reason) {
      setError(reason.message || '学情调查保存失败');
    } finally {
      setSaving(false);
    }
  };

  const moveForward = async () => {
    if (current.required && !currentValue) {
      setError('这是必填项，请先选择一个选项');
      return;
    }
    if (step === surveyQuestions.length - 1) {
      await submit();
      return;
    }
    setStep((value) => value + 1);
    setError('');
  };

  const skip = async () => {
    if (current.type === 'custom_requirements') {
      setCustomRequirements('');
    }
    const nextAnswers = {
      ...answers,
      [current.bucket]: {
        ...(answers[current.bucket] || {}),
        [current.key]: current.key === 'resource_preference' ? [] : '',
      },
    };
    setAnswers(nextAnswers);
    setError('');
    if (step === surveyQuestions.length - 1) {
      await submit(nextAnswers);
      return;
    }
    setStep((value) => value + 1);
  };

  const goBack = () => {
    setError('');
    if (step > 0) {
      setStep((value) => value - 1);
    } else {
      onBackToAccount?.();
    }
  };

  return (
    <RegistrationJourneyFrame
      step={absoluteStep}
      totalSteps={totalSteps}
      eyebrow={current.eyebrow}
      title={current.title}
      description={current.description}
      mascotMessage={current.mascotMessage}
      onExit={onExit}
      exitLabel={exitLabel}
    >
      {loading && step < 2 ? (
        <div className="registration-journey__options" aria-label="正在加载选项">
          <div className="registration-journey__locked">
            <Loader2 className="animate-spin" size={22} />
            <div><strong>正在准备问题</strong><span>马上就好</span></div>
          </div>
        </div>
      ) : current.type === 'custom_requirements' ? (
        <div className="registration-journey__custom" aria-label={current.title}>
          <textarea
            className="registration-journey__custom-input"
            value={customRequirements}
            onChange={(event) => setCustomRequirements(event.target.value)}
            placeholder="例如：希望侧重方剂背诵、每天只学 30 分钟、需要大量重复练习、想同时准备其他考试…"
            rows={5}
            maxLength={500}
          />
        </div>
      ) : (
        <div className="registration-journey__options" role={current.key === 'resource_preference' ? 'group' : 'radiogroup'} aria-label={current.title}>
          {[...current.options, ...(current.key === 'resource_preference' && Array.isArray(currentValue)
            ? currentValue.filter(value => !current.options.includes(value)) : [])].map((option, index) => {
            const value = optionValue(option);
            const label = optionLabel(option);
            const multiple = current.key === 'resource_preference';
            const selected = multiple ? (Array.isArray(currentValue) && currentValue.includes(value)) : String(currentValue) === String(value);
            return (
              <button
                key={value}
                type="button"
                role={multiple ? 'checkbox' : 'radio'}
                aria-checked={selected}
                data-selected={String(selected)}
                className="registration-journey__option"
                onClick={() => setCurrentValue(multiple
                  ? (selected ? currentValue.filter(item => item !== value) : [...(Array.isArray(currentValue) ? currentValue : []), value])
                  : value)}
              >
                <span className="registration-journey__option-index">{String.fromCharCode(65 + index)}</span>
                <span className="registration-journey__option-label">
                  {label}
                  {option.description && <span className="block mt-2 text-sm font-normal">专业背景：{option.description}</span>}
                  {option.readiness && <span className="block mt-1 text-sm font-normal">前置知识就绪度：{option.readiness}</span>}
                </span>
                <Check className="registration-journey__option-check" size={19} aria-hidden="true" />
              </button>
            );
          })}
        </div>
      )}

      {error && <div className="registration-journey__error" role="alert">{error}</div>}
      <div className="registration-journey__actions">
        <button
          type="button"
          className="registration-journey__back"
          onClick={goBack}
          disabled={step === 0 && !onBackToAccount}
        >
          上一步
        </button>
        <button
          type="button"
          className="registration-journey__primary"
          onClick={moveForward}
          disabled={loading || saving || (current.required && !currentValue)}
        >
          {saving
            ? <Loader2 className="mx-auto animate-spin" size={20} />
            : step === surveyQuestions.length - 1 ? '完成并进入学习' : '继续'}
        </button>
      </div>
      {!current.required && (
        <button type="button" className="registration-journey__skip" onClick={skip} disabled={saving}>
          暂时跳过
        </button>
      )}
    </RegistrationJourneyFrame>
  );
}
