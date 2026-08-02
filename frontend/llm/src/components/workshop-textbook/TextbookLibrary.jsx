import React, { useEffect, useMemo, useState } from 'react';
import { ArrowRight, BookOpen, Check, ChevronDown, EyeOff, FileUp, ImagePlus, LoaderCircle, Search, Upload, X } from 'lucide-react';
import {
  filterTextbookViewModels,
  TEXTBOOK_FILTERS,
  textbookFilterCounts,
  textbookSourceCounts,
} from './textbookLibraryModel';
import { loadTextbookCategories, loadTextbookImportStatus, uploadTextbook } from './textbookPdfApi';
import './textbookLibrary.css';

const IMPORT_STEPS = [
  { key: 'upload', label: '上传文件' },
  { key: 'toc', label: '识别目录' },
  { key: 'extract', label: '解析正文' },
  { key: 'chunk', label: '章节切片' },
  { key: 'embed', label: '向量化' },
  { key: 'match', label: '匹配知识库' },
  { key: 'graph', label: '知识图谱' },
  { key: 'done', label: '完成' },
];

const EMPTY_TEXT = {
  learning: '当前没有学习中的教材',
  'not-started': '暂无未开始教材',
  completed: '还没有完成整本教材',
  planned: '尚未加入长期学习计划',
};

const SOURCE_OPTIONS = [
  { id: 'all', label: '全部教材' },
  { id: 'uploaded', label: '用户上传' },
  { id: 'platform', label: '平台自带' },
  { id: 'hidden', label: '已隐藏' },
];

function progressSummary(book) {
  if (!book.statusKnown) return '进度待统计';
  if (book.isCompleted) return '已完成';
  if (!book.hasStarted) return '尚未开始';
  if (book.totalSectionCount > 0) {
    return `已完成 ${book.completedSectionCount}/${book.totalSectionCount} 小节`;
  }
  return '学习中';
}

