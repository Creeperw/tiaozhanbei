import React from 'react';
import { buildProfileConflictSections } from '../profileConflictList.js';

export default function ProfileConflictList({ memories = [], candidates = [] }) {
  const { conflicts, pendingCandidates, hasActionableItems } = buildProfileConflictSections({ memories, candidates });

  if (!hasActionableItems) {
    return <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">当前没有待确认画像冲突。</div>;
  }

  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold text-slate-950">冲突清单</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">系统只列出建议，用户未确认前不会覆盖原画像。</p>
      </div>
      {conflicts.map(({ key, items }) => (
        <div key={key} className="rounded-[24px] border border-amber-200 bg-amber-50 p-4">
          <div className="text-sm font-semibold text-amber-950">事实/偏好冲突：{key}</div>
          <div className="mt-3 space-y-2">
            {items.map((item) => <div key={item.id} className="rounded-2xl bg-white px-3 py-2 text-sm text-slate-700">{item.title || item.content}</div>)}
          </div>
          <div className="mt-3 rounded-2xl border border-amber-200 bg-white/70 px-3 py-2 text-xs leading-5 text-amber-900">
            请到学习画像页确认采用新信息、保留原设置或稍后处理；未确认前系统不会覆盖原画像。
          </div>
        </div>
      ))}
      {pendingCandidates.map((item) => (
        <div key={item.id} className="rounded-[24px] border border-sky-200 bg-sky-50 p-4">
          <div className="text-sm font-semibold text-sky-950">待确认画像建议：{item.title || '候选记忆'}</div>
          <p className="mt-2 text-sm leading-6 text-slate-700">{item.content}</p>
          <p className="mt-2 text-xs text-sky-700">证据来源：{item.source || 'memory_agent'}；原因：{item.reason || '智能体提取到稳定偏好或学情变化'}</p>
        </div>
      ))}
    </section>
  );
}
