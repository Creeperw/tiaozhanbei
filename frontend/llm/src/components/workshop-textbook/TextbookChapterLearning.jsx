/* eslint-disable react-hooks/set-state-in-effect */
import React, { useEffect, useMemo, useState } from 'react';
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
import { loadSectionLearningDetail } from './textbookChapterApi';
import { textbookCoverUrl, textbookIntroduction } from './textbookMetadata';
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

function Directory({ title, icon, items, selectedId, onSelect, emptyText, unitLabel, className = '', selectedExtra = null }) {
  return (
    <section className={`textbook-directory ${className}`.trim()}>
      <header>{React.createElement(icon, { 'aria-hidden': 'true', size: 17 })}<h2>{title}</h2><span>{items.length}</span></header>
      <div className="textbook-directory__items">
        {items.length ? items.map((item, index) => (
          <React.Fragment key={item.id}>
            <button
              type="button"
              className={selectedId === item.id ? 'is-active' : ''}
              onClick={() => onSelect(selectedId === item.id ? null : item)}
            >
              <small>{String(index + 1).padStart(2, '0')}</small>
              <span>
                <strong>{item.name}</strong>
                <em>{item.alias || `${item.children_count || item.count || 0} ${unitLabel}`}</em>
              </span>
              <ChevronRight aria-hidden="true" size={15} />
            </button>
            {selectedId === item.id && selectedExtra}
          </React.Fragment>
        )) : <p className="textbook-directory__empty">{emptyText}</p>}
      </div>
    </section>
  );
}

