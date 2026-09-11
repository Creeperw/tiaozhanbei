import React, { useEffect, useState } from 'react';
import { loadAllLearningHistory } from '../legacyLearningClient';

export default function LegacyPlanSummary() {
  const [state, setState] = useState({ status: 'loading', items: [], error: '' });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: 'loading', items: [], error: '' });
    loadAllLearningHistory('plans', { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setState({ status: 'ready', items: result.items, error: '' });
    }).catch(error => {
      if (!controller.signal.aborted) setState({ status: 'error', items: [], error: error.message });
    });
    return () => controller.abort();
  }, [retry]);
  return (
    <section aria-label="历史计划摘要" className="mt-4 space-y-3 rounded-2xl border border-slate-200 bg-white p-4">
      <h3 className="font-semibold text-slate-900">历史计划摘要</h3>
      <p className="text-sm text-slate-500">保留原计划说明，仅供查看，不是当前考试的可执行计划。</p>
      {state.status === 'loading' && <p role="status">正在读取历史计划…</p>}
      {state.status === 'error' && <p role="alert">历史计划读取失败：{state.error}<button className="ml-2 underline" onClick={() => setRetry(value => value + 1)}>重试</button></p>}
      {state.status === 'ready' && !state.items.length && <p>暂无历史计划记录。</p>}
      {state.items.map(item => <article key={item.id} className="rounded-xl bg-slate-50 p-3">
        <h4 className="font-medium">{item.title || '未命名历史计划'}</h4>
        <p className="mt-2 whitespace-pre-wrap text-sm">{item.summary || '未记录摘要'}</p>
        <p className="mt-2 text-xs text-slate-500">原状态：{item.status || '未记录'} · {item.created_at || '时间未记录'} · 历史归档</p>
      </article>)}
    </section>
  );
}