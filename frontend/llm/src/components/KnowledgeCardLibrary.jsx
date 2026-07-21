import React, { useEffect, useMemo, useState } from 'react';
import { BookOpen, CircleHelp, Film, Library, Loader2, Search } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { loadKnowledgeCard, loadKnowledgeCards, resolveKnowledgeCard } from '../pageDataLoaders';

const text = (value) => {
  if (value === null || value === undefined || value === '') return '暂无';
  if (typeof value === 'string' || typeof value === 'number') return String(value);
  if (Array.isArray(value)) return value.map(text).join('、');
  return JSON.stringify(value, null, 2);
};

const knowledgePointDescription = (knowledgePoint) => (
  knowledgePoint?.description
  || knowledgePoint?.summary
  || knowledgePoint?.objective
  || knowledgePoint?.content
  || ''
);

const explanationText = (value) => {
  if (typeof value !== 'string') {
    return value?.知识讲解 || value?.knowledge_explanation || value?.explanation || value;
  }
  const normalized = value.trim();
  if (!normalized.startsWith('{')) return value;
  try {
    const parsed = JSON.parse(normalized);
    return parsed?.知识讲解 || parsed?.knowledge_explanation || parsed?.explanation || value;
  } catch {
    return value;
  }
};

function ResourceHeading({ icon, title, count }) {
  return (
    <h4 className="flex items-center gap-2 text-sm font-semibold text-slate-900">
      {React.createElement(icon, { size: 16, className: 'text-emerald-700', 'aria-hidden': true })}
      {title} {count}
    </h4>
  );
}