export default function TextbookChapterLearning({ navigationContext = {}, onNavigate }) {
  const route = navigationContext.route || 'textbook_14_5';
  const book = navigationContext.lv1 || navigationContext.book || '';
  const [chapters, setChapters] = useState([]);
  const [sections, setSections] = useState([]);
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

  useEffect(() => {
    if (!book) {
      setLoading(false);
      setError('缺少教材信息，无法加载章节目录。');
      return undefined;
    }
    const controller = new AbortController();
    setLoading(true);
    setError('');
    loadAtlasNodes({ level: 2, route, lv1: book, signal: controller.signal })
      .then((payload) => {
        const next = sortByHeadingNumber(
          Array.isArray(payload.nodes) ? payload.nodes : [],
          '章',
        );
        setChapters(next);
        setSelectedChapter((current) => next.find((item) => item.id === current?.id) || next[0] || null);
      })
      .catch((loadError) => {
        if (loadError.name !== 'AbortError') setError(loadError.message || '章节目录加载失败。');
      })
      .finally(() => setLoading(false));
    return () => controller.abort();
  }, [book, route]);

  useEffect(() => {
    if (!selectedChapter) {
      setSections([]);
      setSelectedSection(null);
      return undefined;
    }
    const controller = new AbortController();
    setError('');
    loadAtlasNodes({
      level: 3,
      route,
      lv1: book,
      chapter: selectedChapter.name,
      chapterId: selectedChapter.id,
      signal: controller.signal,
    }).then((payload) => {
      const next = sortByHeadingNumber(
        Array.isArray(payload.nodes) ? payload.nodes : [],
        '节',
      );
      setSections(next);
      setSelectedSection((current) => next.find((item) => item.id === current?.id) || null);
    }).catch((loadError) => {
      if (loadError.name !== 'AbortError') setError(loadError.message || '小节目录加载失败。');
    });
    return () => controller.abort();
  }, [book, route, selectedChapter]);

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
  useEffect(() => {
    if (!normalizedCatalogQuery || !chapters.length) {
      setSearchCatalog(null);
      return undefined;
    }
    const controller = new AbortController();
    const loadSearchCatalog = async () => {
      const chapterResults = await Promise.allSettled(chapters.map(async (chapter) => {
        const sectionPayload = await loadAtlasNodes({
          level: 3,
          route,
          lv1: book,
          chapter: chapter.name,
          chapterId: chapter.id,
          signal: controller.signal,
        });
        const chapterSections = Array.isArray(sectionPayload.nodes) ? sectionPayload.nodes : [];
        const pointResults = await Promise.allSettled(chapterSections.map(async (section) => {
          const pointPayload = await loadAtlasNodes({
            level: 4,
            route,
            lv1: book,
            chapter: chapter.name,
            chapterId: chapter.id,
            lv2: section.name,
            sectionId: section.id,
            signal: controller.signal,
          });
          return { section, points: Array.isArray(pointPayload.nodes) ? pointPayload.nodes : [] };
        }));
        return {
          chapter,
          sectionsWithPoints: pointResults
            .filter((result) => result.status === 'fulfilled')
            .map((result) => result.value),
        };
      }));
      setSearchCatalog(
        chapterResults
          .filter((result) => result.status === 'fulfilled')
          .map((result) => result.value),
      );
    };
    loadSearchCatalog().catch((loadError) => {
      if (loadError.name !== 'AbortError') setSearchCatalog([]);
    });
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

  const filteredChapters = useMemo(() => {
    const queryMatches = !normalizedCatalogQuery
      ? chapters
      : searchMatches
        ? searchMatches.map(({ chapter }) => chapter)
        : [];
    if (catalogStatus === 'all') return queryMatches;
    return queryMatches.filter((chapter) => {
      const status = String(chapter.status || chapter.learning_status || '').toLowerCase();
      const progress = Number(chapter.progress ?? chapter.progress_rate ?? 0);
      if (catalogStatus === 'completed') return status === 'completed' || progress >= 1;
      if (catalogStatus === 'in_progress') {
        return ['current', 'in_progress', 'learning'].includes(status) || (progress > 0 && progress < 1);
      }
      return !status || ['pending', 'not_started', 'locked'].includes(status) || progress <= 0;
    });
  }, [catalogStatus, chapters, normalizedCatalogQuery, searchMatches]);
  const filteredSections = useMemo(() => {
    if (!normalizedCatalogQuery) return sections;
    const match = searchMatches?.find(({ chapter }) => chapter.id === selectedChapter?.id);
    return match?.sections || [];
  }, [normalizedCatalogQuery, searchMatches, sections, selectedChapter?.id]);
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

  const textbookReturnIntent = {
    page: 'practice',
    params: { ...navigationContext, view: 'textbook-chapters' },
  };

  const openCourseTool = (taskType) => onNavigate?.({
    page: 'practice',
    params: {
      view: 'workspace',
      taskType,
      returnTo: textbookReturnIntent,
    },
  });

  return (
    <main className="textbook-chapter-learning">
      <header className="textbook-chapter-learning__hero">
        <button className="textbook-chapter-learning__back" type="button" onClick={() => onNavigate?.({ page: 'practice', params: {} })}>
          <ArrowLeft aria-hidden="true" size={14} />返回
        </button>
        <div className="textbook-chapter-learning__cover">
          <img src={textbookCoverUrl(book)} alt={`《${book}》教材封面`} />
        </div>
        <div className="textbook-chapter-learning__intro">
          <span>学习工坊 · 教材章节学习</span>
          <h1>《{book || '教材章节'}》</h1>
          <p>{textbookIntroduction(book)}</p>
          <div className="textbook-chapter-learning__stats">
            <span><BookOpen aria-hidden="true" size={14} />{chapters.length} 个章节</span>
            <span><Film aria-hidden="true" size={14} />章节视频与知识点片段</span>
          </div>
        </div>
        <div className="textbook-chapter-learning__progress" aria-label="课程学习进度">
          <div className="textbook-progress-ring"><strong>0%</strong><span>学习进度</span></div>
          <button type="button" className="textbook-start-learning" onClick={() => {
            const firstChapter = chapters[0];
            if (firstChapter) setSelectedChapter(firstChapter);
          }}>
            <PlayCircle aria-hidden="true" size={18} />开始学习
          </button>
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
            <span className="is-active"><BookOpen aria-hidden="true" size={18} />课程内容</span>
            <button type="button" onClick={() => openCourseTool('question_training')}><Layers3 aria-hidden="true" size={18} />作业与考试</button>
            <button type="button" onClick={() => onNavigate?.({
              page: 'knowledge',
              params: { view: 'atlas', route, lv1: book, source: 'textbook-chapters' },
            })}><Layers3 aria-hidden="true" size={18} />知识图谱</button>
            <button type="button" onClick={() => openCourseTool('study_notes')}><BookOpen aria-hidden="true" size={18} />学习笔记</button>
          </aside>
          <div className="textbook-learning-main">
            <div className="textbook-learning-main__toolbar">
              <div><h2>课程内容</h2><p>共 {chapters.length} 个章节 · 章节视频与知识点片段</p></div>
              <div className="textbook-learning-filters">
                <label><Search aria-hidden="true" size={15} /><input aria-label="搜索章节、小节或知识点" value={catalogQuery} onChange={(event) => setCatalogQuery(event.target.value)} placeholder="搜索章节、小节或知识点" /></label>
                {[
                  ['all', '全部'],
                  ['pending', '未完成'],
                  ['in_progress', '学习中'],
                  ['completed', '已完成'],
                ].map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    className={catalogStatus === value ? 'is-active' : ''}
                    aria-pressed={catalogStatus === value}
                    onClick={() => setCatalogStatus(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          {!selectedSection && <div className={`textbook-catalog-stage ${selectedChapter ? 'has-chapter' : ''}`}>
            <Directory
              title="章节"
              icon={BookOpen}
              className="textbook-directory--chapters"
              items={filteredChapters}
              selectedId={selectedChapter?.id}
              onSelect={(chapter) => {
                if (!chapter) {
                  setSelectedChapter(null);
                  setSelectedSection(null);
                  setDetail(null);
                  setVideoHistory([]);
                  return;
                }
                setSelectedChapter(chapter);
                setSelectedSection(null);
                setDetail(null);
                setVideoHistory([]);
              }}
              emptyText={
                normalizedCatalogQuery
                  ? '没有找到匹配的章节或小节。'
                  : catalogStatus !== 'all'
                    ? '当前筛选条件下没有章节。'
                    : '该教材暂无章节数据。'
              }
              unitLabel="个小节"
              selectedExtra={selectedChapter ? (
                <Directory
                  title=""
                  icon={Layers3}
                  className="textbook-directory--sections"
                  items={filteredSections}
                  selectedId={selectedSection?.id}
                  onSelect={(section) => {
                    setSelectedSection(section);
                    setDetail(null);
                    setVideoHistory([]);
                  }}
                  emptyText={normalizedCatalogQuery ? '没有找到匹配的小节。' : '该章节暂无小节数据。'}
                  unitLabel="个知识点"
                />
              ) : null}
            />
          </div>}

          {selectedSection && (
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
          </div>
        </div>
      )}
    </main>
  );
}
