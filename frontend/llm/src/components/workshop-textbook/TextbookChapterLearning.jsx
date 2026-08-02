/* eslint-disable react-hooks/set-state-in-effect */
import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowLeft,
  BookOpen,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Clock3,
  Film,
  Layers3,
  LoaderCircle,
  PlayCircle,
  Search,
  Undo2,
} from 'lucide-react';
import { loadAtlasNodes } from '../knowledge-atlas/knowledgeAtlasApi';
import {
  completeTextbookSection,
  loadSectionLearningDetail,
  loadSectionQuestions,
  loadTextbookProgress,
} from './textbookChapterApi';
import { textbookCoverUrl, textbookIntroduction } from './textbookMetadata';
import SectionExamPanel from './SectionExamPanel';
import TextbookPdfReader from './TextbookPdfReader';
import { loadTextbookPdfMetadata } from './textbookPdfApi';
import './textbookChapterLearning.css';

function formatTime(value) {
  const seconds = Math.max(0, Math.floor(Number(value || 0)));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remaining = seconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remaining).padStart(2, '0')}`
    : `${minutes}:${String(remaining).padStart(2, '0')}`;
}

function playerUrl(video, autoplay = false) {
  const params = new URLSearchParams({
    isOutside: 'true',
    bvid: String(video?.bvid || ''),
    cid: String(video?.cid || ''),
    p: String(video?.page || 1),
    t: String(Math.max(0, Math.floor(Number(video?.start_seconds || 0)))),
    autoplay: autoplay ? '1' : '0',
    danmaku: '0',
  });
  if (video?.aid) params.set('aid', String(video.aid));
  return `https://player.bilibili.com/player.html?${params}`;
}

function cleanDisplayText(value) {
  return String(value || '').replace(/^\?+\s*/, '').trim();
}

function searchableText(value) {
  return String(value || '').toLowerCase().replace(/[\s·、，。；：:（）()《》]/g, '');
}

const headingDigits = {
  零: 0, 〇: 0, 一: 1, 二: 2, 两: 2, 三: 3, 四: 4,
  五: 5, 六: 6, 七: 7, 八: 8, 九: 9,
};
const headingUnits = { 十: 10, 百: 100, 千: 1000 };

function parseHeadingNumber(value) {
  if (/^\d+$/.test(value)) return Number(value);
  let total = 0;
  let digit = 0;
  for (const char of value) {
    if (Object.hasOwn(headingDigits, char)) {
      digit = headingDigits[char];
    } else if (Object.hasOwn(headingUnits, char)) {
      total += (digit || 1) * headingUnits[char];
      digit = 0;
    } else {
      return null;
    }
  }
  return total + digit;
}

function sortByHeadingNumber(items, marker) {
  return items
    .map((item, sourceIndex) => {
      const match = String(item?.name || '').match(
        new RegExp(`第\\s*([0-9零〇一二两三四五六七八九十百千]+)\\s*${marker}`),
      );
      return {
        item,
        sourceIndex,
        headingNumber: match ? parseHeadingNumber(match[1]) : null,
      };
    })
    .sort((left, right) => {
      if (left.headingNumber !== null && right.headingNumber !== null) {
        return left.headingNumber - right.headingNumber || left.sourceIndex - right.sourceIndex;
      }
      if (left.headingNumber !== null) return -1;
      if (right.headingNumber !== null) return 1;
      return left.sourceIndex - right.sourceIndex;
    })
    .map(({ item }) => item);
}

function VideoCard({ video, mode = 'section', displayTitle = '' }) {
  const isTimestamp = mode === 'timestamp' || mode === 'recommended';
  const matchedKps = Array.isArray(video?.matched_kps) ? video.matched_kps : [];
  const modeLabel = mode === 'timestamp'
    ? '知识点时间戳视频'
    : mode === 'recommended'
      ? '知识点推荐视频'
      : '小节完整视频';
  const iframeKey = `${video?.bvid || ''}-${video?.page || 1}-${video?.start_seconds || 0}-${mode}`;
  const title = displayTitle
    || cleanDisplayText(video?.topic)
    || cleanDisplayText(video?.part_title)
    || cleanDisplayText(video?.video_title)
    || '视频内容';

  return (
    <article className="textbook-video-card" data-recommended={String(isTimestamp)}>
      <div className="textbook-video-card__player">
        <iframe
          key={iframeKey}
          title={title}
          src={playerUrl(video, mode === 'timestamp')}
          loading="lazy"
          allow="autoplay; fullscreen; picture-in-picture"
          allowFullScreen
          scrolling="no"
          frameBorder="0"
        />
      </div>
      <div className="textbook-video-card__body">
        <div className="textbook-video-card__meta">
          <span><PlayCircle aria-hidden="true" size={15} />{modeLabel}</span>
          <span>P{video.page || 1}</span>
          {isTimestamp && (
            <span>
              <Clock3 aria-hidden="true" size={14} />
              {formatTime(video.start_seconds)}－{formatTime(video.end_seconds)}
            </span>
          )}
        </div>
        <h3>{title}</h3>
        <p>{video.video_title || video.part_title || video.bvid}</p>
        {video.transcript && <blockquote>{video.transcript}</blockquote>}
        {matchedKps.length > 0 && (
          <div className="textbook-video-card__kps" aria-label="匹配知识点">
            {matchedKps.map((kp) => <span key={kp.kp_id}>{kp.name || kp.kp_id}</span>)}
          </div>
        )}
      </div>
    </article>
  );
}

