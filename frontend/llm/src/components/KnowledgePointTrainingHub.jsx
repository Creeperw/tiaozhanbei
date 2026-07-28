import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  BookOpenText,
  ChevronRight,
  CircleAlert,
  Clock3,
  LibraryBig,
  Search,
  Sparkles,
  Target,
} from 'lucide-react';
import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';
import { loadAtlasNodes } from './knowledge-atlas/knowledgeAtlasApi';
import QuestionTrainingPanel from './QuestionTrainingPanel';

const DEFAULT_ROUTE = 'textbook_14_5';

function normalizeKnowledgePoint(value, fallback = {}) {
  const kpId = String(value?.kpId || value?.kp_id || value?.id || '').trim();
  const kpName = String(value?.kpName || value?.kp_name || value?.title || value?.name || '').trim();
  if (!kpId || !kpName) return null;
  return {
    kpId,
    kpName,
    book: String(value?.book || fallback.book || '').trim(),
    chapter: String(value?.chapter || fallback.chapter || '').trim(),
    questionCount: Number(value?.question_count || value?.questionCount || 0),
  };
}

function dailyKnowledgePoints(task) {
  const cards = Array.isArray(task?.knowledge_cards) ? task.knowledge_cards : [];
  const cardRows = cards.map((card) => normalizeKnowledgePoint(card, task?.learning_chapter || {})).filter(Boolean);
  const byId = new Map(cardRows.map((item) => [item.kpId, item]));
  const items = Array.isArray(task?.items) ? task.items : [];
  items.forEach((item) => {
    const action = item?.action?.params || {};
    const normalized = normalizeKnowledgePoint({
      kp_id: item?.kp_id || action.kpId || action.kp_id,
      kp_name: item?.kp_name || action.kpName || action.kp_name || item?.title,
      book: item?.book,
      chapter: item?.chapter,
    }, task?.learning_chapter || {});
    if (normalized && !byId.has(normalized.kpId)) byId.set(normalized.kpId, normalized);
  });
  return [...byId.values()];
}

async function loadCurrentDailyTask(signal) {
  const response = await fetchWithAuth(`${MAIN_API_BASE}/dashboard/home`, { signal });
  const payload = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(payload.detail || '今日任务加载失败');
  return payload.current_learning_task || payload.today_tasks?.[0] || null;
}