function TextbookUploadDialog({ onClose, onUploaded }) {
  const [categories, setCategories] = useState(['中医药']);
  const [file, setFile] = useState(null);
  const [cover, setCover] = useState(null);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [category, setCategory] = useState('中医药');
  const [newCategory, setNewCategory] = useState('');
  const [creatingCategory, setCreatingCategory] = useState(false);
  const [matchLocal, setMatchLocal] = useState(false);
  const [confirmLarge, setConfirmLarge] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [taskId, setTaskId] = useState(null);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    loadTextbookCategories({ signal: controller.signal })
      .then((payload) => setCategories(payload.items?.length ? payload.items : ['中医药']))
      .catch(() => {});
    return () => controller.abort();
  }, []);

  useEffect(() => {
    if (!taskId) return undefined;
    const controller = new AbortController();
    let timer;
    const poll = async () => {
      try {
        const payload = await loadTextbookImportStatus(taskId, { signal: controller.signal });
        setProgress(payload);
        if (payload.status === 'done') {
          onUploaded?.(payload.book);
          onClose();
          return;
        }
        if (payload.status === 'failed') {
          setError(payload.error?.code === 'TEXTBOOK_TOC_EXTRACTION_FAILED'
            ? '目录未提取成功：该 PDF 未包含可确认的目录页。'
            : payload.error?.message || '教材处理失败');
          setSubmitting(false);
          return;
        }
        timer = window.setTimeout(poll, 1500);
      } catch (reason) {
        if (reason.name !== 'AbortError') {
          setError(reason.message || '查询处理进度失败');
          setSubmitting(false);
        }
      }
    };
    poll();
    return () => {
      controller.abort();
      if (timer) window.clearTimeout(timer);
    };
  }, [taskId, onClose, onUploaded]);

  const visibleSteps = matchLocal
    ? IMPORT_STEPS
    : IMPORT_STEPS.filter((step) => !['chunk', 'embed', 'match', 'graph'].includes(step.key));

  const submit = async (event) => {
    event.preventDefault();
    if (!file) { setError('请选择教材 PDF'); return; }
    if (creatingCategory && !newCategory.trim()) { setError('请输入新类别名称'); return; }
    if (file.size > 200 * 1024 * 1024 && !confirmLarge) { setConfirmLarge(true); return; }
    setSubmitting(true); setError('');
    const body = new FormData();
    body.append('file', file);
    body.append('title', title.trim());
    body.append('description', description.trim());
    body.append('category', category);
    body.append('new_category', creatingCategory ? newCategory.trim() : '');
    body.append('match_local', matchLocal ? 'true' : 'false');
    if (confirmLarge) body.append('allow_large', 'true');
    if (cover) body.append('cover', cover);
    try {
      const payload = await uploadTextbook(body);
      setTaskId(payload.task_id);
      setProgress({
        status: 'running',
        step: payload.step || 'upload',
        step_label: payload.step_label || '已接收文件，准备处理',
      });
    } catch (reason) {
      setError(reason.code === 'TEXTBOOK_TOO_LARGE'
        ? '教材超过 200MB 大小限制，未上传。'
        : reason.code === 'TEXTBOOK_TOC_EXTRACTION_FAILED'
          ? '目录未提取成功：该 PDF 未包含可确认的目录页。'
          : reason.message || '教材上传失败');
      setSubmitting(false);
    }
  };

  return (
    <div className="textbook-upload-dialog" role="dialog" aria-modal="true" aria-labelledby="textbook-upload-title">
      <button className="textbook-upload-dialog__backdrop" type="button" aria-label="关闭上传窗口" onClick={submitting ? undefined : onClose} />
      <form className="textbook-upload-dialog__panel" onSubmit={submit}>
        <header>
          <div><span><FileUp size={18} /></span><div><h2 id="textbook-upload-title">上传教材</h2><p>目录由多模态模型识别，正文由 MinerU 处理</p></div></div>
          <button type="button" aria-label="关闭" disabled={submitting} onClick={onClose}><X size={18} /></button>
        </header>
        <div className="textbook-upload-dialog__fields">
          <label className="textbook-upload-dialog__file">
            <FileUp size={22} aria-hidden="true" />
            <span><strong>{file?.name || '选择教材 PDF'}</strong><small>支持 PDF；超过 200MB 将使用 markitdown 本地解析</small></span>
            <input type="file" accept="application/pdf,.pdf" onChange={(event) => { setFile(event.target.files?.[0] || null); setConfirmLarge(false); }} />
          </label>
          <label className="textbook-upload-dialog__match">
            <input type="checkbox" checked={matchLocal} onChange={(event) => setMatchLocal(event.target.checked)} />
            <span><strong>是否匹配本地数据库（耗时较长）</strong><small>上传后按目录切片、向量化，并匹配现有知识点库与题库</small></span>
          </label>
          <div className="textbook-upload-dialog__row">
            <label><span>教材名称</span><input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="不填则自动识别" /></label>
            <label><span>教材类别</span>
              {creatingCategory ? (
                <input value={newCategory} onChange={(event) => setNewCategory(event.target.value)} placeholder="输入新类别" />
              ) : (
                <select value={category} onChange={(event) => setCategory(event.target.value)}>
                  {categories.map((item) => <option key={item} value={item}>{item}</option>)}
                </select>
              )}
            </label>
          </div>
          <button className="textbook-upload-dialog__category" type="button" onClick={() => setCreatingCategory((current) => !current)}>
            {creatingCategory ? '选择已有类别' : '+ 新建类别'}
          </button>
          <label><span>教材介绍</span><textarea value={description} onChange={(event) => setDescription(event.target.value)} placeholder="不填则根据目录自动生成一行介绍" /></label>
          <label className="textbook-upload-dialog__cover">
            <ImagePlus size={18} aria-hidden="true" />
            <span>{cover?.name || '自定义封面（可选，不上传则取 PDF 第一页）'}</span>
            <input type="file" accept="image/jpeg,image/png,image/webp" onChange={(event) => setCover(event.target.files?.[0] || null)} />
          </label>
        </div>
        {error && <p className="textbook-upload-dialog__error" role="alert">{error}</p>}
        {confirmLarge && !submitting && (
          <div className="textbook-upload-dialog__confirm" role="alertdialog" aria-label="大文件确认">
            <strong>当前教材超过大小限制（200MB），解析质量可能下降，是否继续？</strong>
            <p>继续将使用 markitdown 本地解析，不再经过 MinerU。</p>
            <div className="textbook-upload-dialog__confirm-actions">
              <button type="button" onClick={() => { setConfirmLarge(false); setError('已取消：教材超过 200MB 大小限制'); }}>不继续</button>
              <button type="button" onClick={submit}>继续上传</button>
            </div>
          </div>
        )}
        {submitting && taskId && (
          <div className="textbook-upload-dialog__steps" role="status">
            <p className="textbook-upload-dialog__steps-label">{progress?.step_label || '处理中…'}</p>
            <ol>
              {visibleSteps.map((step) => {
                const currentIndex = visibleSteps.findIndex((item) => item.key === (progress?.step || 'upload'));
                const index = visibleSteps.findIndex((item) => item.key === step.key);
                const stateClass = index < currentIndex ? 'is-done' : index === currentIndex ? 'is-current' : '';
                return (
                  <li key={step.key} className={stateClass}>
                    {index < currentIndex ? <Check size={14} /> : index === currentIndex ? <LoaderCircle className="is-spinning" size={14} /> : <span className="textbook-upload-dialog__step-dot" />}
                    {step.label}
                  </li>
                );
              })}
            </ol>
          </div>
        )}
        <footer><button type="button" disabled={submitting} onClick={onClose}>取消</button><button type="submit" disabled={submitting}>{submitting ? <LoaderCircle className="is-spinning" /> : <Upload size={17} />}开始上传</button></footer>
      </form>
    </div>
  );
}

