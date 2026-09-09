import React, { useEffect, useState } from 'react';
import { loadTextbookImports } from '../workshop-textbook/textbookPdfApi';
import UploadProgress from './UploadProgress';

export default function TextbookUploadHistory({ revision = 0, busy = false, onOpen }) {
  const [items, setItems] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    const load = async () => {
      try {
        const data = await loadTextbookImports({ signal: controller.signal });
        if (controller.signal.aborted) return;
        const next = Array.isArray(data.items) ? data.items : [];
        setItems(next); setError('');
        if (next.some(item => item.status === 'running')) timer = window.setTimeout(load, 3000);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason.message || '教材任务记录加载失败');
      } finally { if (!controller.signal.aborted) setLoading(false); }
    };
    setLoading(true);
    load();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [revision, refresh]);
  return (
    <section className="resource-upload-history" aria-label="教材上传记录">
      <header><h2>教材上传记录</h2><button type="button" disabled={loading} onClick={() => setRefresh(value => value + 1)}>刷新记录</button></header>
      <p>仅显示当前用户的持久任务。中断记录不会自动重跑；较早的内存任务无法补回。</p>
      {loading && <p role="status">正在加载教材上传记录…</p>}
      {error && <p role="alert">{error}</p>}
      {!loading && !error && !items.length && <p>暂无教材上传记录</p>}
      <ul>{items.map(item => <li key={item.task_id}>
        <div><strong>{item.book?.title || item.task_id}</strong>
          <UploadProgress progress={item.progress} fallback={item.step_label || '状态待核实'} />
          <small>{item.created_at || ''}</small>
          {item.error?.message && <p>{item.error.message}</p>}
          {item.status === 'failed' && <p>{item.retry_allowed === true
            ? '本次失败允许重新提交，请在上方重新选择文件。'
            : '结果待核实，请先检查教材书架，勿重复上传。'}</p>}
        </div>
        {item.status === 'done' && item.book?.book_id && <button type="button" disabled={busy} onClick={() => onOpen?.(item.book)}>阅读教材</button>}
      </li>)}</ul>
    </section>
  );
}