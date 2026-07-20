import React, { useEffect, useState } from 'react';
import { Download, RefreshCw, Save, Trash2, ShieldCheck } from 'lucide-react';
import { API_BASE, fetchWithAuth } from '../utils/api';

const emptyEdit = { feedback_type: '', rating: '', reason: '', user_feedback: '', question: '', answer: '', metadata: {} };

const AdminFeedbackPage = () => {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [editing, setEditing] = useState(emptyEdit);

  const fetchRows = async () => {
    setLoading(true);
    try {
      const res = await fetchWithAuth(`${API_BASE}/feedback/admin`);
      if (res.ok) setRows(await res.json());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchRows(); }, []);

  const beginEdit = (row) => {
    setEditingId(row.id);
    setEditing({
      feedback_type: row.feedback_type || '',
      rating: row.rating || '',
      reason: row.reason || '',
      user_feedback: row.user_feedback || '',
      question: row.question || '',
      answer: row.answer || '',
      metadata: row.metadata || {},
    });
  };

  const saveEdit = async () => {
    if (!editingId) return;
    const res = await fetchWithAuth(`${API_BASE}/feedback/admin/items/${editingId}`, {
      method: 'PATCH',
      body: JSON.stringify(editing),
    });
    if (res.ok) {
      const updated = await res.json();
      setRows(prev => prev.map(row => row.id === editingId ? updated : row));
      setEditingId(null);
    }
  };

  const deleteRow = async (id) => {
    if (!window.confirm('确定删除这条反馈数据吗？')) return;
    const res = await fetchWithAuth(`${API_BASE}/feedback/admin/items/${id}`, { method: 'DELETE' });
    if (res.ok) setRows(prev => prev.filter(row => row.id !== id));
  };

  const exportRows = async () => {
    const res = await fetchWithAuth(`${API_BASE}/feedback/admin/export`);
    if (!res.ok) return;
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `feedback_export_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="admin-feedback-page text-slate-800">
      <div className="mx-auto max-w-7xl">
        <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-3">
            <div>
              <div className="mb-1 flex items-center gap-2 font-semibold text-emerald-700"><ShieldCheck size={18}/> 管理员控制台</div>
              <h1 className="text-2xl font-bold text-slate-900">反馈数据管理</h1>
              <p className="mt-1 text-sm text-slate-500">查看、修改、删除所有用户反馈，并支持一键导出 CSV。</p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:justify-end">
            <button onClick={fetchRows} className="inline-flex items-center gap-2 rounded-xl border border-emerald-200 bg-white px-4 py-2 text-sm text-slate-800 transition-colors hover:bg-emerald-50 hover:text-emerald-900"><RefreshCw size={16} className={loading ? 'animate-spin' : ''}/>刷新</button>
            <button onClick={exportRows} className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-sm text-white hover:bg-emerald-700 transition-colors"><Download size={16}/>导出全部</button>
          </div>
        </div>

        <div className="rounded-3xl border border-emerald-100 bg-white/90 shadow-xl shadow-emerald-100/30 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-emerald-50/70 text-emerald-950">
                <tr>
                  <th className="px-4 py-3 text-left font-semibold">ID</th>
                  <th className="px-4 py-3 text-left font-semibold">用户</th>
                  <th className="px-4 py-3 text-left font-semibold">类型</th>
                  <th className="px-4 py-3 text-left font-semibold">原因/反馈</th>
                  <th className="px-4 py-3 text-left font-semibold">问题</th>
                  <th className="px-4 py-3 text-left font-semibold">时间</th>
                  <th className="px-4 py-3 text-right font-semibold">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {rows.map(row => (
                  <tr key={row.id} className="align-top hover:bg-emerald-50/30">
                    <td className="px-4 py-3 font-mono text-xs text-slate-400">{row.id}</td>
                    <td className="px-4 py-3 text-slate-600">{row.user_id}</td>
                    <td className="px-4 py-3"><span className="rounded-full bg-emerald-50 px-2 py-1 text-xs font-semibold text-emerald-700 border border-emerald-100">{row.feedback_type || '-'}</span></td>
                    <td className="px-4 py-3 max-w-xs text-slate-600 whitespace-pre-wrap">{row.reason || row.user_feedback || '-'}</td>
                    <td className="px-4 py-3 max-w-sm text-slate-500 whitespace-pre-wrap line-clamp-4">{row.question || '-'}</td>
                    <td className="px-4 py-3 text-xs text-slate-400">{row.created_at || '-'}</td>
                    <td className="px-4 py-3 text-right">
                      <div className="inline-flex gap-1">
                        <button onClick={() => beginEdit(row)} className="rounded-lg px-2 py-1 text-xs text-emerald-700 hover:bg-emerald-50">编辑</button>
                        <button onClick={() => deleteRow(row.id)} className="rounded-lg p-1.5 text-rose-500 hover:bg-rose-50"><Trash2 size={15}/></button>
                      </div>
                    </td>
                  </tr>
                ))}
                {!rows.length && <tr><td colSpan="7" className="px-4 py-16 text-center text-slate-400">暂无反馈数据</td></tr>}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {editingId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/25 backdrop-blur-sm p-4">
          <div className="w-full max-w-3xl rounded-3xl bg-white shadow-2xl border border-emerald-100 p-5">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-bold text-lg text-slate-900">编辑反馈 #{editingId}</h2>
              <button onClick={() => setEditingId(null)} className="text-slate-400 hover:text-slate-700">×</button>
            </div>
            <div className="grid gap-3 sm:grid-cols-2">
              <input className="rounded-xl border border-slate-200 px-3 py-2 outline-none focus:ring-2 focus:ring-emerald-100" value={editing.feedback_type} onChange={e => setEditing({ ...editing, feedback_type: e.target.value })} placeholder="反馈类型" />
              <input className="rounded-xl border border-slate-200 px-3 py-2 outline-none focus:ring-2 focus:ring-emerald-100" value={editing.rating} onChange={e => setEditing({ ...editing, rating: e.target.value })} placeholder="评分/状态" />
              <textarea className="min-h-20 resize-none rounded-xl border border-slate-200 px-3 py-2 outline-none focus:ring-2 focus:ring-emerald-100 sm:col-span-2" value={editing.reason} onChange={e => setEditing({ ...editing, reason: e.target.value })} placeholder="原因" />
              <textarea className="min-h-20 resize-none rounded-xl border border-slate-200 px-3 py-2 outline-none focus:ring-2 focus:ring-emerald-100 sm:col-span-2" value={editing.user_feedback} onChange={e => setEditing({ ...editing, user_feedback: e.target.value })} placeholder="用户补充反馈" />
              <textarea className="min-h-24 resize-none rounded-xl border border-slate-200 px-3 py-2 outline-none focus:ring-2 focus:ring-emerald-100 sm:col-span-2" value={editing.question} onChange={e => setEditing({ ...editing, question: e.target.value })} placeholder="问题" />
              <textarea className="min-h-32 resize-none rounded-xl border border-slate-200 px-3 py-2 outline-none focus:ring-2 focus:ring-emerald-100 sm:col-span-2" value={editing.answer} onChange={e => setEditing({ ...editing, answer: e.target.value })} placeholder="回答" />
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button onClick={() => setEditingId(null)} className="rounded-xl border border-slate-200 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">取消</button>
              <button onClick={saveEdit} className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2 text-sm text-white hover:bg-emerald-700"><Save size={16}/>保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default AdminFeedbackPage;
