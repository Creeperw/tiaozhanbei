import React, { useEffect, useState } from 'react';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';

export default function LearningContinuitySummary({ userKey, revision }) {
  const [state, setState] = useState({ owner: userKey, data: null, error: '' });
  useEffect(() => {
    const controller = new AbortController();
    setState({ owner: userKey, data: null, error: '' });
    (async () => {
      try {
        const response = await fetchWithAuth(`${API_BASE}/learning-activity/continuity`, { signal: controller.signal });
        const data = await readJsonResponse(response, null);
        if (!response.ok || data?.record_scope !== 'legacy_archive'
            || data?.audit_status !== 'not_evaluated'
            || typeof data.has_history !== 'boolean'
            || !['attempt_count', 'recent_active_days', 'recent_focus_minutes', 'recent_behavior_window_days']
              .every(key => Number.isFinite(data[key]) && data[key] >= 0)) {
          throw new Error('历史学情读取失败');
        }
        if (!controller.signal.aborted) setState({ owner: userKey, data, error: '' });
      } catch {
        if (!controller.signal.aborted) setState({ owner: userKey, data: null, error: '历史学情暂时无法读取，不代表学习进度为零。' });
      }
    })();
    return () => controller.abort();
  }, [userKey, revision]);
  if (state.owner !== userKey) return null;
  if (state.error) return <p role="status">{state.error}</p>;
  const data = state.data;
  if (!data?.has_history) return null;
  return <div aria-label="已有学习经历" className="text-sm text-slate-600">
    <p>近{data.recent_behavior_window_days}天活跃 {data.recent_active_days} 天 · 专注学习 {data.recent_focus_minutes} 分钟 · 历史答题 {data.attempt_count} 次</p>
    <p>已有学习经历将用于规划衔接；下方仅统计当前教材完成率，不代表全部学习进度。历史答题尚未作为正式测评成绩。</p>
  </div>;
}