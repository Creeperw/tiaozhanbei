import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  AlertCircle,
  CheckCircle,
  Database,
  FileText,
  Layers,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  UploadCloud,
  User,
  Zap,
} from 'lucide-react';
import { API_BASE, MAIN_API_BASE, fetchJsonWithAuthFallback, fetchWithAuth } from '../utils/api';
import { knowledgeQueryFromContext } from './exam-atlas/examAtlasPageContext';
import { getKnowledgeScopeNotice, getSearchFeedback } from '../knowledgePageState';
import QuestionWorkspacePage from './QuestionWorkspacePage';
import KnowledgeWorkspaceNav from './knowledge-atlas/KnowledgeWorkspaceNav';
import KnowledgeRecognitionReports from './knowledge-reports/KnowledgeRecognitionReports';
import { uploadIntent } from './resource-upload/uploadNavigation';
import { uploadKnowledgeFiles } from './resource-upload/knowledgeUpload';

const scopeLabel = {
  personal: '个人',
  public: '公共',
};

const KnowledgePage = ({ currentUser, navigationContext = {}, onNavigate }) => {
  const isAdmin = currentUser?.role === 'admin';
  const [activeScope, setActiveScope] = useState('personal');
  const [activeWorkspace, setActiveWorkspace] = useState(
    navigationContext.view === 'questions' ? 'questions' : 'sources',
  );
  const [stats, setStats] = useState({
    total_documents: 0,
    total_chunks: 0,
    status: '就绪',
    progress: 0,
    is_processing: false,
  });
  const [fileList, setFileList] = useState([]);
  const [catalog, setCatalog] = useState({ documents: [], datasets: [], indexes: [], embedding: null });
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState('');
  const [statusError, setStatusError] = useState('');
  const [filesError, setFilesError] = useState('');
  const [searchQuery, setSearchQuery] = useState(() => knowledgeQueryFromContext(navigationContext));
  const [searchResults, setSearchResults] = useState([]);
  const [searchKind, setSearchKind] = useState('content');
  const [searchWarning, setSearchWarning] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [hasSearched, setHasSearched] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [isManaging, setIsManaging] = useState(false);
  const managementInFlight = useRef(false);
  const [isDragging, setIsDragging] = useState(false);
  const [contextBrief, setContextBrief] = useState(null);
  const [recognitionReportsVersion, setRecognitionReportsVersion] = useState(0);
  const [uploadNotice, setUploadNotice] = useState('');
  const [document, setDocument] = useState(null);
  const [documentError, setDocumentError] = useState('');
  const [documentLoading, setDocumentLoading] = useState(false);
  const statsRequest = useRef(0);
  const documentRequest = useRef(0);

  const fileInputRef = useRef(null);
  const dragCounter = useRef(0);

  useEffect(() => {
    if (navigationContext.view === 'questions') setActiveWorkspace('questions');
    if (navigationContext.view === 'personal') {
      setActiveScope('personal');
      setActiveWorkspace('sources');
    }
    if (navigationContext.view === 'public' || navigationContext.view === 'sources') {
      setActiveScope('public');
      setActiveWorkspace('sources');
    }
  }, [navigationContext.view]);

  const fetchStats = useCallback(async () => {
    const version = ++statsRequest.current;
    setStatusError('');
    try {
      const res = await fetchWithAuth(activeScope === 'personal'
        ? `${MAIN_API_BASE}/knowledge/content/library`
        : `${API_BASE}/knowledge/status?scope=public`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || '向量库状态加载失败');
      if (version === statsRequest.current) setStats(activeScope === 'personal' ? data.stats || {} : data);
    } catch (error) {
      if (version === statsRequest.current) setStatusError(error.message || '向量库状态加载失败');
    }
  }, [activeScope]);

  const fetchFiles = useCallback(async () => {
    setFilesError('');
    const results = await Promise.allSettled([
      `${MAIN_API_BASE}/knowledge/content/library`,
      `${API_BASE}/knowledge/files?scope=all`,
    ].map(async url => {
      const res = await fetchWithAuth(url);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error('目录加载失败');
      return Array.isArray(data.files) ? data.files : [];
    }));
    setFileList(results.flatMap((result, index) => result.status === 'fulfilled'
      ? result.value.map(file => ({ ...file, storage: index === 0 ? 'delivery' : 'legacy' })) : []));
    const failures = results.flatMap((result, index) => result.status === 'rejected'
      ? [index === 0 ? '个人导入目录加载失败' : '旧资料目录加载失败'] : []);
    setFilesError(failures.join('；'));
  }, []);

  const openDocument = async file => {
    const version = ++documentRequest.current;
    setDocument(null);
    setDocumentError('');
    setDocumentLoading(true);
    try {
      const res = await fetchWithAuth(`${MAIN_API_BASE}/knowledge/content/library/${encodeURIComponent(file.id)}`);
      const data = await res.json();
      if (!res.ok) throw new Error('个人资料读取失败，请重试');
      if (version === documentRequest.current) setDocument(data);
    } catch {
      if (version === documentRequest.current) setDocumentError('个人资料读取失败，请重试');
    } finally {
      if (version === documentRequest.current) setDocumentLoading(false);
    }
  };

  useEffect(() => {
    documentRequest.current += 1;
    setDocument(null);
    setDocumentError('');
    setDocumentLoading(false);
  }, [activeScope]);

  const fetchCatalog = useCallback(async () => {
    setCatalogLoading(true);
    setCatalogError('');
    try {
      const res = await fetchWithAuth(`${API_BASE}/knowledge/catalog`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || '知识目录加载失败');
      setCatalog({
        documents: Array.isArray(data.documents) ? data.documents : [],
        datasets: Array.isArray(data.datasets) ? data.datasets : [],
        indexes: Array.isArray(data.indexes) ? data.indexes : [],
        embedding: data.embedding || null,
      });
    } catch (error) {
      setCatalogError(error.message || '知识目录加载失败');
    } finally {
      setCatalogLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const loadContextBrief = async () => {
      try {
        const { data } = await fetchJsonWithAuthFallback({ paths: ['/agent/context/brief'], fallback: null });
        if (!cancelled) {
          setContextBrief(data);
        }
      } catch {
        if (!cancelled) {
          setContextBrief(null);
        }
      }
    };

    fetchStats();
    fetchFiles();
    fetchCatalog();
    loadContextBrief();
    return () => {
      cancelled = true;
    };
  }, [activeWorkspace, fetchCatalog, fetchStats, fetchFiles]);

  useEffect(() => {
    let interval = null;
    if (stats.is_processing) {
      interval = window.setInterval(() => fetchStats(), 1000);
    } else {
      fetchFiles();
    }
    return () => {
      if (interval) window.clearInterval(interval);
    };
  }, [activeWorkspace, stats.is_processing, fetchStats, fetchFiles]);

  const activeFiles = fileList.filter(file => file.scope === activeScope);
  const allFilesCount = fileList.length;
  const progressValue = Math.min(100, Math.max(0, Number(stats.progress) || 0));
  const progressScale = progressValue / 100;

  const processUpload = useCallback(async (filesArray) => {
    if (managementInFlight.current) return;
    if (!filesArray || filesArray.length === 0) return;
    const uploadScope = isAdmin ? activeScope : 'personal';
    if (uploadScope === 'public' && !isAdmin) return;

    setIsUploading(true);
    setUploadError('');
    setUploadNotice('');

    try {
      const result = await uploadKnowledgeFiles(filesArray, {
        scope: uploadScope,
        onStage: status => setStats(prev => ({ ...prev, is_processing: true, status, progress: 0 })),
      });
      if (!result.legacyPending && result.contentCount > 0) {
        setUploadNotice(`教材导入完成，已生成 ${result.chapterCount} 个章节`);
      }
      await fetchFiles();
      await fetchStats();
      if (result.contentCount > 0) setRecognitionReportsVersion(value => value + 1);
    } catch (e) {
      setStats(prev => ({ ...prev, is_processing: false }));
      setUploadError(e.message || '上传失败，请检查网络或后端服务');
    } finally {
      setIsUploading(false);
    }
  }, [activeScope, fetchFiles, fetchStats, isAdmin]);

  const handleFileUpload = (e) => {
    processUpload(Array.from(e.target.files || []));
    e.target.value = null;
  };

  const onDragEnter = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current += 1;
    if (e.dataTransfer.items && e.dataTransfer.items.length > 0) setIsDragging(true);
  }, []);

  const onDragLeave = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounter.current -= 1;
    if (dragCounter.current === 0) setIsDragging(false);
  }, []);

  const onDragOver = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const onDrop = useCallback((e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    dragCounter.current = 0;
    if (activeScope === 'public' && !isAdmin) return;
    if (activeScope === 'personal' && onNavigate) {
      setUploadNotice('请在统一上传面板选择个人知识资料后重新选择文件。');
      onNavigate(uploadIntent('knowledge'));
      return;
    }
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      processUpload(Array.from(e.dataTransfer.files));
    }
  }, [activeScope, isAdmin, processUpload, onNavigate]);

  const handleDeleteFile = async (file) => {
    if (!file?.can_delete || managementInFlight.current || isUploading) return;
    const personal = file.storage === 'delivery';
    if (!window.confirm(personal
      ? `确定从个人有效资料中删除「${file.name}」吗？仅由此资料支撑的知识点、关联和向量将移除；历史学习记录、识别报告及恢复备份保留，已激活题目不变。`
      : `确定要删除「${file.name}」及其向量数据吗？此操作无法撤销。`)) return;
    managementInFlight.current = true;
    setIsManaging(true);
    setUploadNotice('');
    try {
      const url = personal
        ? `${MAIN_API_BASE}/knowledge/content/library/${encodeURIComponent(file.id)}`
        : `${API_BASE}/knowledge/files/${encodeURIComponent(file.name)}?scope=${file.scope}`;
      const res = await fetchWithAuth(url, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || '删除失败');
      }
      documentRequest.current += 1;
      setDocument(null);
      setDocumentLoading(false);
      setDocumentError('');
      await fetchFiles();
      await fetchStats();
      setUploadNotice(personal ? '个人有效资料及其索引已更新；历史记录和恢复备份保留。' : '删除完成');
    } catch (e) {
      alert(e.message || '网络异常，无法删除');
    } finally {
      managementInFlight.current = false;
      setIsManaging(false);
    }
  };

  const triggerRebuild = async () => {
    if (managementInFlight.current || isUploading) return;
    if (activeScope === 'public' && !isAdmin) return;
    const personal = activeScope === 'personal';
    if (personal && !window.confirm('根据当前有效知识点重建个人向量索引？不会重新解析文件，不修改公共库或已激活题目。')) return;
    managementInFlight.current = true;
    setIsManaging(true);
    setUploadNotice('');
    try {
      const url = personal ? `${MAIN_API_BASE}/knowledge/content/library/rebuild` : `${API_BASE}/knowledge/rebuild?scope=${activeScope}`;
      const res = await fetchWithAuth(url, { method: 'POST' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || '重建失败');
      }
      if (personal) {
        await fetchFiles();
        await fetchStats();
        setUploadNotice('个人向量索引已重建并校验。');
      } else {
        setStats(prev => ({ ...prev, is_processing: true, status: '准备扫描...', progress: 0 }));
      }
    } catch (e) {
      alert(e.message || '重建失败');
    } finally {
      managementInFlight.current = false;
      setIsManaging(false);
    }
  };

  const handleSearchTest = async () => {
    if (!searchQuery.trim() || isSearching) return;
    setIsSearching(true);
    setSearchError('');
    setSearchWarning('');
    setHasSearched(true);
    setSearchResults([]);
    try {
      const res = await fetchWithAuth(`${MAIN_API_BASE}/knowledge/${searchKind === 'questions' ? 'questions' : 'content'}/search`, {
        method: 'POST',
        body: JSON.stringify({ query: searchQuery.trim(), limit: 5, ...(searchKind === 'questions' ? { scope: 'all' } : {}) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || '检索失败，请稍后重试');
      }
      if (searchKind === 'questions') {
        if (!Array.isArray(data.items)) throw new Error('题目检索返回格式异常');
        setSearchResults(data.items.map(item => ({
          id: item.question_id,
          source: item.question_id,
          scope: '正式题库 / 当前用户题目',
          content: [item.stem, item.options?.join('\n'), item.reference_answer ? `答案：${item.reference_answer}` : '', item.analysis ? `解析：${item.analysis}` : ''].filter(Boolean).join('\n\n'),
          channels: (item.retrieval?.channels || []).map(channel => ({ vector: '向量', bm25: 'BM25', bridge: '知识点关联' }[channel] || channel)).join(' · '),
          score: item.retrieval?.channel_scores?.vector,
          scoreLabel: '向量通道分',
        })));
        if (data.vector_degraded) setSearchWarning('向量通道暂不可用，本次结果来自知识点关联 / BM25，不代表向量检索成功。');
      } else {
        if (!Array.isArray(data.evidence_items)) throw new Error('教材检索返回格式异常');
        setSearchResults(data.evidence_items.map(item => ({
          id: item.evidence_id,
          source: item.source_label || item.source_id,
          scope: 'public',
          content: item.content_summary,
          score: item.confidence,
          scoreLabel: '工具置信度',
        })));
      }
    } catch (e) {
      setSearchError(e.message || '检索失败，请稍后重试');
    } finally {
      setIsSearching(false);
    }
  };

  const canWriteActiveScope = activeScope === 'personal' || isAdmin;
  const scopeNotice = getKnowledgeScopeNotice(activeScope, isAdmin);
  const searchFeedback = getSearchFeedback({
    isSearching,
    error: searchError,
    hasQueried: hasSearched,
    resultCount: searchResults.length,
  });
  const selectWorkspace = (workspace, scope) => {
    if (scope) setActiveScope(scope);
    setActiveWorkspace(workspace);
  };
  const workspaceNavigation = (
      <KnowledgeWorkspaceNav
      activeWorkspace={activeWorkspace}
      onSelect={selectWorkspace}
    />
  );

  if (activeWorkspace === 'questions') {
    return (
      <div className="knowledge-page__secondary knowledge-page__secondary--questions">
        {workspaceNavigation}
        <main className="knowledge-page__main knowledge-page__main--questions">
          <section className="knowledge-page__questions" aria-label="题目数据">
            <QuestionWorkspacePage onUploadRequested={onNavigate ? () => onNavigate(uploadIntent('question')) : undefined} />
          </section>
        </main>
      </div>
    );
  }

  return (
    <section className="knowledge-workbench" role="region" aria-label="知识资料工作台">
      {workspaceNavigation}
      <div className="knowledge-workbench__columns">
      <aside aria-label="资料集合" className="knowledge-workbench__collections flex w-full flex-col bg-white/80 backdrop-blur-xl border-b border-emerald-100 z-10 lg:border-b-0 lg:border-r">
        <div className="flex flex-col gap-3 px-6 py-4 border-b border-emerald-100">
          <div className="flex items-center gap-2 font-bold text-slate-800">
            <Database className="text-emerald-600" size={20} />
            <span>资料目录</span>
          </div>
        </div>

        <div className="p-4 border-b border-emerald-50">
          <div className="grid grid-cols-2 gap-2 text-sm">
            <button
              onClick={() => setActiveScope('personal')}
              disabled={isManaging || isUploading}
              className={`rounded-xl border px-3 py-2 flex items-center justify-center gap-2 transition-colors ${activeScope === 'personal' ? 'bg-emerald-50 border-emerald-200 text-emerald-800 font-semibold' : 'bg-white border-slate-100 text-slate-700 hover:bg-slate-50 hover:text-slate-900'}`}
            >
              <User size={15} />个人库
            </button>
            <button
              onClick={() => setActiveScope('public')}
              disabled={isManaging || isUploading}
              className={`rounded-xl border px-3 py-2 flex items-center justify-center gap-2 transition-colors ${activeScope === 'public' ? 'bg-teal-50 border-teal-200 text-teal-800 font-semibold' : 'bg-white border-slate-100 text-slate-700 hover:bg-slate-50 hover:text-slate-900'}`}
            >
              <ShieldCheck size={15} />公共库
            </button>
          </div>
        </div>

        <div className="p-4 flex-1 overflow-hidden flex flex-col lg:max-h-[calc(100vh-145px)]">
          <div className="flex justify-between items-center mb-2">
            <span className="text-xs font-semibold text-slate-500 uppercase">当前库文件 ({activeFiles.length}) · 全部 {allFilesCount}</span>
            <button onClick={fetchFiles} className="text-slate-500 hover:text-emerald-700"><RefreshCw size={14} /></button>
          </div>
          {filesError && (
            <div role="alert" className="mb-2 flex items-start justify-between gap-2 rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-xs text-rose-700">
              <span>{filesError}</span>
              <button type="button" onClick={fetchFiles} className="shrink-0 font-semibold text-rose-800 underline underline-offset-2">重试</button>
            </div>
          )}
          <div className="overflow-y-auto pr-2 space-y-1 custom-scrollbar">
            {activeFiles.map((file, idx) => (
              <div key={`${file.scope}-${file.name}-${idx}`} className="group flex items-center justify-between px-3 py-2 text-sm text-slate-700 bg-white/85 rounded-xl border border-slate-100 hover:border-emerald-100 hover:bg-emerald-50/40 transition-[background-color,border-color]">
                <div className="flex items-center gap-2 overflow-hidden">
                  <FileText size={16} className={file.scope === 'public' ? 'text-teal-500 shrink-0' : 'text-emerald-500 shrink-0'} />
                  {file.storage === 'delivery' ? (
                    <button className="truncate text-left underline underline-offset-2" title={file.name} onClick={() => openDocument(file)}>{file.name}</button>
                  ) : <span className="truncate" title={file.name}>{file.name}</span>}
                  <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] border ${file.scope === 'public' ? 'bg-teal-50 text-teal-700 border-teal-100' : 'bg-emerald-50 text-emerald-700 border-emerald-100'}`}>{scopeLabel[file.scope]}</span>
                </div>
                {file.can_delete ? (
                  <button disabled={isManaging || isUploading} onClick={() => handleDeleteFile(file)} className="text-rose-400 hover:text-rose-600 disabled:opacity-40 transition-opacity p-1" title={`删除${file.name}及向量数据`}>
                    <Trash2 size={14} />
                  </button>
                ) : (
                  <span className="text-[10px] text-slate-500">{file.storage === 'delivery' ? `${file.chunk_count} 切片` : '旧资料'}</span>
                )}
              </div>
            ))}
            {activeFiles.length === 0 && <div className="text-center text-slate-500 text-sm py-8">暂无{scopeLabel[activeScope]}知识文件</div>}
          </div>
        </div>
      </aside>

      <main
        aria-label="资料检索与阅读"
        className="knowledge-workbench__main knowledge-page__main relative min-w-0"
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {isDragging && canWriteActiveScope && (
          <div className="absolute inset-0 z-50 bg-emerald-50/90 border-4 border-dashed border-emerald-300 rounded-3xl m-4 flex items-center justify-center backdrop-blur-sm pointer-events-none">
            <div className="bg-white p-8 rounded-3xl shadow-xl flex flex-col items-center animate-in zoom-in-95 duration-200">
              <UploadCloud size={64} className="text-emerald-600 mb-4" />
              <p className="text-2xl font-bold text-slate-800 mb-2">{activeScope === 'personal' && onNavigate ? '松开鼠标，前往统一上传面板重新选择文件' : `松开鼠标，上传到${scopeLabel[activeScope]}知识库`}</p>
              <p className="text-slate-600">支持格式: .txt, .md, .pdf, .json, .jsonl</p>
            </div>
          </div>
        )}

        <section className="knowledge-workbench__system p-4 sm:p-6 lg:p-8 bg-white/80 backdrop-blur border-b border-emerald-100">
          <div className="knowledge-workbench__section-heading">
            <p>系统维护</p>
            <h2>索引与导入状态</h2>
          </div>
          <div className="mb-6 overflow-hidden rounded-[28px] border border-emerald-100 bg-white shadow-sm shadow-emerald-100/60">
            <div className="grid gap-0 lg:grid-cols-[minmax(0,0.86fr)_minmax(360px,1.14fr)]">
              <div className="space-y-4 p-6 lg:p-7">
                <div className="inline-flex items-center gap-2 rounded-full border border-emerald-100 bg-emerald-50 px-3 py-1 text-sm font-medium text-emerald-800">
                  <ShieldCheck size={16} />
                  来源分层与证据链路
                </div>
                <div>
                  <h1 className="text-2xl font-bold text-slate-950">知识库</h1>
                  <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">把古籍、教材、期刊与标准指南组织成可检索证据包，支持个人资料优先、公共知识兜底的学习检索。</p>
                </div>
              </div>
              <div className="flex min-h-[220px] items-end bg-emerald-50 p-5">
                <div className="rounded-2xl border border-emerald-100 bg-white/90 p-4 shadow-sm shadow-emerald-100/60">
                  <div className="text-sm font-semibold text-emerald-950">可追溯的学习证据</div>
                  <p className="mt-1 text-sm leading-6 text-emerald-900">从资料来源到检索证据，保持每一步可核验。</p>
                </div>
              </div>
            </div>
          </div>

          {activeScope === 'public' && (
            <div className="mb-6 rounded-[24px] border border-emerald-100 bg-white/90 p-5 shadow-sm shadow-emerald-100/50" aria-label="公共知识目录">
              <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
                <div>
                  <p className="text-xs font-bold uppercase tracking-[.12em] text-emerald-700">Knowledge catalog</p>
                  <h2 className="mt-1 text-xl font-bold text-slate-900">文档、数据集与索引</h2>
                  <p className="mt-1 text-sm text-slate-600">文件只是知识体系的一层；题库、切片、图片、考纲、视频与 FAISS 索引单独核验。</p>
                </div>
                <button type="button" onClick={fetchCatalog} disabled={catalogLoading} className="inline-flex items-center gap-2 rounded-xl border border-emerald-100 bg-emerald-50 px-3 py-2 text-sm font-semibold text-emerald-800 disabled:opacity-50">
                  <RefreshCw size={14} className={catalogLoading ? 'animate-spin' : ''} />刷新目录
                </button>
              </div>
              {catalogError && <div role="alert" className="mb-4 rounded-xl border border-rose-100 bg-rose-50 px-3 py-2 text-sm text-rose-700">{catalogError}</div>}
              <div className="grid gap-3 xl:grid-cols-3">
                <CatalogGroup title="文档" icon={FileText} items={catalog.documents} empty="暂无可读取的公共文档" loading={catalogLoading} />
                <CatalogGroup title="数据集" icon={Database} items={catalog.datasets} empty="Atlas 数据集尚未导入" loading={catalogLoading} />
                <CatalogGroup title="索引" icon={Layers} items={catalog.indexes} empty="向量索引尚未就绪" loading={catalogLoading} />
              </div>
              {catalog.embedding?.state && (
                <p className={`mt-3 rounded-xl px-3 py-2 text-xs ${catalog.embedding.state === 'ready' ? 'bg-emerald-50 text-emerald-800' : 'bg-amber-50 text-amber-800'}`}>
                  旧全库索引服务：{catalog.embedding.state}{catalog.embedding.model_id ? ` · ${catalog.embedding.model_id}` : ''}{catalog.embedding.error ? ` · ${catalog.embedding.error}` : ''}。下方资料检索使用多智能体共享工具，不以此状态判断是否可用。
                </p>
              )}
            </div>
          )}

          <div className="flex flex-col justify-between items-start mb-6 gap-4 sm:flex-row">
            <div>
              <h1 className="text-2xl font-bold text-slate-900 mb-2">向量数据库状态</h1>
              <p className="text-slate-600 text-sm">公共知识所有用户可检索；个人知识仅当前用户可见、可检索。</p>
              <p className="mt-2 text-sm text-slate-600">当前学习目标：{contextBrief?.goal || '暂无全局上下文，知识检索仍可独立运行。'}</p>
            </div>
            <div className="flex flex-col gap-3 w-full sm:w-auto sm:flex-row">
              <button
                onClick={() => activeScope === 'personal' && onNavigate ? onNavigate(uploadIntent('knowledge')) : fileInputRef.current?.click()}
                disabled={isManaging || isUploading || stats.is_processing || !canWriteActiveScope}
                className="flex w-full items-center justify-center gap-2 px-4 py-2 bg-emerald-600 text-white rounded-xl hover:bg-emerald-700 transition-[background-color,opacity] disabled:opacity-50 disabled:cursor-not-allowed shadow-sm shadow-emerald-200 sm:w-auto"
              >
                {isUploading ? <RefreshCw className="animate-spin" size={18} /> : <UploadCloud size={18} />}
                上传{scopeLabel[activeScope]}文档
              </button>
              <input type="file" multiple ref={fileInputRef} onChange={handleFileUpload} className="hidden" accept=".txt,.md,.pdf,.json,.jsonl" />
              <button
                onClick={triggerRebuild}
                disabled={isManaging || isUploading || stats.is_processing || !canWriteActiveScope}
                className="flex w-full items-center justify-center gap-2 px-4 py-2 bg-white border border-emerald-100 text-emerald-900 rounded-xl hover:bg-emerald-50 transition-[color,background-color,opacity] disabled:opacity-50 disabled:text-emerald-800 sm:w-auto"
              >
                <Zap size={18} className="text-amber-500" />
                重建{scopeLabel[activeScope]}库
              </button>
            </div>
          </div>

          <div className={`mb-5 rounded-xl border px-4 py-3 text-sm ${activeScope === 'public' && !isAdmin ? 'border-teal-100 bg-teal-50/70 text-teal-700' : 'border-emerald-100 bg-emerald-50/70 text-emerald-800'}`}>
            {scopeNotice}
            <span className="mt-1 block text-xs opacity-80">PDF 会由服务端 MinerU 解析为结构化 Markdown，再自动建立 Embedding 索引，无需在浏览器填写密钥。</span>
          </div>

          {statusError && (
            <div role="alert" className="mb-5 flex items-start justify-between gap-3 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
              <span>{statusError}</span>
              <button type="button" onClick={fetchStats} className="shrink-0 font-semibold underline underline-offset-2">重试</button>
            </div>
          )}

          {uploadError && (
            <div role="alert" className="mb-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
              {uploadError}
            </div>
          )}

          {uploadNotice && <p role="status" className="mb-4 text-emerald-800">{uploadNotice}</p>}
          {isManaging && <p role="status">正在更新个人资料或索引，请勿重复操作...</p>}
          {activeScope === 'personal' && <p className="mb-4 text-sm text-slate-600">统计来自当前有效的个人教材；旧格式资料单独保留。删除仅移除有效资料及关联索引，历史学习记录、识别报告和恢复备份保留；重建不会重新解析文件。</p>}
          {documentLoading && <p role="status">正在读取个人资料...</p>}
          {documentError && <p role="alert">{documentError}</p>}
          {document && <section aria-label="个人资料原文" className="mb-6 rounded-2xl border border-emerald-100 bg-white p-5">
            <h2 className="font-semibold text-slate-900">{document.name}</h2>
            <p className="text-sm text-slate-500">以下为已入库的解析切片原文，仅本人可见。</p>
            {(document.chunks || []).map(chunk => <article key={chunk.id} className="mt-4">
              <h3 className="font-medium">{chunk.title}</h3>
              <p className="whitespace-pre-wrap text-slate-700">{chunk.text}</p>
            </article>)}
          </section>}

          {activeScope === 'personal' && (
            <div>
              <p className="mb-2 text-xs text-slate-500">以下识别审查为导入时的历史快照，保留已删除资料的历史报告，不代表当前有效资料数量。</p>
              <KnowledgeRecognitionReports refreshToken={recognitionReportsVersion} />
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <StatsCard icon={<FileText className="text-emerald-500" />} label={`${scopeLabel[activeScope]}文档数`} value={stats.total_documents} sub="个源文件" />
            <StatsCard icon={<Layers className="text-teal-500" />} label={activeScope === 'personal' ? '解析切片' : '向量切片'} value={stats.total_chunks} sub={activeScope === 'personal' ? `${stats.total_knowledge_points ?? 0} 个知识点 · ${stats.total_vectors ?? 0} 条向量` : '个文本块'} />
            <StatsCard
              icon={stats.is_processing ? <RefreshCw className="animate-spin text-emerald-500" /> : <CheckCircle className="text-emerald-500" />}
              label={activeScope === 'personal' ? '个人导入索引状态' : '旧索引服务状态'}
              value={stats.status}
              sub={stats.is_processing ? `进度: ${stats.progress}%` : '独立于下方共享检索'}
              highlight={stats.is_processing}
            />
          </div>

          {stats.is_processing && (
            <div className="mt-6 animate-in fade-in duration-300">
              <div className="flex justify-between text-xs text-slate-600 mb-1">
                <span className="font-medium text-emerald-600">{stats.status}</span>
                <span className="font-medium">{stats.progress}%</span>
              </div>
              <div
                role="progressbar"
                aria-label="向量库构建进度"
                aria-valuemin="0"
                aria-valuemax="100"
                aria-valuenow={progressValue}
                className="relative h-2 w-full overflow-hidden rounded-full bg-emerald-50"
              >
                <div className="h-2 w-full origin-left transform-gpu rounded-full bg-emerald-600 transition-transform duration-500 ease-out" style={{ transform: `scaleX(${progressScale})` }} />
              </div>
            </div>
          )}
        </section>

        <section className="knowledge-workbench__search p-4 sm:p-6 lg:p-8">
          <div className="max-w-4xl mx-auto">
            <div className="knowledge-workbench__search-heading">
              <p>Evidence search</p>
              <h1><Search size={20} />资料检索</h1>
              <span>复用多智能体检索工具；手动选择教材或题目，每条结果保留真实来源。</span>
            </div>
            <div className="mb-3 flex items-center gap-3">
              <label htmlFor="knowledge-search-kind" className="text-sm text-slate-700">检索类型</label>
              <select id="knowledge-search-kind" value={searchKind} disabled={isSearching} onChange={event => {
                setSearchKind(event.target.value);
                setSearchResults([]);
                setSearchError('');
                setSearchWarning('');
                setHasSearched(false);
              }} className="rounded-lg border border-emerald-100 bg-white px-3 py-2 text-sm">
                <option value="content">教材资料</option>
                <option value="questions">题目</option>
              </select>
            </div>
            <p className="mb-3 text-xs text-slate-500">{searchKind === 'content'
              ? '范围：公共教材知识点及关联原文，与多智能体教材工具一致；不检索个人上传文件或古籍全库，不自动联网。'
              : '范围：正式题库与当前用户题目，与多智能体题目工具一致；使用向量、BM25、知识点关联混合检索。'}</p>
            <div className="knowledge-workbench__search-box bg-white p-2 rounded-2xl shadow-sm border border-emerald-100 flex flex-col gap-2 mb-6 transition-shadow sm:flex-row sm:items-center">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearchTest()}
                maxLength={2000}
                placeholder={searchKind === 'content' ? '输入知识主题，检索教材资料...' : '输入题干或知识主题，检索题目...'}
                className="flex-1 px-4 py-3 outline-none text-slate-700 placeholder-slate-400 bg-transparent"
              />
              <button onClick={handleSearchTest} disabled={isSearching} className="w-full px-6 py-2.5 bg-slate-900 text-white rounded-xl hover:bg-slate-800 transition-[background-color,opacity] disabled:opacity-50 font-medium sm:w-auto">
                {isSearching ? '搜索中...' : '测试'}
              </button>
            </div>

            <div className="knowledge-workbench__search-results space-y-4">
              {searchWarning && <p role="status" className="rounded-xl bg-amber-50 p-3 text-sm text-amber-800">{searchWarning}</p>}
              {searchResults.map((result, idx) => (
                <div key={result.id || idx} className="bg-white/90 p-5 rounded-2xl border border-emerald-50 shadow-sm hover:shadow-md transition-shadow group animate-in fade-in slide-in-from-bottom-2 duration-300">
                  <div className="flex justify-between items-start mb-2">
                    <div className="flex items-center gap-2">
                      <span className="px-2 py-0.5 bg-emerald-50 text-emerald-600 text-xs rounded font-medium border border-emerald-100">Top {idx + 1}</span>
                      <span className={`px-2 py-0.5 text-xs rounded-full border ${result.scope === 'public' ? 'bg-teal-50 text-teal-700 border-teal-100' : 'bg-emerald-50 text-emerald-700 border-emerald-100'}`}>{scopeLabel[result.scope] || result.scope}</span>
                      <span className="text-xs text-slate-400 font-mono">{result.source}</span>
                    </div>
                    {Number.isFinite(result.score) && <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">{(result.score * 100).toFixed(1)}% {result.scoreLabel}</span>}
                  </div>
                  {result.channels && <p className="mb-2 text-xs text-slate-500">命中通道：{result.channels}</p>}
                  <p className="text-slate-700 text-sm leading-relaxed whitespace-pre-wrap pl-3 border-l-2 border-emerald-100 group-hover:border-emerald-400 transition-colors">
                    {result.content}
                  </p>
                </div>
              ))}
              {searchFeedback && (
                <div role={searchFeedback.tone === 'error' ? 'alert' : 'status'} className={`text-center py-10 ${searchFeedback.tone === 'error' ? 'text-rose-700' : 'text-slate-500'}`}>
                  {searchFeedback.tone !== 'loading' && <AlertCircle className="mx-auto mb-2 opacity-50" size={32} />}
                  <p>{searchFeedback.text}</p>
                </div>
              )}
            </div>
          </div>
        </section>
      </main>
      </div>
    </section>
  );
};

const StatsCard = ({ icon, label, value, sub, highlight }) => (
  <div className={`bg-white/90 p-5 rounded-2xl border ${highlight ? 'border-emerald-200 ring-2 ring-emerald-500/10' : 'border-emerald-50'} shadow-sm flex items-center gap-4 transition-[border-color,box-shadow]`}>
    <div className="w-12 h-12 rounded-xl bg-emerald-50 flex items-center justify-center shrink-0">{icon}</div>
    <div className="min-w-0">
      <p className="text-sm text-slate-500 font-medium">{label}</p>
      <div className="flex items-baseline gap-2 min-w-0">
        <h3 className="text-2xl font-bold text-slate-800 truncate">{value}</h3>
        <span className="text-xs text-slate-400">{sub}</span>
      </div>
    </div>
  </div>
);

const CatalogGroup = ({ title, icon, items, empty, loading }) => {
  const Icon = icon;
  return (
    <section className="min-w-0 rounded-2xl border border-emerald-50 bg-[#f7faf8] p-4" aria-label={title}>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2 text-sm font-bold text-slate-900"><Icon size={16} className="text-emerald-700" />{title}</h3>
        <span className="rounded-full bg-white px-2 py-0.5 text-xs font-bold text-slate-600">{items.length}</span>
      </div>
      <div className="space-y-2">
        {items.slice(0, 5).map((item, index) => {
          const metadata = [
            item.kind,
            item.version,
            item.loaded === true ? '已加载' : item.loaded === false ? '未加载' : '',
            item.dimensions ? `${item.dimensions} 维` : '',
            item.normalized === true ? '已归一化' : '',
            item.embedding_model,
            item.linked_count != null ? `${Number(item.linked_count).toLocaleString('zh-CN')} 已关联` : '',
            item.pending_link_count != null ? `${Number(item.pending_link_count).toLocaleString('zh-CN')} 待关联` : '',
            item.matched_count != null ? `${Number(item.matched_count).toLocaleString('zh-CN')} 已匹配` : '',
          ].filter(Boolean).join(' · ');
          return (
            <div key={item.id || item.name || index} className="flex min-w-0 items-center justify-between gap-3 rounded-xl border border-white bg-white/80 px-3 py-2 text-xs">
              <span className="min-w-0" title={`${item.name || item.id}${metadata ? ` · ${metadata}` : ''}`}>
                <strong className="block truncate font-semibold text-slate-700">{item.name || item.id}</strong>
                {metadata && <small className="mt-0.5 block truncate text-[10px] text-slate-500">{metadata}</small>}
              </span>
              <span className={item.available === false ? 'shrink-0 text-amber-700' : 'shrink-0 text-slate-500'}>
                {item.count != null ? Number(item.count).toLocaleString('zh-CN') : item.available === false ? '不可用' : '可用'}
              </span>
            </div>
          );
        })}
        {!loading && items.length === 0 && <p className="py-4 text-center text-xs text-slate-500">{empty}</p>}
        {loading && items.length === 0 && <p className="py-4 text-center text-xs text-slate-500">正在读取目录…</p>}
        {items.length > 5 && <p className="text-right text-[11px] text-slate-500">另有 {items.length - 5} 项</p>}
      </div>
    </section>
  );
};

export default KnowledgePage;
