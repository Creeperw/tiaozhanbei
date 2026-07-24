import React, { useEffect, useState } from 'react';
import {
  API_BASE,
  AUTH_API_BASE,
  MAIN_API_BASE,
  fetchWithAuth,
  readJsonResponse,
} from '../utils/api';

const emptyTemplate = { groups: [], questions: [], required_fields: ['learner_group'] };
const emptyAnswers = { preferences: {}, goals: {}, background: {}, special_requirements: {}, locked_fields: [] };

const fallbackGroupOptions = [
  { value: '', label: '请选择所属用户群体' },
  { value: 'cross_professional', label: '跨专业进阶群体' },
  { value: 'academic', label: '学历教育群体' },
];

const surveySections = [
  {
    title: 'L0 画像基线',
    description: '用于建立学习基础、时间与资源偏好，后续会作为学情智能体输入。',
    fields: [
      { bucket: 'background', key: 'education_major', label: '学历/专业', options: ['中医药相关专业', '西医/护理/康复相关专业', '非医学专业', '暂不确定'] },
      { bucket: 'background', key: 'foundation_level', label: '基础水平', options: ['零基础', '了解基础术语', '学过核心课程', '具备案例训练基础'] },
      {
        bucket: 'preferences',
        key: 'daily_available_minutes',
        label: '每日可投入时长',
        options: [
          { value: 15, label: '15 分钟以内' },
          { value: 25, label: '15-30 分钟' },
          { value: 45, label: '30-60 分钟' },
          { value: 75, label: '60 分钟以上' },
        ],
      },
      { bucket: 'preferences', key: 'preferred_time_slot', label: '偏好学习时段', options: ['早晨', '午间', '晚间', '碎片时间'] },
      { bucket: 'preferences', key: 'resource_preference', label: '偏好资源类型', options: ['知识卡片', '讲义讲解', '分阶测试题', '案例训练'] },
      { bucket: 'preferences', key: 'default_difficulty', label: '默认难度', options: ['基础入门', '课程学习', '案例辨证', '综合训练'] },
    ],
  },
];

const requiredRegistrationFields = [
  ['background', 'education_major', '学历/专业'],
  ['background', 'foundation_level', '基础水平'],
  ['preferences', 'daily_available_minutes', '每日可投入时长'],
];

const firstValue = (value) => (Array.isArray(value) ? (value[0] || '') : (value || ''));

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
    resource_preference: firstValue(survey.resource_preference),
    default_difficulty: survey.difficulty_preference || '',
  },
  special_requirements: {},
  locked_fields: Array.isArray(survey.locked_fields) ? survey.locked_fields : [],
});

