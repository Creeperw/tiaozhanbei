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
import { API_BASE, fetchJsonWithAuthFallback, fetchWithAuth } from '../utils/api';
import { knowledgeQueryFromContext } from './exam-atlas/examAtlasPageContext';
import { getKnowledgeScopeNotice, getSearchFeedback } from '../knowledgePageState';
import QuestionWorkspacePage from './QuestionWorkspacePage';
import CompactAssistant from './CompactAssistant';
import { isKnowledgeAtlasEnabled } from './knowledge-atlas/knowledgeAtlasFeature';
import KnowledgeWorkspaceNav from './knowledge-atlas/KnowledgeWorkspaceNav';

const KnowledgeAtlas = React.lazy(() => import('./knowledge-atlas/KnowledgeAtlas'));

class KnowledgeAtlasErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <section className="m-4 grid min-h-[420px] place-content-center justify-items-center gap-3 rounded-3xl border border-rose-100 bg-white p-8 text-center" role="alert">
        <h2 className="text-xl font-bold text-slate-900">知识星球暂时无法显示</h2>
        <p className="max-w-lg text-sm text-slate-600">{this.state.error.message || '可视化模块发生异常，资料与题目工作区不受影响。'}</p>
        <button type="button" className="rounded-xl bg-emerald-700 px-4 py-2 text-sm font-semibold text-white" onClick={() => this.setState({ error: null })}>重新加载模块</button>
      </section>
    );
  }
}

const scopeLabel = {
  personal: '个人',
  public: '公共',
};

