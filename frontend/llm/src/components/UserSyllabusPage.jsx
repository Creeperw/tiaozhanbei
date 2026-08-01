import React, { useEffect, useRef, useState } from 'react';
import {
  CheckCircle2,
  FileText,
  RefreshCw,
  Sparkles,
  UploadCloud,
  X,
} from 'lucide-react';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import { Button, EmptyState, InlineError, Skeleton, StatusBadge } from './ui';

const ACCEPT = '.pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.doc,.docx,.xls,.xlsx,.md,.markdown,.txt,.csv';
const ALLOWED = new Set(['pdf', 'png', 'jpg', 'jpeg', 'webp', 'bmp', 'tif', 'tiff', 'doc', 'docx', 'xls', 'xlsx', 'md', 'markdown', 'txt', 'csv']);
const extensionOf = (name) => String(name || '').split('.').pop()?.toLowerCase() || '';
const STATUS_LABELS = {
  pending: '等待解析',
  processing: '解析中',
  success: '已完成',
  failed: '解析失败',
};

const formatBytes = (bytes = 0) => {
  if (!bytes) return '0 KB';
  if (bytes < 1024 * 1024) return Math.max(1, Math.round(bytes / 1024)) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
};

const responseError = (response, payload, fallback) => {
  const detail = payload?.detail?.message || payload?.detail;
  if (response?.status === 404 && (!detail || detail === 'Not Found')) {
    return '考纲服务未加载，请刷新页面后重试';
  }
  return detail || fallback;
};

