import React, { useCallback, useEffect, useState } from 'react';
import { Bell, Check, Clock3, Inbox, Route, X } from 'lucide-react';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';

async function request(path, options) {
  const response = await fetchWithAuth(`${API_BASE}${path}`, options);
  const payload = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(payload.detail || '请求失败');
  return payload;
}

const outcomeLabel = {
  on_track: '按计划推进',
  daily_adjustment_suggested: '建议调整今日任务',
  short_replan_suggested: '建议调整短期计划',
  long_replan_requires_confirmation: '长期规划变更待确认',
};

export default function LearningGovernancePanel() {
  const [notifications, setNotifications] = useState({ unread_count: 0, items: [] });
  const [interventions, setInterventions] = useState([]);
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [notificationData, interventionData, reviewData] = await Promise.all([
        request('/v1/notifications?limit=50'),
        request('/v1/interventions?limit=30'),
        request('/v1/plan-reviews?limit=30'),
      ]);
      setNotifications(notificationData);
      setInterventions(interventionData.items || []);
      setReviews(reviewData.items || []);
    } catch (loadError) {
      setError(loadError.message || '自动治理数据加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const updateNotification = async (notificationId, status) => {
    setBusy(`notification:${notificationId}`);
    try {
      await request(`/v1/notifications/${notificationId}`, { method: 'PATCH', body: JSON.stringify({ status }) });
      await load();
    } catch (actionError) {
      setError(actionError.message || '通知状态更新失败');
    } finally {
      setBusy('');
    }
  };

  const feedback = async (interventionId, action) => {
    setBusy(`intervention:${interventionId}`);
    try {
      await request(`/v1/interventions/${interventionId}/feedback`, { method: 'POST', body: JSON.stringify({ action, reason: '' }) });
      await load();
    } catch (actionError) {
      setError(actionError.message || '干预反馈提交失败');
    } finally {
      setBusy('');
    }
  };

  const decideReview = async (reviewId, decision) => {
    setBusy(`review:${reviewId}`);
    try {
      await request(`/v1/plan-reviews/${reviewId}/decision`, { method: 'POST', body: JSON.stringify({ decision }) });
      await load();
    } catch (actionError) {
      setError(actionError.message || '复盘决定保存失败');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded-[28px] bg-[#f2f8f4] p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div><div className="flex items-center gap-2 text-sm font-medium text-emerald-900"><Inbox size={16} />自动治理中心</div><h2 className="mt-3 text-2xl font-semibold tracking-tight text-slate-950">需要你处理的学习信号</h2><p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">系统只根据可追溯的行为数据提出建议；长期规划不会被自动覆盖。</p></div>
          <div className="rounded-2xl bg-white px-4 py-3 text-right"><div className="font-mono text-2xl font-semibold tabular-nums text-slate-950">{notifications.unread_count || 0}</div><div className="text-xs text-slate-500">未读通知</div></div>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <article className="rounded-[26px] bg-white p-5 shadow-sm shadow-emerald-950/5">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-950"><Bell size={16} />通知</div>
          <div className="mt-4 space-y-3">
            {(notifications.items || []).map((item) => (
              <div key={item.notification_id} className={`rounded-2xl p-4 ${item.status === 'unread' ? 'bg-emerald-50/80' : 'bg-slate-50'}`}>
                <div className="flex items-start justify-between gap-3"><div><div className="text-sm font-semibold text-slate-950">{item.title}</div><p className="mt-1 text-sm leading-6 text-slate-600">{item.message}</p></div><span className="text-xs text-slate-400">{item.category}</span></div>
                {item.status === 'unread' && <div className="mt-3 flex gap-2"><button type="button" className="button button--secondary" disabled={busy === `notification:${item.notification_id}`} onClick={() => updateNotification(item.notification_id, 'read')}><Check size={14} />已读</button><button type="button" className="button button--ghost" disabled={busy === `notification:${item.notification_id}`} onClick={() => updateNotification(item.notification_id, 'dismissed')}><X size={14} />忽略</button></div>}
              </div>
            ))}
            {!loading && (notifications.items || []).length === 0 && <div className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-500">暂无通知。</div>}
          </div>
        </article>

        <article className="rounded-[26px] bg-white p-5 shadow-sm shadow-emerald-950/5">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-950"><Clock3 size={16} />主动干预</div>
          <div className="mt-4 space-y-3">
            {interventions.map((item) => (
              <div key={item.intervention_id} className="rounded-2xl bg-amber-50/70 p-4">
                <div className="flex items-center justify-between gap-3"><strong className="text-sm text-amber-950">{item.action}</strong><span className="text-xs text-amber-800">{item.t_stage}</span></div>
                <p className="mt-2 text-sm leading-6 text-amber-950/80">{item.reason}</p>
                {['delivered', 'suggested', 'postponed'].includes(item.lifecycle_status) && <div className="mt-3 flex flex-wrap gap-2"><button type="button" className="button button--primary" disabled={busy === `intervention:${item.intervention_id}`} onClick={() => feedback(item.intervention_id, 'accept')}>接受建议</button><button type="button" className="button button--secondary" disabled={busy === `intervention:${item.intervention_id}`} onClick={() => feedback(item.intervention_id, 'postpone')}>稍后处理</button><button type="button" className="button button--ghost" disabled={busy === `intervention:${item.intervention_id}`} onClick={() => feedback(item.intervention_id, 'not_relevant')}>不适用</button></div>}
              </div>
            ))}
            {!loading && interventions.length === 0 && <div className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-500">当前没有达到触发标准的干预信号。</div>}
          </div>
        </article>
      </section>

      <section className="rounded-[26px] bg-white p-5 shadow-sm shadow-emerald-950/5 sm:p-6">
        <div className="flex items-center gap-2 text-sm font-semibold text-slate-950"><Route size={16} />规划复盘记录</div>
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {reviews.map((item) => (
            <article key={item.review_id} className="rounded-2xl bg-slate-50 p-4">
              <div className="flex items-start justify-between gap-4"><div><div className="text-sm font-semibold text-slate-950">{outcomeLabel[item.outcome] || item.outcome}</div><p className="mt-2 text-sm leading-6 text-slate-600">{item.summary}</p></div><span className="text-xs text-slate-400">{item.period_key}</span></div>
              <ul className="mt-3 space-y-1 text-xs text-slate-500">{(item.evidence || []).map((evidence) => <li key={evidence}>· {evidence}</li>)}</ul>
              {item.status === 'proposal_pending' && <div className="mt-4 flex gap-2"><button type="button" className="button button--primary" disabled={busy === `review:${item.review_id}`} onClick={() => decideReview(item.review_id, 'accept')}>接受调整</button><button type="button" className="button button--secondary" disabled={busy === `review:${item.review_id}`} onClick={() => decideReview(item.review_id, 'reject')}>保持原计划</button></div>}
            </article>
          ))}
          {!loading && reviews.length === 0 && <div className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-500">暂无规划复盘记录。</div>}
        </div>
      </section>
      {loading && <div role="status" className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-500">正在读取通知、干预和复盘记录…</div>}
      {error && <div role="alert" className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
    </div>
  );
}