const KnowledgePage = ({ currentUser, navigationContext = {}, onNavigate }) => {
  const isAdmin = currentUser?.role === 'admin';
  const atlasEnabled = isKnowledgeAtlasEnabled();
  const [activeScope, setActiveScope] = useState('personal');
  const [activeWorkspace, setActiveWorkspace] = useState(
    navigationContext.view === 'questions'
      ? 'questions'
      : ['sources', 'personal', 'public'].includes(navigationContext.view)
        ? 'sources'
        : atlasEnabled ? 'atlas' : 'sources',
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
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [hasSearched, setHasSearched] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [contextBrief, setContextBrief] = useState(null);

  const fileInputRef = useRef(null);
  const dragCounter = useRef(0);

  useEffect(() => {
    if ((!navigationContext.view || navigationContext.view === 'atlas') && atlasEnabled) setActiveWorkspace('atlas');
    if ((!navigationContext.view || navigationContext.view === 'atlas') && !atlasEnabled) setActiveWorkspace('sources');
    if (navigationContext.view === 'questions') setActiveWorkspace('questions');
    if (navigationContext.view === 'personal') {
      setActiveScope('personal');
      setActiveWorkspace('sources');
    }
    if (navigationContext.view === 'public' || navigationContext.view === 'sources') {
      setActiveScope('public');
      setActiveWorkspace('sources');
    }
  }, [atlasEnabled, navigationContext.view]);

  const fetchStats = useCallback(async () => {
    setStatusError('');
    try {
      const res = await fetchWithAuth(`${API_BASE}/knowledge/status?scope=${activeScope}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || '向量库状态加载失败');
      setStats(data);
    } catch (error) {
      setStatusError(error.message || '向量库状态加载失败');
    }
  }, [activeScope]);

  const fetchFiles = useCallback(async () => {
    setFilesError('');
    try {
      const res = await fetchWithAuth(`${API_BASE}/knowledge/files?scope=all`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || '文件列表加载失败');
      setFileList(Array.isArray(data.files) ? data.files : []);
    } catch (error) {
      setFilesError(error.message || '文件列表加载失败');
    }
  }, []);

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

    if (activeWorkspace === 'atlas') return () => { cancelled = true; };

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
    if (activeWorkspace === 'atlas') return undefined;
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
    if (!filesArray || filesArray.length === 0) return;
    const uploadScope = isAdmin ? activeScope : 'personal';
    if (uploadScope === 'public' && !isAdmin) return;

    setIsUploading(true);
    setUploadError('');
    const formData = new FormData();
    filesArray.forEach(file => formData.append('files', file));

    try {
      const res = await fetchWithAuth(`${API_BASE}/knowledge/upload?scope=${uploadScope}`, {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || '上传失败');
      }
      setStats(prev => ({ ...prev, is_processing: true, status: '准备构建...', progress: 0 }));
      await fetchFiles();
    } catch (e) {
      setUploadError(e.message || '上传失败，请检查网络或后端服务');
    } finally {
      setIsUploading(false);
    }
  }, [activeScope, fetchFiles, isAdmin]);

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
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      processUpload(Array.from(e.dataTransfer.files));
    }
  }, [activeScope, isAdmin, processUpload]);

  const handleDeleteFile = async (file) => {
    if (!file?.can_delete) return;
    if (!window.confirm(`确定要删除「${file.name}」及其向量数据吗？此操作无法撤销。`)) return;

    try {
      const res = await fetchWithAuth(`${API_BASE}/knowledge/files/${encodeURIComponent(file.name)}?scope=${file.scope}`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || '删除失败');
      }
      await fetchFiles();
      await fetchStats();
    } catch (e) {
      alert(e.message || '网络异常，无法删除');
    }
  };

  const triggerRebuild = async () => {
    if (activeScope === 'public' && !isAdmin) return;
    try {
      const res = await fetchWithAuth(`${API_BASE}/knowledge/rebuild?scope=${activeScope}`, { method: 'POST' });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || '重建失败');
      }
      setStats(prev => ({ ...prev, is_processing: true, status: '准备扫描...', progress: 0 }));
    } catch (e) {
      alert(e.message || '重建失败');
    }
  };

  const handleSearchTest = async () => {
    if (!searchQuery.trim()) return;
    setIsSearching(true);
    setSearchError('');
    setHasSearched(true);
    setSearchResults([]);
    try {
      const res = await fetchWithAuth(`${API_BASE}/knowledge/search_test`, {
        method: 'POST',
        body: JSON.stringify({ query: searchQuery, top_k: 5 }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || '检索失败，请稍后重试');
      }
      const data = await res.json();
      setSearchResults(Array.isArray(data) ? data : []);
    } catch (e) {
      setSearchError(e.message || '检索失败，请稍后重试');
    } finally {
      setIsSearching(false);
    }
  };

  const canWriteActiveScope = activeScope === 'personal' || isAdmin;
  const handleAtlasDisabled = useCallback(() => setActiveWorkspace('sources'), []);
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
      atlasEnabled={atlasEnabled}
      activeWorkspace={activeWorkspace}
      activeScope={activeScope}
      onSelect={selectWorkspace}
      className={activeWorkspace === 'atlas' ? 'knowledge-page__workspace-nav--embedded' : ''}
    />
  );

  if (activeWorkspace === 'atlas') {
    return (
      <div className="knowledge-page__atlas-shell" data-workspace="knowledge-atlas">
        <KnowledgeAtlasErrorBoundary>
          <React.Suspense fallback={<div className="grid min-h-[520px] place-content-center text-sm text-slate-500" role="status">正在加载知识星球…</div>}>
            <KnowledgeAtlas initialContext={navigationContext} onDisabled={handleAtlasDisabled} workspaceNavigation={workspaceNavigation} />
          </React.Suspense>
        </KnowledgeAtlasErrorBoundary>
        <CompactAssistant
          className="knowledge-page__atlas-assistant"
          currentUser={currentUser?.username || 'User'}
          contextLabel={navigationContext.query || navigationContext.subject || '知识星球探索'}
          initiallyCollapsed
          onOpenFull={(sessionId) => onNavigate?.('assistant', sessionId)}
        />
      </div>
    );
  }

  if (activeWorkspace === 'questions') {
    return (
      <div className="knowledge-page__secondary knowledge-page__secondary--questions">
        {workspaceNavigation}
        <main className="knowledge-page__main knowledge-page__main--questions">
          <section className="knowledge-page__questions" aria-label="题目数据">
            <QuestionWorkspacePage />
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
              className={`rounded-xl border px-3 py-2 flex items-center justify-center gap-2 transition-colors ${activeScope === 'personal' ? 'bg-emerald-50 border-emerald-200 text-emerald-800 font-semibold' : 'bg-white border-slate-100 text-slate-700 hover:bg-slate-50 hover:text-slate-900'}`}
            >
              <User size={15} />个人库
            </button>
            <button
              onClick={() => setActiveScope('public')}
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
                  <span className="truncate" title={file.name}>{file.name}</span>
                  <span className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] border ${file.scope === 'public' ? 'bg-teal-50 text-teal-700 border-teal-100' : 'bg-emerald-50 text-emerald-700 border-emerald-100'}`}>{scopeLabel[file.scope]}</span>
                </div>
                {file.can_delete ? (
                  <button onClick={() => handleDeleteFile(file)} className="opacity-0 group-hover:opacity-100 text-rose-400 hover:text-rose-600 transition-opacity p-1" title="删除文件及向量数据">
                    <Trash2 size={14} />
                  </button>
                ) : (
                  <span className="text-[10px] text-slate-500">只读</span>
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
              <p className="text-2xl font-bold text-slate-800 mb-2">松开鼠标，上传到{scopeLabel[activeScope]}知识库</p>
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
                  Embedding：{catalog.embedding.state}{catalog.embedding.model_id ? ` · ${catalog.embedding.model_id}` : ''}{catalog.embedding.error ? ` · ${catalog.embedding.error}` : ''}
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
                onClick={() => fileInputRef.current?.click()}
                disabled={isUploading || stats.is_processing || !canWriteActiveScope}
                className="flex w-full items-center justify-center gap-2 px-4 py-2 bg-emerald-600 text-white rounded-xl hover:bg-emerald-700 transition-[background-color,opacity] disabled:opacity-50 disabled:cursor-not-allowed shadow-sm shadow-emerald-200 sm:w-auto"
              >
                {isUploading ? <RefreshCw className="animate-spin" size={18} /> : <UploadCloud size={18} />}
                上传{scopeLabel[activeScope]}文档
              </button>
              <input type="file" multiple ref={fileInputRef} onChange={handleFileUpload} className="hidden" accept=".txt,.md,.pdf,.json,.jsonl" />
              <button
                onClick={triggerRebuild}
                disabled={stats.is_processing || !canWriteActiveScope}
                className="flex w-full items-center justify-center gap-2 px-4 py-2 bg-white border border-emerald-100 text-emerald-900 rounded-xl hover:bg-emerald-50 transition-[color,background-color,opacity] disabled:opacity-50 disabled:text-emerald-800 sm:w-auto"
              >
                <Zap size={18} className="text-amber-500" />
                重建{scopeLabel[activeScope]}库
              </button>
            </div>
          </div>

          <div className={`mb-5 rounded-xl border px-4 py-3 text-sm ${activeScope === 'public' && !isAdmin ? 'border-teal-100 bg-teal-50/70 text-teal-700' : 'border-emerald-100 bg-emerald-50/70 text-emerald-800'}`}>
            {scopeNotice}
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

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <StatsCard icon={<FileText className="text-emerald-500" />} label={`${scopeLabel[activeScope]}文档数`} value={stats.total_documents} sub="个源文件" />
            <StatsCard icon={<Layers className="text-teal-500" />} label="向量切片" value={stats.total_chunks} sub="个文本块" />
            <StatsCard
              icon={stats.is_processing ? <RefreshCw className="animate-spin text-emerald-500" /> : <CheckCircle className="text-emerald-500" />}
              label="系统状态"
              value={stats.status}
              sub={stats.is_processing ? `进度: ${stats.progress}%` : '等待任务'}
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
              <span>先检索，再阅读；每条结果保留来源、范围与相似度。</span>
            </div>
            <div className="knowledge-workbench__search-box bg-white p-2 rounded-2xl shadow-sm border border-emerald-100 flex flex-col gap-2 mb-6 transition-shadow sm:flex-row sm:items-center">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSearchTest()}
                placeholder="输入问题测试公共知识 + 个人知识联合召回效果..."
                className="flex-1 px-4 py-3 outline-none text-slate-700 placeholder-slate-400 bg-transparent"
              />
              <button onClick={handleSearchTest} disabled={isSearching} className="w-full px-6 py-2.5 bg-slate-900 text-white rounded-xl hover:bg-slate-800 transition-[background-color,opacity] disabled:opacity-50 font-medium sm:w-auto">
                {isSearching ? '搜索中...' : '测试'}
              </button>
            </div>

            <div className="knowledge-workbench__search-results space-y-4">
              {searchResults.map((result, idx) => (
                <div key={idx} className="bg-white/90 p-5 rounded-2xl border border-emerald-50 shadow-sm hover:shadow-md transition-shadow group animate-in fade-in slide-in-from-bottom-2 duration-300">
                  <div className="flex justify-between items-start mb-2">
                    <div className="flex items-center gap-2">
                      <span className="px-2 py-0.5 bg-emerald-50 text-emerald-600 text-xs rounded font-medium border border-emerald-100">Top {idx + 1}</span>
                      <span className={`px-2 py-0.5 text-xs rounded-full border ${result.scope === 'public' ? 'bg-teal-50 text-teal-700 border-teal-100' : 'bg-emerald-50 text-emerald-700 border-emerald-100'}`}>{scopeLabel[result.scope] || result.scope}</span>
                      <span className="text-xs text-slate-400 font-mono">{result.source}</span>
                    </div>
                    <span className="text-xs font-bold text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">{(result.score * 100).toFixed(1)}% 相似度</span>
                  </div>
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
      <CompactAssistant
        className="knowledge-workbench__assistant"
        currentUser={currentUser?.username || 'User'}
        dailyGoal={contextBrief?.goal || ''}
        contextLabel={searchQuery ? `资料检索 · ${searchQuery}` : `${scopeLabel[activeScope]}资料库`}
        initiallyCollapsed
        characterHint="点我询问相关内容"
        onOpenFull={(sessionId) => onNavigate?.('assistant', sessionId)}
      />
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
