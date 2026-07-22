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
  const [apiForm, setApiForm] = useState({
    deepseek_api_key: '',
    siliconflow_api_key: '',
    mineru_api_token: '',
  });
  const [apiProviders, setApiProviders] = useState({});
  const [apiLoading, setApiLoading] = useState(true);
  const [apiSaving, setApiSaving] = useState(false);
  const [apiMessage, setApiMessage] = useState('');
  const [apiError, setApiError] = useState('');

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

  useEffect(() => {
    let cancelled = false;
    const loadApiSettings = async () => {
      setApiLoading(true);
      setApiError('');
      try {
        const res = await fetchWithAuth(`${API_BASE}/personalization/api-settings`);
        const data = await readJsonResponse(res, { providers: {} });
        if (!res.ok) throw new Error(data.detail || 'API 配置加载失败');
        if (!cancelled) setApiProviders(data.providers || {});
      } catch (loadError) {
        if (!cancelled) setApiError(loadError.message || 'API 配置加载失败');
      } finally {
        if (!cancelled) setApiLoading(false);
      }
    };
    loadApiSettings();
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

  const saveApiSettings = async () => {
    const payload = Object.fromEntries(
      Object.entries(apiForm).filter(([, value]) => value.trim()),
    );
    if (Object.keys(payload).length === 0) {
      setApiError('请至少填写一个新的 API Key 或 Token。');
      return;
    }
    setApiSaving(true);
    setApiError('');
    setApiMessage('');
    try {
      const res = await fetchWithAuth(`${API_BASE}/personalization/api-settings`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      const data = await readJsonResponse(res, { providers: {} });
      if (!res.ok) throw new Error(data.detail || 'API 配置保存失败');
      setApiProviders(data.providers || {});
      setApiForm({ deepseek_api_key: '', siliconflow_api_key: '', mineru_api_token: '' });
      setApiMessage('API 配置已保存在本机；刷新页面后仍会继续使用。');
    } catch (saveError) {
      setApiError(saveError.message || 'API 配置保存失败');
    } finally {
      setApiSaving(false);
    }
  };

  const clearApiSetting = async (provider) => {
    setApiSaving(true);
    setApiError('');
    setApiMessage('');
    try {
      const res = await fetchWithAuth(`${API_BASE}/personalization/api-settings`, {
        method: 'PUT',
        body: JSON.stringify({ clear: [provider] }),
      });
      const data = await readJsonResponse(res, { providers: {} });
      if (!res.ok) throw new Error(data.detail || 'API 配置清除失败');
      setApiProviders(data.providers || {});
      setApiMessage('已清除对应的本机 API 配置。');
    } catch (clearError) {
      setApiError(clearError.message || 'API 配置清除失败');
    } finally {
      setApiSaving(false);
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
      <section aria-busy={apiLoading || apiSaving} className="rounded-[28px] border border-emerald-200 bg-white p-5 shadow-sm shadow-emerald-100/60 sm:p-6">
        <h2 className="text-xl font-semibold text-slate-950">本机模型 API 配置</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">
          密钥保存在本机后端数据库，浏览器不会保存明文，接口也只返回脱敏状态。留空表示保留原配置。
        </p>
        <div className="mt-5 grid gap-4 lg:grid-cols-3">
          {[
            { provider: 'deepseek', field: 'deepseek_api_key', label: 'DeepSeek API Key', hint: '智能助教：deepseek-v4-flash' },
            { provider: 'siliconflow', field: 'siliconflow_api_key', label: '硅基流动 API Key', hint: '向量模型：Qwen3-Embedding-4B' },
            { provider: 'mineru', field: 'mineru_api_token', label: 'MinerU API Token', hint: 'PDF 与图片资料解析' },
          ].map((item) => {
            const status = apiProviders[item.provider] || {};
            return (
              <div key={item.provider} className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
                <label className="block text-sm font-semibold text-slate-900" htmlFor={item.field}>{item.label}</label>
                <p className="mt-1 text-xs text-slate-500">{item.hint}</p>
                <input
                  id={item.field}
                  type="password"
                  autoComplete="new-password"
                  value={apiForm[item.field]}
                  placeholder={status.configured ? `已保存：${status.masked}` : '尚未配置'}
                  onChange={(event) => setApiForm((current) => ({ ...current, [item.field]: event.target.value }))}
                  className="mt-3 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm outline-none focus:border-emerald-400"
                />
                <div className="mt-3 flex items-center justify-between gap-3 text-xs">
                  <span className={status.configured ? 'text-emerald-700' : 'text-slate-500'}>
                    {status.configured ? '已配置并持久化' : '未配置'}
                  </span>
                  {status.configured && (
                    <button type="button" disabled={apiSaving} onClick={() => clearApiSetting(item.provider)} className="text-rose-600 underline disabled:opacity-50">
                      清除
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        {apiError && <div role="alert" className="mt-4 rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700">{apiError}</div>}
        {apiMessage && <div role="status" className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">{apiMessage}</div>}
        <div className="mt-4 flex justify-end">
          <button type="button" onClick={saveApiSettings} disabled={apiLoading || apiSaving} className="button button--primary">
            {apiSaving ? '正在保存 API…' : '保存 API 配置'}
          </button>
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
