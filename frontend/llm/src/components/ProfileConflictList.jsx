import React, { useState } from 'react';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import { buildProfileConflictSections } from '../profileConflictList.js';

async function requestApi(path, options = {}) {
  const res = await fetchWithAuth(`${API_BASE}${path}`, options);
  if (!res.ok) {
    const payload = await readJsonResponse(res, {});
    throw new Error(payload.detail || '操作失败，请稍后重试');
  }
  return readJsonResponse(res, {});
}

export default function ProfileConflictList({ memories = [], candidates = [], onRefresh }) {
  const { conflicts, pendingCandidates, hasActionableItems } = buildProfileConflictSections({ memories, candidates });
  const [busyId, setBusyId] = useState(null);
  const [editingItem, setEditingItem] = useState(null);
  const [editText, setEditText] = useState('');
  const [editTitle, setEditTitle] = useState('');
  const [message, setMessage] = useState('');

  const notify = (text) => {
    setMessage(text);
    window.setTimeout(() => setMessage(''), 4000);
  };

  const runAction = async (id, action, extra = {}) => {
    setBusyId(id);
    try {
      if (action === 'promote') {
        await requestApi(`/personalization/candidates/${id}/promote`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ category: 'long_term', importance: 'normal', ...extra }),
        });
        notify('已采纳该记忆，后台智能体正在完成更新。');
      } else if (action === 'ignore') {
        await requestApi(`/personalization/candidates/${id}/ignore`, { method: 'PATCH' });
        notify('已忽略该建议，原画像保持不变。');
      } else if (action === 'update') {
        await requestApi(`/personalization/candidates/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content: editText, title: editTitle, ...extra }),
        });
        setEditingItem(null);
        notify('已保存修改，内容进入后台处理。');
      }
      onRefresh?.();
    } catch (error) {
      notify(error.message || '操作失败，请稍后重试');
    } finally {
      setBusyId(null);
    }
  };

  if (!hasActionableItems) {
    return <div className="rounded-2xl border border-dashed border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">当前没有待确认画像冲突。</div>;
  }

  return (
    <section className="space-y-4">
      <div>
        <h2 className="text-xl font-semibold text-slate-950">冲突清单</h2>
        <p className="mt-2 text-sm leading-6 text-slate-600">系统只列出建议，用户未确认前不会覆盖原画像；确认或修改后由多智能体在后台完成更新。</p>
        {message && <div className="mt-3 rounded-2xl border border-emerald-200 bg-emerald-50 px-4 py-2 text-sm text-emerald-800">{message}</div>}
      </div>
      {conflicts.map(({ key, items }) => (
        <div key={key} className="rounded-[24px] border border-amber-200 bg-amber-50 p-4">
          <div className="text-sm font-semibold text-amber-950">事实/偏好冲突：{key}</div>
          <div className="mt-3 space-y-2">
            {items.map((item) => <div key={item.id} className="rounded-2xl bg-white px-3 py-2 text-sm text-slate-700">{item.title || item.content}</div>)}
          </div>
          <div className="mt-3 rounded-2xl border border-amber-200 bg-white/70 px-3 py-2 text-xs leading-5 text-amber-900">
            请确认采用新信息、保留原设置或稍后处理；未确认前系统不会覆盖原画像。点击下方"确认采纳"后，多智能体会在后台完成更新。
          </div>
        </div>
      ))}
      {pendingCandidates.map((item) => (
        <div key={item.id} className="rounded-[24px] border border-sky-200 bg-sky-50 p-4">
          <div className="text-sm font-semibold text-sky-950">待确认画像建议：{item.title || '候选记忆'}</div>
          <p className="mt-2 text-sm leading-6 text-slate-700">{item.content}</p>
          <p className="mt-2 text-xs text-sky-700">证据来源：{item.source || 'memory_agent'}；原因：{item.reason || '智能体提取到稳定偏好或学情变化'}</p>
          {editingItem === item.id ? (
            <div className="mt-3 space-y-2 rounded-2xl bg-white p-3">
              <input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                placeholder="标题（可选）"
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
              />
              <textarea
                value={editText}
                onChange={(e) => setEditText(e.target.value)}
                placeholder="记忆内容"
                rows={3}
                className="w-full rounded-xl border border-slate-200 px-3 py-2 text-sm"
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  disabled={busyId === item.id}
                  onClick={() => runAction(item.id, 'update', {})}
                  className="rounded-xl bg-sky-600 px-3 py-2 text-sm text-white disabled:opacity-60"
                >
                  保存
                </button>
                <button
                  type="button"
                  onClick={() => setEditingItem(null)}
                  className="rounded-xl bg-slate-100 px-3 py-2 text-sm text-slate-600"
                >
                  取消
                </button>
              </div>
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busyId === item.id}
                onClick={() => runAction(item.id, 'promote', {})}
                className="rounded-xl bg-emerald-600 px-3 py-2 text-sm text-white disabled:opacity-60"
              >
                确认采纳
              </button>
              <button
                type="button"
                onClick={() => {
                  setEditingItem(item.id);
                  setEditText(item.content || '');
                  setEditTitle(item.title || '');
                }}
                className="rounded-xl bg-white px-3 py-2 text-sm text-slate-700 ring-1 ring-slate-200"
              >
                修改内容
              </button>
              <button
                type="button"
                disabled={busyId === item.id}
                onClick={() => runAction(item.id, 'ignore', {})}
                className="rounded-xl bg-white px-3 py-2 text-sm text-slate-500 ring-1 ring-slate-200 disabled:opacity-60"
              >
                忽略
              </button>
            </div>
          )}
        </div>
      ))}
    </section>
  );
}