function Directory({ title, icon, items, selectedId, onSelect, emptyText, unitLabel, className = '', selectedExtra = null, showCompletion = false, getItemStatus = () => 'pending' }) {
  return (
    <section className={`textbook-directory ${className}`.trim()}>
      <header>{React.createElement(icon, { 'aria-hidden': 'true', size: 17 })}<h2>{title}</h2><span>{items.length}</span></header>
      <div className="textbook-directory__items">
        {items.length ? items.map((item, index) => {
          const completionStatus = showCompletion ? getItemStatus(item) : 'pending';
          return (
            <React.Fragment key={item.id}>
              <button type="button" className={selectedId === item.id ? 'is-active' : ''} onClick={() => onSelect(selectedId === item.id ? null : item)}>
                <small>{String(index + 1).padStart(2, '0')}</small>
                <span><strong>{item.name}</strong><em>{item.alias || `${item.children_count || item.count || 0} ${unitLabel}`}</em></span>
                {showCompletion && <span className={`textbook-completion-dot ${completionStatus === 'completed' ? 'is-completed' : ''} ${completionStatus === 'partial' ? 'is-partial' : ''}`} aria-label={completionStatus === 'completed' ? '已完成' : completionStatus === 'partial' ? '部分完成' : '未完成'} />}
                <ChevronRight aria-hidden="true" size={15} />
              </button>
              {selectedId === item.id && selectedExtra}
            </React.Fragment>
          );
        }) : <p className="textbook-directory__empty">{emptyText}</p>}
      </div>
    </section>
  );
}

