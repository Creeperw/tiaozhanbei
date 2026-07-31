import React, { useEffect, useMemo, useState } from 'react';
import { ArrowRight, FileUp, ImagePlus, Layers3, LoaderCircle, Search, Upload, X } from 'lucide-react';
import { textbookCoverUrl, textbookIntroduction } from './textbookMetadata';
import { textbookPlanningLabel } from './textbookPlanning';
import { loadTextbookCategories, uploadTextbook } from './textbookPdfApi';
import './textbookLibrary.css';

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
}) {
  const [query, setQuery] = useState('');
  const [uploadOpen, setUploadOpen] = useState(false);
  const normalizedQuery = query.trim().toLowerCase();
  const filteredBooks = useMemo(() => {
    if (!normalizedQuery) return books;
    return books.filter((item) => {
      const name = String(item.book || item.navigation?.book || item.title || '').toLowerCase();
      const description = String(item.description || item.stage_title || '').toLowerCase();
      return name.includes(normalizedQuery) || description.includes(normalizedQuery);
    });
  }, [books, normalizedQuery]);
  return (
    <section className="textbook-library" aria-label="教材学习列表">
      <div className="textbook-library__heading">
        <div><span>Textbook library</span><h2>教材学习</h2></div>
        <label className="textbook-library__search">
          <Search aria-hidden="true" size={16} />
          <input aria-label="搜索教材" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索教材" />
        </label>
        <div className="textbook-library__heading-action"><p>选择一本教材，按章节、小节和知识点循序学习。</p><button type="button" onClick={() => setUploadOpen(true)}><Upload size={16} />上传</button></div>
      </div>
      {filteredBooks.length ? (
        <div className="textbook-library__list">
          {filteredBooks.map((item) => {
            const name = String(item.book || item.navigation?.book || item.title || '').replace(/[《》]/g, '').trim();
            return (
              <button key={item.id || item.node_id || `${item.route_id}:${name}`} type="button" className="textbook-library-card" onClick={() => onOpen?.(item)} aria-label={`学习《${name}》`}>
                <span className="textbook-library-card__cover">
                  <img src={item.cover_url || textbookCoverUrl(name)} alt="" loading="lazy" />
                </span>
                <span className="textbook-library-card__body">
                  <small>{textbookPlanningLabel(name) || item.stage_title || '专业教材'}</small>
                  <strong>《{name}》</strong>
                  <span>{item.description || textbookIntroduction(name)}</span>
                  <em><Layers3 aria-hidden="true" size={14} />进入章节学习<ArrowRight aria-hidden="true" size={15} /></em>
                </span>
              </button>
            );
          })}
        </div>
      ) : <div className="textbook-library__empty">{query ? '没有找到匹配的教材' : emptyText}</div>}
      {remainingCount > 0 && (
        <div className="textbook-library__expand">
          <button type="button" onClick={onExpandAll}>展开所有教材</button>
          <span>还可查看 {remainingCount} 本教材</span>
        </div>
      )}
      {uploadOpen && <TextbookUploadDialog onClose={() => setUploadOpen(false)} onUploaded={onUploaded} />}
    </section>
  );
}
