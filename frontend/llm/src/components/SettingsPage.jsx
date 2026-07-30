import React, { useEffect, useState } from 'react';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';

const defaultNotificationPreferences = {
  in_app_enabled: true,
  categories: { review_due: true, intervention: true, plan_review: true },
  digest_frequency: 'realtime',
  quiet_hours: { start: '22:00', end: '07:00' },
};

const settingsSnapshot = (notifications) => JSON.stringify({
  notifications,
});

export default function SettingsPage({ embedded = false }) {
  const [notificationPreferences, setNotificationPreferences] = useState(defaultNotificationPreferences);
  const [notificationsAvailable, setNotificationsAvailable] = useState(false);
  const [savedSnapshot, setSavedSnapshot] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');

  const currentSnapshot = settingsSnapshot(notificationPreferences);
  const dirty = Boolean(savedSnapshot) && currentSnapshot !== savedSnapshot;

  useEffect(() => {
    if (!embedded) {
      setLoading(false);
      return undefined;
    }

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
        const data = await readJsonResponse(res, { settings: {} });
        const nextNotifications = data.settings?.notification_preferences || defaultNotificationPreferences;
        const nextNotificationsAvailable = true;
        if (!cancelled) {
          setNotificationPreferences(nextNotifications);
          setNotificationsAvailable(nextNotificationsAvailable);
          setSavedSnapshot(settingsSnapshot(nextNotifications));
        }
      } catch (e) {
        if (!cancelled) setError(e.message || '设置加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();
    return () => { cancelled = true; };
  }, [embedded, reloadKey]);

  const save = async () => {
    setError('');
    setMessage('');
    setSaving(true);
    const payload = {
      notification_preferences: notificationPreferences,
    };
    try {
      const res = await fetchWithAuth(`${API_BASE}/personalization/learner-settings`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
      const data = await readJsonResponse(res, {});
      if (!res.ok) throw new Error(data.detail || '设置保存失败');
      setSavedSnapshot(settingsSnapshot(notificationPreferences));
      setMessage('设置已保存');
    } catch (saveError) {
      setError(saveError.message || '设置保存失败');
    } finally {
      setSaving(false);
    }
  };

  if (!embedded) {
    return null;
  }

  return (
    <div className={`settings-page space-y-6${embedded ? ' settings-page--embedded' : ''}`}>
      {loading && <div role="status" className="settings-page__loading">正在加载设置…</div>}
      <section aria-busy={loading} className="rounded-[28px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/60 sm:p-6">
        <h2 className="text-xl font-semibold text-slate-950">通知与主动提醒</h2>
        <p className="mt-2 text-[15px] leading-6 text-slate-600">控制复习到期、主动干预和规划复盘通知。关闭通知不会停止后台计算。</p>
        <label className="mt-4 flex items-center justify-between gap-4 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-900">
          <span>启用站内通知</span>
          <input type="checkbox" checked={notificationPreferences.in_app_enabled} disabled={loading || saving || !notificationsAvailable} onChange={(event) => setNotificationPreferences((current) => ({ ...current, in_app_enabled: event.target.checked }))} />
        </label>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          {[
            ['review_due', '到期复习'],
            ['intervention', '主动干预'],
            ['plan_review', '规划复盘'],
          ].map(([key, label]) => (
            <label key={key} className="flex items-center gap-3 rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-900">
              <input type="checkbox" checked={Boolean(notificationPreferences.categories?.[key])} disabled={loading || saving || !notificationsAvailable} onChange={(event) => setNotificationPreferences((current) => ({ ...current, categories: { ...current.categories, [key]: event.target.checked } }))} />
              {label}
            </label>
          ))}
        </div>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <label className="text-sm text-slate-700">通知频率<select className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2" value={notificationPreferences.digest_frequency} disabled={loading || saving || !notificationsAvailable} onChange={(event) => setNotificationPreferences((current) => ({ ...current, digest_frequency: event.target.value }))}><option value="realtime">实时</option><option value="daily">每日汇总</option><option value="weekly">每周汇总</option><option value="paused">暂停</option></select></label>
          <label className="text-sm text-slate-700">勿扰开始<input type="time" className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2" value={notificationPreferences.quiet_hours?.start || '22:00'} disabled={loading || saving || !notificationsAvailable} onChange={(event) => setNotificationPreferences((current) => ({ ...current, quiet_hours: { ...current.quiet_hours, start: event.target.value } }))} /></label>
          <label className="text-sm text-slate-700">勿扰结束<input type="time" className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2" value={notificationPreferences.quiet_hours?.end || '07:00'} disabled={loading || saving || !notificationsAvailable} onChange={(event) => setNotificationPreferences((current) => ({ ...current, quiet_hours: { ...current.quiet_hours, end: event.target.value } }))} /></label>
        </div>
        {!notificationsAvailable && !loading && <p className="mt-3 text-[15px] text-slate-500">通知偏好接口暂不可用，当前保持默认设置。</p>}
      </section>
      {error && <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}{!savedSnapshot && <button type="button" className="ml-3 font-semibold underline" onClick={() => setReloadKey((value) => value + 1)}>重新加载</button>}</div>}
      {message && <div role="status" className="rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{message}</div>}
      <div className={`settings-save-bar${embedded ? ' settings-save-bar--embedded' : ''}`}>
        <span className={dirty ? 'text-amber-800' : 'text-slate-500'}>{dirty ? '有未保存的更改' : message || '所有更改均已保存'}</span>
        <button type="button" onClick={save} disabled={loading || saving || !dirty} className="button button--primary">
          {saving ? '正在保存…' : '保存更改'}
        </button>
      </div>
    </div>
  );
}