export default function TextbookLibrary({
  books = [],
  emptyText = '教材正在准备中',
  onOpen,
  remainingCount = 0,
  onExpandAll,
  onUploaded,
  onDelete,
  onToggleHidden,
  progressLoading = false,
  catalogBooks = books,
}) {
  const [query, setQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState('all');
  const [activeSource, setActiveSource] = useState('all');
  const [sourceOpen, setSourceOpen] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const normalizedQuery = query.trim();
  const filterSourceBooks = activeFilter === 'all' && !normalizedQuery && activeSource !== 'hidden' ? books : catalogBooks;
  const filteredBooks = useMemo(() => filterTextbookViewModels(filterSourceBooks, {
    filter: activeFilter,
    source: activeSource,
    query,
  }), [activeFilter, activeSource, filterSourceBooks, query]);
  const counts = useMemo(
    () => textbookFilterCounts(catalogBooks, { progressLoading }),
    [catalogBooks, progressLoading],
  );
  const sourceCounts = useMemo(
    () => textbookSourceCounts(catalogBooks),
    [catalogBooks],
  );
  const activeSourceLabel = SOURCE_OPTIONS.find((option) => option.id === activeSource)?.label || '全部教材';
  const activeFilterLabel = TEXTBOOK_FILTERS.find((filter) => filter.id === activeFilter)?.label || '全部状态';
  const learningCount = counts.learning === null ? '--' : counts.learning;
  const noResultsText = query.trim()
    ? '没有找到匹配的教材'
    : EMPTY_TEXT[activeFilter] || emptyText;

  return (
    <section className="textbook-library" aria-label="教材学习列表">
      <div className="textbook-library__title-row">
        <div className="textbook-library__title-group">
          <div><span>Textbook library</span><h2>教材学习</h2></div>
          <p>共 {counts.all} 本教材，其中 {learningCount} 本学习中</p>
        </div>
        <div className="textbook-library__controls">
          <label className="textbook-library__search">
            <Search aria-hidden="true" size={18} />
            <input aria-label="搜索教材" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索教材" />
          </label>
          <div className="textbook-library__filters" aria-label="教材筛选">
          <div className="textbook-library__source-menu">
            <button
              type="button"
              className={`textbook-library__source-trigger${activeSource !== 'all' ? ' is-active' : ''}`}
              aria-expanded={sourceOpen}
              aria-haspopup="menu"
              onClick={() => { setSourceOpen((current) => !current); setFilterOpen(false); }}
            >
              {activeSourceLabel}<span>{sourceCounts[activeSource]}</span><ChevronDown aria-hidden="true" size={14} />
            </button>
            {sourceOpen && (
              <div className="textbook-library__source-menu__dropdown" role="menu" aria-label="教材来源筛选">
                {SOURCE_OPTIONS.map((option) => (
                  <button
                    key={option.id}
                    type="button"
                    role="menuitemradio"
                    aria-checked={activeSource === option.id}
                    className={activeSource === option.id ? 'is-active' : ''}
                    onClick={() => { setActiveSource(option.id); setSourceOpen(false); }}
                  >
                    {option.label}<span>{sourceCounts[option.id]}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="textbook-library__source-menu">
            <button
              type="button"
              className={`textbook-library__source-trigger${activeFilter !== 'all' ? ' is-active' : ''}`}
              aria-expanded={filterOpen}
              aria-haspopup="menu"
              onClick={() => { setFilterOpen((current) => !current); setSourceOpen(false); }}
            >
              {activeFilterLabel}<span>{counts[activeFilter] === null ? '--' : counts[activeFilter]}</span><ChevronDown aria-hidden="true" size={14} />
            </button>
            {filterOpen && (
              <div className="textbook-library__source-menu__dropdown" role="menu" aria-label="教材状态筛选">
                {TEXTBOOK_FILTERS.map((filter) => (
                  <button
                    key={filter.id}
                    type="button"
                    role="menuitemradio"
                    aria-checked={activeFilter === filter.id}
                    className={activeFilter === filter.id ? 'is-active' : ''}
                    onClick={() => { setActiveFilter(filter.id); setFilterOpen(false); }}
                  >
                    {filter.label}<span>{counts[filter.id] === null ? '--' : counts[filter.id]}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
        </div>
        </div>
        <div className="textbook-library__title-actions">
          {onUploaded && (
            <button type="button" className="textbook-library__upload-button" onClick={() => setUploadOpen(true)}>
              <Upload size={15} />上传教材
            </button>
          )}
        </div>
      </div>

      {filteredBooks.length ? (
        <div className="textbook-library__list">
          {filteredBooks.map((book) => {
            const percentage = book.progressAvailable ? Math.round(book.progress * 100) : null;
            const descriptionId = `textbook-description-${String(book.id).replace(/[^\w-]/g, '-')}`;
            return (
              <article key={book.id} className={`textbook-library-card${book.isCurrent ? ' is-current' : ''}`}>
                <span className="textbook-library-card__cover">
                  <img src={book.coverUrl} alt="" loading="lazy" />
                </span>
                <div className="textbook-library-card__body">
                  <div className="textbook-library-card__badges">
                    <span>{book.categoryLabel}</span>
                    {book.isCurrent && <strong>当前在学</strong>}
                    {book.origin === 'user_upload' && (
                      <span className="textbook-library-card__actions">
                        {onToggleHidden && (
                          <button
                            type="button"
                            className="textbook-library-card__action"
                            aria-label={book.isHidden ? `恢复显示《${book.name}》` : `隐藏《${book.name}》`}
                            title={book.isHidden ? '恢复显示此教材' : '隐藏此教材'}
                            onClick={() => onToggleHidden(book)}
                          >
                            <EyeOff aria-hidden="true" size={14} />{book.isHidden ? '恢复显示' : '隐藏'}
                          </button>
                        )}
                        {onDelete && (
                          <button
                            type="button"
                            className="textbook-library-card__action is-danger"
                            aria-label={`删除《${book.name}》`}
                            title="删除此教材"
                            onClick={() => onDelete(book)}
                          >
                            <X aria-hidden="true" size={14} />删除
                          </button>
                        )}
                      </span>
                    )}
                  </div>
                  <h3>《{book.name}》</h3>
                  <p className="textbook-library-card__facts">
                    <span>{book.chapterCount} 章</span><i aria-hidden="true">·</i><span>{book.knowledgePointCount} 个知识点</span>
                  </p>
                  <div className="textbook-library-card__progress-row">
                    <span>{progressSummary(book)}</span>
                    <div
                      className="textbook-library-card__progress"
                      role={percentage === null ? undefined : 'progressbar'}
                      aria-label={`${book.name}学习进度`}
                      aria-valuemin={percentage === null ? undefined : 0}
                      aria-valuemax={percentage === null ? undefined : 100}
                      aria-valuenow={percentage ?? undefined}
                    ><i style={{ width: `${percentage || 0}%` }} /></div>
                    <strong>{percentage === null ? '进度待统计' : `${percentage}%`}</strong>
                  </div>
                  <button type="button" onClick={() => onOpen?.(book)} aria-label={`继续学习《${book.name}》`} aria-describedby={descriptionId}>
                    <BookOpen aria-hidden="true" size={15} />继续学习<ArrowRight aria-hidden="true" size={15} />
                  </button>
                  <div id={descriptionId} className="textbook-library-card__description">
                    <p>{book.description}</p>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      ) : <div className="textbook-library__empty">{noResultsText}</div>}
      {remainingCount > 0 && activeFilter === 'all' && !normalizedQuery && (
        <div className="textbook-library__expand">
          <button type="button" onClick={onExpandAll}>展开全部教材</button>
          <span>还可查看 {remainingCount} 本教材</span>
        </div>
      )}
      {uploadOpen && <TextbookUploadDialog onClose={() => setUploadOpen(false)} onUploaded={onUploaded} />}
    </section>
  );
}
