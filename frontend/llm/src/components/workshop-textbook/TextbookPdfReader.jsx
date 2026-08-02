import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowUpRight,
  Bookmark,
  ChevronLeft,
  ChevronRight,
  Circle,
  Columns3,
  Download,
  Eraser,
  Expand,
  FileDown,
  FlipVertical2,
  Highlighter,
  List,
  LoaderCircle,
  MousePointer2,
  Palette,
  PenLine,
  Redo2,
  Rows3,
  Shrink,
  Slash,
  Sparkles,
  Square,
  Trash2,
  Type,
  Undo2,
  ZoomIn,
  ZoomOut,
} from 'lucide-react';
import { GlobalWorkerOptions, TextLayer, getDocument } from 'pdfjs-dist';
import PdfWorker from '../../lib/pdfWorkerEntry.js?worker';
import TextbookPageNotePopover from './TextbookPageNotePopover';
import PdfAiPanel from './PdfAiPanel';
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

if (typeof Worker !== 'undefined') GlobalWorkerOptions.workerPort = new PdfWorker();

const PAGE_FAVORITES = '教材页收藏';
const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
const annotationId = () => `${Date.now()}-${Math.random().toString(16).slice(2)}`;

const PEN_COLORS = ['#19845f', '#1d6fb8', '#e8842a', '#e0b31c', '#8b5cf6', '#333'];
const HIGHLIGHTER_COLORS = ['#f3d66b', '#f2a96b', '#9dc8f2', '#a9d9a1'];
const SHORTCUTS = {
  prev: '← / PageUp',
  next: '→ / PageDown / 空格',
  outline: 'Ctrl+B',
  ai: 'Ctrl+A',
  fullscreen: 'F',
  zoomIn: 'Ctrl+=',
  zoomOut: 'Ctrl+-',
  zoomReset: 'Ctrl+0',
  undo: 'Ctrl+Z',
  redo: 'Ctrl+Y',
  tool: { select: 'V', pen: 'P', highlighter: 'H', text: 'T', eraser: 'E' },
};