export default function TextbookChapterLearning({ navigationContext = {}, onNavigate }) {
  const route = navigationContext.route || 'textbook_14_5';
  const book = navigationContext.lv1 || navigationContext.book || '';
  const bookId = navigationContext.bookId || '';
  const [uploadedBook, setUploadedBook] = useState(null);
  const [chapters, setChapters] = useState([]);
  const [sections, setSections] = useState([]);
  const [sectionsByChapter, setSectionsByChapter] = useState({});
  const [completedSectionIds, setCompletedSectionIds] = useState(() => new Set());
  const [lastSectionId, setLastSectionId] = useState('');
  const [progressLoading, setProgressLoading] = useState(true);
  const [selectedChapter, setSelectedChapter] = useState(null);
  const [selectedSection, setSelectedSection] = useState(null);
  const [detail, setDetail] = useState(null);
  const [activeKnowledgePoint, setActiveKnowledgePoint] = useState(null);
  const [videoHistory, setVideoHistory] = useState([]);
  const [knowledgePointsExpanded, setKnowledgePointsExpanded] = useState(false);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const [catalogQuery, setCatalogQuery] = useState('');
  const [catalogStatus, setCatalogStatus] = useState('all');
  const [searchCatalog, setSearchCatalog] = useState(null);
  // PDF / 目录 / 作业与考试 模式
  const [courseMode, setCourseMode] = useState('catalog');
  const [sectionExamMode, setSectionExamMode] = useState(false);
  const [pdfInitialPage, setPdfInitialPage] = useState(navigationContext.pdfPage || 1);
  const [pageNotesOpen, setPageNotesOpen] = useState(false);
  // 作业与考试独立数据
  const [sectionQuestionCounts, setSectionQuestionCounts] = useState({});
  const [sectionKpIdsMap, setSectionKpIdsMap] = useState({});
  const [examSections, setExamSections] = useState([]);

  useEffect(() => {
    setCourseMode('pdf');
    setPdfInitialPage(navigationContext.pdfPage || 1);
    setPageNotesOpen(false);
  }, [book, navigationContext.openPdf, navigationContext.pdfPage]);

  useEffect(() => {
    if (!book) {
      setLoading(false);
      setProgressLoading(false);
      setError('缺少教材信息，无法加载章节目录。');
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true); setProgressLoading(true); setError('');
    setChapters([]); setSections([]); setSectionsByChapter({}); setSelectedChapter(null); setSelectedSection(null);
    setCompletedSectionIds(new Set()); setLastSectionId('');
    if (bookId) {
      loadTextbookPdfMetadata(bookId, { signal: controller.signal })
        .then((payload) => {
          const item = payload.book || null;
          setUploadedBook(item);
          const chapterItems = (item?.toc?.chapters || []).map((chapter, chapterIndex) => ({
            ...chapter,
            id: chapter.id || `upload-chapter-${chapterIndex + 1}`,
            name: chapter.title,
            children_count: (chapter.sections || []).length,
          }));
          const nextSections = Object.fromEntries(chapterItems.map((chapter) => [
            chapter.id,
            (chapter.sections || []).map((section, sectionIndex) => ({
              ...section,
              id: section.id || `${chapter.id}-section-${sectionIndex + 1}`,
              name: section.title,
              count: 0,
            })),
          ]));
          setChapters(chapterItems);
          setSectionsByChapter(nextSections);
          setSelectedChapter(chapterItems[0] || null);
        })
        .catch((loadError) => { if (loadError.name !== 'AbortError') setError(loadError.message || '上传教材目录加载失败。'); })
        .finally(() => { if (!controller.signal.aborted) { setLoading(false); setProgressLoading(false); } });
      return () => controller.abort();
    }
    setUploadedBook(null);
    const chapterPromise = loadAtlasNodes({ level: 2, route, lv1: book, signal: controller.signal }).then(async (payload) => {
      const next = sortByHeadingNumber(Array.isArray(payload.nodes) ? payload.nodes : [], '章');
      setChapters(next); setSelectedChapter((current) => next.find((item) => item.id === current?.id) || next[0] || null);
      const results = await Promise.all(next.map(async (chapter) => {
        const sectionPayload = await loadAtlasNodes({ level: 3, route, lv1: book, chapter: chapter.name, chapterId: chapter.id, signal: controller.signal });
        return [chapter.id, sortByHeadingNumber(Array.isArray(sectionPayload.nodes) ? sectionPayload.nodes : [], '节')];
      }));
      setSectionsByChapter(Object.fromEntries(results));
    }).catch((loadError) => { if (loadError.name !== 'AbortError') setError(loadError.message || '章节目录加载失败。'); });
    const progressPromise = loadTextbookProgress(book, { signal: controller.signal }).then((payload) => {
      setCompletedSectionIds(new Set(Array.isArray(payload.completed_section_ids) ? payload.completed_section_ids : []));
      setLastSectionId(payload.last_section_id || '');
    }).catch((loadError) => { if (loadError.name !== 'AbortError') setError((current) => current || loadError.message || '教材学习进度加载失败。'); });
    Promise.allSettled([chapterPromise, progressPromise]).then(() => { if (!controller.signal.aborted) { setLoading(false); setProgressLoading(false); } });
    return () => controller.abort();
  }, [book, bookId, route]);

  useEffect(() => {
    if (!selectedChapter) { setSections([]); setSelectedSection(null); return; }
    const next = sectionsByChapter[selectedChapter.id];
    if (next) { setSections(next); setSelectedSection((current) => next.find((item) => item.id === current?.id) || null); }
  }, [selectedChapter, sectionsByChapter]);

  useEffect(() => {
    setActiveKnowledgePoint(null);
    setVideoHistory([]);
    setKnowledgePointsExpanded(false);
    if (!selectedSection) {
      setDetail(null);
      return undefined;
    }
    const controller = new AbortController();
    setDetailLoading(true);
    setError('');
    loadSectionLearningDetail(selectedSection.id, { signal: controller.signal })
      .then(setDetail)
      .catch((loadError) => {
        if (loadError.name !== 'AbortError') setError(loadError.message || '小节学习内容加载失败。');
      })
      .finally(() => setDetailLoading(false));
    return () => controller.abort();
  }, [selectedSection]);

  // 作业与考试独立数据加载（不依赖 sectionsByChapter）
  const examLoadGenerationRef = useRef(0);
  useEffect(() => {
    if (!sectionExamMode || !selectedChapter?.id || !book) {
      setExamSections([]);
      return undefined;
    }
    const generation = examLoadGenerationRef.current + 1;
    examLoadGenerationRef.current = generation;
    const controller = new AbortController();
    const load = async () => {
      let chapterSections = [];
      try {
        const payload = await loadAtlasNodes({
          level: 3, route, lv1: book,
          chapter: selectedChapter.name, chapterId: selectedChapter.id,
          signal: controller.signal,
        });
        chapterSections = sortByHeadingNumber(
          Array.isArray(payload.nodes) ? payload.nodes : [], '节',
        );
      } catch (err) {
        if (err.name !== 'AbortError' && generation === examLoadGenerationRef.current) {
          setExamSections([]); setSectionQuestionCounts({});
        }
        return;
      }
      if (controller.signal.aborted || generation !== examLoadGenerationRef.current) return;
      setExamSections(chapterSections);
      if (!chapterSections.length) { setSectionQuestionCounts({}); return; }
      const sectionKpIds = {};
      const allKpIds = [];
      await Promise.all(chapterSections.map(async (section) => {
        try {
          const kpPayload = await loadAtlasNodes({
            level: 4, route, lv1: book,
            chapter: selectedChapter.name, chapterId: selectedChapter.id,
            lv2: section.name, sectionId: section.id, signal: controller.signal,
          });
          const kps = Array.isArray(kpPayload.nodes) ? kpPayload.nodes : [];
          const ids = kps.map((kp) => kp.kp_id || kp.id).filter(Boolean);
          sectionKpIds[section.id] = new Set(ids);
          allKpIds.push(...ids);
        } catch (err) { if (err.name !== 'AbortError') sectionKpIds[section.id] = new Set(); }
      }));
      if (controller.signal.aborted || generation !== examLoadGenerationRef.current) return;
      const uniqueKpIds = [...new Set(allKpIds)];
      const counts = {};
      if (uniqueKpIds.length > 0) {
        try {
          const result = await loadSectionQuestions(uniqueKpIds, { signal: controller.signal });
          const items = Array.isArray(result.items) ? result.items : [];
          for (const sectionId of Object.keys(sectionKpIds)) {
            const kpSet = sectionKpIds[sectionId];
            counts[sectionId] = items.filter((q) => {
              const qKps = Array.isArray(q.kp_ids) ? q.kp_ids : [];
              return qKps.some((id) => kpSet.has(id));
            }).length;
          }
        } catch (err) { if (err.name !== 'AbortError') { for (const sId of Object.keys(sectionKpIds)) counts[sId] = -1; } }
      } else { for (const sId of Object.keys(sectionKpIds)) counts[sId] = 0; }
      if (!controller.signal.aborted && generation === examLoadGenerationRef.current) {
        setSectionQuestionCounts(counts);
        const kpIdsMap = {};
        for (const [sId, kpSet] of Object.entries(sectionKpIds)) kpIdsMap[sId] = [...kpSet];
        setSectionKpIdsMap(kpIdsMap);
      }
    };
    load();
    return () => controller.abort();
  }, [sectionExamMode, selectedChapter?.id, book, route]);

  const exactVideos = useMemo(
    () => (Array.isArray(detail?.section_videos) ? detail.section_videos : []),
    [detail],
  );
  const recommendedVideos = useMemo(
    () => (Array.isArray(detail?.recommended_videos) ? detail.recommended_videos : []),
    [detail],
  );
  const knowledgePoints = useMemo(
    () => (Array.isArray(detail?.knowledge_points) ? detail.knowledge_points : []),
    [detail],
  );
  const hasFoldedKnowledgePoints = knowledgePoints.length > 20;
  const timestampKnowledgePoints = useMemo(
    () => knowledgePoints.filter((kp) => Boolean(kp.timestamp_video)),
    [knowledgePoints],
  );
  const plainKnowledgePoints = useMemo(
    () => knowledgePoints.filter((kp) => !kp.timestamp_video),
    [knowledgePoints],
  );
  const visibleKnowledgePoints = hasFoldedKnowledgePoints
    ? (knowledgePointsExpanded
      ? [...timestampKnowledgePoints, ...plainKnowledgePoints]
      : timestampKnowledgePoints)
    : knowledgePoints;
  const normalizedCatalogQuery = searchableText(catalogQuery);
  const allSections = useMemo(() => Object.values(sectionsByChapter).flat(), [sectionsByChapter]);
  const progress = allSections.length ? Math.round((completedSectionIds.size / allSections.length) * 100) : 0;
  const getChapterCompletionStatus = (chapter) => {
    const list = sectionsByChapter[chapter.id] || [];
    if (completedSectionIds.size === 0) {
      const status = String(chapter.status || chapter.learning_status || '').toLowerCase();
      const progressValue = Number(chapter.progress ?? chapter.progress_rate ?? 0);
      if (status === 'completed' || progressValue >= 1) return 'completed';
      if (['in_progress', 'current', 'learning'].includes(status) || (progressValue > 0 && progressValue < 1)) return 'partial';
      return 'pending';
    }
    if (!list.length) return 'pending';
    const completedCount = list.filter((section) => completedSectionIds.has(section.id)).length;
    if (completedCount === list.length) return 'completed';
    if (completedCount > 0) return 'partial';
    return 'pending';
  };
  const isSectionCompleted = (section) => completedSectionIds.has(section.id);
  useEffect(() => {
    if (!normalizedCatalogQuery || !chapters.length) {
      setSearchCatalog(null);
      return undefined;
    }
    const controller = new AbortController();
    const loadSearchCatalog = async () => {
      const chapterResults = await Promise.allSettled(chapters.map(async (chapter) => {
        const sectionPayload = await loadAtlasNodes({
          level: 3, route, lv1: book, chapter: chapter.name, chapterId: chapter.id, signal: controller.signal,
        });
        const chapterSections = Array.isArray(sectionPayload.nodes) ? sectionPayload.nodes : [];
        const pointResults = await Promise.allSettled(chapterSections.map(async (section) => {
          const pointPayload = await loadAtlasNodes({
            level: 4, route, lv1: book, chapter: chapter.name, chapterId: chapter.id,
            lv2: section.name, sectionId: section.id, signal: controller.signal,
          });
          return { section, points: Array.isArray(pointPayload.nodes) ? pointPayload.nodes : [] };
        }));
        return { chapter, sectionsWithPoints: pointResults.filter((r) => r.status === 'fulfilled').map((r) => r.value) };
      }));
      setSearchCatalog(chapterResults.filter((r) => r.status === 'fulfilled').map((r) => r.value));
    };
    loadSearchCatalog().catch((loadError) => { if (loadError.name !== 'AbortError') setSearchCatalog([]); });
    return () => controller.abort();
  }, [book, chapters, normalizedCatalogQuery, route]);

  const searchMatches = useMemo(() => {
    if (!normalizedCatalogQuery || !Array.isArray(searchCatalog)) return null;
    return searchCatalog.map(({ chapter, sectionsWithPoints }) => ({
      chapter,
      sections: sectionsWithPoints
        .filter(({ section, points }) => (
          searchableText(section.name).includes(normalizedCatalogQuery)
          || points.some((point) => searchableText(point.name).includes(normalizedCatalogQuery))
        ))
        .map(({ section }) => section),
    })).filter(({ chapter, sections }) => (
      searchableText(chapter.name).includes(normalizedCatalogQuery) || sections.length > 0
    ));
  }, [normalizedCatalogQuery, searchCatalog]);

  const sectionMatchesStatus = (section) => {
    if (catalogStatus === 'all') return true;
    const completed = isSectionCompleted(section);
    return catalogStatus === 'completed' ? completed : !completed;
  };

  const filteredChapters = useMemo(() => {
    const queryMatches = !normalizedCatalogQuery
      ? chapters
      : searchMatches ? searchMatches.map(({ chapter }) => chapter) : [];
    if (catalogStatus === 'all') return queryMatches;
    return queryMatches.filter((chapter) => {
      const status = getChapterCompletionStatus(chapter);
      if (catalogStatus === 'completed') return status === 'completed' || status === 'partial';
      return status === 'pending' || status === 'partial';
    });
  }, [catalogStatus, chapters, completedSectionIds, normalizedCatalogQuery, searchMatches, sectionsByChapter]);

  const filteredSections = useMemo(() => {
    const sourceSections = sectionExamMode ? examSections : sections;
    const querySections = !normalizedCatalogQuery
      ? sourceSections
      : searchMatches?.find(({ chapter }) => chapter.id === selectedChapter?.id)?.sections || [];
    const matchingSections = querySections.filter(sectionMatchesStatus);
    if (!sectionExamMode) return matchingSections;
    return matchingSections.map((section) => {
      const count = sectionQuestionCounts[section.id];
      return {
        ...section,
        alias: Number.isFinite(count) ? `${count} 道题目` : count === -1 ? '题目加载失败' : '正在匹配题目…',
      };
    });
  }, [catalogStatus, normalizedCatalogQuery, searchMatches, sections, examSections, selectedChapter?.id, completedSectionIds, sectionExamMode, sectionQuestionCounts]);

  useEffect(() => {
    if (catalogStatus === 'all' || !filteredChapters.length) return;
    if (!filteredChapters.some((chapter) => chapter.id === selectedChapter?.id)) {
      setSelectedChapter(filteredChapters[0]);
      setSelectedSection(null);
      setDetail(null);
      setVideoHistory([]);
    }
  }, [catalogStatus, filteredChapters, selectedChapter?.id]);

  useEffect(() => {
    if (!normalizedCatalogQuery || !searchMatches?.length) return;
    const matchingChapter = searchMatches.find(({ chapter }) => (
      searchableText(chapter.name).includes(normalizedCatalogQuery)
    ))?.chapter || searchMatches[0].chapter;
    if (matchingChapter && matchingChapter.id !== selectedChapter?.id) {
      setSelectedChapter(matchingChapter);
      setSelectedSection(null);
    }
  }, [normalizedCatalogQuery, searchMatches, selectedChapter?.id]);

  const sectionVideo = exactVideos[0] || null;
  const recommendedVideo = recommendedVideos[0] || null;
  const activeTimestampVideo = videoHistory.at(-1)?.video || null;
  const activeTimestampName = videoHistory.at(-1)?.knowledgePointName || '';
  const videoBackDisabled = videoHistory.length === 0;

  const findSectionLocation = (sectionId) => {
    for (const chapter of chapters) { const section = (sectionsByChapter[chapter.id] || []).find((item) => item.id === sectionId); if (section) return { chapter, section }; }
    return null;
  };
  const openSection = async (section, chapter = selectedChapter) => {
    if (!section || !chapter) return;
    setSelectedChapter(chapter); setSelectedSection(section); setLastSectionId(section.id);
    setCompletedSectionIds((current) => current.has(section.id) ? current : new Set([...current, section.id]));
    try { await completeTextbookSection({ book, route, chapter_id: chapter.id, chapter_name: chapter.name, section_id: section.id, section_name: section.name }); }
    catch (saveError) { if (saveError.name !== 'AbortError') setError((current) => current || saveError.message || '保存小节学习进度失败。'); }
  };

  // 深链定位：从今日任务的“看视频”任务进入时，自动打开指定小节。
  const deepLinkSectionId = navigationContext.sectionId || navigationContext.section_id || '';
  const deepLinkHandledRef = useRef(false);
  useEffect(() => {
    if (deepLinkSectionId) deepLinkHandledRef.current = false;
  }, [deepLinkSectionId, book, bookId, route]);
  useEffect(() => {
    if (!deepLinkSectionId || deepLinkHandledRef.current) return undefined;
    if (loading || !chapters.length) return undefined;
    const location = findSectionLocation(deepLinkSectionId);
    if (!location) return undefined;
    deepLinkHandledRef.current = true;
    setCourseMode('catalog');
    openSection(location.section, location.chapter);
    return undefined;
  }, [deepLinkSectionId, loading, chapters, sectionsByChapter]);
  const startOrContinueLearning = () => {
    const location = progress > 0 ? findSectionLocation(lastSectionId) : null;
    const firstChapter = chapters[0]; const firstSection = firstChapter ? (sectionsByChapter[firstChapter.id] || [])[0] : null;
    const target = location || (firstChapter && firstSection ? { chapter: firstChapter, section: firstSection } : null);
    if (target) openSection(target.section, target.chapter);
  };

  const playKnowledgePoint = (kp) => {
    if (!kp?.timestamp_video) return;
    const video = {
      ...kp.timestamp_video,
      topic: kp.timestamp_video.topic || kp.name || '知识点时间戳视频',
      matched_kps: [{ kp_id: kp.kp_id, name: kp.name || kp.kp_id }],
    };
    setActiveKnowledgePoint(kp);
    setVideoHistory((current) => [...current, { video, knowledgePointName: kp.name || kp.kp_id }]);
  };

  const returnPreviousVideo = () => {
    setVideoHistory((current) => {
      const next = current.slice(0, -1);
      const previous = next.at(-1);
      setActiveKnowledgePoint(previous ? { kp_id: previous.video.matched_kps?.[0]?.kp_id } : null);
      return next;
    });
  };

  return (
    <main className="textbook-chapter-learning">
      <header className="textbook-chapter-learning__hero">
        <div className="textbook-chapter-learning__cover">
          <img src={uploadedBook?.cover_url || textbookCoverUrl(book)} alt={`《${book}》教材封面`} />
        </div>
        <div className="textbook-chapter-learning__intro">
          <span>学习工坊 · 教材章节学习</span>
          <h1>《{book || '教材章节'}》</h1>
          <p>{uploadedBook?.description || textbookIntroduction(book)}</p>
          <div className="textbook-chapter-learning__stats">
            <span><BookOpen aria-hidden="true" size={14} />{chapters.length} 个章节</span>
            <span><Film aria-hidden="true" size={14} />章节视频与知识点片段</span>
          </div>
        </div>
        <div className="textbook-chapter-learning__actions">
          <button className="textbook-chapter-learning__back" type="button" onClick={() => (navigationContext.returnTo ? onNavigate?.(navigationContext.returnTo) : onNavigate?.({ page: 'practice', params: {} }))}>
            <ArrowLeft aria-hidden="true" size={14} />返回
          </button>
          <div className="textbook-chapter-learning__progress" aria-label="课程学习进度">
            <div className="textbook-progress-ring" style={{ background: `conic-gradient(#3b936c ${progress}%, #dce6e1 0)` }}><div><strong>{progress}%</strong><span>学习进度</span></div></div>
            <button type="button" className="textbook-start-learning" onClick={startOrContinueLearning} disabled={progressLoading || !allSections.length}>
              <PlayCircle aria-hidden="true" size={18} />{progress > 0 ? '继续学习' : '开始学习'}
            </button>
          </div>
        </div>
      </header>

      {error && <div className="textbook-chapter-learning__error" role="alert">{error}</div>}
      {loading ? (
        <div className="textbook-chapter-learning__loading" role="status">
          <LoaderCircle aria-hidden="true" size={22} />正在加载教材目录…
        </div>
      ) : (
        <div className="textbook-chapter-learning__body">
          <aside className="textbook-learning-nav" aria-label="课程导航">
            <button type="button" className={!sectionExamMode ? 'is-active' : ''} onClick={() => { setSectionExamMode(false); setCourseMode('catalog'); setPageNotesOpen(false); }}>
              <BookOpen aria-hidden="true" size={18} />课程内容
            </button>
            <button type="button" className={sectionExamMode ? 'is-active' : ''} onClick={() => { setSectionExamMode(true); setCourseMode('catalog'); setPageNotesOpen(false); }}>
              <Layers3 aria-hidden="true" size={18} />作业与考试
            </button>
            <button type="button" className={pageNotesOpen ? 'is-active' : ''} onClick={() => setPageNotesOpen((current) => !current)}>
              <BookOpen aria-hidden="true" size={18} />笔记本
            </button>
          </aside>
          <div className="textbook-learning-main">
          {!sectionExamMode && courseMode === 'pdf' ? (
            <TextbookPdfReader
              bookTitle={book}
              bookId={bookId}
              toc={uploadedBook?.toc?.chapters || []}
              initialPage={pdfInitialPage}
              route={route}
              notesOpen={pageNotesOpen}
              onNotesOpenChange={setPageNotesOpen}
              onClose={() => { setPageNotesOpen(false); setCourseMode('catalog'); }}
            />
          ) : (
          <>
            {!sectionExamMode && (
            <div className="textbook-learning-main__toolbar">
              <div><h2>课程内容</h2><p>共 {chapters.length} 个章节 · 章节视频与知识点片段</p></div>
              <div className="textbook-learning-filters">
                <button type="button" className="textbook-open-pdf" onClick={() => setCourseMode('pdf')}>
                  <BookOpen aria-hidden="true" size={15} />阅读电子教材
                </button>
                <label><Search aria-hidden="true" size={15} /><input aria-label="搜索章节、小节或知识点" value={catalogQuery} onChange={(event) => setCatalogQuery(event.target.value)} placeholder="搜索章节、小节或知识点" /></label>
                {[
                  ['all', '全部'],
                  ['pending', '未完成'],
                  ['completed', '已完成'],
                ].map(([value, label]) => (
                  <button key={value} type="button" className={catalogStatus === value ? 'is-active' : ''} aria-pressed={catalogStatus === value} onClick={() => setCatalogStatus(value)}>
                    {label}
                  </button>
                ))}
              </div>
            </div>
            )}
          {!selectedSection && <div className={`textbook-catalog-stage ${selectedChapter ? 'has-chapter' : ''}`}>
            <Directory
              title="章节"
              icon={BookOpen}
              className="textbook-directory--chapters"
              items={filteredChapters}
              selectedId={selectedChapter?.id}
              onSelect={(chapter) => {
                if (!chapter) { setSelectedChapter(null); setSelectedSection(null); setDetail(null); setVideoHistory([]); return; }
                setSelectedChapter(chapter); setSelectedSection(null); setDetail(null); setVideoHistory([]);
              }}
              emptyText={normalizedCatalogQuery ? '没有找到匹配的章节或小节。' : catalogStatus !== 'all' ? '当前筛选条件下没有章节。' : '该教材暂无章节数据。'}
              unitLabel="个小节"
              showCompletion
              getItemStatus={getChapterCompletionStatus}
              selectedExtra={selectedChapter ? (
                <Directory
                  title=""
                  icon={Layers3}
                  className="textbook-directory--sections"
                  items={filteredSections}
                  selectedId={selectedSection?.id}
                  onSelect={(section) => {
                    if (!section) { setSelectedSection(null); setDetail(null); setVideoHistory([]); return; }
                    openSection(section, selectedChapter);
                  }}
                  emptyText={normalizedCatalogQuery ? '没有找到匹配的小节。' : '该章节暂无小节数据。'}
                  unitLabel="个知识点"
                  showCompletion
                  getItemStatus={(section) => (isSectionCompleted(section) ? 'completed' : 'pending')}
                />
              ) : null}
            />
          </div>}

          {selectedSection && sectionExamMode ? (
            <div style={{padding:'20px 0'}}>
              <SectionExamPanel
                sectionName={selectedSection.name}
                kpIds={sectionKpIdsMap[selectedSection.id] || []}
                onBack={() => setSelectedSection(null)}
              />
            </div>
          ) : selectedSection && !sectionExamMode && (
          <section className="textbook-section-content" aria-live="polite">
            <header>
              <div className="textbook-section-content__eyebrow">
                <span>{selectedChapter?.name || '请选择章节'}</span>
                <button type="button" onClick={() => setSelectedSection(null)}>
                  <ArrowLeft aria-hidden="true" size={14} />返回小节目录
                </button>
              </div>
              <h2>{selectedSection?.name || '请选择小节'}</h2>
              {detail?.section && <p>{detail.section.book} / {detail.section.chapter} / {detail.section.name}</p>}
            </header>

            {detailLoading ? (
              <div className="textbook-section-content__loading" role="status">
                <LoaderCircle aria-hidden="true" size={20} />正在加载小节学习内容…
              </div>
            ) : selectedSection && detail ? (
              <div className="textbook-section-workspace">
                <section className="textbook-section-content__kps">
                  <div className="textbook-section-content__heading">
                    <Layers3 aria-hidden="true" size={18} />
                    <h2>本节知识点</h2>
                    <span>{knowledgePoints.length}</span>
                  </div>
                  {knowledgePoints.length ? (
                    <>
                      <div className="textbook-knowledge-points-grid">
                        {visibleKnowledgePoints.map((kp) => {
                          const playable = Boolean(kp.timestamp_video);
                          const active = activeKnowledgePoint?.kp_id === kp.kp_id;
                          return (
                            <button type="button" key={kp.kp_id} className={`${active ? 'is-active' : ''} ${playable ? 'has-timestamp' : ''}`.trim()} aria-disabled={!playable} onClick={playable ? () => playKnowledgePoint(kp) : undefined}>
                              <strong>{kp.name || kp.kp_id}</strong>
                              {kp.alias && <small>{kp.alias}</small>}
                              {playable && <em><PlayCircle aria-hidden="true" size={12} />播放时间戳视频</em>}
                            </button>
                          );
                        })}
                      </div>
                      {hasFoldedKnowledgePoints && plainKnowledgePoints.length > 0 && (
                        <button type="button" className="textbook-kp-fold-toggle" onClick={() => setKnowledgePointsExpanded((current) => !current)} aria-expanded={knowledgePointsExpanded}>
                          <span>{knowledgePointsExpanded ? '收起' : `展开其余 ${plainKnowledgePoints.length} 个知识点`}</span>
                          {knowledgePointsExpanded ? <ChevronUp aria-hidden="true" size={15} /> : <ChevronDown aria-hidden="true" size={15} />}
                        </button>
                      )}
                    </>
                  ) : <p className="textbook-section-content__empty">该小节暂无知识点数据。</p>}
                </section>

                <section className="textbook-section-content__videos">
                  <div className="textbook-section-content__heading">
                    <Film aria-hidden="true" size={18} />
                    <h2>{activeTimestampVideo ? `${activeTimestampName} · 知识点视频` : sectionVideo ? '小节视频' : '推荐视频'}</h2>
                    <button type="button" className="textbook-return-video" onClick={returnPreviousVideo} disabled={videoBackDisabled}>
                      <Undo2 aria-hidden="true" size={14} />返回上一个视频
                    </button>
                  </div>
                  {activeTimestampVideo ? (
                    <div className="textbook-video-grid">
                      <VideoCard video={activeTimestampVideo} mode="timestamp" />
                    </div>
                  ) : sectionVideo ? (
                    <div className="textbook-video-grid">
                      <VideoCard video={sectionVideo} mode="section" displayTitle={`小节完整视频：${detail.section.name}`} />
                    </div>
                  ) : recommendedVideo ? (
                    <div className="textbook-video-grid">
                      <p className="textbook-section-content__recommendation-title">该小节暂无完整视频，当前展示推荐内容</p>
                      <VideoCard video={recommendedVideo} mode="recommended" />
                    </div>
                  ) : (
                    <p className="textbook-section-content__empty">该小节暂无可播放的视频内容。</p>
                  )}
                </section>
              </div>
            ) : <p className="textbook-section-content__empty">请先选择一个小节。</p>}
          </section>
          )}
          </>
          )}
          </div>
        </div>
      )}
    </main>
  );
}
