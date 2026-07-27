import React, { useEffect, useMemo, useState } from 'react';
import {
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  Clock3,
  FileCheck2,
  History,
  ListChecks,
  Loader2,
  Minus,
  Plus,
  Sparkles,
  Sprout,
} from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { generateWorkshopPaperWithAgents, loadPaper, loadPapers } from '../pageDataLoaders';
import PaperGenerationPanel from './PaperGenerationPanel';

const questionTypes = [
  ['single_choice', '单选题'],
  ['multiple_choice', '多选题'],
  ['fill_blank', '填空题'],
  ['short_answer', '简答题'],
  ['case_quiz', '案例题'],
];

const sectionButton = 'inline-flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold transition duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:ring-offset-2 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-40';

export default function SmartPaperPanel({ paperId = '', taskItemId = '' }) {
  const [papers, setPapers] = useState([]);
  const [kind, setKind] = useState('special');
  const [topic, setTopic] = useState('');
  const [distribution, setDistribution] = useState({
    single_choice: 5,
    multiple_choice: 0,
    fill_blank: 0,
    short_answer: 0,
    case_quiz: 0,
  });
  const [answerMode, setAnswerMode] = useState('practice');
  const [duration, setDuration] = useState(60);
  const [activePaperId, setActivePaperId] = useState(paperId);
  const [activeTaskItemId, setActiveTaskItemId] = useState(taskItemId);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    loadPapers({ fetcher: fetchJsonWithAuthFallback }).then((result) => {
      if (active && !result.error) setPapers(result.papers.items);
    });
    return () => { active = false; };
  }, []);

  const pendingPapers = useMemo(() => papers.filter((item) => item.status === 'published'), [papers]);
  const historyPapers = useMemo(() => papers.filter((item) => item.status !== 'published'), [papers]);
  const total = Object.values(distribution).reduce((sum, value) => sum + value, 0);
  const selectedTypes = questionTypes.filter(([key]) => distribution[key] > 0);

  if (activePaperId || activeTaskItemId) {
    return (
      <PaperGenerationPanel
        enabled
        paperId={activePaperId}
        taskItemId={activeTaskItemId}
        onExit={() => {
          setActivePaperId('');
          setActiveTaskItemId('');
        }}
      />
    );
  }

  const setCount = (key, value) => {
    setDistribution((current) => ({
      ...current,
      [key]: Math.max(0, Math.min(50, Number(value) || 0)),
    }));
  };

  const generate = async () => {
    if (!total || total > 50 || (kind === 'special' && !topic.trim())) {
      setError('请填写专项练主题，并设置 1 至 50 道题的题型分布。');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const response = await generateWorkshopPaperWithAgents({
        fetcher: fetchJsonWithAuthFallback,
        topic: kind === 'free'
          ? '根据当前薄弱知识点、近期错题和待复习内容生成随心练'
          : topic.trim(),
        distribution: Object.fromEntries(Object.entries(distribution).filter(([, value]) => value > 0)),
        answerMode,
        durationMinutes: answerMode === 'test' ? duration : null,
        taskItemId: activeTaskItemId,
      });
      if (response.error) throw new Error(response.error);
      const loaded = await loadPaper({ fetcher: fetchJsonWithAuthFallback, paperId: response.paperId });
      if (loaded.error) throw new Error(loaded.error);
      setActivePaperId(response.paperId);
    } catch (reason) {
      setError(reason.message || '生成试卷失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="mt-5 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <header className="relative overflow-hidden border-b border-emerald-100 bg-[radial-gradient(circle_at_top_right,rgba(167,243,208,0.45),transparent_42%),linear-gradient(135deg,#f7fcf8,#eef8f1)] px-5 pb-6 pt-5 sm:px-7">
        <div className="relative z-[1] max-w-2xl">
          <span className="inline-flex items-center gap-2 text-xs font-semibold tracking-wide text-emerald-700"><BrainCircuit size={16} />智能组卷</span>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">把学习目标变成一张可作答的试卷</h2>
          <p className="mt-2 max-w-xl text-sm leading-6 text-slate-600">选择练习主题、题型和作答方式，系统完成检索、补题与审核后再发布试卷。</p>
        </div>
        <div className="pointer-events-none absolute -right-10 -top-16 h-44 w-44 rounded-full border-[28px] border-white/50" aria-hidden="true" />
      </header>

      <section className="smart-paper__archive-grid grid gap-3 border-b border-slate-200 bg-slate-50/70 p-4 md:grid-cols-2 sm:p-5" role="region" aria-label="试卷存档">
        <article className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3"><h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900"><ListChecks size={16} className="text-emerald-700" />待办试卷</h3><span className="rounded-md bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800">{pendingPapers.length}</span></header>
          <PaperList papers={pendingPapers} onOpen={setActivePaperId} empty="当前没有待作答试卷" mode="pending" compact />
        </article>
        <article className="overflow-hidden rounded-xl border border-slate-200 bg-white">
          <header className="flex items-center justify-between border-b border-slate-100 px-4 py-3"><h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900"><History size={16} className="text-slate-600" />历史存档</h3><span className="rounded-md bg-slate-100 px-2 py-0.5 text-xs font-semibold text-slate-600">{historyPapers.length}</span></header>
          <PaperList papers={historyPapers} onOpen={setActivePaperId} empty="完成的试卷会保存在这里" mode="history" compact />
        </article>
      </section>

      <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_18rem]">
        <main className="min-w-0 space-y-7 p-5 sm:p-7">
          <section aria-labelledby="paper-source-title">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div><h3 id="paper-source-title" className="text-base font-semibold text-slate-950">1. 选择出题范围</h3><p className="mt-1 text-sm leading-6 text-slate-500">明确主题，或交给系统依据学习状态选择。</p></div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <button type="button" aria-pressed={kind === 'special'} onClick={() => setKind('special')} className={`rounded-xl border p-4 text-left transition duration-200 ${kind === 'special' ? 'border-emerald-500 bg-emerald-50 shadow-sm' : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50'}`}>
                <span className="flex items-center gap-2 text-sm font-semibold text-slate-950"><FileCheck2 size={18} className="text-emerald-700" />专项练</span>
                <span className="mt-2 block text-xs leading-5 text-slate-600">围绕指定教材章节、知识点或能力目标出题。</span>
              </button>
              <button type="button" aria-pressed={kind === 'free'} onClick={() => setKind('free')} className={`rounded-xl border p-4 text-left transition duration-200 ${kind === 'free' ? 'border-emerald-500 bg-emerald-50 shadow-sm' : 'border-slate-200 hover:border-slate-300 hover:bg-slate-50'}`}>
                <span className="flex items-center gap-2 text-sm font-semibold text-slate-950"><Sprout size={18} className="text-emerald-700" />随心练</span>
                <span className="mt-2 block text-xs leading-5 text-slate-600">结合薄弱知识点、近期错题和复习队列智能选题。</span>
              </button>
            </div>
            {kind === 'special' && <label className="mt-4 block text-sm font-semibold text-slate-800">练习主题
              <textarea aria-label="专项练主题" value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="例如：四君子汤的组成、功效主治与配伍意义" className="mt-2 min-h-24 w-full resize-y rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 outline-none transition focus:border-emerald-500 focus:bg-white focus:ring-4 focus:ring-emerald-100" />
            </label>}
            {kind === 'free' && <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm leading-6 text-emerald-900">系统会读取当前学习计划、掌握度、错题和待复习知识点，组合本次试卷范围。</div>}
          </section>

          <section aria-labelledby="paper-types-title">
            <div><h3 id="paper-types-title" className="text-base font-semibold text-slate-950">2. 设置题型与题量</h3><p className="mt-1 text-sm leading-6 text-slate-500">支持单一题型和混合组卷，总题量不超过 50 题。</p></div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {questionTypes.map(([key, label]) => (
                <div key={key} className={`rounded-xl border p-3 transition ${distribution[key] > 0 ? 'border-emerald-300 bg-emerald-50/60' : 'border-slate-200 bg-white'}`}>
                  <label htmlFor={`paper-count-${key}`} className="block text-sm font-semibold text-slate-800">{label}</label>
                  <div className="mt-3 flex items-center rounded-lg border border-slate-200 bg-white">
                    <button type="button" aria-label={`减少${label}`} onClick={() => setCount(key, distribution[key] - 1)} disabled={distribution[key] === 0} className="inline-flex h-9 w-9 items-center justify-center text-slate-500 transition hover:bg-slate-50 hover:text-slate-900 disabled:opacity-30"><Minus size={14} /></button>
                    <input id={`paper-count-${key}`} aria-label={label} type="number" min="0" max="50" value={distribution[key]} onChange={(event) => setCount(key, event.target.value)} className="h-9 min-w-0 flex-1 border-x border-slate-200 bg-transparent text-center text-sm font-semibold tabular-nums text-slate-900 outline-none" />
                    <button type="button" aria-label={`增加${label}`} onClick={() => setCount(key, distribution[key] + 1)} disabled={total >= 50} className="inline-flex h-9 w-9 items-center justify-center text-slate-500 transition hover:bg-slate-50 hover:text-slate-900 disabled:opacity-30"><Plus size={14} /></button>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section aria-labelledby="paper-mode-title">
            <div><h3 id="paper-mode-title" className="text-base font-semibold text-slate-950">3. 选择作答方式</h3><p className="mt-1 text-sm leading-6 text-slate-500">练习模式适合巩固，测试模式适合阶段验收。</p></div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <button type="button" aria-pressed={answerMode === 'practice'} onClick={() => setAnswerMode('practice')} className={`rounded-xl border p-4 text-left transition duration-200 ${answerMode === 'practice' ? 'border-emerald-500 bg-emerald-50 shadow-sm' : 'border-slate-200 hover:border-slate-300'}`}>
                <span className="flex items-center gap-2 text-sm font-semibold text-slate-950"><CheckCircle2 size={18} className="text-emerald-700" />练习模式</span>
                <span className="mt-2 block text-xs leading-5 text-slate-600">完成后逐题查看答案、解析和专家批改结果。</span>
              </button>
              <button type="button" aria-pressed={answerMode === 'test'} onClick={() => setAnswerMode('test')} className={`rounded-xl border p-4 text-left transition duration-200 ${answerMode === 'test' ? 'border-emerald-500 bg-emerald-50 shadow-sm' : 'border-slate-200 hover:border-slate-300'}`}>
                <span className="flex items-center gap-2 text-sm font-semibold text-slate-950"><Clock3 size={18} className="text-emerald-700" />测试模式</span>
                <span className="mt-2 block text-xs leading-5 text-slate-600">限时完成整张试卷，交卷后统一查看结果。</span>
              </button>
            </div>
            {answerMode === 'test' && <label className="mt-4 inline-flex items-center gap-3 text-sm font-semibold text-slate-800">测试时长
              <span className="inline-flex items-center rounded-lg border border-slate-300 bg-white px-3 py-2"><input aria-label="测试时长" type="number" min="10" max="300" value={duration} onChange={(event) => setDuration(Math.max(10, Math.min(300, Number(event.target.value) || 60)))} className="w-14 bg-transparent text-right font-semibold tabular-nums outline-none" /><span className="ml-1 text-slate-500">分钟</span></span>
            </label>}
          </section>
        </main>

        <aside className="border-t border-slate-200 bg-slate-50/70 p-5 xl:border-l xl:border-t-0" aria-label="组卷预览">
          <div className="sticky top-5">
            <p className="text-xs font-semibold tracking-wide text-emerald-700">组卷预览</p>
            <p className="mt-2 text-3xl font-semibold tracking-tight tabular-nums text-slate-950">{total}<span className="ml-1 text-sm font-medium text-slate-500">题</span></p>
            <dl className="mt-5 space-y-3 border-y border-slate-200 py-4 text-sm">
              <div className="flex items-start justify-between gap-3"><dt className="text-slate-500">范围</dt><dd className="max-w-40 text-right font-medium text-slate-800">{kind === 'free' ? '基于学情智能选择' : topic.trim() || '尚未填写主题'}</dd></div>
              <div className="flex items-center justify-between gap-3"><dt className="text-slate-500">题型</dt><dd className="font-medium text-slate-800">{selectedTypes.length || 0} 种</dd></div>
              <div className="flex items-center justify-between gap-3"><dt className="text-slate-500">模式</dt><dd className="font-medium text-slate-800">{answerMode === 'test' ? `测试 · ${duration} 分钟` : '练习'}</dd></div>
              <div className="flex items-center justify-between gap-3"><dt className="text-slate-500">发布门禁</dt><dd className="font-medium text-emerald-700">智能体审核</dd></div>
            </dl>
            <button type="button" onClick={generate} disabled={loading || !total || total > 50 || (kind === 'special' && !topic.trim())} className={`${sectionButton} mt-5 w-full border-emerald-700 bg-emerald-700 px-4 py-3 text-white shadow-[0_10px_24px_rgba(22,101,52,0.16)] hover:-translate-y-0.5 hover:bg-emerald-800`}>
              {loading ? <Loader2 size={17} className="animate-spin" /> : <Sparkles size={17} />}
              {loading ? '正在组卷并审核' : '生成试卷'}
            </button>
            <p className="mt-3 text-xs leading-5 text-slate-500">审核通过后进入单题作答界面；不在对话区展开试卷正文。</p>
          </div>
        </aside>
      </div>
      {error && <p role="alert" className="mx-5 mb-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-800">{error}</p>}
    </div>
  );
}

function PaperList({ papers, onOpen, empty, mode, compact = false }) {
  if (!papers.length) {
    return <div className={`grid place-items-center px-5 text-center ${compact ? 'min-h-32 py-6' : 'min-h-64 py-12'}`}><div><FileCheck2 className="mx-auto text-slate-300" size={compact ? 26 : 34} /><h3 className="mt-3 text-sm font-semibold text-slate-800">{empty}</h3><p className="mt-1 text-xs leading-5 text-slate-500">{mode === 'pending' ? '生成并审核通过的试卷将自动进入待办。' : '提交试卷后可随时回来查看结果与解析。'}</p></div></div>;
  }
  return (
    <div className={`grid gap-3 ${compact ? 'max-h-52 overflow-y-auto p-3' : 'p-5 sm:p-7 md:grid-cols-2'}`}>
      {papers.map((paper) => (
        <article key={paper.paper_id} className={`group flex flex-col rounded-xl border border-slate-200 bg-white transition duration-200 hover:-translate-y-0.5 hover:border-emerald-300 hover:shadow-md ${compact ? 'min-h-24 p-3' : 'min-h-32 p-4'}`}>
          <div className="flex items-start justify-between gap-3">
            <span className={`rounded-md px-2 py-1 text-xs font-semibold ${mode === 'pending' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}`}>{mode === 'pending' ? '待作答' : '已完成'}</span>
            <span className="inline-flex items-center gap-1 text-xs text-slate-500"><Clock3 size={13} />{paper.duration_minutes} 分钟</span>
          </div>
          <h3 className="mt-3 text-sm font-semibold leading-6 text-slate-950">{paper.title}</h3>
          <button type="button" onClick={() => onOpen(paper.paper_id)} className="mt-auto inline-flex items-center gap-1 self-end pt-3 text-sm font-semibold text-emerald-700 transition group-hover:gap-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-600">
            {mode === 'pending' ? '开始答题' : '查看试卷'}<ChevronRight size={16} />
          </button>
        </article>
      ))}
    </div>
  );
}
