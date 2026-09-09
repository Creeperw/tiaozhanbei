import React, { useEffect, useRef, useState } from 'react';
import { uploadKnowledgeFiles } from './knowledgeUpload';
import UploadProgress from './UploadProgress';

export default function KnowledgeUploadForm({ onBusyChange, onNavigate }) {
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState('');
  const inFlight = useRef(false);
  useEffect(() => { onBusyChange?.(busy); }, [busy, onBusyChange]);
  const submit = async (event) => {
    event.preventDefault();
    if (inFlight.current || !files.length) return;
    inFlight.current = true;
    setBusy(true); setError('');
    setProgress({ state: 'running', label: '正在上传个人知识资料' });
    try {
      const result = await uploadKnowledgeFiles(files, {
        onStage: label => setProgress({ state: 'running', label }),
      });
      setProgress({ state: result.legacyPending ? 'running' : 'succeeded', label: result.legacyPending
        ? '文件已接收，旧格式资料的索引状态请到个人知识库查看。'
        : `资料导入完成，已生成 ${result.chapterCount} 个章节。` });
    } catch (reason) {
      setError(`${reason.message || '上传失败'} 多文件上传可能已有部分完成，请先检查个人知识库，勿直接重复上传。`);
      setProgress({ state: 'failed' });
    } finally { inFlight.current = false; setBusy(false); }
  };
  return (
    <form className="resource-upload-form" onSubmit={submit} aria-label="个人知识资料上传">
      <h2>上传个人知识资料</h2>
      <p>PDF、Markdown、TXT 进入正式个人知识库；JSON、JSONL 保留原导入方式。仅当前用户可检索，不修改公共库。</p>
      <label>选择知识资料<input type="file" multiple accept=".pdf,.md,.txt,.json,.jsonl" disabled={busy}
        onChange={event => { setFiles(Array.from(event.target.files || [])); setError(''); setProgress(null); }} /></label>
      <p>{files.map(file => file.name).join('、')}</p>
      <button type="submit" disabled={busy || !files.length}>{busy ? '资料处理中…' : '开始导入个人知识库'}</button>
      {progress && <UploadProgress progress={progress} />}
      {error && <p role="alert">{error}</p>}
      {progress && !busy && <button type="button" onClick={() => onNavigate?.({ page: 'knowledge', params: { view: 'personal' } })}>查看个人知识库</button>}
    </form>
  );
}