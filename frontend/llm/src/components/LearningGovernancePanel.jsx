import React, { useCallback, useEffect, useId, useState } from 'react';
import { Bell, Check, Clock3, Route, Settings2, X } from 'lucide-react';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import SettingsPage from './SettingsPage';
import { useModalFocus } from './ui/useModalFocus';

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

const executionLabel = {
  not_started: '待执行',
  queued: '已排队',
  running: '执行中',
  succeeded: '已完成',
  failed: '执行失败',
};

// 复盘决策状态与 execution_status（异步重规划执行）是两条独立状态线。
// 已决策的复盘必须显示用户的决定，不能回落到执行态的「待执行」。
const reviewStatusLabel = {
  proposal_pending: '待你确认',
  accepted: '已接受调整',
  rejected: '已保持原计划',
};

function isAsyncPlanReview(item) {
  const proposal = item?.proposal || {};
  return proposal.target_layer === 'short_term'
    && ['replan_for_low_completion', 'add_review_window', 'slow_progress'].includes(proposal.operation);
}

function NotificationSettingsDialog({ open, onClose }) {
  const dialogRef = useModalFocus(open);
  const titleId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/35 px-4 py-6 backdrop-blur-sm" onMouseDown={onClose}>
      <section
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="flex max-h-[min(90dvh,760px)] w-full max-w-3xl flex-col overflow-hidden rounded-[28px] border border-white/80 bg-[#f8fbf9] shadow-2xl shadow-slate-950/20"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-emerald-100 bg-white px-5 py-5 sm:px-6">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-emerald-800"><Settings2 aria-hidden="true" size={16} />通知偏好</div>
            <h2 id={titleId} className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">通知设置</h2>
            <p className="mt-2 max-w-xl text-sm leading-6 text-slate-600">按你的学习节奏设置到期复习、主动干预与规划复盘提醒。</p>
          </div>
          <button type="button" data-autofocus className="icon-button" aria-label="关闭通知设置" onClick={onClose}>
            <X aria-hidden="true" size={20} />
          </button>
        </header>
        <div className="min-h-0 overflow-y-auto px-5 py-5 sm:px-6 sm:py-6">
          <SettingsPage embedded />
        </div>
      </section>
    </div>
  );
}

