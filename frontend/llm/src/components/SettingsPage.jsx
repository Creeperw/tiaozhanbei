import React, { useEffect, useState } from 'react';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';

const frequencyOptions = [
  { value: 'daily', label: '每日一次' },
  { value: 'weekly', label: '每周一次' },
  { value: 'manual', label: '仅手动刷新' },
  { value: 'paused', label: '暂停自动分析' },
];

const lockableFields = [
  { key: 'learner_group', label: '学习群体' },
  { key: 'learning_goal', label: '主要学习目标' },
  { key: 'time_constraints', label: '可学习时间' },
  { key: 'resource_preferences', label: '资源偏好' },
  { key: 'preferred_time_slot', label: '可学习时段' },
  { key: 'current_difficulties', label: '当前困难' },
];

export default function SettingsPage() {
  const [analysisFrequency, setAnalysisFrequency] = useState('daily');
  const [lockedFields, setLockedFields] = useState([]);
  const [savedSnapshot, setSavedSnapshot] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const createSnapshot = (frequency, fields) => JSON.stringify({
    analysis_frequency: frequency,
    locked_fields: [...fields].sort(),
  });
  const currentSnapshot = createSnapshot(analysisFrequency, lockedFields);
  const dirty = Boolean(savedSnapshot) && currentSnapshot !== savedSnapshot;

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const res = await fetchWithAuth(`${API_BASE}/personalization/learner-settings`);
        if (!res.ok) {
          const payload = await readJsonResponse(res, {});
          throw new Error(payload.detail || '设置加载失败');
        }
        const data = await readJsonResponse(res, { settings: {}, locked_fields: [] });
        if (!cancelled) {
          const nextFrequency = data.settings?.analysis_frequency || 'daily';
          const nextFields = Array.isArray(data.locked_fields) ? data.locked_fields : [];
          setAnalysisFrequency(nextFrequency);
          setLockedFields(nextFields);
          setSavedSnapshot(createSnapshot(nextFrequency, nextFields));
        }
      } catch (e) {
        if (!cancelled) setError(e.message || '设置加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [reloadKey]);

  const toggleField = (key) => {
    setLockedFields((current) => (current.includes(key) ? current.filter((item) => item !== key) : [...current, key]));
  };

  const save = async () => {
    setError('');
    setMessage('');
    setSaving(true);
    const payload = { analysis_frequency: analysisFrequency, locked_fields: lockedFields };
    try {
      const res = await fetchWithAuth(`${API_BASE}/personalization/learner-settings`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      const data = await readJsonResponse(res, {});
      if (!res.ok) throw new Error(data.detail || '设置保存失败');
      setSavedSnapshot(createSnapshot(payload.analysis_frequency, payload.locked_fields));
      setMessage('设置已保存');
    } catch (saveError) {
      setError(saveError.message || '设置保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="settings-page space-y-6">
      {loading && <div role="status" className="settings-page__loading">正在加载设置…</div>}
      <section aria-busy={loading} className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60 sm:p-6">
        <h2 className="text-xl font-semibold text-slate-950">学情分析智能体更新频率</h2>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {frequencyOptions.map((option) => (
            <button key={option.value} type="button" disabled={loading || saving || !savedSnapshot} aria-pressed={analysisFrequency === option.value} onClick={() => { setAnalysisFrequency(option.value); setMessage(''); }} className={`rounded-2xl border px-4 py-3 text-sm font-medium transition ${analysisFrequency === option.value ? 'border-emerald-300 bg-emerald-50 text-emerald-900' : 'border-slate-200 bg-white text-slate-700 hover:bg-slate-50'}`}>
              {option.label}
            </button>
          ))}
        </div>
      </section>
      <section aria-busy={loading} className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60 sm:p-6">
        <h2 className="text-xl font-semibold text-slate-950">可锁定画像字段</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">锁定字段不会被行为日志或自动分析直接覆盖，只会进入冲突清单等待你确认。</p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {lockableFields.map((field) => (
            <label key={field.key} className="flex items-center gap-3 rounded-2xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-900">
              <input type="checkbox" checked={lockedFields.includes(field.key)} disabled={loading || saving || !savedSnapshot} onChange={() => { toggleField(field.key); setMessage(''); }} />
              {field.label}
            </label>
          ))}
        </div>
      </section>
      {error && <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}{!savedSnapshot && <button type="button" className="ml-3 font-semibold underline" onClick={() => setReloadKey((value) => value + 1)}>重新加载</button>}</div>}
      {message && <div role="status" className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}
      <div className="settings-save-bar">
        <span className={dirty ? 'text-amber-800' : 'text-slate-500'}>{dirty ? '有未保存的更改' : message || '所有更改均已保存'}</span>
        <button type="button" onClick={save} disabled={loading || saving || !dirty} className="button button--primary">
          {saving ? '正在保存…' : '保存更改'}
        </button>
      </div>
    </div>
  );
}
