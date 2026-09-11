import React, { useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { loadLegacySessionMessages } from '../legacyLearningClient';

export default function LegacyConversation({ session, onNewConversation }) {
  const [state, setState] = useState({ status: 'loading', items: [], error: '' });
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setState({ status: 'loading', items: [], error: '' });
    loadLegacySessionMessages(session.id, { signal: controller.signal }).then((result) => {
      if (!controller.signal.aborted) setState({ status: 'ready', items: result.items, error: '' });
    }).catch((error) => {
      if (!controller.signal.aborted) setState({ status: 'error', items: [], error: error.message });
    });
    return () => controller.abort();
  }, [session.id, retry]);
  return (
    <section aria-label="历史会话内容" className="flex min-h-0 flex-1 flex-col">
      <header className="border-b border-emerald-100 p-4">
        <h2 className="font-semibold text-slate-900">{session.title || '未命名历史会话'}</h2>
        <p className="mt-2 text-sm text-slate-500">历史会话 · 只读。新对话不会自动携带这里的上下文。</p>
        <button type="button" onClick={onNewConversation} className="mt-3 rounded-xl bg-emerald-700 px-4 py-2 text-white">另开新对话</button>
      </header>
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
        {state.status === 'loading' && <p role="status">正在读取历史消息…</p>}
        {state.status === 'error' && <div role="alert">历史消息读取失败：{state.error}<button type="button" className="ml-3 underline" onClick={() => setRetry(value => value + 1)}>重试历史消息</button></div>}
        {state.status === 'ready' && state.items.length === 0 && <p>此历史会话暂无消息。</p>}
        {state.status === 'ready' && state.items.map(message => (
          <article key={message.id} aria-label="历史消息" className="rounded-2xl border border-slate-100 bg-white p-4">
            <div className="mb-2 text-xs text-slate-500">{message.role === 'user' ? '我' : message.role === 'assistant' ? '智能助教' : message.role} · {message.created_at || '时间未记录'}</div>
            <div className="prose max-w-none break-words"><ReactMarkdown>{String(message.content ?? '')}</ReactMarkdown></div>
          </article>
        ))}
      </div>
    </section>
  );
}