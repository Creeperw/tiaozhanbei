import React, { useEffect, useId, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Trash2, Save, Search, Edit2, X, RotateCcw, Database, Clock, Sparkles, ArrowUpCircle, ChevronDown, BookOpen, Flag, GraduationCap, Loader2, Lock, Pencil, PieChart, Target, UserRound } from 'lucide-react';
import { API_BASE, MAIN_API_BASE, fetchWithAuth } from '../utils/api';
import { useModalFocus } from './ui/useModalFocus';

const emptyProfile = {
  display_name: '', constitution: '', health_goals: '', diet_restrictions: '',
  exercise_preferences: '', medical_history: '', custom_needs: ''
};

const getDefaultMemoryExpiration = () => {
  const date = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

const emptyMemory = { category: 'short_term', importance: 'important', title: '', content: '', expires_at: getDefaultMemoryExpiration() };
const emptyCandidate = { title: '', content: '', importance: 'normal', reason: '' };

const categoryLabels = { long_term: '长期记忆', short_term: '短期记忆', preference: '偏好', feedback: '反馈', note: '备注' };
const sourceLabels = { manual: '手动录入', auto_extract: '智能体抽取', agent: '智能体', feedback: '反馈', md_upload: 'MD 导入' };
const candidateStatusLabels = { pending: '待确认', promoted: '已晋升', ignored: '已忽略' };

const userProfileColumns = {
  background: [
    { key: 'education_major', label: '专业背景', target: 'learner', icon: GraduationCap },
    { key: 'learning_background', label: '当前基础', target: 'learner', icon: BookOpen },
    { key: 'health_goals', label: '学习目标', icon: Flag },
    { key: 'diet_restrictions', label: '可投入时间', icon: Clock },
  ],
  preferences: [
    { key: 'exercise_preferences', lockKey: 'resource_preferences', label: '资源偏好', icon: BookOpen },
    { key: 'medical_history', lockKey: 'current_difficulties', label: '当前困难/薄弱点', icon: Target },
    { key: 'custom_needs', lockKey: 'learning_needs', label: '个性化学习需求', icon: Sparkles },
    { key: 'learning_habits', lockKey: 'learning_habits', label: '学习习惯', target: 'learner', icon: Clock },
  ],
};

const getProfileFieldValue = (field, profile, learnerProfile) => (
  field.target === 'learner' ? learnerProfile?.[field.key] : profile?.[field.key]
);

const formatProfileValue = (value) => {
  if (Array.isArray(value)) return value.filter(Boolean).join('、');
  return String(value ?? '').trim();
};

const copyLearnerProfile = (value = {}) => ({
  ...value,
  locked_fields: [...(value.locked_fields || [])],
  lock_reason: { ...(value.lock_reason || {}) },
});

const formatTime = (value) => {
  if (!value) return '无';
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (Number.isNaN(date.getTime())) return '无';
  return date.toLocaleString('zh-CN', { hour12: false });
};

const normalizeMemoryPayload = (item) => ({
  category: item.category,
  importance: item.importance,
  title: item.title,
  content: item.content,
  expires_at: item.expires_at ? new Date(item.expires_at).toISOString() : null,
});

const softInputClass = "w-full rounded-2xl border border-emerald-100/80 bg-white/80 px-4 py-2.5 text-slate-700 shadow-inner shadow-emerald-50/70 outline-none transition-[border-color,background-color,box-shadow] duration-150 placeholder:text-slate-300 focus:border-emerald-300 focus:bg-white focus:ring-4 focus:ring-emerald-100/80";
const softTextareaClass = "w-full resize-none rounded-[22px] border border-emerald-100/80 bg-white/75 p-4 text-slate-700 shadow-inner shadow-emerald-50/80 outline-none transition-[border-color,background-color,box-shadow] duration-150 placeholder:text-slate-300 focus:border-emerald-300 focus:bg-white focus:ring-4 focus:ring-emerald-100/80";
const softCardClass = "rounded-[28px] border border-emerald-100/70 bg-white/82 shadow-sm shadow-emerald-100/50 backdrop-blur-sm";
const softIconButtonClass = "rounded-xl p-2 text-slate-500 transition-[color,background-color,transform] duration-150 hover:-translate-y-0.5 hover:bg-emerald-50 hover:text-emerald-700 active:translate-y-px";

const toOptions = (entries) => entries.map(([value, label]) => ({ value, label }));
const categoryOptions = toOptions(Object.entries(categoryLabels));
const memoryImportanceOptions = [{ value: 'important', label: '重要' }, { value: 'normal', label: '普通' }];
const candidateImportanceOptions = [{ value: 'normal', label: '普通' }, { value: 'low', label: '低' }];
const candidateStatusOptions = [
  { value: 'pending', label: '待确认' },
  { value: 'promoted', label: '已晋升' },
  { value: 'ignored', label: '已忽略' },
  { value: 'all', label: '全部状态' },
];
const filterCategoryOptions = [{ value: 'all', label: '全部分类' }, ...categoryOptions];
const filterImportanceOptions = [{ value: 'all', label: '全部重要性' }, ...memoryImportanceOptions];
const analysisFrequencyOptions = [
  { value: 'daily', label: '每日一次' },
  { value: 'weekly', label: '每周一次' },
  { value: 'manual', label: '仅手动刷新' },
  { value: 'paused', label: '暂停自动分析' },
];

function SoftSelect({ value, options, onChange, className = '', menuClassName = '' }) {
  const [open, setOpen] = useState(false);
  const selectRef = useRef(null);
  const selected = options.find(option => option.value === value) || options[0];

  useEffect(() => {
    if (!open) return undefined;
    const handleClickOutside = (event) => {
      if (selectRef.current && !selectRef.current.contains(event.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [open]);

  return (
    <div ref={selectRef} className={`relative min-w-0 ${className}`}>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex w-full items-center justify-between gap-2 rounded-2xl border border-emerald-100/80 bg-white/85 px-4 py-2.5 text-left text-slate-700 shadow-sm shadow-emerald-50/70 outline-none transition-[border-color,background-color,box-shadow] duration-150 hover:border-emerald-200 hover:bg-white focus:border-emerald-300 focus:ring-4 focus:ring-emerald-100/80"
      >
        <span className="truncate">{selected?.label || '请选择'}</span>
        <ChevronDown size={16} className={`shrink-0 text-emerald-500 transition-transform duration-200 ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className={`absolute left-0 right-0 top-[calc(100%+8px)] z-50 overflow-hidden rounded-2xl border border-emerald-100/90 bg-white/95 p-1.5 shadow-2xl shadow-emerald-100/70 backdrop-blur-xl ${menuClassName}`}>
          {options.map(option => {
            const active = option.value === value;
            return (
              <button
                key={option.value}
                type="button"
                onClick={() => { onChange(option.value); setOpen(false); }}
                className={`flex w-full items-center justify-between rounded-xl px-3 py-2 text-left text-sm transition-[color,background-color,box-shadow] duration-150 ${active ? 'bg-gradient-to-r from-emerald-500 to-teal-500 text-white shadow-sm shadow-emerald-100' : 'text-slate-600 hover:bg-emerald-50 hover:text-emerald-700'}`}
              >
                <span className="truncate">{option.label}</span>
                {active && <span className="ml-2 h-1.5 w-1.5 rounded-full bg-white/90" />}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

function MemoryForm({ value, onChange, onSubmit, submitText, onCancel }) {
  return (
    <div className="rounded-[28px] border border-emerald-100/70 bg-gradient-to-br from-emerald-50/70 via-white/85 to-teal-50/50 p-5 shadow-inner shadow-emerald-50/80">
      <div className="grid md:grid-cols-4 gap-2 mb-3">
        <input placeholder="标题" value={value.title} onChange={e => onChange({ ...value, title: e.target.value })} className={softInputClass} />
        <SoftSelect value={value.category} options={categoryOptions} onChange={category => onChange({ ...value, category })} />
        <SoftSelect value={value.importance} options={memoryImportanceOptions} onChange={importance => onChange({ ...value, importance })} />
        <input type="datetime-local" value={value.expires_at || ''} onChange={e => onChange({ ...value, expires_at: e.target.value })} className={softInputClass} title="可选：过期时间" />
      </div>
      <textarea placeholder="填写学习目标、资源偏好、近期薄弱点、时间约束、阶段反馈或个性化学习需求。记忆管理智能体自动抽取的内容也会进入这里。" value={value.content} onChange={e => onChange({ ...value, content: e.target.value })} className={`${softTextareaClass} min-h-[110px]`} />
      <div className="mt-3 flex gap-2">
        <button onClick={onSubmit} className="flex items-center gap-2 rounded-2xl bg-gradient-to-r from-[#C8E6C9] to-[#A8E6CF] border border-[#B2DFDB] px-4 py-2 text-emerald-900 shadow-sm transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-md active:translate-y-px"><Save size={16}/>{submitText}</button>
        {onCancel && <button onClick={onCancel} className="flex items-center gap-2 rounded-2xl border border-emerald-100 bg-white/80 px-4 py-2 text-slate-600 shadow-sm transition-[color,background-color,transform] duration-150 hover:bg-emerald-50 hover:text-emerald-800 active:translate-y-px"><X size={16}/>取消</button>}
      </div>
    </div>
  );
}

function LearningProfileEditor({ profile, learnerProfile, saving, onClose, onSave }) {
  const dialogRef = useModalFocus(true);
  const titleId = useId();
  const [draftProfile, setDraftProfile] = useState(() => ({ ...emptyProfile, ...profile }));
  const [draftLearnerProfile, setDraftLearnerProfile] = useState(
    () => copyLearnerProfile(learnerProfile),
  );

  useEffect(() => {
    const closeOnEscape = (event) => {
      if (event.key === 'Escape' && !saving) onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [onClose, saving]);

  const updateValue = (field, value) => {
    if (field.target === 'learner') {
      setDraftLearnerProfile((current) => ({ ...current, [field.key]: value }));
      return;
    }
    setDraftProfile((current) => ({ ...current, [field.key]: value }));
  };

  const toggleLock = (field) => {
    setDraftLearnerProfile((current) => {
      const locked = current.locked_fields || [];
      const isLocked = locked.includes(field.lockKey);
      const lockReason = { ...(current.lock_reason || {}) };
      if (isLocked) delete lockReason[field.lockKey];
      else lockReason[field.lockKey] = '用户在学习画像中锁定';
      return {
        ...current,
        locked_fields: isLocked
          ? locked.filter((item) => item !== field.lockKey)
          : [...locked, field.lockKey],
        lock_reason: lockReason,
      };
    });
  };

  const renderField = (field) => {
    const value = getProfileFieldValue(field, draftProfile, draftLearnerProfile);
    const isPreference = Boolean(field.lockKey);
    const isLocked = isPreference && (draftLearnerProfile.locked_fields || []).includes(field.lockKey);
    const Icon = field.icon;
    return (
      <div key={field.key} className="flex flex-col rounded-2xl border border-emerald-100/90 bg-white p-4 shadow-sm shadow-emerald-100/45 h-full">
        <div className="mb-2 flex items-center justify-between gap-3 shrink-0">
          <label htmlFor={`learning-profile-editor-${field.key}`} className="flex items-center gap-2 text-base font-semibold text-slate-800">
            {Icon && <Icon aria-hidden="true" size={18} className="text-emerald-600" />}
            {field.label}
          </label>
          {isPreference && (
            <button
              type="button"
              aria-pressed={isLocked}
              aria-label={`${isLocked ? '解除锁定' : '锁定'}${field.label}`}
              onClick={() => toggleLock(field)}
              className={`inline-flex min-h-9 items-center gap-1.5 rounded-xl border px-3 text-sm font-semibold transition-colors ${isLocked ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : 'border-slate-200 bg-white text-slate-600 hover:border-emerald-200 hover:bg-emerald-50'}`}
            >
              <Lock aria-hidden="true" size={15} />{isLocked ? '已锁定' : '锁定'}
            </button>
          )}
        </div>
        <textarea
          id={`learning-profile-editor-${field.key}`}
          aria-label={field.label}
          value={value || ''}
          onChange={(event) => updateValue(field, event.target.value)}
          placeholder={`请填写${field.label}`}
          className={`${softTextareaClass} flex-1 min-h-[88px] text-base leading-6`}
        />
      </div>
    );
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6 backdrop-blur-sm" onMouseDown={() => !saving && onClose()}>
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="flex max-h-[min(92dvh,900px)] w-full max-w-5xl flex-col overflow-hidden rounded-[30px] border border-white/85 bg-[#f8fbf9] shadow-2xl shadow-slate-950/20"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-emerald-100 bg-white px-5 py-5 sm:px-7">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-emerald-800"><Pencil aria-hidden="true" size={16} />学习画像</div>
            <h2 id={titleId} className="mt-2 text-2xl font-bold tracking-tight text-slate-950">编辑画像</h2>
            <p className="mt-2 text-sm leading-6 text-slate-600">修改基础信息与学习偏好；锁定的偏好不会被智能分析自动覆盖。</p>
          </div>
          <button type="button" data-autofocus aria-label="关闭编辑画像" disabled={saving} onClick={onClose} className="icon-button"><X aria-hidden="true" size={20} /></button>
        </header>
        <div className="min-h-0 overflow-y-auto px-5 py-5 sm:px-7 sm:py-6">
          <div className="grid gap-y-3 gap-x-5 lg:grid-cols-2">
            <section aria-label="编辑基础信息">
              <h3 className="flex items-center gap-2 text-lg font-bold text-slate-900"><UserRound aria-hidden="true" size={19} className="text-emerald-600" />基础信息</h3>
            </section>
            <section aria-label="编辑学习偏好">
              <h3 className="flex items-center gap-2 text-lg font-bold text-slate-900"><Sparkles aria-hidden="true" size={19} className="text-emerald-600" />学习偏好</h3>
            </section>
            {userProfileColumns.background.reduce((acc, bgField, index) => {
              acc.push(renderField(bgField));
              acc.push(renderField(userProfileColumns.preferences[index]));
              return acc;
            }, [])}
          </div>
        </div>
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-emerald-100 bg-white px-5 py-4 sm:px-7">
          <p className="text-sm text-slate-600">已锁定 {draftLearnerProfile.locked_fields?.length || 0} 项学习偏好</p>
          <div className="flex gap-3">
            <button type="button" disabled={saving} onClick={onClose} className="button button--secondary">取消</button>
            <button type="button" disabled={saving} onClick={() => onSave(draftProfile, draftLearnerProfile)} className="button button--primary">
              {saving ? <Loader2 aria-hidden="true" size={17} className="animate-spin" /> : <Save aria-hidden="true" size={17} />}
              {saving ? '正在保存' : '保存画像'}
            </button>
          </div>
        </footer>
      </section>
    </div>
  );
}

export default function PersonalizationPage({ onBackHome, onBack, embedded = false, view = 'profile' }) {
  const [profile, setProfile] = useState(emptyProfile);
  const [learnerProfile, setLearnerProfile] = useState({ locked_fields: [], survey: {}, lock_reason: {} });
  const [overview, setOverview] = useState(null);
  const [memories, setMemories] = useState([]);
  const [candidates, setCandidates] = useState([]);
  const [newMemory, setNewMemory] = useState(emptyMemory);
  const [editingId, setEditingId] = useState(null);
  const [editingMemory, setEditingMemory] = useState(emptyMemory);
  const [editingCandidateId, setEditingCandidateId] = useState(null);
  const [editingCandidate, setEditingCandidate] = useState(emptyCandidate);
  const [filters, setFilters] = useState({ q: '', category: 'all', importance: 'all', source: 'all', includeInactive: false });
  const [candidateStatus, setCandidateStatus] = useState('pending');
  const [analysisFrequency, setAnalysisFrequency] = useState('daily');
  const [savedAnalysisFrequency, setSavedAnalysisFrequency] = useState('daily');
  const [isSavingAnalysisFrequency, setIsSavingAnalysisFrequency] = useState(false);
  const analysisFrequencySaveRef = useRef(0);
  const [profileEditorOpen, setProfileEditorOpen] = useState(false);
  const [isSavingProfile, setIsSavingProfile] = useState(false);
  const [message, setMessage] = useState('');
  const isUnifiedView = view === 'unified';
  const isUserProfileView = view === 'user-profile';
  const isProfileView = view === 'profile' || isUnifiedView || isUserProfileView;
  const isMemoryView = view === 'memory' || isUnifiedView;

  const categoryEntries = Object.entries(overview?.stats?.by_category || {});
  const sourceEntries = Object.entries(overview?.stats?.by_source || {});
  const maxCategoryCount = Math.max(1, ...categoryEntries.map(([, count]) => count));
  const hasConflicts = useMemo(() => {
    const seen = new Map();
    for (const memory of memories) {
      if (!memory?.is_active) continue;
      const key = memory.conflict_key || `${memory.category || ''}:${(memory.title || '').trim() || (memory.content || '').trim()}`;
      if (!key) continue;
      const existing = seen.get(key) || 0;
      seen.set(key, existing + 1);
      if (existing + 1 > 1) return true;
    }
    return false;
  }, [memories]);

  const queryString = useMemo(() => {
    const params = new URLSearchParams();
    if (filters.q.trim()) params.set('q', filters.q.trim());
    if (filters.category !== 'all') params.set('category', filters.category);
    if (filters.importance !== 'all') params.set('importance', filters.importance);
    if (filters.source !== 'all') params.set('source', filters.source);
    if (filters.includeInactive) params.set('include_inactive', 'true');
    return params.toString();
  }, [filters]);

  const notify = (text) => {
    setMessage(text);
    setTimeout(() => setMessage(''), 1800);
  };

  const changeAnalysisFrequency = async (nextFrequency) => {
    if (nextFrequency === savedAnalysisFrequency || isSavingAnalysisFrequency) return;
    const saveVersion = analysisFrequencySaveRef.current + 1;
    analysisFrequencySaveRef.current = saveVersion;
    setAnalysisFrequency(nextFrequency);
    setIsSavingAnalysisFrequency(true);
    try {
      const response = await fetchWithAuth(`${API_BASE}/personalization/learner-settings`, {
        method: 'PUT',
        body: JSON.stringify({ analysis_frequency: nextFrequency }),
      });
      if (!response.ok) throw new Error('更新频率保存失败');
      if (analysisFrequencySaveRef.current === saveVersion) setSavedAnalysisFrequency(nextFrequency);
      notify('更新频率已保存');
    } catch (error) {
      console.error(error);
      if (analysisFrequencySaveRef.current === saveVersion) setAnalysisFrequency(savedAnalysisFrequency);
      notify(error.message || '更新频率保存失败');
    } finally {
      setIsSavingAnalysisFrequency(false);
    }
  };

  const load = async () => {
    try {
      const [overviewRes, memoriesRes, learnerRes, learningContextRes, learnerSettingsRes] = await Promise.all([
        fetchWithAuth(`${API_BASE}/personalization/overview`),
        fetchWithAuth(`${API_BASE}/personalization/memories${queryString ? `?${queryString}` : ''}`),
        fetchWithAuth(`${API_BASE}/personalization/learner-profile`),
        fetchWithAuth(`${MAIN_API_BASE}/learning-context`),
        fetchWithAuth(`${API_BASE}/personalization/learner-settings`),
      ]);
      const candidateRes = await fetchWithAuth(`${API_BASE}/personalization/candidates?status=${candidateStatus}`);
      const overviewData = await overviewRes.json();
      const memoryData = await memoriesRes.json();
      const learnerData = await learnerRes.json();
      const learningContextData = learningContextRes.ok ? await learningContextRes.json() : {};
      const learnerSettingsData = learnerSettingsRes.ok ? await learnerSettingsRes.json() : {};
      const confirmedProfile = learningContextData.user_profile || {};
      const confirmedGoal = confirmedProfile.learning_goal || '';
      const candidateData = await candidateRes.json();
      setOverview(overviewData);
      setProfile({
        ...emptyProfile,
        ...(overviewData.profile || {}),
        display_name: overviewData.profile?.display_name || confirmedProfile.display_name || '',
        constitution: overviewData.profile?.constitution || (confirmedProfile.learner_group === '未选择用户群体'
          ? ''
          : (confirmedProfile.learner_group || '')),
        health_goals: overviewData.profile?.health_goals || confirmedGoal
          || confirmedProfile.goals?.goal_name
          || '',
        diet_restrictions: overviewData.profile?.diet_restrictions || confirmedProfile.time_constraints
          || '',
      });
      setLearnerProfile({
        locked_fields: [],
        survey: {},
        lock_reason: {},
        ...(learnerData || {}),
        learning_background: learnerData?.learning_background || confirmedProfile.learning_background || '',
        education_major: learnerData?.education_major
          || learningContextData.onboarding?.survey_answers?.major_or_role
          || learningContextData.onboarding?.survey_answers?.education_major
          || learnerData?.survey?.background?.education_major
          || '',
        learning_habits: learnerData?.learning_habits
          || learningContextData.onboarding?.survey_answers?.learning_mode
          || learnerData?.survey?.preferences?.learning_mode
          || '',
      });
      setMemories(Array.isArray(memoryData) ? memoryData : []);
      setCandidates(Array.isArray(candidateData) ? candidateData : []);
      if (analysisFrequencySaveRef.current === 0) {
        const nextAnalysisFrequency = learnerSettingsData?.settings?.analysis_frequency || 'daily';
        setAnalysisFrequency(nextAnalysisFrequency);
        setSavedAnalysisFrequency(nextAnalysisFrequency);
      }
    } catch (e) {
      console.error(e);
      notify('加载学习记忆失败');
    }
  };

  // `load` intentionally follows the current filter and candidate status.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { load(); }, [queryString, candidateStatus]);
  const saveProfile = async (nextProfile = profile, nextLearnerProfile = learnerProfile) => {
    setIsSavingProfile(true);
    try {
      const [profileResponse, learnerResponse] = await Promise.all([
        fetchWithAuth(`${API_BASE}/personalization/profile`, { method: 'PUT', body: JSON.stringify(nextProfile) }),
        fetchWithAuth(`${API_BASE}/personalization/learner-profile`, {
          method: 'PUT',
          body: JSON.stringify({
            learner_group: nextProfile.constitution,
            learning_goal: nextProfile.health_goals,
            education_major: nextLearnerProfile.education_major,
            learning_background: nextLearnerProfile.learning_background,
            time_constraints: nextProfile.diet_restrictions,
            resource_preferences: nextProfile.exercise_preferences,
            current_difficulties: nextProfile.medical_history,
            learning_needs: nextProfile.custom_needs,
            learning_habits: nextLearnerProfile.learning_habits,
            locked_fields: nextLearnerProfile.locked_fields || [],
            lock_reason: nextLearnerProfile.lock_reason || {},
          }),
        }),
      ]);
      if (!profileResponse.ok || !learnerResponse.ok) throw new Error('画像保存失败');
      setProfile(nextProfile);
      setLearnerProfile(nextLearnerProfile);
      setProfileEditorOpen(false);
      notify('学习画像已保存');
      await load();
    } catch (error) {
      console.error(error);
      notify(error.message || '画像保存失败');
    } finally {
      setIsSavingProfile(false);
    }
  };

  const addMemory = async () => {
    if (!newMemory.content.trim()) return notify('请填写记忆内容');
    await fetchWithAuth(`${API_BASE}/personalization/memories`, { method: 'POST', body: JSON.stringify(normalizeMemoryPayload(newMemory)) });
    setNewMemory(emptyMemory);
    notify('记忆已新增');
    await load();
  };

  const startEdit = (memory) => {
    setEditingId(memory.id);
    setEditingMemory({
      category: memory.category || 'long_term',
      importance: memory.importance || 'normal',
      title: memory.title || '',
      content: memory.content || '',
      expires_at: memory.expires_at ? memory.expires_at.slice(0, 16) : '',
    });
  };

  const saveMemory = async () => {
    if (!editingId) return;
    await fetchWithAuth(`${API_BASE}/personalization/memories/${editingId}`, { method: 'PUT', body: JSON.stringify(normalizeMemoryPayload(editingMemory)) });
    setEditingId(null);
    notify('记忆已更新');
    await load();
  };

  const deleteMemory = async (id) => {
    if (!confirm('确定要停用这条记忆吗？')) return;
    await fetchWithAuth(`${API_BASE}/personalization/memories/${id}`, { method: 'DELETE' });
    notify('记忆已停用');
    await load();
  };

  const restoreMemory = async (id) => {
    await fetchWithAuth(`${API_BASE}/personalization/memories/${id}/restore`, { method: 'PATCH' });
    notify('记忆已恢复');
    await load();
  };

  const promoteMemory = async (id) => {
    await fetchWithAuth(`${API_BASE}/personalization/memories/${id}/promote`, { method: 'PATCH' });
    notify('已转化为长期记忆');
    await load();
  };

  const startEditCandidate = (candidate) => {
    setEditingCandidateId(candidate.id);
    setEditingCandidate({
      title: candidate.title || '',
      content: candidate.content || '',
      importance: candidate.importance || 'normal',
      reason: candidate.reason || '',
    });
  };

  const saveCandidate = async () => {
    if (!editingCandidateId) return;
    if (!editingCandidate.content.trim()) return notify('请填写候选内容');
    await fetchWithAuth(`${API_BASE}/personalization/candidates/${editingCandidateId}`, { method: 'PUT', body: JSON.stringify(editingCandidate) });
    setEditingCandidateId(null);
    notify('候选记忆已更新');
    await load();
  };

  const ignoreCandidate = async (id) => {
    await fetchWithAuth(`${API_BASE}/personalization/candidates/${id}/ignore`, { method: 'PATCH' });
    notify('候选记忆已忽略');
    await load();
  };

  const deleteCandidate = async (id) => {
    if (!confirm('确定要永久删除这条候选记忆吗？')) return;
    await fetchWithAuth(`${API_BASE}/personalization/candidates/${id}`, { method: 'DELETE' });
    notify('候选记忆已删除');
    await load();
  };

  const promoteCandidate = async (id, category) => {
    await fetchWithAuth(`${API_BASE}/personalization/candidates/${id}/promote`, { method: 'PATCH', body: JSON.stringify({ category, importance: category === 'long_term' ? 'important' : 'normal' }) });
    notify(category === 'long_term' ? '已晋升为长期记忆' : '已晋升为短期记忆');
    await load();
  };

  const rootClassName = embedded
    ? `text-gray-800${isUserProfileView ? ' user-profile-page' : ''}`
    : 'min-h-screen bg-[radial-gradient(circle_at_top_left,#dcfce7,transparent_34%),radial-gradient(circle_at_top_right,#ccfbf1,transparent_30%),linear-gradient(135deg,#f8fafc_0%,#f0fdfa_46%,#ecfdf5_100%)] p-6 text-gray-800';

  return (
    <div className={rootClassName}>
      <div className={isUserProfileView ? 'user-profile-page__content mx-auto max-w-7xl' : 'mx-auto max-w-7xl'}>
        {!(embedded && isUserProfileView) && <div className={embedded ? 'mb-6 overflow-hidden rounded-[30px] border border-emerald-100/80 bg-gradient-to-br from-white via-emerald-50/70 to-teal-50/60 p-4 shadow-sm shadow-emerald-100/45 sm:p-5' : 'relative mb-6 overflow-hidden rounded-[36px] border border-white/80 bg-white/72 p-6 shadow-xl shadow-emerald-100/50 backdrop-blur-xl'}>
          {!embedded && <div className="absolute -right-20 -top-20 w-64 h-64 rounded-full bg-emerald-200/40 blur-3xl" />}
          {!embedded && <div className="absolute right-24 bottom-0 w-40 h-40 rounded-full bg-teal-200/35 blur-3xl" />}
          <div className={`relative flex flex-wrap items-start gap-5 ${embedded ? 'justify-between' : 'justify-between'}`}>
            {!isUserProfileView && <div className="min-w-0">
              {(onBackHome || onBack) && (
                <button onClick={onBackHome || onBack} className="mb-5 inline-flex items-center gap-2 text-gray-500 hover:text-emerald-600 transition-colors"><ArrowLeft size={18}/> 返回主页</button>
              )}
              <div className="flex items-center gap-3">
                <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-[#C8E6C9] to-[#A8E6CF] text-emerald-900 flex items-center justify-center shadow-sm">
                  <Database size={24} />
                </div>
                <div>
                  <h1 className="text-xl font-black tracking-tight text-slate-900 sm:text-2xl">学习记忆</h1>
                  <p className="mt-1 text-sm leading-6 text-slate-600">集中沉淀、管理并调用影响后续学习推荐的关键信息。</p>
                </div>
              </div>
            </div>}
            {message && <span className="rounded-2xl border border-emerald-100 bg-white/80 px-3 py-2 text-sm text-emerald-600 shadow-sm">{message}</span>}
          </div>
          {isMemoryView && <section aria-label="学情分析智能体更新频率" className="relative mt-5 border-t border-emerald-100/90 pt-5">
            <div>
              <h2 className="text-lg font-bold text-slate-900">学情分析智能体更新频率</h2>
              <p className="mt-1 text-sm leading-6 text-slate-600">设置智能体自动汇总学习状态与更新学习记忆的节奏。</p>
            </div>
            <div className="mt-4 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
              {analysisFrequencyOptions.map((option) => {
                const active = analysisFrequency === option.value;
                return (
                  <button
                    key={option.value}
                    type="button"
                    aria-pressed={active}
                    disabled={isSavingAnalysisFrequency}
                    onClick={() => changeAnalysisFrequency(option.value)}
                    className={`rounded-2xl border px-4 py-3 text-sm font-semibold transition-[color,background-color,border-color,box-shadow,transform] duration-150 ${active ? 'border-[#B2DFDB] bg-gradient-to-r from-[#C8E6C9] to-[#A8E6CF] text-emerald-900 shadow-sm' : 'border-[#E0F2F1] bg-white/90 text-slate-600 hover:-translate-y-0.5 hover:border-[#B2DFDB] hover:bg-emerald-50 hover:text-emerald-800'}`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </section>}
        </div>
        }

        {isMemoryView && <div className="grid grid-cols-2 gap-3 mb-5 sm:grid-cols-3 xl:grid-cols-5 xl:gap-4">
          <div className="group bg-white/82 rounded-[28px] border border-white/80 shadow-sm shadow-emerald-100/40 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-emerald-100 transition-[transform,box-shadow] duration-200"><div className="w-10 h-10 rounded-2xl bg-emerald-50 text-emerald-700 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform"><Database size={20}/></div><p className="text-sm text-slate-600">启用记忆</p><p className="text-3xl font-black text-slate-900">{overview?.stats?.active_count || 0}</p></div>
          <div className="group bg-white/82 rounded-[28px] border border-white/80 shadow-sm shadow-amber-100/40 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-amber-100 transition-[transform,box-shadow] duration-200"><div className="w-10 h-10 rounded-2xl bg-amber-50 text-amber-700 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform"><Sparkles size={20}/></div><p className="text-sm text-slate-600">重要记忆</p><p className="text-3xl font-black text-slate-900">{overview?.stats?.important_count || 0}</p></div>
          <div className="group bg-white/82 rounded-[28px] border border-white/80 shadow-sm shadow-emerald-100/40 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-emerald-100 transition-[transform,box-shadow] duration-200"><div className="w-10 h-10 rounded-2xl bg-teal-50 text-teal-700 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform"><ArrowUpCircle size={20}/></div><p className="text-sm text-slate-600">待确认候选</p><p className="text-3xl font-black text-slate-900">{overview?.stats?.candidate_pending_count || 0}</p></div>
          {hasConflicts && <div className="group bg-white/82 rounded-[28px] border border-amber-100/80 shadow-sm shadow-amber-100/40 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-amber-100 transition-[transform,box-shadow] duration-200 md:col-span-5"><p className="text-sm text-amber-600 font-semibold">检测到重复语义记忆</p><p className="text-xs text-amber-500 mt-1">系统会自动保留最新有效值，并将旧版本停用。</p></div>}
          <div className="group bg-white/82 rounded-[28px] border border-white/80 shadow-sm shadow-slate-100/60 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-gray-200 transition-[transform,box-shadow] duration-200"><div className="w-10 h-10 rounded-2xl bg-slate-50 text-slate-500 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform"><Trash2 size={20}/></div><p className="text-sm text-gray-500">停用记忆</p><p className="text-3xl font-black text-slate-900">{overview?.stats?.inactive_count || 0}</p></div>
          <div className="group bg-white/82 rounded-[28px] border border-white/80 shadow-sm shadow-rose-100/40 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-rose-100 transition-[transform,box-shadow] duration-200"><div className="w-10 h-10 rounded-2xl bg-rose-50 text-rose-500 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform"><Clock size={20}/></div><p className="text-sm text-gray-500">已过期</p><p className="text-3xl font-black text-slate-900">{overview?.stats?.expired_count || 0}</p></div>
        </div>}

        {isMemoryView && <div className="grid gap-4 mb-5 lg:grid-cols-3">
          <div className="lg:col-span-2 bg-white/82 rounded-[30px] border border-white/80 shadow-sm shadow-emerald-100/50 p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-bold text-slate-800">记忆分类分布</h2>
              <span className="text-xs text-gray-400">按启用记忆统计</span>
            </div>
            <div className="space-y-3">
              {categoryEntries.length === 0 && <div className="text-sm text-gray-400 py-6 text-center">暂无分类数据</div>}
              {categoryEntries.map(([key, count]) => (
                <div key={key} className="grid grid-cols-[88px_1fr_36px] items-center gap-3">
                  <span className="text-sm text-gray-600">{categoryLabels[key] || key}</span>
                  <div className="h-3 bg-emerald-50 rounded-full overflow-hidden shadow-inner shadow-emerald-100/60">
                    <div className="h-full rounded-full bg-gradient-to-r from-[#C8E6C9] to-[#A8E6CF] transition-[width] duration-200" style={{ width: `${Math.max(8, (count / maxCategoryCount) * 100)}%` }} />
                  </div>
                  <span className="text-sm font-bold text-slate-700 text-right">{count}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="bg-white/82 rounded-[30px] border border-white/80 shadow-sm shadow-emerald-100/50 p-5">
            <h2 className="font-bold text-slate-800 mb-4">来源构成</h2>
            <div className="flex flex-wrap gap-2">
              {sourceEntries.length === 0 && <span className="text-sm text-gray-400">暂无来源数据</span>}
              {sourceEntries.map(([key, count]) => (
                <span key={key} className="px-3 py-2 rounded-2xl bg-gradient-to-br from-white to-emerald-50/80 border border-emerald-100 text-sm text-slate-600 shadow-sm shadow-emerald-50">
                  {sourceLabels[key] || key} <b className="text-emerald-600 ml-1">{count}</b>
                </span>
              ))}
            </div>
          </div>
        </div>}

        <div className={isProfileView ? 'mx-auto max-w-7xl' : ''}>
          {isUserProfileView && (() => {
            const profileFields = [...userProfileColumns.background, ...userProfileColumns.preferences];
            const completedFields = profileFields.filter((field) => formatProfileValue(getProfileFieldValue(field, profile, learnerProfile))).length;
            const completion = Math.round((completedFields / profileFields.length) * 100);
            const renderReadOnlyField = (field) => {
              const Icon = field.icon;
              const value = formatProfileValue(getProfileFieldValue(field, profile, learnerProfile));
              const isLocked = field.lockKey && (learnerProfile.locked_fields || []).includes(field.lockKey);
              return (
                <div key={field.key} className="user-profile-panel__field grid grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-1 border-b border-emerald-100/85 py-4 last:border-b-0 xl:grid-cols-[auto_minmax(7.5rem,11.5rem)_minmax(0,1fr)]">
                  <Icon aria-hidden="true" size={21} className="mt-0.5 text-emerald-600" />
                  <div className="min-w-0 space-y-1 xl:contents">
                    <dt className="user-profile-panel__field-label text-base font-semibold text-slate-700 sm:text-lg lg:text-[1.2rem] xl:col-start-2">{field.label}</dt>
                    <dd className="user-profile-panel__field-value flex min-w-0 items-start justify-between gap-2 text-base text-slate-900 sm:text-lg lg:text-[1.2rem] xl:col-start-3">
                      <span className={value ? 'min-w-0 break-words font-medium leading-7' : 'min-w-0 font-normal leading-7 text-slate-400'}>{value || '暂未填写'}</span>
                      {isLocked && <span className="user-profile-panel__lock inline-flex shrink-0 items-center gap-1 rounded-full bg-emerald-50 px-2.5 py-1.5 text-sm font-semibold text-emerald-700"><Lock aria-hidden="true" size={13} />已锁定</span>}
                    </dd>
                  </div>
                </div>
              );
            };
            return <>
              <section className="user-profile-panel flex min-h-[calc(100dvh-11rem)] flex-col overflow-hidden rounded-[32px] border border-white/80 bg-white/86 p-5 shadow-lg shadow-emerald-100/45 backdrop-blur-sm sm:p-6 lg:min-h-0 lg:flex-1 lg:p-7 xl:p-8">
                <header className="user-profile-panel__header flex flex-wrap items-center justify-between gap-5 border-b border-emerald-100 pb-5 lg:pb-6">
                  <div className="flex items-center gap-4 text-slate-900">
                    <div className="user-profile-panel__heading-icon flex h-14 w-14 items-center justify-center rounded-2xl bg-emerald-50 text-emerald-700 lg:h-16 lg:w-16"><Database aria-hidden="true" size={25} /></div>
                    <div>
                      <h2 className="user-profile-panel__heading text-2xl font-black tracking-tight text-slate-950 sm:text-3xl lg:text-[2.35rem]">我的学习画像</h2>
                    </div>
                  </div>
                  <div className="user-profile-panel__actions flex flex-wrap items-center justify-end gap-3">
                    <div aria-label={`画像完整度 ${completion}%`} className="user-profile-panel__completion flex min-h-15 items-center gap-3 rounded-2xl border border-emerald-100 bg-emerald-50/75 px-4 py-2.5 shadow-sm shadow-emerald-100/50 lg:px-5 lg:py-3">
                      <div className="relative grid h-10 w-10 place-items-center rounded-full" style={{ background: `conic-gradient(#C8E6C9 0%, #A5D6A7 50%, #7CB342 ${completion}%, #E8F5E9 ${completion}%, #E8F5E9 100%)` }}>
                        <span className="grid h-7 w-7 place-items-center rounded-full bg-white"><PieChart aria-hidden="true" size={15} className="text-emerald-700" /></span>
                      </div>
                      <div><p className="text-sm font-semibold text-slate-700 lg:text-base">画像完整度</p><p className="text-xl font-black tabular-nums text-emerald-600 lg:text-2xl">{completion}%</p></div>
                    </div>
                    <button type="button" onClick={() => setProfileEditorOpen(true)} className="user-profile-panel__edit inline-flex min-h-15 items-center gap-2 rounded-2xl bg-gradient-to-r from-[#C8E6C9] to-[#A8E6CF] border border-[#B2DFDB] px-5 text-base font-semibold text-emerald-900 shadow-sm transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-md active:translate-y-px lg:px-6 lg:text-lg"><Pencil aria-hidden="true" size={20} />编辑画像</button>
                  </div>
                </header>
                {message && <p role="status" className="mt-5 rounded-2xl border border-emerald-100 bg-emerald-50 px-4 py-3 text-base text-emerald-700">{message}</p>}

                <div className="user-profile-panel__grid mt-5 grid flex-1 gap-5 lg:mt-6 lg:min-h-0 lg:grid-cols-2 lg:gap-6">
                  <section aria-label="基础信息" className="user-profile-panel__card flex min-h-0 flex-col rounded-[26px] border border-emerald-100 bg-gradient-to-br from-emerald-50/70 via-white to-teal-50/35 p-5 sm:p-6 lg:p-7">
                    <h3 className="user-profile-panel__card-title mb-4 flex items-center gap-2.5 text-2xl font-bold text-emerald-950 lg:text-[1.7rem]"><UserRound aria-hidden="true" size={22} className="text-emerald-600" />基础信息</h3>
                    <dl className="flex flex-1 flex-col justify-between">{userProfileColumns.background.map(renderReadOnlyField)}</dl>
                  </section>
                  <section aria-label="学习偏好" className="user-profile-panel__card flex min-h-0 flex-col rounded-[26px] border border-teal-100 bg-gradient-to-br from-teal-50/65 via-white to-emerald-50/40 p-5 sm:p-6 lg:p-7">
                    <h3 className="user-profile-panel__card-title mb-4 flex items-center gap-2.5 text-2xl font-bold text-emerald-950 lg:text-[1.7rem]"><Sparkles aria-hidden="true" size={22} className="text-emerald-600" />学习偏好</h3>
                    <dl className="flex flex-1 flex-col justify-between">{userProfileColumns.preferences.map(renderReadOnlyField)}</dl>
                  </section>
                </div>
              </section>
              {profileEditorOpen && <LearningProfileEditor
                profile={profile}
                learnerProfile={learnerProfile}
                saving={isSavingProfile}
                onClose={() => setProfileEditorOpen(false)}
                onSave={saveProfile}
              />}
            </>;
          })()}

          {isMemoryView && <section className="bg-white/86 rounded-[32px] border border-white/80 shadow-lg shadow-emerald-100/45 p-5 backdrop-blur-sm sm:p-6">
            <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
              <div>
              <h2 className="text-xl font-bold">学习记忆数据库</h2>
              <p className="text-sm text-gray-500 mt-1">手动维护和记忆管理智能体自动抽取的学习目标、偏好、薄弱点、阶段反馈都会在这里统一管理。</p>
              </div>
              <span className="rounded-full border border-emerald-100 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700">自动沉淀 · 人工可控</span>
            </div>

            <MemoryForm value={newMemory} onChange={setNewMemory} onSubmit={addMemory} submitText="新增记忆" />

            <div className="mt-5 rounded-[30px] border border-emerald-100/80 bg-gradient-to-br from-emerald-50/80 via-white/90 to-teal-50/45 p-5 shadow-inner shadow-emerald-50/80">
              <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
                <div>
                  <h3 className="font-black text-slate-900 flex items-center gap-2"><Sparkles size={18} className="text-emerald-600"/> 候选记忆池</h3>
                  <p className="text-xs text-gray-500 mt-1">记忆管理智能体认为“可能影响后续推荐但暂不重要”的学习信息会先进入这里，最多保留 30 条待确认候选。</p>
                </div>
                <SoftSelect value={candidateStatus} options={candidateStatusOptions} onChange={setCandidateStatus} className="w-[150px] text-sm" />
              </div>
              <div className="space-y-3 max-h-[360px] overflow-auto pr-1">
                {candidates.map(c => (
                  <div key={c.id} className={`${softCardClass} p-4 transition-[transform,box-shadow] duration-200 hover:-translate-y-0.5 hover:shadow-md hover:shadow-emerald-100`}>
                    {editingCandidateId === c.id ? (
                      <div className="space-y-2">
                        <div className="grid md:grid-cols-[1fr_130px] gap-2">
                          <input placeholder="候选标题" value={editingCandidate.title} onChange={e => setEditingCandidate({ ...editingCandidate, title: e.target.value })} className={softInputClass} />
                          <SoftSelect value={editingCandidate.importance} options={candidateImportanceOptions} onChange={importance => setEditingCandidate({ ...editingCandidate, importance })} />
                        </div>
                        <textarea value={editingCandidate.content} onChange={e => setEditingCandidate({ ...editingCandidate, content: e.target.value })} className={`${softTextareaClass} min-h-[90px]`} />
                        <input placeholder="抽取原因" value={editingCandidate.reason} onChange={e => setEditingCandidate({ ...editingCandidate, reason: e.target.value })} className={softInputClass} />
                        <div className="flex gap-2">
                          <button onClick={saveCandidate} className="bg-gradient-to-r from-emerald-500 to-teal-500 text-white px-3 py-2 rounded-2xl text-sm flex items-center gap-1 shadow-md shadow-emerald-100"><Save size={15}/>保存</button>
                          <button onClick={() => setEditingCandidateId(null)} className="bg-white/80 border border-emerald-100 px-3 py-2 rounded-2xl text-sm flex items-center gap-1 text-slate-500 hover:bg-emerald-50"><X size={15}/>取消</button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <div className="flex justify-between gap-3">
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-xs bg-emerald-50 text-emerald-600 px-2 py-1 rounded-full">{candidateStatusLabels[c.status] || c.status}</span>
                              <span className="text-xs bg-slate-100 text-slate-500 px-2 py-1 rounded-full">{c.importance === 'low' ? '低重要性' : '普通'}</span>
                              <span className="text-xs bg-white border px-2 py-1 rounded-full text-gray-500">{sourceLabels[c.source] || c.source}</span>
                            </div>
                            <h4 className="font-semibold mt-2 truncate">{c.title || '未命名候选'}</h4>
                          </div>
                          <div className="flex gap-1 shrink-0">
                            {c.status === 'pending' && <button onClick={() => promoteCandidate(c.id, 'short_term')} className="px-2 py-1 text-xs rounded-lg bg-blue-50 text-blue-600 hover:bg-blue-100">转短期</button>}
                            {c.status === 'pending' && <button onClick={() => promoteCandidate(c.id, 'long_term')} className="px-2 py-1 text-xs rounded-lg bg-emerald-50 text-emerald-600 hover:bg-emerald-100">转长期</button>}
                            <button onClick={() => startEditCandidate(c)} className={softIconButtonClass}><Edit2 size={15}/></button>
                            {c.status === 'pending' && <button onClick={() => ignoreCandidate(c.id)} className="rounded-xl p-2 text-slate-400 transition-[color,background-color,transform] duration-150 hover:-translate-y-0.5 hover:bg-amber-50 hover:text-amber-600 active:translate-y-px"><X size={15}/></button>}
                            <button onClick={() => deleteCandidate(c.id)} className="rounded-xl p-2 text-slate-400 transition-[color,background-color,transform] duration-150 hover:-translate-y-0.5 hover:bg-red-50 hover:text-red-500 active:translate-y-px"><Trash2 size={15}/></button>
                          </div>
                        </div>
                        <p className="text-sm text-gray-700 whitespace-pre-wrap mt-3 leading-relaxed">{c.content}</p>
                        {c.reason && <p className="text-xs text-emerald-700 bg-emerald-50 rounded-xl px-3 py-2 mt-3">原因：{c.reason}</p>}
                        <div className="text-xs text-gray-400 mt-3 flex flex-wrap gap-3"><span>更新：{formatTime(c.updated_at)}</span>{c.promoted_memory_id && <span>已关联记忆 #{c.promoted_memory_id}</span>}</div>
                      </>
                    )}
                  </div>
                ))}
                {candidates.length === 0 && <div className="text-center py-10 text-gray-400 border border-dashed border-emerald-100 rounded-2xl bg-white/60">暂无{candidateStatusLabels[candidateStatus] || ''}候选记忆</div>}
              </div>
            </div>

            <div className="mt-5 grid md:grid-cols-5 gap-2 rounded-[26px] border border-emerald-100/70 bg-emerald-50/35 p-3 shadow-inner shadow-emerald-50/80">
              <div className="md:col-span-2 relative">
                <Search size={16} className="absolute left-4 top-3.5 text-emerald-400" />
                <input placeholder="搜索标题或内容" value={filters.q} onChange={e => setFilters({ ...filters, q: e.target.value })} className={`${softInputClass} pl-10`} />
              </div>
              <SoftSelect value={filters.category} options={filterCategoryOptions} onChange={category => setFilters({ ...filters, category })} />
              <SoftSelect value={filters.importance} options={filterImportanceOptions} onChange={importance => setFilters({ ...filters, importance })} />
              <label className="flex items-center gap-2 rounded-2xl border border-emerald-100/80 bg-white/80 px-4 py-2.5 text-sm text-slate-600 shadow-sm shadow-emerald-50 transition-[color,background-color,box-shadow] duration-150 hover:bg-white hover:text-emerald-700">
                <input type="checkbox" checked={filters.includeInactive} onChange={e => setFilters({ ...filters, includeInactive: e.target.checked })} /> 包含停用
              </label>
            </div>

            <div className="mt-6 space-y-3 max-h-[720px] overflow-auto pr-2">
              {memories.map(m => (
                <div key={m.id} className={`${softCardClass} p-4 transition-[transform,box-shadow,opacity] duration-200 hover:-translate-y-0.5 hover:shadow-md hover:shadow-emerald-100 ${m.is_active ? '' : 'opacity-70 grayscale-[0.2]'}`}>
                  {editingId === m.id ? (
                    <MemoryForm value={editingMemory} onChange={setEditingMemory} onSubmit={saveMemory} submitText="保存修改" onCancel={() => setEditingId(null)} />
                  ) : (
                    <>
                      <div className="flex justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="text-xs bg-emerald-50 text-emerald-600 px-2 py-1 rounded-full">{categoryLabels[m.category] || m.category}</span>
                            <span className={`text-xs px-2 py-1 rounded-full ${m.importance === 'important' ? 'bg-amber-50 text-amber-600' : 'bg-gray-100 text-gray-500'}`}>{m.importance === 'important' ? '重要' : '普通'}</span>
                            <span className="text-xs bg-white border px-2 py-1 rounded-full text-gray-500">{sourceLabels[m.source] || m.source}</span>
                            {!m.is_active && <span className="text-xs bg-red-50 text-red-500 px-2 py-1 rounded-full">已停用</span>}
                          </div>
                          <h3 className="font-semibold mt-2 truncate">{m.title || '未命名'}</h3>
                        </div>
                        <div className="flex gap-1 shrink-0">
                          <button onClick={() => startEdit(m)} className={softIconButtonClass}><Edit2 size={16}/></button>
                          {m.category === 'short_term' && m.is_active && <button onClick={() => promoteMemory(m.id)} className={softIconButtonClass} title="转化为长期记忆"><ArrowUpCircle size={16}/></button>}
                          {m.is_active ? <button onClick={() => deleteMemory(m.id)} className="rounded-xl p-2 text-slate-400 transition-[color,background-color,transform] duration-150 hover:-translate-y-0.5 hover:bg-red-50 hover:text-red-500 active:translate-y-px"><Trash2 size={16}/></button> : <button onClick={() => restoreMemory(m.id)} className={softIconButtonClass}><RotateCcw size={16}/></button>}
                        </div>
                      </div>
                      <p className="text-sm text-gray-700 whitespace-pre-wrap mt-3 leading-relaxed">{m.content}</p>
                      <div className="text-xs text-gray-400 mt-3 flex flex-wrap gap-3">
                        <span>更新：{formatTime(m.updated_at)}</span>
                        <span>过期：{formatTime(m.expires_at)}</span>
                      </div>
                    </>
                  )}
                </div>
              ))}
              {memories.length === 0 && <div className="text-center py-16 text-gray-400 border border-dashed border-emerald-100 rounded-[26px] bg-white/65 shadow-inner shadow-emerald-50">暂无符合条件的个性化记忆</div>}
            </div>
          </section>}
        </div>
      </div>
    </div>
  );
}
