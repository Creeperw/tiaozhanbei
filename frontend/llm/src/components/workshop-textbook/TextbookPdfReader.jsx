import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bookmark,
  BookOpenText,
  ChevronLeft,
  ChevronRight,
  Eraser,
  Highlighter,
  List,
  LoaderCircle,
  Maximize2,
  PenLine,
  Redo2,
  Type,
  Undo2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist';
import pdfWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import TextbookPageNotePopover from './TextbookPageNotePopover';
import { loadAtlasNodes } from '../knowledge-atlas/knowledgeAtlasApi';
import {
  createFavoriteFolder,
  deleteFavorite,
  loadFavoriteFolders,
  loadFavorites,
  saveFavorite,
} from '../workshopLibraryApi';
import {
  loadPdfAnnotations,
  loadPdfReadingState,
  resolveTextbookPdf,
  savePdfAnnotations,
  savePdfReadingState,
} from './textbookPdfApi';
import './textbookPdfReader.css';

GlobalWorkerOptions.workerSrc = pdfWorker;

const PAGE_FAVORITES = '教材页收藏';
const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
const annotationId = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`;

function AnnotationLayer({ annotations, tool, onChange }) {
  const svgRef = useRef(null);
  const drawingRef = useRef(null);

  const pointFor = (event) => {
    const bounds = svgRef.current.getBoundingClientRect();
    return {
      x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1),
      y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1),
    };
  };

  const start = (event) => {
    if (tool === 'text') {
      const value = window.prompt('输入页内批注');
      if (value?.trim()) onChange([...annotations, { id: annotationId(), type: 'text', point: pointFor(event), text: value.trim() }]);
      return;
    }
    if (tool === 'eraser') {
      const point = pointFor(event);
      let nearest = null;
      let distance = Number.POSITIVE_INFINITY;
      annotations.forEach((item) => {
        const candidates = item.points || (item.point ? [item.point] : []);
        candidates.forEach((candidate) => {
          const nextDistance = Math.hypot(candidate.x - point.x, candidate.y - point.y);
          if (nextDistance < distance) { distance = nextDistance; nearest = item.id; }
        });
      });
      if (nearest && distance < 0.08) onChange(annotations.filter((item) => item.id !== nearest));
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    const item = {
      id: annotationId(),
      type: tool,
      color: tool === 'highlighter' ? '#f3d66b' : '#19845f',
      points: [pointFor(event)],
    };
    drawingRef.current = item;
    onChange([...annotations, item], { history: false });
  };

  const move = (event) => {
    if (!drawingRef.current) return;
    drawingRef.current = { ...drawingRef.current, points: [...drawingRef.current.points, pointFor(event)] };
    onChange(annotations.map((item) => item.id === drawingRef.current.id ? drawingRef.current : item), { history: false });
  };

  const end = () => {
    if (!drawingRef.current) return;
    const completed = drawingRef.current;
    const nextAnnotations = annotations.map((item) => item.id === completed.id ? completed : item);
    drawingRef.current = null;
    onChange(nextAnnotations, { history: true, force: true });
  };

  return (
    <svg
      ref={svgRef}
      className={`textbook-pdf__annotations tool-${tool}`}
      viewBox="0 0 1000 1000"
      preserveAspectRatio="none"
      onPointerDown={start}
      onPointerMove={move}
      onPointerUp={end}
      onPointerCancel={end}
    >
      {annotations.map((item) => item.type === 'text' ? (
        <text key={item.id} x={item.point.x * 1000} y={item.point.y * 1000} fill="#17684d" fontSize="24" fontWeight="700">{item.text}</text>
      ) : (
        <polyline
          key={item.id}
          points={(item.points || []).map((point) => `${point.x * 1000},${point.y * 1000}`).join(' ')}
          fill="none"
          stroke={item.color || '#19845f'}
          strokeWidth={item.type === 'highlighter' ? 22 : 4}
          strokeOpacity={item.type === 'highlighter' ? 0.36 : 0.9}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      ))}
    </svg>
  );
}

export default function TextbookPdfReader({ bookTitle, initialPage = 1, route, notesOpen = false, onNotesOpenChange, onClose }) {
  const hostRef = useRef(null);
  const canvasRef = useRef(null);
  const annotationSaveTimer = useRef(null);
  const readingSaveTimer = useRef(null);
  const loadedPageRef = useRef(false);
  const [book, setBook] = useState(null);
  const [pdf, setPdf] = useState(null);
  const [pageNumber, setPageNumber] = useState(Math.max(1, Number(initialPage) || 1));
  const [pageCount, setPageCount] = useState(0);
  const [zoom, setZoom] = useState(1);
  const [hostWidth, setHostWidth] = useState(900);
  const [annotations, setAnnotations] = useState([]);
  const [history, setHistory] = useState([[]]);
  const [historyIndex, setHistoryIndex] = useState(0);
  const [tool, setTool] = useState('pen');
  const [favorite, setFavorite] = useState(null);
  const [favoritePending, setFavoritePending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState('');
  const [outline, setOutline] = useState([]);          // 统一目录：[{id, title, page, children}]
  const [outlineOpen, setOutlineOpen] = useState(false);
  const [outlineLoading, setOutlineLoading] = useState(false);
  const [outlineSource, setOutlineSource] = useState(''); // 'pdf' | 'atlas'
  const [expandedChapters, setExpandedChapters] = useState(new Set());

  useEffect(() => {
    const observer = new ResizeObserver(([entry]) => setHostWidth(entry.contentRect.width));
    if (hostRef.current) observer.observe(hostRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    let documentTask;
    setLoading(true); setError(''); setBook(null); setPdf(null);
    setOutline([]); setOutlineSource(''); setOutlineLoading(false); setExpandedChapters(new Set());
    resolveTextbookPdf(bookTitle, { signal: controller.signal }).then(async (payload) => {
      if (!payload.book) { setLoading(false); return; }
      setBook(payload.book);
      if (!payload.available) return;
      const state = await loadPdfReadingState(payload.book.book_id, { signal: controller.signal });
      if (!initialPage || Number(initialPage) <= 1) setPageNumber(Math.max(1, Number(state.page_number) || 1));
      setZoom(clamp(Number(state.zoom) || 1, 0.5, 2.5));
      documentTask = getDocument({ url: payload.book.file_url, withCredentials: true });
      const document = await documentTask.promise;
      if (controller.signal.aborted) return;
      setPdf(document); setPageCount(document.numPages); setPageNumber((current) => clamp(current, 1, document.numPages));
      // 优先提取 PDF 内嵌书签作为目录
      try {
        const rawOutline = await document.getOutline();
        if (!controller.signal.aborted && Array.isArray(rawOutline) && rawOutline.length > 0) {
          const resolvePage = async (item) => {
            try {
              if (!item.dest) return null;
              // dest 可能是字符串、数组 [ref, {name}, left, top] 或数字
              let ref = item.dest;
              if (Array.isArray(item.dest)) ref = item.dest[0]; // 提取 page ref
              if (typeof ref === 'number') return ref + 1;
              if (ref && typeof ref === 'object') {
                const idx = await document.getPageIndex(ref);
                return idx + 1;
              }
              if (typeof ref === 'string') {
                const idx = await document.getPageIndex(ref);
                return idx + 1;
              }
            } catch (_) {}
            return null;
          };
          const tree = [];
          for (const item of rawOutline) {
            const page = await resolvePage(item);
            const node = { id: `pdf_${tree.length}`, title: item.title || '', page };
            if (Array.isArray(item.items) && item.items.length > 0) {
              node.children = [];
              for (const sub of item.items) {
                const subPage = await resolvePage(sub);
                node.children.push({ id: `pdf_${tree.length}_${node.children.length}`, title: sub.title || '', page: subPage });
              }
            }
            tree.push(node);
          }
          if (!controller.signal.aborted) {
            setOutline(tree); setOutlineSource('pdf'); setOutlineLoading(false);
          }
        }
      } catch (_) { /* no PDF outline */ }

      const [foldersPayload, favoritesPayload] = await Promise.all([loadFavoriteFolders(), loadFavorites()]);
      const resourceId = `${payload.book.book_id}:page:${Math.max(1, Number(initialPage) || Number(state.page_number) || 1)}`;
      setFavorite((favoritesPayload.items || []).find((item) => item.resource_type === 'textbook_pdf_page' && item.resource_id === resourceId) || null);
      if (!(foldersPayload.items || []).some((item) => item.name === PAGE_FAVORITES)) {
        await createFavoriteFolder(PAGE_FAVORITES);
      }
    }).catch((reason) => {
      if (reason.name !== 'AbortError') setError(reason.message || '电子教材加载失败');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); documentTask?.destroy?.(); };
  }, [bookTitle, initialPage]);

  // 加载 Atlas 章节目录（仅当 PDF 无内嵌书签时作为回退）
  useEffect(() => {
    if (!bookTitle) return;
    if (outlineSource === 'pdf') return; // PDF 已有书签，不需要 Atlas
    const controller = new AbortController();
    setOutlineLoading(true);
    setOutline([]);
    const load = async () => {
      try {
        const chPayload = await loadAtlasNodes({
          level: 2, route: 'textbook_14_5', lv1: bookTitle, signal: controller.signal,
        });
        const chapters = Array.isArray(chPayload.nodes) ? chPayload.nodes : [];
        const totalChapters = chapters.length;
        const tree = [];
        for (let ci = 0; ci < totalChapters; ci++) {
          const chapter = chapters[ci];
          const sections = [];
          try {
            const secPayload = await loadAtlasNodes({
              level: 3, route: 'textbook_14_5', lv1: bookTitle,
              chapter: chapter.name, chapterId: chapter.id, signal: controller.signal,
            });
            const secNodes = Array.isArray(secPayload.nodes) ? secPayload.nodes : [];
            sections.push(...secNodes.map((s) => ({ id: s.id, title: s.name })));
          } catch (_) {}
          // 按章节序号均匀估算页码（精确页码需要 PDF 内嵌书签）
          const page = pdf ? Math.max(1, Math.round((ci / Math.max(1, totalChapters)) * pdf.numPages) + 1) : null;
          const node = { id: chapter.id, title: chapter.name, page };
          if (sections.length > 0) {
            const chPageStart = page || 1;
            const chNext = ci + 1 < totalChapters
              ? Math.max(1, Math.round(((ci + 1) / Math.max(1, totalChapters)) * (pdf?.numPages || 100)) + 1)
              : (pdf?.numPages || 100);
            const span = Math.max(1, chNext - chPageStart);
            node.children = sections.map((s, si) => ({
              id: s.id,
              title: s.title,
              page: chPageStart + Math.round((si / Math.max(1, sections.length)) * span),
            }));
          }
          tree.push(node);
        }
        if (!controller.signal.aborted) {
          setOutline(tree);
          setOutlineSource('atlas');
          setOutlineLoading(false);
        }
      } catch (err) {
        if (err.name !== 'AbortError') setOutlineLoading(false);
      }
    };
    load();
    return () => controller.abort();
  }, [bookTitle, outlineSource, pdf]);

  useEffect(() => {
    if (!pdf || !canvasRef.current) return undefined;
    let cancelled = false;
    let renderTask;
    setRendering(true);
    pdf.getPage(pageNumber).then((page) => {
      if (cancelled) return;
      const natural = page.getViewport({ scale: 1 });
      const fitScale = Math.max(0.2, (hostWidth - 36) / natural.width);
      const viewport = page.getViewport({ scale: fitScale * zoom });
      const canvas = canvasRef.current;
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.floor(viewport.width * ratio);
      canvas.height = Math.floor(viewport.height * ratio);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      const context = canvas.getContext('2d');
      renderTask = page.render({ canvasContext: context, viewport, transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0] });
      return renderTask.promise;
    }).catch((reason) => {
      if (reason?.name !== 'RenderingCancelledException') setError(reason.message || '页面渲染失败');
    }).finally(() => { if (!cancelled) setRendering(false); });
    return () => { cancelled = true; renderTask?.cancel?.(); };
  }, [pdf, pageNumber, zoom, hostWidth, loading]);

  useEffect(() => {
    if (!book?.available) return undefined;
    const controller = new AbortController();
    loadedPageRef.current = false;
    loadPdfAnnotations(book.book_id, pageNumber, { signal: controller.signal }).then((payload) => {
      const items = Array.isArray(payload.annotations) ? payload.annotations : [];
      setAnnotations(items); setHistory([items]); setHistoryIndex(0); loadedPageRef.current = true;
    }).catch((reason) => { if (reason.name !== 'AbortError') setError(reason.message || '批注加载失败'); });
    loadFavorites().then((payload) => {
      const resourceId = `${book.book_id}:page:${pageNumber}`;
      setFavorite((payload.items || []).find((item) => item.resource_type === 'textbook_pdf_page' && item.resource_id === resourceId) || null);
    }).catch(() => {});
    return () => controller.abort();
  }, [book, pageNumber]);

  useEffect(() => {
    if (!book?.available) return undefined;
    clearTimeout(readingSaveTimer.current);
    readingSaveTimer.current = setTimeout(() => savePdfReadingState(book.book_id, pageNumber, zoom).catch(() => {}), 450);
    return () => clearTimeout(readingSaveTimer.current);
  }, [book, pageNumber, zoom]);

  const updateAnnotations = useCallback((next, options = {}) => {
    setAnnotations(next);
    if (options.history) {
      setHistory((current) => [...current.slice(0, historyIndex + 1), next]);
      setHistoryIndex((current) => current + 1);
    }
    if (loadedPageRef.current) {
      clearTimeout(annotationSaveTimer.current);
      annotationSaveTimer.current = setTimeout(() => savePdfAnnotations(book.book_id, pageNumber, next).catch(() => {}), 500);
    }
  }, [book, historyIndex, pageNumber]);

  const undo = () => {
    if (historyIndex <= 0) return;
    const nextIndex = historyIndex - 1; setHistoryIndex(nextIndex); updateAnnotations(history[nextIndex]);
  };
  const redo = () => {
    if (historyIndex >= history.length - 1) return;
    const nextIndex = historyIndex + 1; setHistoryIndex(nextIndex); updateAnnotations(history[nextIndex]);
  };

  const toggleFavorite = async () => {
    if (!book || favoritePending) return;
    setFavoritePending(true);
    setError('');
    try {
      if (favorite) { await deleteFavorite(favorite.favorite_id); setFavorite(null); return; }
      let folders = (await loadFavoriteFolders()).items || [];
      let folder = folders.find((item) => item.name === PAGE_FAVORITES);
      if (!folder) folder = (await createFavoriteFolder(PAGE_FAVORITES)).folder;
      const payload = await saveFavorite({
        folder_id: folder.folder_id,
        resource_type: 'textbook_pdf_page',
        resource_id: `${book.book_id}:page:${pageNumber}`,
        title: `《${book.title}》第 ${pageNumber} 页`,
        content: { book_id: book.book_id, book_title: book.title, edition: book.edition, route, pdf_page: pageNumber },
        source: '教学资源',
      });
      setFavorite(payload.favorite);
    } catch (reason) {
      setError(reason.message || '教材页收藏操作失败');
    } finally {
      setFavoritePending(false);
    }
  };

  const tools = useMemo(() => [
    ['pen', '画笔', PenLine], ['highlighter', '荧光笔', Highlighter], ['text', '文字', Type], ['eraser', '橡皮擦', Eraser],
  ], []);

  if (loading) return <div className="textbook-pdf__state" role="status"><LoaderCircle className="is-spinning" />正在准备电子教材…</div>;
  if (!book) return <div className="textbook-pdf__state"><BookFallback /><h2>暂无电子教材</h2><p>该教材未收录在电子教材索引中。</p><button type="button" onClick={onClose}>返回课程目录</button></div>;
  if (!book.available) return <div className="textbook-pdf__state"><BookFallback /><h2>电子教材文件未部署</h2><p>《{book.title}》已在教材索引中，但服务端尚未提供对应 PDF 文件。</p><button type="button" onClick={onClose}>返回课程目录</button></div>;
  if (error && !pdf) return <div className="textbook-pdf__state" role="alert"><h2>电子教材加载失败</h2><p>{error}</p><button type="button" onClick={onClose}>返回课程目录</button></div>;

  return (
    <section className="textbook-pdf" aria-label={`${book.title}电子教材`}>
      <header className="textbook-pdf__toolbar">
        <div className="textbook-pdf__toolbar-group">
          <button type="button" onClick={onClose}><ChevronLeft size={16} />课程目录</button>
          <strong>《{book.title}》</strong><span>{book.edition}</span>
        </div>
        <div className="textbook-pdf__toolbar-group textbook-pdf__paging">
          <button type="button" aria-label="上一页" disabled={pageNumber <= 1} onClick={() => setPageNumber((current) => current - 1)}><ChevronLeft size={17} /></button>
          <label><input aria-label="当前页码" type="number" min="1" max={pageCount} value={pageNumber} onChange={(event) => setPageNumber(clamp(Number(event.target.value) || 1, 1, pageCount))} /> / {pageCount}</label>
          <button type="button" aria-label="下一页" disabled={pageNumber >= pageCount} onClick={() => setPageNumber((current) => current + 1)}><ChevronRight size={17} /></button>
        </div>
        <div className="textbook-pdf__toolbar-group">
          <button type="button" aria-label="缩小" onClick={() => setZoom((current) => clamp(current - 0.1, 0.5, 2.5))}><ZoomOut size={16} /></button>
          <span>{Math.round(zoom * 100)}%</span>
          <button type="button" aria-label="放大" onClick={() => setZoom((current) => clamp(current + 0.1, 0.5, 2.5))}><ZoomIn size={16} /></button>
          <button type="button" disabled={favoritePending} className={favorite ? 'is-active' : ''} onClick={toggleFavorite}><Bookmark size={16} fill={favorite ? 'currentColor' : 'none'} />{favorite ? '已收藏' : '收藏本页'}</button>
          <button type="button" className={outlineOpen ? 'is-active' : ''} onClick={() => setOutlineOpen((v) => !v)} title="章节目录"><List size={16} />目录</button>
        </div>
      </header>
      <div className="textbook-pdf__workspace">
        <aside className={`textbook-pdf__sidebar${outlineOpen ? ' has-outline' : ''}`} aria-label="工具与目录">
          <div className="textbook-pdf__draw-tools">
            {tools.map(([value, label, Icon]) => <button key={value} type="button" title={label} aria-label={label} className={tool === value ? 'is-active' : ''} onClick={() => setTool(value)}><Icon size={17} /></button>)}
            <span />
            <button type="button" aria-label="撤销" disabled={historyIndex <= 0} onClick={undo}><Undo2 size={17} /></button>
            <button type="button" aria-label="重做" disabled={historyIndex >= history.length - 1} onClick={redo}><Redo2 size={17} /></button>
            <button type="button" aria-label="适合宽度" onClick={() => setZoom(1)}><Maximize2 size={17} /></button>
          </div>
          <div className="textbook-pdf__outline-section">
            <button
              type="button"
              className="textbook-pdf__outline-toggle"
              onClick={() => setOutlineOpen((v) => !v)}
              aria-expanded={outlineOpen}
              title={outlineSource === 'pdf' ? '目录来源：PDF 书签（精确页码）' : '目录来源：教材章节目录（估算页码）'}
            >
              <BookOpenText size={14} aria-hidden="true" />
              <span>目录</span>
              {outlineSource === 'pdf' && <small>书签</small>}
            </button>
            {outlineOpen && (
              <div className="textbook-pdf__outline-list">
                {outlineLoading ? (
                  <p className="textbook-pdf__outline-loading"><LoaderCircle className="is-spinning" size={14} />加载章节目录…</p>
                ) : outline.length > 0 ? (
                  outline.map((chapter) => {
                    const hasChildren = Array.isArray(chapter.children) && chapter.children.length > 0;
                    const isExpanded = expandedChapters.has(chapter.id);
                    const chPage = chapter.page;
                    const canJump = chPage != null;
                    return (
                      <div key={chapter.id}>
                        <button
                          type="button"
                          className="textbook-pdf__outline-item textbook-pdf__outline-chapter"
                          onClick={() => {
                            if (canJump && !hasChildren) { setPageNumber(chPage); return; }
                            if (canJump) setPageNumber(chPage);
                            setExpandedChapters((prev) => {
                              const next = new Set(prev);
                              next.has(chapter.id) ? next.delete(chapter.id) : next.add(chapter.id);
                              return next;
                            });
                          }}
                          title={canJump ? `跳转到第 ${chPage} 页` : '点击展开小节'}
                        >
                          <span>{chapter.title}</span>
                          {canJump ? <em>第{chPage}页</em> : hasChildren && <em>{chapter.children.length} 节</em>}
                        </button>
                        {isExpanded && hasChildren && chapter.children.map((sub) => {
                          const subPage = sub.page;
                          return (
                            <button
                              key={sub.id}
                              type="button"
                              className={`textbook-pdf__outline-item is-indented${subPage === pageNumber ? ' is-current' : ''}`}
                              onClick={() => subPage != null && setPageNumber(subPage)}
                              disabled={subPage == null}
                              title={subPage != null ? `跳转到第 ${subPage} 页` : sub.title}
                            >
                              <span>{sub.title}</span>
                              {subPage != null && <em>{subPage}</em>}
                            </button>
                          );
                        })}
                      </div>
                    );
                  })
                ) : (
                  <p className="textbook-pdf__outline-empty">暂无章节目录</p>
                )}
              </div>
            )}
          </div>
        </aside>
        <div ref={hostRef} className="textbook-pdf__viewport">
          {rendering && <LoaderCircle className="textbook-pdf__rendering is-spinning" aria-label="正在渲染页面" />}
          <div className="textbook-pdf__page">
            <canvas ref={canvasRef} />
            <AnnotationLayer annotations={annotations} tool={tool} onChange={updateAnnotations} />
          </div>
        </div>
        {notesOpen && <TextbookPageNotePopover book={book} page={pageNumber} route={route} onClose={() => onNotesOpenChange?.(false)} />}
      </div>
      {error && <p className="textbook-pdf__warning" role="alert">{error}</p>}
    </section>
  );
}

function BookFallback() {
  return <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/></svg>;
}