export default function LearningGovernancePanel({ focusNotificationId = '' }) {
  const [notifications, setNotifications] = useState({ unread_count: 0, items: [] });
  const [interventions, setInterventions] = useState([]);
  const [interventionStatus, setInterventionStatus] = useState(null);
  const [reviews, setReviews] = useState([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [settingsOpen, setSettingsOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [notificationData, interventionData, reviewData] = await Promise.all([
        request('/v1/notifications?limit=50'),
        request('/v1/interventions?limit=30'),
        request('/v1/plan-reviews?limit=30'),
      ]);
      const insightData = await request('/v1/learning-insights?days=30&run_automation=false').catch(() => ({}));
      setNotifications(notificationData);
      setInterventions(interventionData.items || []);
      setInterventionStatus(insightData.intervention_status || null);
      setReviews(reviewData.items || []);
    } catch (loadError) {
      setError(loadError.message || '自动治理数据加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const active = reviews.some((item) => (
      isAsyncPlanReview(item) && ['queued', 'running'].includes(item.execution_status)
    ));
    if (!active) return undefined;
    const timer = window.setInterval(load, 2500);
    return () => window.clearInterval(timer);
  }, [load, reviews]);

  useEffect(() => {
    if (!focusNotificationId || loading) return;
    window.setTimeout(() => {
      document.getElementById(`notification-${focusNotificationId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 80);
  }, [focusNotificationId, loading]);

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
    setError('');
    try {
      const payload = await request(`/v1/interventions/${interventionId}/feedback`, { method: 'POST', body: JSON.stringify({ action, reason: '' }) });
      const applied = payload?.applied;
      if (applied?.applied || applied?.already_applied) {
        setNotice(`已安排：${applied.title || '学习调整'}，已加入今日任务。`);
      } else if (action === 'accept') {
        setError(applied?.reason || '学习调整未能落地，请稍后重试。');
      } else {
        setNotice(action === 'postpone' ? '已设为稍后处理。' : '已记录你的反馈。');
      }
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
      const payload = await request(`/v1/plan-reviews/${reviewId}/decision`, { method: 'POST', body: JSON.stringify({ decision }) });
      const applied = payload?.applied;
      if (decision === 'accept' && applied) {
        setNotice(applied.applied
          ? (applied.replan_started ? '重规划已排队，系统会在完成后刷新今日任务。' : `${applied.summary || '规划调整已完成'}`)
          : (applied.reason || '调整执行失败，请稍后重试。'));
      }
      await load();
    } catch (actionError) {
      setError(actionError.message || '复盘决定保存失败');
    } finally {
      setBusy('');
    }
  };

  const runReview = async () => {
    setBusy('review:run');
    setError('');
    setNotice('');
    try {
      const payload = await request('/v1/plan-reviews/run', { method: 'POST' });
      setNotice(payload?.summary || '复盘已完成，可在下方查看结论。');
      await load();
    } catch (actionError) {
      setError(actionError.message || '复盘运行失败，请稍后重试。');
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="space-y-6">
      <section className="rounded-[28px] bg-[#f2f8f4] p-5 sm:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <button type="button" className="button button--secondary" aria-haspopup="dialog" aria-expanded={settingsOpen} onClick={() => setSettingsOpen(true)}>
              <Settings2 aria-hidden="true" size={16} />通知设置
            </button>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight text-slate-950">需要你处理的学习信号</h2>
            <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">系统只根据可追溯的行为数据提出建议；长期规划不会被自动覆盖。</p>
          </div>
          <div className="rounded-2xl bg-white px-4 py-3 text-right"><div className="font-mono text-2xl font-semibold tabular-nums text-slate-950">{notifications.unread_count || 0}</div><div className="text-xs text-slate-500">未读通知</div></div>
        </div>
      </section>

      <section className="grid gap-6 xl:grid-cols-[1fr_1fr]">
        <article className="rounded-[26px] bg-white p-5 shadow-sm shadow-emerald-950/5">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-950"><Bell size={16} />通知</div>
          <div className="mt-4 space-y-3">
            {(notifications.items || []).map((item) => (
              <div id={`notification-${item.notification_id}`} key={item.notification_id} className={`rounded-2xl p-4 transition-shadow ${item.notification_id === focusNotificationId ? 'ring-2 ring-emerald-400 ring-offset-2' : ''} ${item.status === 'unread' ? 'bg-emerald-50/80' : 'bg-slate-50'}`}>
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
                {['delivered', 'suggested', 'postponed'].includes(item.lifecycle_status) && <div className="mt-3 flex flex-wrap gap-2">{item.actionable !== false && <button type="button" className="button button--primary" disabled={busy === `intervention:${item.intervention_id}`} onClick={() => feedback(item.intervention_id, 'accept')}>接受建议</button>}<button type="button" className="button button--secondary" disabled={busy === `intervention:${item.intervention_id}`} onClick={() => feedback(item.intervention_id, 'postpone')}>稍后处理</button><button type="button" className="button button--ghost" disabled={busy === `intervention:${item.intervention_id}`} onClick={() => feedback(item.intervention_id, 'not_relevant')}>不适用</button></div>}
                {item.lifecycle_status === 'accepted' && <p className="mt-3 rounded-xl bg-emerald-50 px-3 py-2 text-xs text-emerald-700">该调整已成功应用到学习任务。</p>}
              </div>
            ))}
            {!loading && interventions.length === 0 && <div className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-500">{interventionStatus?.reason || '当前没有达到触发标准的干预信号。'}{interventionStatus?.gate ? `（${interventionStatus.gate}）` : ''}</div>}
          </div>
        </article>
      </section>

      <section className="rounded-[26px] bg-white p-5 shadow-sm shadow-emerald-950/5 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-950"><Route size={16} />规划复盘记录</div>
          <button type="button" className="button button--secondary" disabled={busy === 'review:run'} onClick={runReview}>{busy === 'review:run' ? '复盘中…' : '立即复盘'}</button>
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-2">
          {reviews.map((item) => {
            const asyncPlanReview = isAsyncPlanReview(item);
            const executionStatus = asyncPlanReview ? item.execution_status : 'not_started';
            const showExecution = asyncPlanReview && Boolean(executionStatus) && executionStatus !== 'not_started';
            return (
            <article key={item.review_id} className="rounded-2xl bg-slate-50 p-4">
              <div className="flex items-start justify-between gap-4"><div><div className="text-sm font-semibold text-slate-950">{outcomeLabel[item.outcome] || item.outcome}</div><p className="mt-2 text-sm leading-6 text-slate-600">{item.summary}</p></div><div className="text-right"><span className="text-xs text-slate-400">{item.period_key}</span>{reviewStatusLabel[item.status] && <div className="mt-1 text-xs text-slate-500">{reviewStatusLabel[item.status]}</div>}{showExecution && <div className={`mt-1 text-xs ${executionStatus === 'failed' ? 'text-rose-600' : executionStatus === 'succeeded' ? 'text-emerald-600' : 'text-amber-600'}`}>{executionLabel[executionStatus] || executionStatus}</div>}</div></div>
              <ul className="mt-3 space-y-1 text-xs text-slate-500">{(item.evidence || []).map((evidence) => <li key={evidence}>· {evidence}</li>)}</ul>
              {executionStatus === 'failed' && <p className="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-xs text-rose-700">{item.execution?.error || '执行失败，可重新接受调整。'}</p>}
              {(item.status === 'proposal_pending' || executionStatus === 'failed') && <div className="mt-4 flex gap-2"><button type="button" className="button button--primary" disabled={busy === `review:${item.review_id}`} onClick={() => decideReview(item.review_id, 'accept')}>{executionStatus === 'failed' ? '重试调整' : '接受调整'}</button>{item.status === 'proposal_pending' && <button type="button" className="button button--secondary" disabled={busy === `review:${item.review_id}`} onClick={() => decideReview(item.review_id, 'reject')}>保持原计划</button>}</div>}
            </article>
            );
          })}
          {!loading && reviews.length === 0 && <div className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-500">暂无规划复盘记录。系统会按周生成，也可以立即运行一次。</div>}
        </div>
      </section>
      {loading && <div role="status" className="rounded-2xl bg-slate-50 p-4 text-sm text-slate-500">正在读取通知、干预和复盘记录…</div>}
      {notice && <div role="status" className="flex items-center justify-between gap-3 rounded-2xl bg-emerald-50 px-4 py-3 text-sm text-emerald-800"><span>{notice}</span><button type="button" className="icon-button" aria-label="关闭提示" onClick={() => setNotice('')}><X aria-hidden="true" size={16} /></button></div>}
      {error && <div role="alert" className="rounded-2xl bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>}
      <NotificationSettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </div>
  );
}