export default function OnboardingSurveyPanel({ onSaved, required = false }) {
  const [template, setTemplate] = useState(emptyTemplate);
  const [routes, setRoutes] = useState([]);
  const [selectedRouteId, setSelectedRouteId] = useState('');
  const [selectedGroup, setSelectedGroup] = useState('');
  const [answers, setAnswers] = useState(emptyAnswers);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
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
        if (!cancelled) {
          setTemplate({ ...emptyTemplate, ...templateData });
          const targetItems = Array.isArray(routesData.items) ? routesData.items : [];
          setRoutes(targetItems);
          const savedSurvey = statusData.survey_answers || {};
          if (statusData.status === 'onboarding_completed') {
            setSelectedGroup(savedSurvey.learner_group || '');
            const savedTarget = targetItems.find(
              (item) => item.official_name === savedSurvey.target_exam_or_course,
            );
            setSelectedRouteId(savedTarget?.target_id || '');
            setAnswers(restoreSurveyAnswers(savedSurvey));
          }
        }
      } catch (e) {
        if (!cancelled) setError(e.message || '学情调查模板加载失败');
      }
    };
    load();
    return () => { cancelled = true; };
  }, []);

  const groupOptions = template.groups.length > 0
    ? [{ value: '', label: '请选择所属用户群体' }, ...template.groups.map((group) => ({ value: group.key, label: group.title }))]
    : fallbackGroupOptions;
  const selectedTemplate = template.groups.find((item) => item.key === selectedGroup);

  const updateAnswer = (bucket, key, value) => {
    setAnswers((current) => ({
      ...current,
      [bucket]: {
        ...(current[bucket] || {}),
        [key]: value,
      },
    }));
  };

  const submit = async () => {
    setError('');
    setMessage('');
    if (!selectedGroup) {
      setError('请选择所属用户群体');
      return;
    }
    if (!selectedRouteId) {
      setError('请选择资格考试');
      return;
    }
    if (required) {
      const missing = requiredRegistrationFields.find(
        ([bucket, key]) => !answers[bucket]?.[key],
      );
      if (missing) {
        setError(`请填写${missing[2]}`);
        return;
      }
    }
    setSaving(true);
    try {
      const selectedRoute = routes.find((route) => route.target_id === selectedRouteId);
      if (!selectedRoute) {
        setError('所选资格考试已不可用，请重新选择');
        return;
      }
      const surveyRes = await fetchWithAuth(`${API_BASE}/training/onboarding/survey`, {
        method: 'POST',
        body: JSON.stringify({
          ...answers,
          goals: {
            ...(answers.goals || {}),
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
      if (!surveyRes.ok) {
        setError(surveyData.detail || '学情调查保存失败');
        return;
      }
      let completionData = surveyData;
      if (required) {
        const completionRes = await fetchWithAuth(`${AUTH_API_BASE}/onboarding/complete`, {
          method: 'POST',
        });
        completionData = await readJsonResponse(completionRes, {});
        if (!completionRes.ok) {
          setError(completionData.detail || '学情调查已保存，但注册初始化未完成');
          return;
        }
      }
      setMessage('学情调查与考试目标已保存。');
      if (onSaved) onSaved(completionData);
    } catch (e) {
      setError(e.message || '学情调查保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="space-y-5">
      <div>
        <h2 className="text-xl font-semibold text-slate-950">学情调查</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          {required
            ? '请选择资格考试，并填写用户群体、学历/专业、基础水平和每日可投入时长；学习目标由所选考试确定。'
            : '群体是必选项；其它字段可跳过，系统会按群体模板建立可修改的先验画像。'}
        </p>
      </div>

      <label className="block rounded-[24px] border border-slate-200 bg-white p-4">
        <span className="text-sm font-medium text-slate-700">所属用户群体</span>
        <select
          value={selectedGroup}
          onChange={(event) => setSelectedGroup(event.target.value)}
          className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-800 outline-none transition focus:border-emerald-300 focus:ring-4 focus:ring-emerald-100"
        >
          {groupOptions.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </label>

      <label className="block rounded-[24px] border border-slate-200 bg-white p-4">
        <span className="text-sm font-medium text-slate-700">学习/考试方向</span>
        <select
          value={selectedRouteId}
          onChange={(event) => setSelectedRouteId(event.target.value)}
          className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-800 outline-none transition focus:border-emerald-300 focus:ring-4 focus:ring-emerald-100"
        >
          <option value="">请选择资格考试</option>
          {routes.map((route) => (
            <option key={route.target_id} value={route.target_id}>
              {route.official_name || route.target_id}
            </option>
          ))}
        </select>
      </label>

      {selectedTemplate && (
        <div className="rounded-[24px] border border-emerald-100 bg-emerald-50/70 p-4 text-sm leading-6 text-emerald-950">
          结合当前群体，优先推荐资源：{selectedTemplate.default_profile.resource_preference.join('、')}。
        </div>
      )}

      {surveySections.map((section) => (
        <div key={section.title} className="rounded-[24px] border border-slate-200 bg-white p-4">
          <div>
            <h3 className="text-sm font-semibold text-slate-950">{section.title}</h3>
            <p className="mt-1 text-xs leading-5 text-slate-500">{section.description}</p>
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {section.fields.map((field) => (
              <label key={`${field.bucket}.${field.key}`} className="block">
                <span className="text-xs font-medium text-slate-500">{field.label}</span>
                <select
                  value={answers[field.bucket]?.[field.key] || ''}
                  onChange={(event) => updateAnswer(
                    field.bucket,
                    field.key,
                    field.key === 'daily_available_minutes'
                      ? Number(event.target.value)
                      : event.target.value,
                  )}
                  className="mt-1 w-full rounded-2xl border border-slate-200 bg-white px-3 py-2.5 text-sm text-slate-800 outline-none transition focus:border-emerald-300 focus:ring-4 focus:ring-emerald-100"
                >
                  <option value="">{required && requiredRegistrationFields.some(([bucket, key]) => bucket === field.bucket && key === field.key) ? '请选择' : '暂不填写'}</option>
                  {field.options.map((option) => {
                    const value = typeof option === 'object' ? option.value : option;
                    const label = typeof option === 'object' ? option.label : option;
                    return <option key={value} value={value}>{label}</option>;
                  })}
                </select>
              </label>
            ))}
          </div>
        </div>
      ))}

      {error && <div className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
      {message && <div className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}
      <button
        type="button"
        onClick={submit}
        disabled={saving}
        className="rounded-full bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {saving ? '保存中…' : '保存学情调查'}
      </button>
    </section>
  );
}
