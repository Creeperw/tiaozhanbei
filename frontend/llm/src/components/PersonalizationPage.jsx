import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, Trash2, Save, Search, Edit2, X, RotateCcw, Download, RefreshCw, Database, Clock, Sparkles, UploadCloud, ArrowUpCircle, ChevronDown, LineChart } from 'lucide-react';
import LearningTrendChart from './LearningTrendChart';
import { buildLearningTrendCharts } from '../learningTrendDisplay.js';
import { API_BASE, MAIN_API_BASE, fetchWithAuth } from '../utils/api';

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

const profileFields = [
  ['display_name', '昵称'], ['constitution', '用户群体'], ['health_goals', '学习目标'],
  ['diet_restrictions', '可投入时间/节奏约束'], ['exercise_preferences', '资源偏好'],
  ['medical_history', '当前困难/薄弱点'], ['custom_needs', '个性化学习需求']
];

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
const uploadCategoryOptions = [
  { value: 'long_term', label: '长期记忆' },
  { value: 'short_term', label: '短期记忆（7天）' },
  { value: 'preference', label: '偏好' },
  { value: 'note', label: '备注' },
];
const candidateStatusOptions = [
  { value: 'pending', label: '待确认' },
  { value: 'promoted', label: '已晋升' },
  { value: 'ignored', label: '已忽略' },
  { value: 'all', label: '全部状态' },
];
const filterCategoryOptions = [{ value: 'all', label: '全部分类' }, ...categoryOptions];
const filterImportanceOptions = [{ value: 'all', label: '全部重要性' }, ...memoryImportanceOptions];

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
        <button onClick={onSubmit} className="flex items-center gap-2 rounded-2xl bg-gradient-to-r from-emerald-500 to-teal-500 px-4 py-2 text-white shadow-lg shadow-emerald-100 transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-emerald-200 active:translate-y-px"><Save size={16}/>{submitText}</button>
        {onCancel && <button onClick={onCancel} className="flex items-center gap-2 rounded-2xl border border-emerald-100 bg-white/80 px-4 py-2 text-slate-600 shadow-sm transition-[color,background-color,transform] duration-150 hover:bg-emerald-50 hover:text-emerald-800 active:translate-y-px"><X size={16}/>取消</button>}
      </div>
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
  const [uploadOptions, setUploadOptions] = useState({ category: 'long_term', importance: 'important' });
  const [filters, setFilters] = useState({ q: '', category: 'all', importance: 'all', source: 'all', includeInactive: false });
  const [candidateStatus, setCandidateStatus] = useState('pending');
  const [isLoading, setIsLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [trendWindowDays, setTrendWindowDays] = useState(30);
  const [learningTrend, setLearningTrend] = useState({ series: [] });
  const [trendError, setTrendError] = useState('');
  const trendRequestVersion = useRef(0);
  const isProfileView = view === 'profile';
  const isMemoryView = view === 'memory';

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

  const learningTrendCharts = useMemo(() => buildLearningTrendCharts(learningTrend), [learningTrend]);
  const loadLearningTrend = async () => {
    const requestVersion = ++trendRequestVersion.current;
    try {
      setTrendError('');
      const response = await fetchWithAuth(`${API_BASE}/personalization/learning-trends?days=${trendWindowDays}`);
      if (!response.ok) throw new Error('学情趋势加载失败');
      const payload = await response.json();
      if (requestVersion === trendRequestVersion.current) setLearningTrend(payload);
    } catch (error) {
      if (requestVersion === trendRequestVersion.current) {
        setTrendError(error.message || '学情趋势加载失败');
      }
    }
  };

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

  const toggleLockedField = (field) => {
    setLearnerProfile((current) => {
      const locked = current.locked_fields || [];
      return {
        ...current,
        locked_fields: locked.includes(field)
          ? locked.filter((item) => item !== field)
          : [...locked, field],
      };
    });
  };

  const load = async () => {
    setIsLoading(true);
    try {
      const [overviewRes, memoriesRes, learnerRes, learningContextRes] = await Promise.all([
        fetchWithAuth(`${API_BASE}/personalization/overview`),
        fetchWithAuth(`${API_BASE}/personalization/memories${queryString ? `?${queryString}` : ''}`),
        fetchWithAuth(`${API_BASE}/personalization/learner-profile`),
        fetchWithAuth(`${MAIN_API_BASE}/learning-context`),
      ]);
      const candidateRes = await fetchWithAuth(`${API_BASE}/personalization/candidates?status=${candidateStatus}`);
      const overviewData = await overviewRes.json();
      const memoryData = await memoriesRes.json();
      const learnerData = await learnerRes.json();
      const learningContextData = learningContextRes.ok ? await learningContextRes.json() : {};
      const confirmedProfile = learningContextData.user_profile || {};
      const confirmedGoal = confirmedProfile.learning_goal
        || learningContextData.long_term_plan?.planning_route?.goal_name
        || learningContextData.long_term_plan?.goal_contract?.goal_name
        || '';
      const candidateData = await candidateRes.json();
      setOverview(overviewData);
      setProfile({
        ...emptyProfile,
        ...(overviewData.profile || {}),
        health_goals: overviewData.profile?.health_goals
          || confirmedGoal
          || confirmedProfile.goals?.goal_name
          || '',
        diet_restrictions: overviewData.profile?.diet_restrictions
          || confirmedProfile.time_constraints
          || '',
      });
      setLearnerProfile({
        locked_fields: [],
        survey: {},
        lock_reason: {},
        ...(learnerData || {}),
        learning_background: confirmedProfile.learning_background || '',
      });
      setMemories(Array.isArray(memoryData) ? memoryData : []);
      setCandidates(Array.isArray(candidateData) ? candidateData : []);
    } catch (e) {
      console.error(e);
      notify('加载学习画像与记忆失败');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => { load(); }, [queryString, candidateStatus]);
  useEffect(() => {
    loadLearningTrend();
    const refresh = window.setInterval(loadLearningTrend, 60_000);
    return () => window.clearInterval(refresh);
  }, [trendWindowDays]);

  const saveProfile = async () => {
    await fetchWithAuth(`${API_BASE}/personalization/profile`, { method: 'PUT', body: JSON.stringify(profile) });
    await fetchWithAuth(`${API_BASE}/personalization/learner-profile`, {
      method: 'PUT',
      body: JSON.stringify({
        learner_group: profile.constitution,
        learning_goal: profile.health_goals,
        time_constraints: profile.diet_restrictions,
        resource_preferences: profile.exercise_preferences,
        current_difficulties: profile.medical_history,
        learning_needs: profile.custom_needs,
        locked_fields: learnerProfile.locked_fields || [],
      }),
    });
    notify('画像与设置已保存');
    await load();
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

  const uploadMarkdown = async (file) => {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.md')) return notify('请上传 .md 文件');
    const formData = new FormData();
    formData.append('file', file);
    const params = new URLSearchParams(uploadOptions);
    const res = await fetchWithAuth(`${API_BASE}/personalization/memories/upload-md?${params.toString()}`, { method: 'POST', body: formData });
    if (!res.ok) return notify('MD 上传失败');
    const data = await res.json();
    notify(`已从 MD 导入 ${data.count || 0} 条记忆`);
    await load();
  };

  const cleanupExpired = async () => {
    const res = await fetchWithAuth(`${API_BASE}/personalization/memories/cleanup`, { method: 'POST' });
    const data = await res.json();
    notify(`已清理 ${data.cleaned || 0} 条过期记忆`);
    await load();
  };

  const exportData = async () => {
    const res = await fetchWithAuth(`${API_BASE}/personalization/export`);
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `personalization-${Date.now()}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const rootClassName = embedded
    ? 'text-gray-800'
    : 'min-h-screen bg-[radial-gradient(circle_at_top_left,#dcfce7,transparent_34%),radial-gradient(circle_at_top_right,#ccfbf1,transparent_30%),linear-gradient(135deg,#f8fafc_0%,#f0fdfa_46%,#ecfdf5_100%)] p-6 text-gray-800';

  return (
    <div className={rootClassName}>
      <div className="max-w-7xl mx-auto">
        <div className={embedded ? 'mb-5 flex justify-end border-b border-emerald-100 pb-4' : 'relative mb-6 overflow-hidden rounded-[36px] border border-white/80 bg-white/72 p-6 shadow-xl shadow-emerald-100/50 backdrop-blur-xl'}>
          {!embedded && <div className="absolute -right-20 -top-20 w-64 h-64 rounded-full bg-emerald-200/40 blur-3xl" />}
          {!embedded && <div className="absolute right-24 bottom-0 w-40 h-40 rounded-full bg-teal-200/35 blur-3xl" />}
          <div className={`relative flex flex-wrap items-start gap-4 ${embedded ? 'justify-end' : 'justify-between'}`}>
            {!embedded && <div>
              {(onBackHome || onBack) && (
                <button onClick={onBackHome || onBack} className="mb-5 inline-flex items-center gap-2 text-gray-500 hover:text-emerald-600 transition-colors"><ArrowLeft size={18}/> 返回主页</button>
              )}
              <div className="flex items-center gap-3 mb-3">
                <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-emerald-500 to-teal-500 text-white flex items-center justify-center shadow-lg shadow-emerald-200">
                  <Database size={24} />
                </div>
                <div>
                  <h1 className="text-3xl font-black tracking-tight text-slate-900">个性化数据库</h1>
                  <p className="text-sm text-gray-500 mt-1">统一管理学习群体、学习目标、时间约束、资源偏好、薄弱点与智能体抽取的学习记忆。</p>
                </div>
              </div>
              <div className="flex flex-wrap gap-2 mt-4">
                <span className="px-3 py-1 rounded-full bg-emerald-50 text-emerald-600 text-xs font-semibold border border-emerald-100">画像驱动回答</span>
                <span className="px-3 py-1 rounded-full bg-emerald-50 text-emerald-600 text-xs font-semibold border border-emerald-100">自动记忆沉淀</span>
                <span className="px-3 py-1 rounded-full bg-amber-50 text-amber-600 text-xs font-semibold border border-amber-100">过期记忆治理</span>
              </div>
            </div>}
            <div className="flex flex-wrap items-center gap-2 justify-end">
              {message && <span className="text-sm text-emerald-600 bg-white/80 border border-emerald-100 px-3 py-2 rounded-2xl shadow-sm">{message}</span>}
              <button onClick={load} className="bg-white/90 border border-white px-3 py-2 rounded-2xl flex items-center gap-2 text-slate-700 hover:text-emerald-700 hover:shadow-md transition-[color,box-shadow,transform] duration-150 active:translate-y-px"><RefreshCw size={16} className={isLoading ? 'animate-spin' : ''}/>刷新</button>
              {isMemoryView && <button onClick={cleanupExpired} className="bg-white/90 border border-white px-3 py-2 rounded-xl flex items-center gap-2 text-slate-700 hover:text-rose-700 hover:shadow-md transition-[color,box-shadow,transform] duration-150 active:translate-y-px"><Clock size={16}/>清理过期</button>}
              <button onClick={exportData} className="bg-gradient-to-r from-emerald-600 to-teal-600 text-white px-3 py-2 rounded-2xl flex items-center gap-2 hover:from-emerald-700 hover:to-teal-700 transition-[transform,box-shadow] duration-150 shadow-lg shadow-emerald-100 active:translate-y-px"><Download size={16}/>导出</button>
            </div>
          </div>
        </div>

        {isMemoryView && <div className="grid md:grid-cols-5 gap-4 mb-6">
          <div className="group bg-white/82 rounded-[28px] border border-white/80 shadow-sm shadow-emerald-100/40 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-emerald-100 transition-[transform,box-shadow] duration-200"><div className="w-10 h-10 rounded-2xl bg-emerald-50 text-emerald-700 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform"><Database size={20}/></div><p className="text-sm text-slate-600">启用记忆</p><p className="text-3xl font-black text-slate-900">{overview?.stats?.active_count || 0}</p></div>
          <div className="group bg-white/82 rounded-[28px] border border-white/80 shadow-sm shadow-amber-100/40 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-amber-100 transition-[transform,box-shadow] duration-200"><div className="w-10 h-10 rounded-2xl bg-amber-50 text-amber-700 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform"><Sparkles size={20}/></div><p className="text-sm text-slate-600">重要记忆</p><p className="text-3xl font-black text-slate-900">{overview?.stats?.important_count || 0}</p></div>
          <div className="group bg-white/82 rounded-[28px] border border-white/80 shadow-sm shadow-emerald-100/40 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-emerald-100 transition-[transform,box-shadow] duration-200"><div className="w-10 h-10 rounded-2xl bg-teal-50 text-teal-700 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform"><ArrowUpCircle size={20}/></div><p className="text-sm text-slate-600">待确认候选</p><p className="text-3xl font-black text-slate-900">{overview?.stats?.candidate_pending_count || 0}</p></div>
          {hasConflicts && <div className="group bg-white/82 rounded-[28px] border border-amber-100/80 shadow-sm shadow-amber-100/40 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-amber-100 transition-[transform,box-shadow] duration-200 md:col-span-5"><p className="text-sm text-amber-600 font-semibold">检测到重复语义记忆</p><p className="text-xs text-amber-500 mt-1">系统会自动保留最新有效值，并将旧版本停用。</p></div>}
          <div className="group bg-white/82 rounded-[28px] border border-white/80 shadow-sm shadow-slate-100/60 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-gray-200 transition-[transform,box-shadow] duration-200"><div className="w-10 h-10 rounded-2xl bg-slate-50 text-slate-500 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform"><Trash2 size={20}/></div><p className="text-sm text-gray-500">停用记忆</p><p className="text-3xl font-black text-slate-900">{overview?.stats?.inactive_count || 0}</p></div>
          <div className="group bg-white/82 rounded-[28px] border border-white/80 shadow-sm shadow-rose-100/40 p-5 hover:-translate-y-1 hover:shadow-xl hover:shadow-rose-100 transition-[transform,box-shadow] duration-200"><div className="w-10 h-10 rounded-2xl bg-rose-50 text-rose-500 flex items-center justify-center mb-3 group-hover:scale-110 transition-transform"><Clock size={20}/></div><p className="text-sm text-gray-500">已过期</p><p className="text-3xl font-black text-slate-900">{overview?.stats?.expired_count || 0}</p></div>
        </div>}

        {isMemoryView && <div className="grid lg:grid-cols-3 gap-4 mb-6">
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
                    <div className="h-full rounded-full bg-gradient-to-r from-emerald-400 to-teal-400 transition-[width] duration-200" style={{ width: `${Math.max(8, (count / maxCategoryCount) * 100)}%` }} />
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

        {isProfileView && <section className="mb-6 rounded-[30px] border border-emerald-100 bg-emerald-50/40 p-5 shadow-sm shadow-emerald-100/50">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-slate-900">
                <LineChart size={18} className="text-emerald-700" />
                <h2 className="text-xl font-semibold">学习状态趋势</h2>
              </div>
              <p className="mt-1 text-sm leading-6 text-emerald-950">按北京时间自然日汇总；切换时间段或每分钟自动刷新。</p>
            </div>
            <div className="flex rounded-full border border-emerald-200 bg-white p-1">
              {[7, 30, 90].map((days) => (
                <button
                  key={days}
                  type="button"
                  onClick={() => setTrendWindowDays(days)}
                  className={`rounded-full px-3 py-1.5 text-sm font-medium transition ${trendWindowDays === days ? 'bg-emerald-600 text-white' : 'text-emerald-900 hover:bg-emerald-50'}`}
                >
                  {days} 天
                </button>
              ))}
            </div>
          </div>
          {trendError ? (
            <p className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800">{trendError}</p>
          ) : (
            <div className="mt-4 grid gap-4 lg:grid-cols-3">
              {learningTrendCharts.map((chart) => <LearningTrendChart key={chart.key} chart={chart} />)}
            </div>
          )}
        </section>}

        <div className={isProfileView ? 'mx-auto max-w-3xl' : ''}>
          {isProfileView && <section className="bg-white/86 rounded-[32px] border border-white/80 shadow-lg shadow-emerald-100/45 p-6 h-fit backdrop-blur-sm">
            <h2 className="text-xl font-bold mb-4">学习者画像</h2>
            <div className="space-y-3">
              {learnerProfile.learning_background && (
                <label className="block">
                  <span className="text-sm text-gray-500">学习基础（智能体已确认）</span>
                  <textarea
                    value={learnerProfile.learning_background}
                    readOnly
                    className={`${softTextareaClass} mt-1 min-h-[58px] bg-emerald-50/60`}
                  />
                </label>
              )}
              {profileFields.map(([key, label]) => (
                <label key={key} className="block">
                  <span className="text-sm text-gray-500">{label}</span>
                  <textarea value={profile[key] || ''} onChange={e => setProfile({ ...profile, [key]: e.target.value })} className={`${softTextareaClass} mt-1 min-h-[58px]`} />
                </label>
              ))}
            </div>
            <div className="mt-4 rounded-3xl border border-emerald-100 bg-emerald-50/70 p-4">
              <div className="text-sm font-semibold text-emerald-950">可锁定画像字段</div>
              <p className="mt-1 text-sm leading-6 text-emerald-900">锁定后，行为日志和学情诊断只会提出更新建议，不会自动覆盖这些设置；你仍可在本页手动修改。</p>
              <div className="mt-3 space-y-2 text-sm text-emerald-950">
                {[
                  ['time_constraints', '锁定可投入时间/学习时段'],
                  ['resource_preferences', '锁定资源偏好'],
                  ['current_difficulties', '锁定当前困难/薄弱点'],
                ].map(([field, label]) => (
                  <label key={field} className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={(learnerProfile.locked_fields || []).includes(field)}
                      onChange={() => toggleLockedField(field)}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </div>
            <button onClick={saveProfile} className="mt-4 bg-gradient-to-r from-emerald-500 to-teal-500 hover:from-emerald-600 hover:to-teal-600 text-white px-4 py-2 rounded-2xl flex items-center gap-2 shadow-lg shadow-emerald-100 transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 active:translate-y-px"><Save size={16}/> 保存画像与设置</button>
          </section>}

          {isMemoryView && <section className="bg-white/86 rounded-[32px] border border-white/80 shadow-lg shadow-emerald-100/45 p-6 backdrop-blur-sm">
            <div className="mb-4">
              <h2 className="text-xl font-bold">学习记忆数据库</h2>
              <p className="text-sm text-gray-500 mt-1">手动维护和记忆管理智能体自动抽取的学习目标、偏好、薄弱点、阶段反馈都会在这里统一管理。</p>
            </div>

            <MemoryForm value={newMemory} onChange={setNewMemory} onSubmit={addMemory} submitText="新增记忆" />

            <div className="mt-4 rounded-[28px] border border-dashed border-emerald-200/80 bg-gradient-to-br from-white/90 to-emerald-50/70 p-4 shadow-inner shadow-emerald-50/80">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="w-10 h-10 rounded-2xl bg-emerald-100 text-emerald-600 flex items-center justify-center"><UploadCloud size={20}/></div>
                  <div>
                    <h3 className="font-bold text-slate-800">上传 Markdown 学习资料</h3>
                    <p className="text-xs text-gray-500 mt-0.5">按标题分段导入，支持长期学习记忆或 7 天短期学习状态。</p>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <SoftSelect value={uploadOptions.category} options={uploadCategoryOptions} onChange={category => setUploadOptions({ ...uploadOptions, category })} className="min-w-[150px] text-sm" />
                  <SoftSelect value={uploadOptions.importance} options={memoryImportanceOptions} onChange={importance => setUploadOptions({ ...uploadOptions, importance })} className="min-w-[110px] text-sm" />
                  <label className="cursor-pointer bg-gradient-to-r from-emerald-500 to-teal-500 text-white px-4 py-2.5 rounded-2xl text-sm flex items-center gap-2 shadow-lg shadow-emerald-100 transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:from-emerald-600 hover:to-teal-600 active:translate-y-px">
                    <UploadCloud size={16}/> 选择 .md
                    <input type="file" accept=".md,text/markdown" className="hidden" onChange={e => { uploadMarkdown(e.target.files?.[0]); e.target.value = ''; }} />
                  </label>
                </div>
              </div>
            </div>

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