export default function UserSyllabusPage() {
  const fileInputRef = useRef(null);
  const [file, setFile] = useState(null);
  const [dragging, setDragging] = useState(false);
  const [title, setTitle] = useState('');
  const [subject, setSubject] = useState('');
  const [examType, setExamType] = useState('');
  const [items, setItems] = useState([]);
  const [selected, setSelected] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = async () => {
    setLoading(true);
    setError('');
    try {
      const response = await fetchWithAuth(API_BASE + '/v1/user-syllabi');
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(responseError(response, payload, '考纲列表加载失败'));
      setItems(Array.isArray(payload.items) ? payload.items : []);
    } catch (loadError) {
      setError(loadError instanceof TypeError ? '网络连接失败，请确认后端服务已启动' : (loadError.message || '考纲列表加载失败'));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const chooseFile = (nextFile) => {
    setNotice('');
    if (!nextFile) {
      setFile(null);
      return;
    }
    if (!ALLOWED.has(extensionOf(nextFile.name))) {
      setFile(null);
      setError('文件格式不支持，请上传 PDF、图片、Word、Excel、Markdown、TXT 或 CSV');
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }
    setError('');
    setFile(nextFile);
    if (!title.trim()) setTitle(nextFile.name.replace(/\.[^.]+$/, ''));
  };

  const clearFile = () => {
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  };

  const upload = async () => {
    if (!file) {
      setError('请选择考纲文件');
      return;
    }
    setUploading(true);
    setError('');
    setNotice('');
    const body = new FormData();
    body.append('file', file);
    body.append('title', title);
    body.append('subject', subject);
    body.append('exam_type', examType);
    try {
      const response = await fetchWithAuth(API_BASE + '/v1/user-syllabi', { method: 'POST', body });
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(responseError(response, payload, '考纲解析失败'));
      setNotice('考纲已解析并自动激活');
      clearFile();
      setTitle('');
      setSubject('');
      setExamType('');
      await load();
      setSelected(payload);
    } catch (uploadError) {
      setError(uploadError.message || '考纲解析失败');
    } finally {
      setUploading(false);
    }
  };

  const open = async (id) => {
    setError('');
    try {
      const response = await fetchWithAuth(API_BASE + '/v1/user-syllabi/' + encodeURIComponent(id));
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(responseError(response, payload, '考纲加载失败'));
      setSelected(payload);
    } catch (openError) {
      setError(openError.message || '考纲加载失败');
    }
  };

  const activate = async (id) => {
    setError('');
    try {
      const response = await fetchWithAuth(API_BASE + '/v1/user-syllabi/' + encodeURIComponent(id) + '/activate', { method: 'PUT' });
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(responseError(response, payload, '激活失败'));
      setNotice('已切换激活考纲');
      await load();
      setSelected(payload);
    } catch (activateError) {
      setError(activateError.message || '激活失败');
    }
  };

  const mappingsById = new Map(
    (selected?.mappings || []).map((row) => [row.requirement_id, row])
  );
  const matchStats = (() => {
    const sections = selected?.structured?.sections || [];
    let total = 0;
    let matched = 0;
    let weak = 0;
    let questions = 0;
    for (const section of sections) {
      for (const requirement of section.requirements || []) {
        total += 1;
        const mapping = mappingsById.get(requirement.requirement_id);
        if (mapping?.match_status === 'matched') matched += 1;
        if (mapping?.match_grade === 'weak') weak += 1;
        questions += mapping?.question_count || 0;
      }
    }
    return { total, matched, weak, questions };
  })();

  return (
    <section className="question-workspace__section user-syllabus-page" aria-label="上传考纲">
      <header className="user-syllabus-page__header">
        <div className="user-syllabus-page__heading">
          <span className="user-syllabus-page__eyebrow"><Sparkles aria-hidden="true" size={15} />个人考纲</span>
          <h3>上传并结构化考纲</h3>
          <p>上传文件后，系统会识别章节、考试要求和重点知识，并用于后续练题与讲解。</p>
        </div>
        <Button className="user-syllabus-page__refresh" variant="secondary" onClick={load} disabled={loading}>
          <RefreshCw aria-hidden="true" size={18} />刷新记录
        </Button>
      </header>

      <div className="user-syllabus-page__content">
        <div className="user-syllabus-page__upload-layout">
          <div className="user-syllabus-page__file-field">
            <span className="user-syllabus-page__field-label">考纲文件</span>
            <label
              className={'user-syllabus-page__dropzone' + (dragging ? ' is-dragging' : '') + (file ? ' has-file' : '')}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={(event) => { event.preventDefault(); setDragging(false); }}
              onDrop={(event) => {
                event.preventDefault();
                setDragging(false);
                chooseFile(event.dataTransfer.files?.[0] || null);
              }}
            >
              <input
                ref={fileInputRef}
                aria-label="考纲文件"
                type="file"
                accept={ACCEPT}
                onChange={(event) => chooseFile(event.target.files?.[0] || null)}
              />
              <span className="user-syllabus-page__dropzone-icon"><UploadCloud aria-hidden="true" size={28} /></span>
              <span className="user-syllabus-page__dropzone-copy">
                <strong>{file ? file.name : '拖拽文件到这里，或点击选择文件'}</strong>
                <small>{file ? extensionOf(file.name).toUpperCase() + ' · ' + formatBytes(file.size) : '支持 PDF、图片、Word、Excel、Markdown、TXT、CSV'}</small>
              </span>
              {file ? (
                <button
                  type="button"
                  className="user-syllabus-page__file-remove"
                  aria-label="移除已选文件"
                  onClick={(event) => { event.preventDefault(); clearFile(); }}
                >
                  <X aria-hidden="true" size={17} />
                </button>
              ) : <span className="user-syllabus-page__browse">选择文件</span>}
            </label>
          </div>

          <div className="user-syllabus-page__metadata">
            <label>
              <span>考纲名称</span>
              <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="例如：中药期末考试考纲" />
            </label>
            <label>
              <span>科目</span>
              <input value={subject} onChange={(event) => setSubject(event.target.value)} placeholder="例如：中药" />
            </label>
            <label>
              <span>考试类型</span>
              <input value={examType} onChange={(event) => setExamType(event.target.value)} placeholder="例如：期末考试" />
            </label>
          </div>
        </div>

        <div className="user-syllabus-page__action-row">
          <div>
            <strong>确认上传后将由多智能体自动完成结构化处理</strong>
            <span>匹配不到的公共知识点会保留为“未匹配”，不会阻断考纲使用。</span>
          </div>
          <Button className="user-syllabus-page__submit" variant="primary" aria-label="上传考纲" loading={uploading} disabled={!file} onClick={upload}>
            <UploadCloud aria-hidden="true" size={18} />上传并解析
          </Button>
        </div>

        {(error || notice) && (
          <div className="user-syllabus-page__messages">
            {error && <InlineError message={error} onRetry={(error.includes('服务未加载') || error.includes('网络连接失败')) ? load : undefined} />}
            {notice && <p role="status" className="user-syllabus-page__success"><CheckCircle2 aria-hidden="true" size={18} />{notice}</p>}
          </div>
        )}

        <section className="user-syllabus-page__records" aria-label="个人考纲记录">
          <div className="user-syllabus-page__records-heading">
            <div>
              <span>上传记录</span>
              <h4>我的考纲</h4>
            </div>
            {!loading && <small>{items.length} 份</small>}
          </div>

          {loading && <Skeleton label="正在加载考纲" lines={3} />}
          {!loading && items.length === 0 && (
            <EmptyState title="还没有个人考纲" description="上传一份考纲后，智能助教会按激活考纲约束练题和讲解。" />
          )}
          {!loading && items.length > 0 && (
            <ul className="user-syllabus-page__list">
              {items.map((item) => (
                <li key={item.syllabus_id}>
                  <span className="user-syllabus-page__record-icon"><FileText aria-hidden="true" size={20} /></span>
                  <div className="user-syllabus-page__record-copy">
                    <strong>{item.title}</strong>
                    <span>{item.subject || '未填写科目'} · {item.exam_type || '未填写考试类型'} · {STATUS_LABELS[item.processing_status] || item.processing_status}</span>
                  </div>
                  <div className="user-syllabus-page__record-actions">
                    {item.is_active ? <StatusBadge status="success">已激活</StatusBadge> : <Button variant="secondary" onClick={() => activate(item.syllabus_id)}>设为当前考纲</Button>}
                    <Button variant="ghost" onClick={() => open(item.syllabus_id)}>查看结构</Button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {selected?.structured && (
          <section className="user-syllabus-page__detail" aria-label="考纲结构">
            <div className="user-syllabus-page__detail-heading">
              <div className="user-syllabus-page__detail-title">
                <span>结构化结果</span>
                <h4>{selected.structured.title}</h4>
              </div>
              {matchStats.total > 0 && (
                <div className="user-syllabus-page__match-summary" role="status">
                  <span className="user-syllabus-page__match-chip is-matched">{matchStats.matched}/{matchStats.total} 条已匹配</span>
                  {matchStats.weak > 0 && <span className="user-syllabus-page__match-chip is-weak">{matchStats.weak} 条弱匹配</span>}
                  <span className="user-syllabus-page__match-chip">{matchStats.questions} 道相关题</span>
                </div>
              )}
            </div>
            <div className="user-syllabus-page__sections">
              {selected.structured.sections?.map((section, sectionIndex) => {
                const sectionMatched = (section.requirements || []).filter(
                  (requirement) => mappingsById.get(requirement.requirement_id)?.match_status === 'matched'
                ).length;
                return (
                  <details key={section.section_id} className="user-syllabus-page__section" open={sectionIndex < 2}>
                    <summary>
                      <span className="user-syllabus-page__chevron" aria-hidden="true">▸</span>
                      <strong>{section.title}</strong>
                      <small>{section.requirements?.length || 0} 条</small>
                      {section.requirements?.length > 0 && (
                        <span className={sectionMatched === section.requirements.length ? 'user-syllabus-page__section-match is-all' : 'user-syllabus-page__section-match'}>
                          {sectionMatched}/{section.requirements.length}
                        </span>
                      )}
                    </summary>
                    <ul className="user-syllabus-page__requirement-list">
                      {section.requirements?.map((requirement) => {
                        const mapping = mappingsById.get(requirement.requirement_id) || {};
                        const matched = mapping.match_status === 'matched';
                        const grade = mapping.match_grade || (matched ? 'medium' : 'unmatched');
                        const gradeLabel = { strong: '强匹配', medium: '已匹配', weak: '弱匹配', unmatched: '未匹配' }[grade] || '未匹配';
                        const gradeClass = { strong: 'is-strong', medium: 'is-matched', weak: 'is-weak', unmatched: '' }[grade] || '';
                        return (
                          <li key={requirement.requirement_id} className="user-syllabus-page__requirement">
                            <span className={'user-syllabus-page__req-dot' + (matched ? ' is-matched' : grade === 'weak' ? ' is-weak' : '')} aria-hidden="true" />
                            <div className="user-syllabus-page__req-copy">
                              <strong>{requirement.title}</strong>
                              <div className="user-syllabus-page__req-meta">
                                {requirement.mastery_level && requirement.mastery_level !== '未注明' && (
                                  <span className="user-syllabus-page__req-badge">{requirement.mastery_level}</span>
                                )}
                                <span className={'user-syllabus-page__req-badge' + (gradeClass ? ' ' + gradeClass : '')}>
                                  {gradeLabel}{matched && mapping.confidence ? ` ${Math.round(mapping.confidence * 100)}%` : ''}
                                </span>
                                {mapping.kp_name && <span className="user-syllabus-page__req-badge is-kp">{mapping.kp_name}</span>}
                                {mapping.question_count > 0 && (
                                  <span className="user-syllabus-page__req-badge is-question">{mapping.question_count} 道相关题</span>
                                )}
                              </div>
                              {(mapping.textbook_evidence?.length > 0 || mapping.question_evidence?.length > 0) && (
                                <details className="user-syllabus-page__evidence">
                                  <summary>查看匹配证据</summary>
                                  <div className="user-syllabus-page__evidence-body">
                                    {(mapping.textbook_evidence || []).map((hit, hitIndex) => (
                                      <div key={hitIndex} className="user-syllabus-page__evidence-item is-textbook">
                                        <strong>{hit.book}{hit.heading ? ' · ' + hit.heading : ''}</strong>
                                        <p>{hit.snippet}</p>
                                        <small>相似度 {Math.round(hit.score * 100)}%</small>
                                      </div>
                                    ))}
                                    {(mapping.question_evidence || []).map((hit, hitIndex) => (
                                      <div key={hitIndex} className="user-syllabus-page__evidence-item is-question">
                                        <strong>{hit.source}{hit.type ? ' · ' + hit.type : ''}</strong>
                                        <p>{hit.stem}</p>
                                        <small>相似度 {Math.round(hit.score * 100)}%</small>
                                      </div>
                                    ))}
                                  </div>
                                </details>
                              )}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  </details>
                );
              })}
            </div>
          </section>
        )}
      </div>
    </section>
  );
}
