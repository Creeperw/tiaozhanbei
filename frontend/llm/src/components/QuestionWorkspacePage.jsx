import React, { useEffect, useMemo, useState } from 'react';
import {
  Archive,
  CheckCircle2,
  ChevronDown,
  FileText,
  RefreshCw,
  ShieldCheck,
  UploadCloud,
  XCircle,
} from 'lucide-react';
import { Button, EmptyState, InlineError, Skeleton, StatusBadge } from './ui';
import { API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import UploadProgress from './resource-upload/UploadProgress';

const ALLOWED_EXTENSIONS = new Set(['pdf', 'png', 'jpg', 'jpeg', 'webp', 'bmp', 'tif', 'tiff', 'md', 'txt']);

function extensionOf(filename) {
  return String(filename || '').split('.').at(-1)?.toLowerCase() || '';
}

function statusLabel(status) {
  return {
    processing: '处理中',
    preview_ready: '待确认',
    needs_human_review: '需人工修订',
    failed: '失败',
    completed: '已完成',
    active: '已激活',
    published: '已发布公共题库',
    rejected: '已拒绝',
    inactive: '已停用',
  }[status] || status;
}

export default function QuestionWorkspacePage({ isAdmin = false, reviewerUsername = '', onUploadRequested, onBusyChange }) {
  const [activeQuestions, setActiveQuestions] = useState([]);
  const [importJobs, setImportJobs] = useState([]);
  const [previewItems, setPreviewItems] = useState([]);
  const [activeImportJobId, setActiveImportJobId] = useState('');
  const [selectedFile, setSelectedFile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [busyQuestionId, setBusyQuestionId] = useState('');
  const [draftAnswers, setDraftAnswers] = useState({});
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [adminReviews, setAdminReviews] = useState([]);
  const [reviewFilter, setReviewFilter] = useState('preview_ready');
  const [selectedReviewId, setSelectedReviewId] = useState('');
  useEffect(() => { onBusyChange?.(uploading); }, [uploading, onBusyChange]);

  const activeIds = useMemo(
    () => new Set(activeQuestions.map((item) => item.question_id)),
    [activeQuestions],
  );

  const loadWorkspace = async () => {
    setLoading(true);
    setError('');
    try {
      if (isAdmin) {
        const response = await fetchWithAuth(`${API_BASE}/question-workspace/admin/reviews?status=${reviewFilter}`);
        const payload = await readJsonResponse(response, {});
        if (!response.ok) throw new Error(payload.detail || '审核队列加载失败');
        setAdminReviews(Array.isArray(payload.items) ? payload.items : []);
        return;
      }
      const [questionsResponse, importsResponse] = await Promise.all([
        fetchWithAuth(`${API_BASE}/question-workspace/questions`),
        fetchWithAuth(`${API_BASE}/question-workspace/imports`),
      ]);
      const [questionsPayload, importsPayload] = await Promise.all([
        readJsonResponse(questionsResponse, {}),
        readJsonResponse(importsResponse, {}),
      ]);
      if (!questionsResponse.ok) throw new Error(questionsPayload.detail || '个人题目加载失败');
      if (!importsResponse.ok) throw new Error(importsPayload.detail || '导入历史加载失败');
      setActiveQuestions(Array.isArray(questionsPayload.items) ? questionsPayload.items : []);
      setImportJobs(Array.isArray(importsPayload.items) ? importsPayload.items : []);
    } catch (loadError) {
      setError(loadError.message || '个人题目加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadWorkspace();
  }, [isAdmin, reviewFilter]);

  useEffect(() => {
    if (!adminReviews.some((item) => item.question_id === selectedReviewId)) {
      setSelectedReviewId(adminReviews[0]?.question_id || '');
    }
  }, [adminReviews, selectedReviewId]);

  const reviewAdminQuestion = async (questionId, decision) => {
    setBusyQuestionId(questionId);
    setError('');
    try {
      const response = await fetchWithAuth(
        `${API_BASE}/question-workspace/admin/reviews/${encodeURIComponent(questionId)}`,
        {
          method: 'POST',
          body: JSON.stringify({
            decision,
            review_note: decision === 'approve' ? '管理员审核通过并发布公共题库' : '管理员审核未通过',
          }),
        },
      );
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '审核操作失败');
      setAdminReviews((items) => items.filter((item) => item.question_id !== questionId));
      setNotice(decision === 'approve' ? '题目已通过审核并发布到公共题库。' : '题目已驳回。');
    } catch (reviewError) {
      setError(reviewError.message || '审核操作失败');
    } finally {
      setBusyQuestionId('');
    }
  };

  const uploadQuestions = async () => {
    if (!selectedFile) {
      setError('请先选择题目文件');
      return;
    }
    if (!ALLOWED_EXTENSIONS.has(extensionOf(selectedFile.name))) {
      setError('仅支持 PDF、图片、Markdown 和 TXT 文件');
      return;
    }
    setUploading(true);
    setError('');
    setNotice('');
    setPreviewItems([]);
    setActiveImportJobId('');
    const body = new FormData();
    body.append('file', selectedFile);
    try {
      const response = await fetchWithAuth(`${API_BASE}/question-workspace/imports`, {
        method: 'POST',
        body,
      });
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '题目解析失败');
      setPreviewItems(Array.isArray(payload.items) ? payload.items : []);
      setActiveImportJobId(payload.job_id || '');
      setImportJobs((jobs) => [{
        job_id: payload.job_id,
        status: payload.status,
        item_count: payload.item_count,
        original_filename: selectedFile.name,
        error_message: '',
      }, ...jobs.filter((job) => job.job_id !== payload.job_id)]);
      setDraftAnswers(Object.fromEntries(
        (Array.isArray(payload.items) ? payload.items : []).map((item) => [item.question_id, item.answer || '']),
      ));
      setNotice(payload.status === 'needs_human_review'
        ? '解析完成，部分题目需要补充答案后再确认。'
        : '解析完成，请逐题核对后确认导入。');
    } catch (uploadError) {
      setError(uploadError.message || '题目解析失败');
      try {
        const historyResponse = await fetchWithAuth(`${API_BASE}/question-workspace/imports`);
        const history = await readJsonResponse(historyResponse, {});
        if (historyResponse.ok && Array.isArray(history.items)) setImportJobs(history.items);
      } catch {
        // Keep the original import error if refreshing history also fails.
      }
    } finally {
      setUploading(false);
    }
  };

  const reviseQuestion = async (questionId) => {
    const answer = String(draftAnswers[questionId] || '').trim();
    if (!answer) return;
    setBusyQuestionId(questionId);
    setError('');
    try {
      const response = await fetchWithAuth(
        `${API_BASE}/question-workspace/items/${encodeURIComponent(questionId)}`,
        {
          method: 'PATCH',
          body: JSON.stringify({ answer }),
        },
      );
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '题目修订失败');
      setPreviewItems((items) => items.map((item) => (
        item.question_id === questionId
          ? { ...item, answer, status: payload.status, review_reason: '' }
          : item
      )));
      setNotice('修订已保存，请再次核对后确认导入。');
    } catch (reviseError) {
      setError(reviseError.message || '题目修订失败');
    } finally {
      setBusyQuestionId('');
    }
  };

  const restoreImport = async (jobId) => {
    setBusyQuestionId(jobId);
    setError('');
    try {
      const response = await fetchWithAuth(
        `${API_BASE}/question-workspace/imports/${encodeURIComponent(jobId)}/items`,
      );
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '导入任务恢复失败');
      const items = Array.isArray(payload.items) ? payload.items : [];
      setPreviewItems(items);
      setActiveImportJobId(jobId);
      setDraftAnswers(Object.fromEntries(
        items.map((item) => [item.question_id, item.answer || '']),
      ));
    } catch (restoreError) {
      setError(restoreError.message || '导入任务恢复失败');
    } finally {
      setBusyQuestionId('');
    }
  };

  const rejectQuestion = async (questionId) => {
    setBusyQuestionId(questionId);
    setError('');
    try {
      const response = await fetchWithAuth(
        `${API_BASE}/question-workspace/items/${encodeURIComponent(questionId)}/reject`,
        { method: 'POST' },
      );
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '题目拒绝失败');
      setPreviewItems((items) => items.map((item) => (
        item.question_id === questionId ? { ...item, status: 'rejected' } : item
      )));
    } catch (rejectError) {
      setError(rejectError.message || '题目拒绝失败');
    } finally {
      setBusyQuestionId('');
    }
  };

  const deactivateQuestion = async (questionId) => {
    setBusyQuestionId(questionId);
    setError('');
    try {
      const response = await fetchWithAuth(
        `${API_BASE}/question-workspace/questions/${encodeURIComponent(questionId)}/deactivate`,
        { method: 'POST' },
      );
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '题目停用失败');
      setActiveQuestions((items) => items.filter((item) => item.question_id !== questionId));
      setPreviewItems((items) => items.map((item) => (
        item.question_id === questionId ? { ...item, status: 'inactive' } : item
      )));
    } catch (deactivateError) {
      setError(deactivateError.message || '题目停用失败');
    } finally {
      setBusyQuestionId('');
    }
  };

  const confirmQuestion = async (questionId) => {
    setBusyQuestionId(questionId);
    setError('');
    try {
      const response = await fetchWithAuth(
        `${API_BASE}/question-workspace/items/${encodeURIComponent(questionId)}/confirm`,
        { method: 'POST' },
      );
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '题目确认失败');
      setPreviewItems((items) => items.map((item) => (
        item.question_id === questionId ? { ...item, status: 'active' } : item
      )));
      setActiveQuestions((items) => {
        const active = { ...previewItems.find((item) => item.question_id === questionId), status: 'active' };
        return [active, ...items.filter((item) => item.question_id !== questionId)];
      });
      setNotice(payload.vector_index?.ok
        ? '题目已激活并同步到个人题目索引。'
        : '题目已激活；个人索引将在服务可用后重建。');
    } catch (confirmError) {
      setError(confirmError.message || '题目确认失败');
    } finally {
      setBusyQuestionId('');
    }
  };

  const confirmAllQuestions = async () => {
    if (!activeImportJobId) return;
    setBusyQuestionId(activeImportJobId);
    setError('');
    try {
      const response = await fetchWithAuth(
        `${API_BASE}/question-workspace/imports/${encodeURIComponent(activeImportJobId)}/confirm`,
        { method: 'POST' },
      );
      const payload = await readJsonResponse(response, {});
      if (!response.ok) throw new Error(payload.detail || '题目批量确认失败');
      const confirmedItems = Array.isArray(payload.items) ? payload.items : [];
      const confirmedIds = new Set(confirmedItems.map((item) => item.question_id));
      setPreviewItems((items) => items.map((item) => (
        confirmedIds.has(item.question_id) ? { ...item, status: 'active' } : item
      )));
      setActiveQuestions((items) => [
        ...confirmedItems,
        ...items.filter((item) => !confirmedIds.has(item.question_id)),
      ]);
      setNotice(payload.vector_index?.ok
        ? `已确认导入 ${payload.confirmed_count || confirmedItems.length} 题，并同步到个人题目索引。`
        : `已确认导入 ${payload.confirmed_count || confirmedItems.length} 题；个人索引将在服务可用后重建。`);
    } catch (confirmError) {
      setError(confirmError.message || '题目批量确认失败');
    } finally {
      setBusyQuestionId('');
    }
  };

  if (isAdmin) {
    const selectedReview = adminReviews.find((item) => item.question_id === selectedReviewId) || adminReviews[0];

    return (
      <section className="question-workspace question-review-console" aria-labelledby="question-review-title">
        <div className="question-workspace__intro">
          <div>
            <span className="app-shell__section-label">内容治理中心</span>
            <h2 id="question-review-title">题目审核台</h2>
            <p>审核用户提交的题目，确认内容、选项、答案和知识点后发布到公共题库。</p>
          </div>
          <div className="question-workspace__guardrail"><ShieldCheck aria-hidden="true" size={19} /><span>当前审核账号：{reviewerUsername || '当前管理员'}</span></div>
        </div>
        <div className="question-review-console__toolbar">
          <div className="question-review-console__queue-summary"><strong>审核队列</strong><span>{adminReviews.length} 道题目</span></div>
          <label className="question-review-console__filter">
            <span>筛选状态</span>
            <span className="question-review-console__filter-control">
              <select aria-label="筛选审核状态" value={reviewFilter} onChange={(event) => setReviewFilter(event.target.value)}>
                <option value="preview_ready">待审核</option>
                <option value="needs_human_review">需补充资料</option>
                <option value="published">已发布</option>
                <option value="rejected">已驳回</option>
              </select>
              <ChevronDown aria-hidden="true" size={16} />
            </span>
          </label>
          <Button variant="ghost" onClick={loadWorkspace} disabled={loading}><RefreshCw aria-hidden="true" size={16} />刷新队列</Button>
        </div>
        {error && <InlineError message={error} />}
        {notice && <p className="question-workspace__notice" role="status">{notice}</p>}
        {!loading && adminReviews.length === 0 && <EmptyState title="当前没有待审核题目" description="用户提交的新题目会出现在这里。" />}
        {adminReviews.length > 0 && (
          <div className="question-review-console__workspace">
            <aside className="question-review-console__queue" aria-label="题目列表">
              <div className="question-review-console__queue-heading"><span>题目列表</span><strong>{adminReviews.length}</strong></div>
              <div className="question-review-console__list">
                {adminReviews.map((item, index) => (
                  <button
                    className={`question-review-card ${item.question_id === selectedReview?.question_id ? 'is-selected' : ''}`}
                    key={item.question_id}
                    type="button"
                    aria-label={`查看题目 ${index + 1}`}
                    aria-pressed={item.question_id === selectedReview?.question_id}
                    onClick={() => setSelectedReviewId(item.question_id)}
                  >
                    <span className="question-review-card__index">{String(index + 1).padStart(2, '0')}</span>
                    <span className="question-review-card__summary">
                      <span className="question-review-card__status"><StatusBadge status={item.status === 'published' ? 'success' : item.status === 'rejected' ? 'danger' : 'info'}>{statusLabel(item.status)}</StatusBadge><small>{item.question_type}</small></span>
                      <strong>{item.stem}</strong>
                      <span className="question-review-card__source">{item.owner_username} · {item.original_filename}</span>
                    </span>
                  </button>
                ))}
              </div>
            </aside>
            {selectedReview && (
              <article className="question-review-console__detail" aria-label="题目审核详情">
                <header className="question-review-console__detail-header">
                  <div><span className="app-shell__section-label">审核详情</span><h3>题目审核</h3></div>
                  <StatusBadge status={selectedReview.status === 'published' ? 'success' : selectedReview.status === 'rejected' ? 'danger' : 'info'}>{statusLabel(selectedReview.status)}</StatusBadge>
                </header>
                <div className="question-review-card__meta"><span>提交人：{selectedReview.owner_username}</span><span>来源：{selectedReview.original_filename}</span><span>题目 ID：{selectedReview.question_id}</span></div>
                <h4>{selectedReview.stem}</h4>
                {selectedReview.options?.length > 0 && <ul className="question-workspace__options" aria-label="题目选项">{selectedReview.options.map((option, optionIndex) => <li key={`${selectedReview.question_id}-review-option-${optionIndex}`}>{option}</li>)}</ul>}
                <div className="question-review-card__answer"><span>参考答案</span><strong>{selectedReview.answer || '未提供'}</strong></div>
                {selectedReview.explanation && <p className="question-review-console__explanation"><span>解析：</span>{selectedReview.explanation}</p>}
                {selectedReview.kp_ids?.length > 0 && <div className="question-review-console__knowledge"><span>关联知识点</span>{selectedReview.kp_ids.map((kp) => <em key={kp}>{kp}</em>)}</div>}
                <footer>
                  <div className="question-review-console__review-meta"><span>审核人：{selectedReview.reviewer_username || '尚未审核'}</span>{selectedReview.reviewed_at && <span>审核时间：{selectedReview.reviewed_at}</span>}{selectedReview.review_note && <span>审核意见：{selectedReview.review_note}</span>}</div>
                  {(selectedReview.status === 'preview_ready' || selectedReview.status === 'needs_human_review') && <div className="question-review-console__actions"><Button variant="ghost" onClick={() => reviewAdminQuestion(selectedReview.question_id, 'reject')} disabled={busyQuestionId === selectedReview.question_id}>驳回</Button><Button variant="secondary" onClick={() => reviewAdminQuestion(selectedReview.question_id, 'approve')} loading={busyQuestionId === selectedReview.question_id}>通过并发布</Button></div>}
                </footer>
              </article>
            )}
          </div>
        )}
      </section>
    );
  }

  return (
    <section className="question-workspace" aria-labelledby="question-workspace-title">
      <div className="question-workspace__intro">
        <div>
          <span className="app-shell__section-label">个人题目工作区</span>
          <h2 id="question-workspace-title">从学习资料中沉淀自己的题库</h2>
          <p>上传 PDF、图片、Markdown 或 TXT；PDF 与图片先由 MinerU 解析，只有你最终确认的题目才会进入个人练习范围。</p>
        </div>
        <div className="question-workspace__guardrail">
          <ShieldCheck aria-hidden="true" size={19} />
          <span><strong>仅本人可见</strong>不会进入公共题库或公共向量索引</span>
        </div>
      </div>

      {onUploadRequested ? <Button onClick={onUploadRequested}><UploadCloud size={17} />上传个人题库</Button> : <div className="question-workspace__upload-card">
        <div className="question-workspace__dropzone">
          <UploadCloud aria-hidden="true" size={28} />
          <label htmlFor="question-workspace-file">选择题目文件</label>
          <p>单文件不超过 10 MiB，支持常见 PDF、图片、Markdown 与 TXT 文件</p>
          <input
            id="question-workspace-file"
            type="file"
            accept=".pdf,.png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.md,.txt,application/pdf,image/png,image/jpeg,image/webp,image/bmp,image/tiff,text/markdown,text/plain"
            aria-label="选择题目文件"
            onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
          />
          {selectedFile && <span className="question-workspace__filename">{selectedFile.name}</span>}
        </div>
        <Button className="question-workspace__parse-button" onClick={uploadQuestions} loading={uploading} disabled={uploading}>
          <FileText aria-hidden="true" size={17} />
          解析并预览
        </Button>
      </div>}

      {error && <InlineError message={error} />}
      {notice && <p className="question-workspace__notice" role="status">{notice}</p>}

      <section className="question-workspace__section" aria-label="导入历史">
        <header>
          <div><span>持久任务</span><h3>导入历史</h3></div>
          <StatusBadge status="info">{importJobs.length} 次</StatusBadge>
        </header>
        {loading && <Skeleton label="正在加载导入历史" lines={2} />}
        {!loading && importJobs.length === 0 && (
          <EmptyState title="暂无导入记录" description="成功或失败的导入任务都会保存在这里。" />
        )}
        {!loading && importJobs.length > 0 && (
          <ul className="question-workspace__active-list question-workspace__import-list">
            {importJobs.map((job) => (
              <li key={job.job_id}>
                <Archive aria-hidden="true" size={18} />
                <div>
                  <strong>{job.original_filename}</strong>
                  {job.progress ? <span><UploadProgress progress={job.progress} /> · {job.item_count || 0} 题</span>
                    : <span>{statusLabel(job.status)} · {job.item_count || 0} 题</span>}
                  {job.error_message && <small>{job.error_message}</small>}
                </div>
                {['preview_ready', 'needs_human_review'].includes(job.status) && (
                  <Button
                    variant="ghost"
                    loading={busyQuestionId === job.job_id}
                    onClick={() => restoreImport(job.job_id)}
                  >
                    继续处理
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {previewItems.length > 0 && (
        <section className="question-workspace__section" aria-label="待确认题目">
          <header>
            <div className="question-workspace__section-title">
              <span>本次解析</span>
              <div className="question-workspace__title-row">
                <h3>待确认题目</h3>
                <StatusBadge status="info">{previewItems.length} 题</StatusBadge>
              </div>
            </div>
            <Button
              className="question-workspace__confirm-all"
              loading={busyQuestionId === activeImportJobId}
              disabled={
                !activeImportJobId
                || previewItems.every((item) => ['active', 'rejected', 'inactive'].includes(item.status))
                || previewItems.some((item) => !['preview_ready', 'active', 'rejected', 'inactive'].includes(item.status))
              }
              onClick={confirmAllQuestions}
            >
              <CheckCircle2 aria-hidden="true" size={17} />
              全部确认导入
            </Button>
          </header>
          <ul className="question-workspace__question-list">
            {previewItems.map((item, index) => (
              <li key={item.question_id}>
                <div className="question-workspace__question-index">{String(index + 1).padStart(2, '0')}</div>
                <div className="question-workspace__question-body">
                  <div className="question-workspace__question-meta">
                    <StatusBadge status={item.status === 'active' ? 'success' : item.status === 'needs_human_review' ? 'warning' : 'info'}>
                      {statusLabel(item.status)}
                    </StatusBadge>
                    <span>{item.question_type}</span>
                    <span>{item.kp_ids?.join(' · ') || '待关联知识点'}</span>
                  </div>
                  <strong>{item.stem}</strong>
                  {item.options?.length > 0 && (
                    <ul className="question-workspace__options" aria-label="题目选项">
                      {item.options.map((option, optionIndex) => (
                        <li key={`${item.question_id}-option-${optionIndex}`}>
                          {option}
                        </li>
                      ))}
                    </ul>
                  )}
                  {item.status === 'needs_human_review' ? (
                    <label className="question-workspace__revision">
                      <span>修订参考答案</span>
                      <textarea
                        aria-label="修订参考答案"
                        value={draftAnswers[item.question_id] || ''}
                        onChange={(event) => setDraftAnswers((current) => ({
                          ...current,
                          [item.question_id]: event.target.value,
                        }))}
                      />
                      <Button
                        variant="secondary"
                        disabled={!String(draftAnswers[item.question_id] || '').trim()}
                        loading={busyQuestionId === item.question_id}
                        onClick={() => reviseQuestion(item.question_id)}
                      >
                        保存修订
                      </Button>
                    </label>
                  ) : (
                    <p><span>参考答案</span>{item.answer || '尚未提供答案'}</p>
                  )}
                  {item.review_reason && <small>{item.review_reason}</small>}
                </div>
                <div className="question-workspace__question-actions">
                  {item.status === 'active' || activeIds.has(item.question_id) ? (
                    <span className="question-workspace__confirmed"><CheckCircle2 aria-hidden="true" size={17} />已激活</span>
                  ) : item.status === 'rejected' ? (
                    <span className="question-workspace__confirmed"><XCircle aria-hidden="true" size={17} />已拒绝</span>
                  ) : (
                    <>
                      <Button
                        variant="ghost"
                        disabled={busyQuestionId === item.question_id}
                        onClick={() => rejectQuestion(item.question_id)}
                      >
                        拒绝
                      </Button>
                      <Button
                        variant="secondary"
                        disabled={item.status !== 'preview_ready' || busyQuestionId === item.question_id}
                        loading={busyQuestionId === item.question_id}
                        onClick={() => confirmQuestion(item.question_id)}
                      >
                        确认导入
                      </Button>
                    </>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="question-workspace__section" aria-label="已激活个人题目">
        <header>
          <div><span>个人练习范围</span><h3>已激活题目</h3></div>
          <Button variant="ghost" onClick={loadWorkspace} disabled={loading}>
            <RefreshCw aria-hidden="true" size={16} />刷新
          </Button>
        </header>
        {loading && <Skeleton label="正在加载个人题目" lines={3} />}
        {!loading && activeQuestions.length === 0 && (
          <EmptyState
            title="还没有已激活的个人题目"
            description="上传资料并逐题确认后，题目会出现在这里，并可通过练习工坊的个人题目范围练习。"
          />
        )}
        {!loading && activeQuestions.length > 0 && (
          <ul className="question-workspace__active-list">
            {activeQuestions.map((item) => (
              <li key={item.question_id}>
                <CheckCircle2 aria-hidden="true" size={18} />
                <div className="question-workspace__active-body">
                  <strong>{item.stem}</strong>
                  <span>{item.question_type} · {item.kp_ids?.join(' · ')}</span>
                  {item.options?.length > 0 && (
                    <ul className="question-workspace__options" aria-label="题目选项">
                      {item.options.map((option, optionIndex) => (
                        <li key={`${item.question_id}-active-option-${optionIndex}`}>
                          {option}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <Button
                  variant="ghost"
                  loading={busyQuestionId === item.question_id}
                  onClick={() => deactivateQuestion(item.question_id)}
                >
                  停用
                </Button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </section>
  );
}