function EmptyPanel({ icon, title, description }) {
  return (
    <div className="kp-training-empty">
      {React.createElement(icon, { 'aria-hidden': true, size: 28 })}
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}

export default function KnowledgePointTrainingHub({
  initialKnowledgePoint = null,
  taskItemId = '',
  onResult,
}) {
  const normalizedInitial = useMemo(() => normalizeKnowledgePoint(initialKnowledgePoint), [initialKnowledgePoint]);
  const [selected, setSelected] = useState(normalizedInitial);
  const [dailyTask, setDailyTask] = useState(null);
  const [dailyLoading, setDailyLoading] = useState(true);
  const [dailyError, setDailyError] = useState('');
  const [books, setBooks] = useState([]);
  const [chapters, setChapters] = useState([]);
  const [sections, setSections] = useState([]);
  const [knowledgePoints, setKnowledgePoints] = useState([]);
  const [book, setBook] = useState('');
  const [chapter, setChapter] = useState(null);
  const [section, setSection] = useState(null);
  const [libraryLoading, setLibraryLoading] = useState(true);
  const [libraryError, setLibraryError] = useState('');
  const [query, setQuery] = useState('');

  useEffect(() => {
    const controller = new AbortController();
    loadCurrentDailyTask(controller.signal)
      .then(setDailyTask)
      .catch((error) => {
        if (error.name !== 'AbortError') setDailyError(error.message || '今日任务加载失败');
      })
      .finally(() => setDailyLoading(false));
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setLibraryLoading(true);
    loadAtlasNodes({ level: 1, route: DEFAULT_ROUTE, signal: controller.signal })
      .then((payload) => setBooks(payload.nodes || []))
      .catch((error) => {
        if (error.name !== 'AbortError') setLibraryError(error.message || '教材目录加载失败');
      })
      .finally(() => setLibraryLoading(false));
    return () => controller.abort();
  }, []);

  const openBook = async (node) => {
    setBook(node.name);
    setChapter(null);
    setSection(null);
    setChapters([]);
    setSections([]);
    setKnowledgePoints([]);
    setLibraryLoading(true);
    setLibraryError('');
    try {
      const payload = await loadAtlasNodes({ level: 2, route: DEFAULT_ROUTE, lv1: node.name });
      setChapters(payload.nodes || []);
    } catch (error) {
      setLibraryError(error.message || '章节加载失败');
    } finally {
      setLibraryLoading(false);
    }
  };

  const openChapter = async (node) => {
    setChapter(node);
    setSection(null);
    setSections([]);
    setKnowledgePoints([]);
    setLibraryLoading(true);
    setLibraryError('');
    try {
      const payload = await loadAtlasNodes({
        level: 3,
        route: DEFAULT_ROUTE,
        lv1: book,
        chapter: node.name,
        chapterId: node.id,
      });
      setSections(payload.nodes || []);
    } catch (error) {
      setLibraryError(error.message || '小节加载失败');
    } finally {
      setLibraryLoading(false);
    }
  };

  const openSection = async (node) => {
    setSection(node);
    setKnowledgePoints([]);
    setLibraryLoading(true);
    setLibraryError('');
    try {
      const payload = await loadAtlasNodes({
        level: 4,
        route: DEFAULT_ROUTE,
        lv1: book,
        chapter: chapter?.name || '',
        chapterId: chapter?.id || '',
        lv2: node.name,
        sectionId: node.id,
      });
      setKnowledgePoints((payload.nodes || []).map((item) => normalizeKnowledgePoint(item, {
        book,
        chapter: chapter?.name || '',
      })).filter(Boolean));
    } catch (error) {
      setLibraryError(error.message || '知识点加载失败');
    } finally {
      setLibraryLoading(false);
    }
  };

  const resetLibrary = (level) => {
    if (level <= 1) {
      setBook('');
      setChapter(null);
      setSection(null);
      setChapters([]);
      setSections([]);
      setKnowledgePoints([]);
    } else if (level === 2) {
      setChapter(null);
      setSection(null);
      setSections([]);
      setKnowledgePoints([]);
    } else {
      setSection(null);
      setKnowledgePoints([]);
    }
    setQuery('');
  };

  const dailyPoints = dailyKnowledgePoints(dailyTask);
  const visibleRows = (section ? knowledgePoints : chapter ? sections : book ? chapters : books).filter((item) => {
    const label = String(item.kpName || item.name || '');
    return !query.trim() || label.toLowerCase().includes(query.trim().toLowerCase());
  });
  const currentLevelLabel = section ? '知识点' : chapter ? '小节' : book ? '章节' : '教材';

  if (selected) {
    return (
      <section className="kp-training-session" aria-label="知识点训练">
        <header className="kp-training-session__header">
          {taskItemId
            ? <span className="kp-training-session__locked"><Target aria-hidden="true" size={16} />今日任务知识点已锁定</span>
            : <button type="button" onClick={() => setSelected(null)}><ArrowLeft aria-hidden="true" size={17} />选择其他知识点</button>}
          <div>
            <span><Target aria-hidden="true" size={15} />当前训练知识点</span>
            <h2>{selected.kpName}</h2>
            <p>{[selected.book, selected.chapter].filter(Boolean).join(' · ') || '围绕当前知识点进行训练'}</p>
          </div>
        </header>
        <QuestionTrainingPanel
          enabled
          selectedKnowledgePoint={selected}
          initialMode="objective"
          onResult={onResult}
          taskItemId={taskItemId}
        />
      </section>
    );
  }

  if (taskItemId) {
    return (
      <section className="kp-training-hub" aria-labelledby="kp-training-binding-error">
        <EmptyPanel
          icon={CircleAlert}
          title="今日任务缺少知识点绑定"
          description="该任务未携带可解析的知识点，已停止加载，避免把其他知识点题目计入今日任务。请刷新或重新制定今日任务。"
        />
        <h2 id="kp-training-binding-error" className="sr-only">今日任务缺少知识点绑定</h2>
      </section>
    );
  }

  return (
    <section className="kp-training-hub" aria-labelledby="kp-training-title">
      <header className="kp-training-hub__heading">
        <span><Sparkles aria-hidden="true" size={16} />Knowledge point training</span>
        <h2 id="kp-training-title">按知识点开始专题训练</h2>
        <p>每次只聚焦一个知识点。先完成今日任务要求，再按教材目录训练其他知识点。</p>
      </header>

      <div className="kp-training-hub__sections">
        <section className="kp-training-today" aria-labelledby="kp-training-today-title">
          <header>
            <div><Clock3 aria-hidden="true" size={19} /><span><small>板块一</small><h3 id="kp-training-today-title">今日要训练的知识点</h3></span></div>
            <em>{dailyPoints.length} 个</em>
          </header>
          {dailyLoading ? <p role="status" className="kp-training-status">正在读取今日任务…</p>
            : dailyError ? <EmptyPanel icon={CircleAlert} title="今日任务暂时无法读取" description={dailyError} />
              : dailyPoints.length > 0 ? (
                <div className="kp-training-today__list">
                  {dailyPoints.map((item, index) => (
                    <button key={item.kpId} type="button" onClick={() => setSelected(item)}>
                      <span>{String(index + 1).padStart(2, '0')}</span>
                      <div><strong>{item.kpName}</strong><small>{[item.book, item.chapter].filter(Boolean).join(' · ') || item.kpId}</small></div>
                      <ChevronRight aria-hidden="true" size={18} />
                    </button>
                  ))}
                </div>
              ) : <EmptyPanel icon={Target} title="今日没有指定知识点" description="制定或刷新每日任务后，计划中的知识点会自动出现在这里。" />}
        </section>

        <section className="kp-training-library" aria-labelledby="kp-training-library-title">
          <header>
            <div><LibraryBig aria-hidden="true" size={19} /><span><small>板块二</small><h3 id="kp-training-library-title">全部教材知识点</h3></span></div>
            <em>{currentLevelLabel}</em>
          </header>
          <nav className="kp-training-library__breadcrumbs" aria-label="教材知识点路径">
            <button type="button" onClick={() => resetLibrary(1)}>全部教材</button>
            {book && <><ChevronRight aria-hidden="true" size={14} /><button type="button" onClick={() => resetLibrary(2)}>{book}</button></>}
            {chapter && <><ChevronRight aria-hidden="true" size={14} /><button type="button" onClick={() => resetLibrary(3)}>{chapter.name}</button></>}
            {section && <><ChevronRight aria-hidden="true" size={14} /><span>{section.name}</span></>}
          </nav>
          <label className="kp-training-library__search">
            <Search aria-hidden="true" size={16} />
            <span className="sr-only">筛选当前{currentLevelLabel}</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={`筛选当前${currentLevelLabel}`} />
          </label>
          {libraryLoading ? <p role="status" className="kp-training-status">正在加载{currentLevelLabel}…</p>
            : libraryError ? <EmptyPanel icon={CircleAlert} title="教材知识点暂时无法读取" description={libraryError} />
              : visibleRows.length > 0 ? (
                <div className="kp-training-library__list">
                  {visibleRows.map((item) => {
                    const isKnowledgePoint = Boolean(section);
                    const label = item.kpName || item.name;
                    const description = isKnowledgePoint
                      ? `${item.questionCount} 道可训练题目`
                      : (item.alias || `${item.count || item.children_count || 0} 个下级节点`);
                    return (
                      <button
                        key={item.kpId || item.id}
                        type="button"
                        disabled={isKnowledgePoint && item.questionCount <= 0}
                        onClick={() => (isKnowledgePoint ? setSelected(item) : chapter ? openSection(item) : book ? openChapter(item) : openBook(item))}
                      >
                        <BookOpenText aria-hidden="true" size={18} />
                        <span><strong>{label}</strong><small>{description}</small></span>
                        {isKnowledgePoint && item.questionCount <= 0 ? <em>待补题</em> : <ChevronRight aria-hidden="true" size={17} />}
                      </button>
                    );
                  })}
                </div>
              ) : <EmptyPanel icon={Search} title={`没有匹配的${currentLevelLabel}`} description="清除筛选词或返回上一级目录继续选择。" />}
        </section>
      </div>
    </section>
  );
}