export default function KnowledgeCardLibrary({ cardId = '', kpId = '' }) {
  const [cards, setCards] = useState([]);
  const [activeCard, setActiveCard] = useState(null);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const filteredCards = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return cards;
    return cards.filter((card) => `${card.title || ''} ${card.kp_id || ''}`.toLocaleLowerCase().includes(normalized));
  }, [cards, query]);

  const refresh = async () => {
    const result = await loadKnowledgeCards({ fetcher: fetchJsonWithAuthFallback });
    setCards(result.cards.items);
    if (result.error) setError(result.error);
  };

  useEffect(() => {
    let active = true;
    const load = async () => {
      setLoading(true);
      setError('');
      const detail = cardId
        ? await loadKnowledgeCard({ fetcher: fetchJsonWithAuthFallback, cardId })
        : kpId
          ? await resolveKnowledgeCard({ fetcher: fetchJsonWithAuthFallback, kpId })
          : null;
      if (!active) return;
      if (detail?.error) setError(detail.error);
      if (detail?.card) setActiveCard(detail.card);
      await refresh();
      if (active) setLoading(false);
    };
    load();
    return () => { active = false; };
  }, [cardId, kpId]);

  const openCard = async (id) => {
    setLoading(true);
    setError('');
    const result = await loadKnowledgeCard({ fetcher: fetchJsonWithAuthFallback, cardId: id });
    setActiveCard(result.card);
    setError(result.error);
    setLoading(false);
  };

  const bundle = activeCard?.resource_bundle || {};
  const knowledgePoint = bundle.knowledge_point || {};
  const textbookSlices = Array.isArray(bundle.textbook_slices) ? bundle.textbook_slices : [];
  const videos = Array.isArray(bundle.videos) ? bundle.videos : [];
  const questions = Array.isArray(bundle.questions) ? bundle.questions : [];
  const fallbackUsed = Array.isArray(bundle.coverage?.fallback_used) ? bundle.coverage.fallback_used : [];

  return (
    <div className="knowledge-card-library mt-5 grid gap-5 lg:grid-cols-[240px_minmax(0,1fr)]">
      <aside className="knowledge-card-library__rail rounded-2xl border border-slate-200 bg-slate-50 p-3" aria-label="已学习知识点">
        <div className="flex items-center gap-2 px-2 pb-3 text-sm font-semibold text-slate-900">
          <Library size={16} className="text-emerald-700" aria-hidden="true" />
          已学习知识点
          {cards.length > 0 && <span className="ml-auto text-xs font-medium text-slate-500">{cards.length}</span>}
        </div>
        {cards.length > 0 && (
          <label className="relative mb-3 block">
            <span className="sr-only">筛选知识卡</span>
            <Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" aria-hidden="true" />
            <input
              type="search"
              aria-label="筛选知识卡"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索知识点"
              className="h-10 w-full rounded-xl border border-slate-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-emerald-300 focus:ring-2 focus:ring-emerald-100"
            />
          </label>
        )}
        {cards.length === 0 && !loading && <p className="px-2 py-4 text-sm leading-6 text-slate-500">学习记录会按知识点保存在这里。</p>}
        {cards.length > 0 && filteredCards.length === 0 && <p className="px-2 py-4 text-sm leading-6 text-slate-500">没有匹配的知识卡。</p>}
        <div className="space-y-1">
          {filteredCards.map((card) => (
            <button key={card.card_id} type="button" onClick={() => openCard(card.card_id)} className={`w-full rounded-xl px-3 py-2.5 text-left text-sm transition ${activeCard?.card_id === card.card_id ? 'bg-white font-semibold text-emerald-900 shadow-sm ring-1 ring-emerald-100' : 'text-slate-700 hover:bg-white'}`}>
              {card.title}
            </button>
          ))}
        </div>
      </aside>

      <section className="knowledge-card-library__content min-w-0 rounded-2xl border border-slate-200 bg-white p-5" aria-live="polite">
        {loading && <p className="flex items-center gap-2 text-sm text-slate-600"><Loader2 size={16} className="animate-spin" />正在加载知识卡…</p>}
        {!loading && !activeCard && (
          <div className="flex min-h-64 flex-col items-center justify-center px-4 text-center">
            <div className="mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-emerald-50 text-emerald-700"><BookOpen size={23} /></div>
            <h3 className="text-base font-semibold text-slate-900">{cards.length === 0 ? '还没有知识卡' : '选择一张知识卡'}</h3>
            <p className="mt-2 max-w-md text-sm leading-6 text-slate-500">{cards.length === 0 ? '完成知识讲解和配套题目后，系统会把知识点、教材证据、视频和题目整理到这里。' : '从左侧选择已学知识点，查看完整讲解、教材切片和配套资源。'}</p>
          </div>
        )}
        {activeCard && !loading && (
          <article className="space-y-6" aria-label={`${activeCard.title}知识卡`}>
            <header className="border-b border-slate-200 pb-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold tracking-wide text-emerald-700">知识卡</p>
                  <h3 className="mt-1 text-xl font-semibold text-slate-950">{activeCard.title}</h3>
                  <p className="mt-1 font-mono text-xs text-slate-500">{activeCard.kp_id}</p>
                </div>
                {fallbackUsed.length > 0 && <span className="rounded-full border border-sky-200 bg-sky-50 px-2.5 py-1 text-xs font-medium text-sky-800">含网络补充资源</span>}
              </div>
              {knowledgePointDescription(knowledgePoint) && <p className="mt-4 whitespace-pre-wrap text-sm leading-7 text-slate-700">{text(knowledgePointDescription(knowledgePoint))}</p>}
            </header>

            <section>
              <ResourceHeading icon={BookOpen} title="知识点讲解" count="" />
              <p className="mt-2 whitespace-pre-wrap text-sm leading-7 text-slate-700">{text(explanationText(bundle.explanation?.content))}</p>
            </section>

            <section>
              <ResourceHeading icon={Library} title="教材切片" count={textbookSlices.length} />
              {textbookSlices.length > 0 ? textbookSlices.slice(0, 5).map((item, index) => (
                <blockquote key={item.chunk_uid || item.source_id || index} className="mt-3 border-l-2 border-emerald-200 pl-3 whitespace-pre-wrap text-sm leading-6 text-slate-700">{text(item.retrieval_text || item.text || item.summary)}</blockquote>
              )) : <p className="mt-2 text-sm text-slate-500">当前暂无教材切片。</p>}
            </section>

            <section>
              <ResourceHeading icon={Film} title="视频资源" count={videos.length} />
              {videos.length > 0 ? <div className="mt-3 grid gap-2 sm:grid-cols-2">{videos.map((item, index) => (
                <a key={item.source_id || index} href={item.url} target="_blank" rel="noreferrer" className="rounded-xl border border-slate-200 px-3 py-3 text-sm font-medium text-emerald-800 transition hover:border-emerald-300 hover:bg-emerald-50">{item.video_title || item.title || item.summary || '查看视频'}</a>
              ))}</div> : <p className="mt-2 text-sm text-slate-500">当前暂无视频资源。</p>}
            </section>

            <section>
              <ResourceHeading icon={CircleHelp} title="配套题目" count={questions.length} />
              {questions.length > 0 ? <ol className="mt-3 space-y-3">{questions.slice(0, 10).map((item, index) => (
                <li key={item.question_id || item.source_id || index} className="rounded-xl border border-slate-100 bg-slate-50 p-3 text-sm leading-6 text-slate-700">
                  <div className="mb-1 text-xs font-medium text-slate-500">{item.question_type || '练习题'}</div>
                  <p>{index + 1}. {item.stem || item.title || item.summary}</p>
                  {item.url && <a href={item.url} target="_blank" rel="noreferrer" className="mt-2 inline-block font-medium text-emerald-700 underline">打开网络题源</a>}
                </li>
              ))}</ol> : <p className="mt-2 text-sm text-slate-500">当前暂无配套题目。</p>}
            </section>
          </article>
        )}
        {error && <p role="alert" className="mt-4 rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">{error}</p>}
      </section>
    </div>
  );
}