function AnnotationLayer({ annotations, tool, color, onChange }) {
  const svgRef = useRef(null);
  const drawingRef = useRef(null);
  const [textInput, setTextInput] = useState(null); // {point:{x,y}, value}

  const pointFor = (event) => {
    const bounds = svgRef.current.getBoundingClientRect();
    return {
      x: clamp((event.clientX - bounds.left) / bounds.width, 0, 1),
      y: clamp((event.clientY - bounds.top) / bounds.height, 0, 1),
    };
  };

  const commitText = (value) => {
    const trimmed = String(value || '').trim();
    if (textInput) {
      if (trimmed) {
        if (textInput.id) {
          // 编辑已有批注：更新文字
          onChange(annotations.map((item) => (item.id === textInput.id ? { ...item, text: trimmed } : item)));
        } else {
          onChange([...annotations, { id: annotationId(), type: 'text', point: textInput.point, text: trimmed, color }]);
        }
      } else if (textInput.id) {
        // 编辑已有批注且清空：删除该批注
        onChange(annotations.filter((item) => item.id !== textInput.id));
      }
    }
    setTextInput(null);
  };

  // 文字工具下查找点击命中的已有文字批注（返回其 {id, point, text}，未命中返回 null）
  const findTextAnnotationAt = (point) => {
    const svg = svgRef.current;
    if (!svg) return null;
    const x = point.x * 1000;
    const y = point.y * 1000;
    for (const item of annotations) {
      if (item.type !== 'text' || !item.id) continue;
      const el = svg.querySelector(`[data-annotation-id="${item.id}"]`);
      if (!el) continue;
      const bbox = el.getBBox();
      if (x >= bbox.x - 6 && x <= bbox.x + bbox.width + 6 && y >= bbox.y - 6 && y <= bbox.y + bbox.height + 6) {
        return { id: item.id, point: item.point, text: item.text || '' };
      }
    }
    return null;
  };

  const start = (event) => {
    if (tool === 'select') return;
    if (tool === 'text') {
      if (textInput) commitText(textInput.value);
      // 阻止该次点击后续 mousedown 导致的焦点转移（否则刚聚焦的输入框会被 blur 关闭）
      event.preventDefault();
      const point = pointFor(event);
      const existing = findTextAnnotationAt(point);
      setTextInput(existing ? { ...existing, value: existing.text } : { point, value: '' });
      return;
    }
    if (tool === 'eraser') {
      const point = pointFor(event);
      let nearest = null;
      let distance = Number.POSITIVE_INFINITY;
      annotations.forEach((item) => {
        const candidates = item.points || (item.start ? [item.start, item.end] : (item.point ? [item.point] : []));
        candidates.forEach((candidate) => {
          const nextDistance = Math.hypot(candidate.x - point.x, candidate.y - point.y);
          if (nextDistance < distance) { distance = nextDistance; nearest = item.id; }
        });
      });
      if (nearest && distance < 0.08) onChange(annotations.filter((item) => item.id !== nearest));
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    if (['line', 'arrow', 'rect', 'ellipse'].includes(tool)) {
      drawingRef.current = { id: annotationId(), type: tool, color, start: pointFor(event), end: pointFor(event) };
    } else {
      drawingRef.current = { id: annotationId(), type: tool, color, points: [pointFor(event)] };
    }
    onChange([...annotations, drawingRef.current], { history: false });
  };

  const move = (event) => {
    if (!drawingRef.current) return;
    const nextPoint = pointFor(event);
    if (drawingRef.current.type === 'line' || drawingRef.current.type === 'arrow' || drawingRef.current.type === 'rect' || drawingRef.current.type === 'ellipse') {
      drawingRef.current = { ...drawingRef.current, end: nextPoint };
    } else {
      drawingRef.current = { ...drawingRef.current, points: [...drawingRef.current.points, nextPoint] };
    }
    onChange(annotations.map((item) => item.id === drawingRef.current.id ? drawingRef.current : item), { history: false });
  };

  const end = () => {
    if (!drawingRef.current) return;
    const completed = drawingRef.current;
    const nextAnnotations = annotations.map((item) => item.id === completed.id ? completed : item);
    drawingRef.current = null;
    onChange(nextAnnotations, { history: true, force: true });
  };

  // 用原生事件监听绑定指针事件（React 合成事件委托对 trusted pointer 事件不可靠）
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return undefined;
    const onDown = (event) => start(event);
    const onMove = (event) => move(event);
    const onUp = () => end();
    svg.addEventListener('pointerdown', onDown);
    svg.addEventListener('pointermove', onMove);
    svg.addEventListener('pointerup', onUp);
    svg.addEventListener('pointercancel', onUp);
    return () => {
      svg.removeEventListener('pointerdown', onDown);
      svg.removeEventListener('pointermove', onMove);
      svg.removeEventListener('pointerup', onUp);
      svg.removeEventListener('pointercancel', onUp);
    };
  });

  const renderShape = (item) => {
    if (item.type === 'line' || item.type === 'arrow' || item.type === 'rect' || item.type === 'ellipse') {
      const x1 = item.start.x * 1000; const y1 = item.start.y * 1000;
      const x2 = item.end.x * 1000; const y2 = item.end.y * 1000;
      if (item.type === 'line') {
        return <line key={item.id} x1={x1} y1={y1} x2={x2} y2={y2} stroke={item.color || '#19845f'} strokeWidth="4" strokeLinecap="round" />;
      }
      if (item.type === 'arrow') {
        const angle = Math.atan2(y2 - y1, x2 - x1);
        const head = 22;
        return (
          <g key={item.id}>
            <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={item.color || '#19845f'} strokeWidth="4" strokeLinecap="round" />
            <line x1={x2} y1={y2} x2={x2 - head * Math.cos(angle - Math.PI / 7)} y2={y2 - head * Math.sin(angle - Math.PI / 7)} stroke={item.color || '#19845f'} strokeWidth="4" strokeLinecap="round" />
            <line x1={x2} y1={y2} x2={x2 - head * Math.cos(angle + Math.PI / 7)} y2={y2 - head * Math.sin(angle + Math.PI / 7)} stroke={item.color || '#19845f'} strokeWidth="4" strokeLinecap="round" />
          </g>
        );
      }
      if (item.type === 'rect') {
        return <rect key={item.id} x={Math.min(x1, x2)} y={Math.min(y1, y2)} width={Math.abs(x2 - x1)} height={Math.abs(y2 - y1)} fill="none" stroke={item.color || '#19845f'} strokeWidth="4" />;
      }
      return <ellipse key={item.id} cx={(x1 + x2) / 2} cy={(y1 + y2) / 2} rx={Math.abs(x2 - x1) / 2} ry={Math.abs(y2 - y1) / 2} fill="none" stroke={item.color || '#19845f'} strokeWidth="4" />;
    }
    return null;
  };

  return (
    <svg
      ref={svgRef}
      className={`textbook-pdf__annotations tool-${tool}`}
      viewBox="0 0 1000 1000"
      preserveAspectRatio="none"
    >
      {annotations.map((item) => item.type === 'text' ? (
        <text key={item.id} data-annotation-id={item.id} x={item.point.x * 1000} y={item.point.y * 1000} fill={item.color || '#17684d'} fontSize="24" fontWeight="700">{item.text}</text>
      ) : item.type === 'line' || item.type === 'arrow' || item.type === 'rect' || item.type === 'ellipse' ? renderShape(item) : (
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
      {tool === 'text' && textInput && (
        <foreignObject
          x={textInput.point.x * 1000}
          y={textInput.point.y * 1000}
          width="460"
          height="44"
        >
          <div className="textbook-pdf__text-input">
            <input
              autoFocus
              value={textInput.value}
              placeholder="输入页内批注，回车确认"
              onPointerDown={(event) => event.stopPropagation()}
              onKeyDown={(event) => {
                event.stopPropagation();
                if (event.key === 'Enter') commitText(textInput.value);
                if (event.key === 'Escape') setTextInput(null);
              }}
              onChange={(event) => setTextInput({ ...textInput, value: event.target.value })}
              onBlur={() => commitText(textInput.value)}
            />
          </div>
        </foreignObject>
      )}
    </svg>
  );
}

export default function TextbookPdfReader({ bookTitle, bookId = '', toc = [], initialPage = 1, route, notesOpen = false, onNotesOpenChange, onClose }) {
  const hostRef = useRef(null);
  const workspaceRef = useRef(null);
  const canvasRef = useRef(null);
  const textLayerRef = useRef(null);
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
  const [tool, setTool] = useState('select');
  const [penColor, setPenColor] = useState(PEN_COLORS[0]);
  const [highlighterColor, setHighlighterColor] = useState(HIGHLIGHTER_COLORS[0]);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [aiOpen, setAiOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [pageLayout, setPageLayout] = useState('vertical'); // 'single' | 'vertical' | 'horizontal'
  const [layoutMenuOpen, setLayoutMenuOpen] = useState(false);
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

  // Ctrl + 滚轮缩放（仅作用于 PDF 区域）
  useEffect(() => {
    const viewport = hostRef.current;
    if (!viewport) return undefined;
    const handleWheel = (event) => {
      if (!event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      const factor = event.deltaY < 0 ? 0.1 : -0.1;
      setZoom((current) => clamp(current + factor, 0.5, 2.5));
    };
    viewport.addEventListener('wheel', handleWheel, { passive: false });
    return () => viewport.removeEventListener('wheel', handleWheel);
  }, [fullscreen]);

  // 按 Esc / 浏览器退出全屏时同步状态
  useEffect(() => {
    const sync = () => {
      if (!document.fullscreenElement) setFullscreen(false);
    };
    document.addEventListener('fullscreenchange', sync);
    return () => document.removeEventListener('fullscreenchange', sync);
  }, []);

  // 离开电子教材（组件卸载）时若仍在全屏，释放浏览器全屏状态
  useEffect(() => () => {
    if (document.fullscreenElement) document.exitFullscreen?.().catch(() => {});
  }, []);

  // 全局快捷键：翻页/缩放/工具/撤销/全屏/目录/AI
  useEffect(() => {
    const handleKeyDown = (event) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      const key = event.key.toLowerCase();
      const modifier = event.ctrlKey || event.metaKey;
      if (modifier) {
        if (key === 'z' && !event.shiftKey) { event.preventDefault(); undo(); return; }
        if (key === 'y' || (key === 'z' && event.shiftKey)) { event.preventDefault(); redo(); return; }
        if (key === 'b') { event.preventDefault(); setOutlineOpen((v) => !v); return; }
        if (key === 'a') { event.preventDefault(); setAiOpen((v) => !v); return; }
        if (key === '0') { event.preventDefault(); setZoom(1); return; }
        if (key === '=' || key === '+') { event.preventDefault(); setZoom((c) => clamp(c + 0.1, 0.5, 2.5)); return; }
        if (key === '-') { event.preventDefault(); setZoom((c) => clamp(c - 0.1, 0.5, 2.5)); return; }
        return;
      }
      if (event.altKey) return;
      if (key === 'arrowleft' || key === 'pageup') { event.preventDefault(); scrollToPage(pageNumberRef.current - 1); return; }
      if (key === 'arrowright' || key === 'pagedown') { event.preventDefault(); scrollToPage(pageNumberRef.current + 1); return; }
      if (key === ' ') { event.preventDefault(); scrollToPage(pageNumberRef.current + 1); return; }
      if (key === 'f') { event.preventDefault(); toggleFullscreen(); return; }
      const toolMap = { v: 'select', p: 'pen', h: 'highlighter', t: 'text', e: 'eraser' };
      if (toolMap[key]) { setTool(toolMap[key]); }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  });

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
            } catch { /* 页码解析失败按无页码处理 */ }
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
      } catch { /* no PDF outline */ }

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
  }, [bookId, bookTitle, initialPage]);

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
          } catch { /* 小节目录加载失败时仅保留章节条目 */ }
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

  const renderPageInto = (container, page, viewport, ratio) => {
    const canvas = document.createElement('canvas');
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    const context = canvas.getContext('2d');
    const sheet = document.createElement('div');
    sheet.className = 'textbook-pdf__page-sheet';
    const textLayerDiv = document.createElement('div');
    textLayerDiv.className = 'textbook-pdf__text-layer';
    sheet.append(canvas, textLayerDiv);
    container.append(sheet);
    const renderTask = page.render({ canvasContext: context, viewport, transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0] });
    const promise = renderTask.promise.then(() => {
      if (tool !== 'select') return;
      return page.getTextContent().then((textContent) => {
        textLayerDiv.innerHTML = '';
        textLayerDiv.style.width = `${viewport.width}px`;
        textLayerDiv.style.height = `${viewport.height}px`;
        textLayerDiv.style.setProperty('--total-scale-factor', String(viewport.scale));
        const textLayer = new TextLayer({ textContentSource: textContent, container: textLayerDiv, viewport });
        textLayer.render();
        textLayerDiv.dataset.textRendered = String(container.dataset.page);
      });
    }).catch((reason) => {
      if (reason?.name !== 'RenderingCancelledException') setError(reason.message || '页面渲染失败');
    });
    return { promise, cancel: () => renderTask.cancel() };
  };

  // 渲染当前页（单页模式）或当前页 ± 3 页窗口（滚动模式，增量渲染窗口内页面）
  useEffect(() => {
    // eslint-disable-next-line react-hooks/exhaustive-deps
    if (!pdf || !hostRef.current) return undefined;
    let cancelled = false;
    let renderTask;
    let textLayer;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    setRendering(true);

    if (pageLayout === 'single') {
      // 清理滚动模式遗留的页面
      hostRef.current.querySelectorAll('.textbook-pdf__sheet-wrap').forEach((el) => el.remove());
      pdf.getPage(pageNumber).then((page) => {
        if (cancelled || !canvasRef.current) return;
        const natural = page.getViewport({ scale: 1 });
        const fitScale = Math.max(0.2, (hostWidth - 36) / natural.width);
        const viewport = page.getViewport({ scale: fitScale * zoom });
        const canvas = canvasRef.current;
        canvas.width = Math.floor(viewport.width * ratio);
        canvas.height = Math.floor(viewport.height * ratio);
        canvas.style.width = `${viewport.width}px`;
        canvas.style.height = `${viewport.height}px`;
        const context = canvas.getContext('2d');
        renderTask = page.render({ canvasContext: context, viewport, transform: ratio === 1 ? null : [ratio, 0, 0, ratio, 0, 0] });
        return renderTask.promise.then(() => {
          const container = textLayerRef.current;
          if (!container || cancelled) return;
          if (tool !== 'select') {
            container.innerHTML = '';
            return;
          }
          return page.getTextContent().then((textContent) => {
            if (cancelled || tool !== 'select') return;
            container.innerHTML = '';
            container.style.width = `${viewport.width}px`;
            container.style.height = `${viewport.height}px`;
            container.style.setProperty('--total-scale-factor', String(viewport.scale));
            textLayer = new TextLayer({ textContentSource: textContent, container, viewport });
            textLayer.render();
          });
        });
      }).catch((reason) => {
        if (reason?.name !== 'RenderingCancelledException') setError(reason.message || '页面渲染失败');
      }).finally(() => { if (!cancelled) setRendering(false); });
      return () => { cancelled = true; renderTask?.cancel?.(); textLayer?.cancel?.(); };
    }

    // 滚动模式：增量渲染窗口内页面（不重建已有页面，避免渲染风暴）
    const host = hostRef.current;
    const windowStart = Math.max(1, pageNumber - 3);
    const windowEnd = Math.min(pdf.numPages, pageNumber + 3);
    const firstLocate = !renderMetaRef.current.initialized && pendingLocateRef.current == null;
    const zoomChanged = renderMetaRef.current.zoom !== zoom;
    const widthChanged = renderMetaRef.current.hostWidth !== hostWidth;
    if (firstLocate) pendingLocateRef.current = pageNumber;
    if (zoomChanged || widthChanged) {
      // 缩放或宽度变化：全部重建（保留当前页定位，重建完成后 scrollIntoView 恢复）
      renderMetaRef.current.zoom = zoom;
      renderMetaRef.current.hostWidth = hostWidth;
      pendingLocateRef.current = pageNumberRef.current;
      host.innerHTML = '';
      renderMetaRef.current.initialized = true;
      const rebuildPages = [];
      for (let page = windowStart; page <= windowEnd; page++) rebuildPages.push(page);
      const rebuildTasks = rebuildPages.map((targetPage) =>
        pdf.getPage(targetPage).then((page) => {
          if (cancelled) return;
          const natural = page.getViewport({ scale: 1 });
          const fitScale = Math.max(0.2, (hostWidth - 36) / natural.width);
          const viewport = page.getViewport({ scale: fitScale * zoom });
          const container = document.createElement('div');
          container.className = 'textbook-pdf__sheet-wrap';
          container.dataset.page = String(targetPage);
          return renderPageInto(container, page, viewport, ratio).promise.then(() => {
            if (cancelled) return;
            host.append(container);
            host.append(...Array.from(host.querySelectorAll('.textbook-pdf__sheet-wrap'))
              .sort((a, b) => Number(a.dataset.page) - Number(b.dataset.page)));
          });
        }),
      );
      Promise.allSettled(rebuildTasks).then(() => {
        if (cancelled) return;
        setRendering(false);
        positionScrollAnnotation();
        settlePendingLocate(host);
      });
      return () => { cancelled = true; rebuildTasks.forEach((task) => { if (typeof task?.cancel === 'function') task.cancel(); }); };
    }
    const wrapped = Array.from(host.querySelectorAll('.textbook-pdf__sheet-wrap'));
    renderMetaRef.current.initialized = true;
    const existing = new Map(wrapped.map((el) => [Number(el.dataset.page), el]));
    let tasks = [];
    // 移除窗口外的页面
    wrapped.forEach((el) => {
      const page = Number(el.dataset.page);
      if (page < windowStart || page > windowEnd) {
        el.remove();
        existing.delete(page);
      }
    });
    // 按窗口内页码排序补位（乱序时重排，保证视觉顺序）
    const sortedKeys = Array.from(existing.keys()).sort((a, b) => a - b);
    const placed = new Set(sortedKeys);
    const renderWindow = [];
    for (let page = windowStart; page <= windowEnd; page++) {
      if (!placed.has(page)) renderWindow.push(page);
    }
    if (renderWindow.length > 0) {
      // 渲染缺失页面：全部渲染完成后按页码插入正确位置，避免 DOM 乱序；
      // 若新页插入到视口上方（往前翻时），补偿 scrollTop 保持视觉位置不跳
      tasks = renderWindow.map((targetPage) =>
        pdf.getPage(targetPage).then((page) => {
          if (cancelled) return;
          const natural = page.getViewport({ scale: 1 });
          const fitScale = Math.max(0.2, (hostWidth - 36) / natural.width);
          const viewport = page.getViewport({ scale: fitScale * zoom });
          const container = document.createElement('div');
          container.className = 'textbook-pdf__sheet-wrap';
          container.dataset.page = String(targetPage);
          return renderPageInto(container, page, viewport, ratio).promise.then(() => {
            if (cancelled) return;
            const scrollTopBefore = host.scrollTop;
            host.append(container);
            host.append(...Array.from(host.querySelectorAll('.textbook-pdf__sheet-wrap'))
              .sort((a, b) => Number(a.dataset.page) - Number(b.dataset.page)));
            // 新页在视口上方时补偿滚动位置，避免视口内容下移
            if (container.offsetTop + container.offsetHeight <= scrollTopBefore) {
              host.scrollTop = scrollTopBefore + container.offsetHeight + 18;
            }
          });
        }),
      );
    }
    Promise.allSettled(tasks).then(() => {
      if (cancelled) return;
      setRendering(false);
      positionScrollAnnotation();
      settlePendingLocate(host);
    });
    return () => { cancelled = true; tasks.forEach((task) => { if (typeof task?.cancel === 'function') task.cancel(); }); };
  }, [pdf, pageNumber, zoom, hostWidth, loading, pageLayout]);

  // 文本层：仅 select 工具时，渲染滚动窗口内各页文本层
  useEffect(() => {
    if (!pdf || pageLayout === 'single' || !hostRef.current) return undefined;
    let cancelled = false;
    const hosts = [...hostRef.current.querySelectorAll('.textbook-pdf__text-layer')];
    if (tool !== 'select') {
      hosts.forEach((el) => { el.innerHTML = ''; delete el.dataset.textRendered; });
      return undefined;
    }
    const tasks = hosts.map((el, index) => {
      const sheet = el.closest('.textbook-pdf__sheet-wrap');
      const targetPage = Number(sheet?.dataset.page);
      if (!targetPage || el.dataset.textRendered === String(targetPage)) return Promise.resolve();
      el.dataset.textRendered = String(targetPage);
      return pdf.getPage(targetPage).then((page) => {
        if (cancelled) return;
        const canvas = el.previousElementSibling;
        const natural = page.getViewport({ scale: 1 });
        const fitScale = Math.max(0.2, (hostWidth - 36) / natural.width);
        const viewport = page.getViewport({ scale: fitScale * zoom });
        const width = canvas?.offsetWidth || viewport.width;
        const height = canvas?.offsetHeight || viewport.height;
        el.style.width = `${width}px`;
        el.style.height = `${height}px`;
        el.style.setProperty('--total-scale-factor', String(viewport.scale));
        return page.getTextContent().then((textContent) => {
          if (cancelled || el.dataset.textRendered !== String(targetPage)) return;
          el.innerHTML = '';
          const textLayer = new TextLayer({ textContentSource: textContent, container: el, viewport });
          textLayer.render();
        });
      }).catch((reason) => {
        if (reason?.name !== 'RenderingCancelledException') setError(reason.message || '文本层渲染失败');
      });
    });
    const done = Promise.allSettled(tasks);
    return () => { cancelled = true; done.then(() => {}); };
  }, [pdf, pageNumber, zoom, hostWidth, tool, pageLayout]);

  const pageNumberRef = useRef(pageNumber);
  useEffect(() => { pageNumberRef.current = pageNumber; }, [pageNumber]);
  const renderMetaRef = useRef({ zoom: null, hostWidth: null });
  const pendingLocateRef = useRef(null); // 程序化跳页后等待渲染完成再滚动定位
  const scrollAnnotationHolderRef = useRef(null);

  // 滚动模式下把批注层覆盖到当前中心页 sheet 上（用视口坐标差分，滚动时跟随）
  const positionScrollAnnotation = () => {
    const holder = scrollAnnotationHolderRef.current;
    const host = hostRef.current;
    const workspace = workspaceRef.current;
    if (!holder || !host || !workspace) return;
    const sheet = host.querySelector(`.textbook-pdf__sheet-wrap[data-page="${pageNumber}"]`);
    if (!sheet) return;
    const sheetRect = sheet.getBoundingClientRect();
    const workspaceRect = workspace.getBoundingClientRect();
    holder.style.left = `${sheetRect.left - workspaceRect.left}px`;
    holder.style.top = `${sheetRect.top - workspaceRect.top}px`;
    holder.style.width = `${sheetRect.width}px`;
    holder.style.height = `${sheetRect.height}px`;
  };

  // 滚动模式下同步中心页到 pageNumber（loading 依赖：pdf 加载完成后 hostRef 才挂载，需重跑挂监听）
  useEffect(() => {
    if (pageLayout === 'single' || !hostRef.current) return undefined;
    const host = hostRef.current;
    let ticking = false;
    let isSmoothScrolling = false;
    const sync = () => {
      if (ticking) return;
      if (pendingLocateRef.current != null) return; // 程序化定位期间不干预
      ticking = true;
      requestAnimationFrame(() => {
        ticking = false;
        if (isSmoothScrolling) return;
        const sheets = [...host.querySelectorAll('.textbook-pdf__sheet-wrap')];
        if (!sheets.length) return;
        let best = sheets[0];
        let bestDistance = Infinity;
        const hostRect = host.getBoundingClientRect();
        const hostCenter = hostRect.left + hostRect.width / 2;
        const hostMidY = hostRect.top + hostRect.height / 2;
        for (const sheet of sheets) {
          const rect = sheet.getBoundingClientRect();
          const distance = pageLayout === 'horizontal'
            ? Math.abs((rect.left + rect.width / 2) - hostCenter)
            : Math.abs((rect.top + rect.height / 2) - hostMidY);
          if (distance < bestDistance) { bestDistance = distance; best = sheet; }
        }
        const target = Number(best.dataset.page);
        if (target && target !== pageNumberRef.current) {
          pageNumberRef.current = target;
          setPageNumber(target);
        }      });
    };
    const onScroll = () => {
      // 实时同步批注层位置，避免滚动中点击时浮层错位（批注层挂在滚动容器外）
      positionScrollAnnotation();
      sync();
    };
    host.addEventListener('scroll', onScroll, { passive: true });
    sync();
    const positionTimer = setInterval(() => {
      positionScrollAnnotation();
      sync();
    }, 300);
    return () => {
      host.removeEventListener('scroll', onScroll);
      clearInterval(positionTimer);
    };
  }, [pageLayout, hostWidth, pageNumber, loading]);

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

  const clearPageAnnotations = () => {
    if (!annotations.length) return;
    updateAnnotations([], { history: true });
  };

  const toggleFullscreen = () => {
    setFullscreen((current) => {
      const next = !current;
      if (next) {
        // 等渲染后再请求全屏，确保 DOM 已是全屏布局
        requestAnimationFrame(() => {
          document.documentElement.requestFullscreen?.()
            .catch(() => { /* 用户拒绝或环境不支持时保持页面内全屏布局 */ });
        });
      } else if (document.fullscreenElement) {
        document.exitFullscreen?.().catch(() => {});
      }
      return next;
    });
  };

  const scrollToPage = (targetPage) => {
    const target = Math.max(1, Math.min(pageCount, targetPage));
    setPageNumber(target);
    if (pageLayout === 'single') return;
    // 滚动模式：记录目标页，渲染完成后由渲染 effect 瞬时定位（避免平滑滚动与页码同步竞态）
    pendingLocateRef.current = target;
  };

  // 瞬时把目标页滚动到视口（滚动容器直接定位，offsetTop 不受已渲染窗口影响）
  const scrollSheetTo = (targetPage) => {
    const host = hostRef.current;
    if (!host) return false;
    const sheet = host.querySelector(`.textbook-pdf__sheet-wrap[data-page="${targetPage}"]`);
    if (!sheet) return false;
    if (pageLayout === 'horizontal') {
      host.scrollLeft = sheet.offsetLeft - Math.max(0, (host.clientWidth - sheet.offsetWidth) / 2);
    } else {
      host.scrollTop = sheet.offsetTop - Math.max(0, (host.clientHeight - sheet.offsetHeight) / 2);
    }
    return true;
  };

  const settlePendingLocate = (host) => {
    const target = pendingLocateRef.current;
    if (target == null) return;
    pendingLocateRef.current = null;
    if (!scrollSheetTo(target)) return;
    // 定位后立即同步一次中心页，避免滚动事件异步滞后
    requestAnimationFrame(() => {
      const input = document.querySelector('input[aria-label="当前页码"]');
      const sheets = host ? [...host.querySelectorAll('.textbook-pdf__sheet-wrap')] : [];
      const hostRect = host?.getBoundingClientRect();
      if (!hostRect || !sheets.length) return;
      const hostMidY = hostRect.top + hostRect.height / 2;
      let best = null, bestDist = Infinity;
      for (const sheet of sheets) {
        const r = sheet.getBoundingClientRect();
        const d = Math.abs((r.top + r.height / 2) - hostMidY);
        if (d < bestDist) { bestDist = d; best = Number(sheet.dataset.page); }
      }
      if (best && best !== pageNumberRef.current) {
        pageNumberRef.current = best;
        setPageNumber(best);
      }
      if (input) input.value = String(best);
    });
  };

  const exportPageImage = () => {
    const canvas = pageLayout === 'single'
      ? canvasRef.current
      : hostRef.current?.querySelector(`.textbook-pdf__sheet-wrap[data-page="${pageNumber}"] canvas`);
    if (!canvas) return;
    const out = document.createElement('canvas');
    out.width = canvas.width;
    out.height = canvas.height;
    const context = out.getContext('2d');
    context.drawImage(canvas, 0, 0);
    context.save();
    context.setTransform(out.width / 1000, 0, 0, out.height / 1000, 0, 0);
    annotations.forEach((item) => {
      if (item.type === 'text') {
        context.fillStyle = item.color || '#17684d';
        context.font = '700 24px sans-serif';
        context.fillText(item.text, item.point.x * 1000, item.point.y * 1000);
        return;
      }
      if (['line', 'arrow', 'rect', 'ellipse'].includes(item.type)) {
        const x1 = item.start.x * 1000; const y1 = item.start.y * 1000;
        const x2 = item.end.x * 1000; const y2 = item.end.y * 1000;
        context.strokeStyle = item.color || '#19845f';
        context.lineWidth = 4;
        context.beginPath();
        if (item.type === 'line') {
          context.moveTo(x1, y1); context.lineTo(x2, y2);
        } else if (item.type === 'arrow') {
          context.moveTo(x1, y1); context.lineTo(x2, y2); context.stroke();
          const angle = Math.atan2(y2 - y1, x2 - x1);
          const head = 22;
          context.beginPath();
          context.moveTo(x2, y2);
          context.lineTo(x2 - head * Math.cos(angle - Math.PI / 7), y2 - head * Math.sin(angle - Math.PI / 7));
          context.moveTo(x2, y2);
          context.lineTo(x2 - head * Math.cos(angle + Math.PI / 7), y2 - head * Math.sin(angle + Math.PI / 7));
          context.stroke();
          return;
        } else if (item.type === 'rect') {
          context.rect(Math.min(x1, x2), Math.min(y1, y2), Math.abs(x2 - x1), Math.abs(y2 - y1));
        } else {
          context.ellipse((x1 + x2) / 2, (y1 + y2) / 2, Math.abs(x2 - x1) / 2, Math.abs(y2 - y1) / 2, 0, 0, Math.PI * 2);
        }
        context.stroke();
        return;
      }
      const points = item.points || [];
      if (points.length < 2) return;
      context.strokeStyle = item.color || '#19845f';
      context.lineWidth = item.type === 'highlighter' ? 22 : 4;
      context.globalAlpha = item.type === 'highlighter' ? 0.36 : 0.9;
      context.lineCap = 'round';
      context.lineJoin = 'round';
      context.beginPath();
      context.moveTo(points[0].x * 1000, points[0].y * 1000);
      points.slice(1).forEach((point) => context.lineTo(point.x * 1000, point.y * 1000));
      context.stroke();
      context.globalAlpha = 1;
    });
    context.restore();
    const link = document.createElement('a');
    link.download = `${book.title}-第${pageNumber}页.png`;
    link.href = out.toDataURL('image/png');
    link.click();
  };

  const activeColor = tool === 'highlighter' ? highlighterColor : penColor;
  const palette = tool === 'highlighter' ? HIGHLIGHTER_COLORS : PEN_COLORS;

  const tools = useMemo(() => [
    ['select', '选择', MousePointer2],
    ['pen', '画笔', PenLine],
    ['highlighter', '荧光笔', Highlighter],
    ['text', '文字', Type],
    ['eraser', '橡皮擦', Eraser],
  ], []);

  const shapeTools = useMemo(() => [
    ['line', '直线', Slash],
    ['arrow', '箭头', ArrowUpRight],
    ['rect', '矩形', Square],
    ['ellipse', '圆形', Circle],
  ], []);

  if (loading) return <div className="textbook-pdf__state" role="status"><LoaderCircle className="is-spinning" />正在准备电子教材…</div>;
  if (!book) return <div className="textbook-pdf__state"><BookFallback /><h2>暂无电子教材</h2><p>该教材未收录在电子教材索引中。</p><button type="button" onClick={onClose}>返回课程内容</button></div>;
  if (!book.available) return <div className="textbook-pdf__state"><BookFallback /><h2>电子教材文件未部署</h2><p>《{book.title}》已在教材索引中，但服务端尚未提供对应 PDF 文件。</p><button type="button" onClick={onClose}>返回课程内容</button></div>;
  if (error && !pdf) return <div className="textbook-pdf__state" role="alert"><h2>电子教材加载失败</h2><p>{error}</p><button type="button" onClick={onClose}>返回课程内容</button></div>;

  return (
    <section className={`textbook-pdf${fullscreen ? ' is-fullscreen' : ''}`} aria-label={`${book.title}电子教材`}>
      <header className="textbook-pdf__toolbar">
        <div className="textbook-pdf__toolbar-group">
          <button type="button" onClick={onClose}><ChevronLeft size={16} />返回教材目录</button>
          <strong>《{book.title}》</strong><span>{book.edition}</span>
          <span className="textbook-pdf__toolbar-divider" />
          <button type="button" className={outlineOpen ? 'is-active' : ''} onClick={() => setOutlineOpen((v) => !v)} title={`章节目录（${SHORTCUTS.outline}）`}><List size={16} />目录</button>
          <button type="button" aria-label="AI 助手" title={`AI 助手 · 总结与问答（${SHORTCUTS.ai}）`} className={aiOpen ? 'is-active' : ''} onClick={() => setAiOpen((current) => !current)}><Sparkles size={16} />AI 助手</button>
          <button type="button" aria-label={fullscreen ? '退出全屏' : '全屏阅读'} title={`${fullscreen ? '退出全屏' : '全屏阅读'}（${SHORTCUTS.fullscreen}）`} onClick={toggleFullscreen}>
            {fullscreen ? <Shrink size={16} /> : <Expand size={16} />}{fullscreen ? '退出全屏' : '全屏'}
          </button>
        </div>
        <div className="textbook-pdf__toolbar-group textbook-pdf__paging">
          <button type="button" aria-label="上一页" title={`上一页（${SHORTCUTS.prev}）`} disabled={pageNumber <= 1} onClick={() => scrollToPage(pageNumber - 1)}><ChevronLeft size={17} /></button>
          <label><input aria-label="当前页码" type="number" min="1" max={pageCount} value={pageNumber} onChange={(event) => scrollToPage(Number(event.target.value) || 1)} /> / {pageCount}</label>
          <button type="button" aria-label="下一页" title={`下一页（${SHORTCUTS.next}）`} disabled={pageNumber >= pageCount} onClick={() => scrollToPage(pageNumber + 1)}><ChevronRight size={17} /></button>
          <button
            type="button"
            aria-label="翻页方式"
            title="翻页方式"
            className={layoutMenuOpen ? 'is-active' : ''}
            onClick={() => setLayoutMenuOpen((current) => !current)}
          >
            <Rows3 size={16} />翻页方式
          </button>
          {layoutMenuOpen && (
            <div className="textbook-pdf__layout-menu" role="menu" aria-label="翻页方式选择">
              {[
                ['vertical', '纵向滚动'],
                ['single', '单页翻页'],
                ['horizontal', '横向翻页'],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  role="menuitemradio"
                  aria-checked={pageLayout === value}
                  className={pageLayout === value ? 'is-active' : ''}
                  onClick={() => {
                    setPageLayout(value);
                    setLayoutMenuOpen(false);
                    if (value !== 'single') pendingLocateRef.current = pageNumberRef.current;
                  }}
                >
                  {value === 'single' ? <FlipVertical2 size={14} /> : value === 'horizontal' ? <Columns3 size={14} /> : <Rows3 size={14} />}
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="textbook-pdf__toolbar-group">
          <button type="button" aria-label="缩小" title={`缩小（${SHORTCUTS.zoomOut}）`} onClick={() => setZoom((current) => clamp(current - 0.1, 0.5, 2.5))}><ZoomOut size={16} /></button>
          <span>{Math.round(zoom * 100)}%</span>
          <button type="button" aria-label="放大" title={`放大（${SHORTCUTS.zoomIn}）`} onClick={() => setZoom((current) => clamp(current + 0.1, 0.5, 2.5))}><ZoomIn size={16} /></button>
          <span className="textbook-pdf__toolbar-divider" />
          <button type="button" aria-label="导出本页图片" title="导出本页图片（含批注）" onClick={exportPageImage}><Download size={16} />导出本页</button>
          <button type="button" disabled={favoritePending} className={favorite ? 'is-active' : ''} onClick={toggleFavorite}><Bookmark size={16} fill={favorite ? 'currentColor' : 'none'} />{favorite ? '已收藏' : '收藏本页'}</button>
          <button type="button" aria-label="下载电子教材 PDF" title="下载电子教材 PDF"><a href={book.file_url} download style={{ display: 'contents' }}><FileDown size={16} />下载PDF</a></button>
        </div>
      </header>
      <div className="textbook-pdf__workspace" ref={workspaceRef}>
        <aside className={`textbook-pdf__sidebar${outlineOpen ? ' has-outline' : ''}`} aria-label="工具与目录">
          <div className="textbook-pdf__draw-tools">
            {tools.map(([value, label, Icon]) => <button key={value} type="button" title={`${label}（${SHORTCUTS.tool[value]}）`} aria-label={label} className={tool === value ? 'is-active' : ''} onClick={() => setTool(value)}><Icon aria-hidden="true" size={17} /></button>)}
            <span />
            {shapeTools.map(([value, label, ShapeIcon]) => <button key={value} type="button" title={label} aria-label={label} className={tool === value ? 'is-active' : ''} onClick={() => setTool(value)}><ShapeIcon aria-hidden="true" size={17} /></button>)}
            <span />
            <button
              type="button"
              className="has-palette"
              title="批注颜色"
              aria-label="批注颜色"
              aria-expanded={paletteOpen}
              onClick={() => setPaletteOpen((current) => !current)}
            >
              <Palette size={17} />
              <i className="textbook-pdf__color-dot" style={{ background: activeColor }} />
            </button>
            {paletteOpen && (
              <div className="textbook-pdf__palette" role="group" aria-label="批注颜色选择">
                {palette.map((value) => (
                  <button
                    key={value}
                    type="button"
                    aria-label={`颜色 ${value}`}
                    title={value}
                    className={value === activeColor ? 'is-active' : ''}
                    style={{ background: value }}
                    onClick={() => { tool === 'highlighter' ? setHighlighterColor(value) : setPenColor(value); setPaletteOpen(false); }}
                  />
                ))}
              </div>
            )}
            <span />
            <button type="button" aria-label="撤销" title={`撤销（${SHORTCUTS.undo}）`} disabled={historyIndex <= 0} onClick={undo}><Undo2 size={17} /></button>
            <button type="button" aria-label="重做" title={`重做（${SHORTCUTS.redo}）`} disabled={historyIndex >= history.length - 1} onClick={redo}><Redo2 size={17} /></button>
            <button type="button" aria-label="清空本页批注" disabled={annotations.length === 0} onClick={clearPageAnnotations}><Trash2 size={17} /></button>
          </div>
          {outlineOpen && (
            <div className="textbook-pdf__outline-section">
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
            </div>
          )}
        </aside>
        {toc.length > 0 && (
          <aside className="textbook-pdf__toc" aria-label="教材目录">
            <header><strong>教材目录</strong><span>{toc.length} 章</span></header>
            <div>
              {toc.map((chapter) => (
                <section key={chapter.id || chapter.title}>
                  <button type="button" onClick={() => setPageNumber(clamp(Number(chapter.pdf_page) || 1, 1, pageCount))}>
                    <strong>{chapter.title}</strong><span>{chapter.printed_page || chapter.pdf_page || ''}</span>
                  </button>
                  {(chapter.sections || []).map((section) => (
                    <button key={section.id || section.title} type="button" className="is-section" onClick={() => setPageNumber(clamp(Number(section.pdf_page) || 1, 1, pageCount))}>
                      <span>{section.title}</span><small>{section.printed_page || section.pdf_page || ''}</small>
                    </button>
                  ))}
                </section>
              ))}
            </div>
          </aside>
        )}
        <div
          ref={hostRef}
          className={`textbook-pdf__viewport${pageLayout === 'single' ? '' : ` is-${pageLayout}`}`}
          data-layout={pageLayout}
        />
        {pageLayout === 'single' && (
          <div className="textbook-pdf__page">
            <canvas ref={canvasRef} />
            <div className="textbook-pdf__text-layer" ref={textLayerRef} />
            <AnnotationLayer annotations={annotations} tool={tool} color={activeColor} onChange={updateAnnotations} />
          </div>
        )}
        {pageLayout !== 'single' && (
          <div
            className="textbook-pdf__scroll-annotations"
            ref={scrollAnnotationHolderRef}
          >
            <AnnotationLayer annotations={annotations} tool={tool} color={activeColor} onChange={updateAnnotations} />
          </div>
        )}
        {rendering && <LoaderCircle className="textbook-pdf__rendering is-spinning" aria-label="正在渲染页面" />}
        {notesOpen && <TextbookPageNotePopover book={book} page={pageNumber} route={route} onClose={() => onNotesOpenChange?.(false)} />}
        {aiOpen && <PdfAiPanel bookId={book.book_id} bookTitle={book.title} pageNumber={pageNumber} onClose={() => setAiOpen(false)} />}
      </div>
      {error && <p className="textbook-pdf__warning" role="alert">{error}</p>}
    </section>
  );
}

function BookFallback() {
  return <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/></svg>;
}
