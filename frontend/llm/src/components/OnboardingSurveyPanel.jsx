import React, { useEffect, useState } from 'react';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';

const emptyTemplate = { groups: [], questions: [], required_fields: ['learner_group'] };
const emptyAnswers = { preferences: {}, goals: {}, background: {}, special_requirements: {}, locked_fields: [] };

const fallbackGroupOptions = [
  { value: '', label: '请选择所属用户群体' },
  { value: 'cross_professional', label: '跨专业进阶群体' },
  { value: 'academic', label: '学历教育群体' },
  { value: 'public_interest', label: '大众兴趣群体' },
];

const surveySections = [
  {
    title: 'L0 画像基线',
    description: '用于建立“原本应该怎样学”的先验画像，后续会作为学情智能体输入。',
    fields: [
      { bucket: 'background', key: 'education_major', label: '学历/专业', options: ['中医药相关专业', '西医/护理/康复相关专业', '非医学专业', '暂不确定'] },
      { bucket: 'background', key: 'foundation_level', label: '基础水平', options: ['零基础', '了解基础术语', '学过核心课程', '具备案例训练基础'] },
      { bucket: 'goals', key: 'long_term_goal', label: '长期目标', options: ['职业技能认证', '课程/考试达标', '辨证思维进阶', '大众健康素养提升'] },
      { bucket: 'goals', key: 'short_term_goal', label: '短期目标', options: ['章节补弱', '错题复盘', '期末/阶段测评', '案例辨证训练'] },
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
  {
    title: '规划输入',
    description: '对应长期规划、短期规划和今日任务输入维度，便于后续生成可执行路径。',
    fields: [
      { bucket: 'goals', key: 'application_scenario', label: '应用场景', options: ['专业素质养成', '职业技能认证', '大众健康素养', '机构资源生产'] },
      { bucket: 'goals', key: 'target_timeline', label: '目标期限', options: ['1-4 周', '1-3 个月', '3-12 个月', '一年以上'] },
      { bucket: 'background', key: 'current_stage', label: '当前阶段', options: ['基础入门', '核心技能', '案例实战', '冲刺复盘'] },
      { bucket: 'background', key: 'weak_area', label: '当前薄弱点', options: ['中医基础理论', '中医诊断', '中药方剂', '案例辨证'] },
      { bucket: 'special_requirements', key: 'temporary_constraints', label: '近期约束', options: ['时间减少', '考试周', '任务过载', '无明显约束'] },
      { bucket: 'special_requirements', key: 'today_window', label: '今日可执行窗口', options: ['10 分钟轻量任务', '20 分钟标准任务', '40 分钟集中学习', '今天不适合安排'] },
    ],
  },
];

export default function OnboardingSurveyPanel({ onSaved }) {
  const [template, setTemplate] = useState(emptyTemplate);
  const [tracks, setTracks] = useState([]);
  const [selectedTrackId, setSelectedTrackId] = useState('');
  const [selectedGroup, setSelectedGroup] = useState('');
  const [answers, setAnswers] = useState(emptyAnswers);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        const [templateRes, tracksRes] = await Promise.all([
          fetchWithAuth(`${API_BASE}/training/onboarding/group-templates`),
          fetchWithAuth(`${API_BASE}/exam-learning/tracks`),
        ]);
        const [templateData, tracksData] = await Promise.all([
          readJsonResponse(templateRes, emptyTemplate),
          readJsonResponse(tracksRes, { items: [] }),
        ]);
        if (!cancelled) {
          setTemplate({ ...emptyTemplate, ...templateData });
          setTracks(Array.isArray(tracksData.items) ? tracksData.items : []);
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
    if (!selectedTrackId) {
      setError('请选择具体考试目标');
      return;
    }
    setSaving(true);
    try {
      const surveyRes = await fetchWithAuth(`${API_BASE}/training/onboarding/survey`, {
        method: 'POST',
        body: JSON.stringify({
          ...answers,
          learner_group: selectedGroup,
          target_type: 'certification',
          exam_track_id: selectedTrackId,
          is_locked: true,
          lock_reason: '用户手动选择',
        }),
      });
      const surveyData = await readJsonResponse(surveyRes, {});
      if (!surveyRes.ok) {
        setError(surveyData.detail || '学情调查保存失败');
        return;
      }
      setMessage('学情调查与考试目标已保存。');
      if (onSaved) onSaved(surveyData);
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
        <p className="mt-2 text-sm leading-6 text-slate-600">群体是必选项；其它字段可跳过，系统会按群体模板建立可修改的先验画像。</p>
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
        <span className="text-sm font-medium text-slate-700">具体考试目标</span>
        <select
          value={selectedTrackId}
          onChange={(event) => setSelectedTrackId(event.target.value)}
          className="mt-2 w-full rounded-2xl border border-slate-200 bg-white px-4 py-2.5 text-sm text-slate-800 outline-none transition focus:border-emerald-300 focus:ring-4 focus:ring-emerald-100"
        >
          <option value="">请选择当前可用考证轨道</option>
          {tracks.map((track) => (
            <option key={track.track_id} value={track.track_id}>
              {track.title_normalized || track.title || track.track_id}
            </option>
          ))}
        </select>
      </label>

      {selectedTemplate && (
        <div className="rounded-[24px] border border-emerald-100 bg-emerald-50/70 p-4 text-sm leading-6 text-emerald-950">
          默认建议：{selectedTemplate.default_profile.learning_goal}；推荐资源：{selectedTemplate.default_profile.resource_preference.join('、')}。
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
                  <option value="">暂不填写</option>
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
