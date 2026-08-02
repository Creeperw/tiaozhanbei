import React, { useEffect, useMemo, useState } from 'react';
import { ArrowRight, BookOpen, ChevronDown, FileUp, ImagePlus, LoaderCircle, Search, Upload, X } from 'lucide-react';
import {
  filterTextbookViewModels,
  TEXTBOOK_FILTERS,
  textbookFilterCounts,
  textbookSourceCounts,
} from './textbookLibraryModel';
import { loadTextbookCategories, uploadTextbook } from './textbookPdfApi';
import './textbookLibrary.css';

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
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    loadTextbookCategories({ signal: controller.signal })
      .then((payload) => setCategories(payload.items?.length ? payload.items : ['中医药']))
      .catch(() => {});
    return () => controller.abort();
  }, []);

  const submit = async (event) => {
    event.preventDefault();
    if (!file) { setError('请选择教材 PDF'); return; }
    if (creatingCategory && !newCategory.trim()) { setError('请输入新类别名称'); return; }
    setSubmitting(true); setError('');
    const body = new FormData();
    body.append('file', file);
    body.append('title', title.trim());
    body.append('description', description.trim());
    body.append('category', category);
    body.append('new_category', creatingCategory ? newCategory.trim() : '');
    if (cover) body.append('cover', cover);
    try {
      const payload = await uploadTextbook(body);
      onUploaded?.(payload.book);
      onClose();
    } catch (reason) {
      setError(reason.code === 'TEXTBOOK_TOC_EXTRACTION_FAILED'
        ? '目录未提取成功：该 PDF 未包含可确认的目录页。'
        : reason.message || '教材上传失败');
    } finally {
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
            <span><strong>{file?.name || '选择教材 PDF'}</strong><small>支持 PDF，最大 512 MB</small></span>
            <input type="file" accept="application/pdf,.pdf" onChange={(event) => setFile(event.target.files?.[0] || null)} />
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
        {submitting && <div className="textbook-upload-dialog__progress" role="status"><LoaderCircle className="is-spinning" />正在识别目录并处理教材，完整教材可能需要数分钟，请勿关闭页面。</div>}
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
  progressLoading = false,
  catalogBooks = books,
}) {
  const [query, setQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState('all');
  const [activeSource, setActiveSource] = useState('all');
  const [sourceOpen, setSourceOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const normalizedQuery = query.trim();
  const filterSourceBooks = activeFilter === 'all' && !normalizedQuery ? books : catalogBooks;
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
  const learningCount = counts.learning === null ? '--' : counts.learning;
  const noResultsText = query.trim()
    ? '没有找到匹配的教材'
    : EMPTY_TEXT[activeFilter] || emptyText;

  return (
    <section className="textbook-library" aria-label="教材学习列表">
      <div className="textbook-library__title-row">
        <div><span>Textbook library</span><h2>教材学习</h2></div>
        <label className="textbook-library__search">
          <Search aria-hidden="true" size={18} />
          <input aria-label="搜索教材" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索教材" />
        </label>
        <div className="textbook-library__filters" aria-label="教材状态筛选">
          <div className="textbook-library__source-menu">
            <button
              type="button"
              className={`textbook-library__source-trigger${activeSource !== 'all' ? ' is-active' : ''}`}
              aria-expanded={sourceOpen}
              aria-haspopup="menu"
              onClick={() => setSourceOpen((current) => !current)}
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
          {TEXTBOOK_FILTERS.filter((filter) => filter.id !== 'all').map((filter) => {
            const count = counts[filter.id];
            return (
              <button
                key={filter.id}
                type="button"
                className={activeFilter === filter.id ? 'is-active' : ''}
                aria-pressed={activeFilter === filter.id}
                onClick={() => setActiveFilter(filter.id)}
              >
                {filter.label}<span>{count === null ? '--' : count}</span>
              </button>
            );
          })}
        </div>
        <div className="textbook-library__title-actions">
          <p>共 {counts.all} 本教材，其中 {learningCount} 本学习中</p>
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
