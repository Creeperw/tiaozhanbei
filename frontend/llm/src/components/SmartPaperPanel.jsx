import React, { useEffect, useState } from 'react';
import { ChevronDown, Loader2, Sprout } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { generateWorkshopPaperWithAgents, loadPaper, loadPapers } from '../pageDataLoaders';
import PaperGenerationPanel from './PaperGenerationPanel';

const questionTypes = [
  ['single_choice', '单选题'], ['multiple_choice', '多选题'], ['fill_blank', '填空题'], ['short_answer', '简答题'], ['case_quiz', '案例题'],
];

export default function SmartPaperPanel({ enabled, paperId = '' }) {
  const [open, setOpen] = useState('compose');
  const [papers, setPapers] = useState([]);
  const [kind, setKind] = useState('special');
  const [topic, setTopic] = useState('');
  const [distribution, setDistribution] = useState({ single_choice: 5, multiple_choice: 0, fill_blank: 0, short_answer: 0, case_quiz: 0 });
  const [answerMode, setAnswerMode] = useState('practice');
  const [duration, setDuration] = useState(60);
  const [activePaperId, setActivePaperId] = useState(paperId);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { loadPapers({ fetcher: fetchJsonWithAuthFallback }).then((result) => !result.error && setPapers(result.papers.items)); }, []);
  if (activePaperId) return <PaperGenerationPanel enabled paperId={activePaperId} />;
  const total = Object.values(distribution).reduce((sum, value) => sum + value, 0);
  const generate = async () => {
    if (!total || total > 50 || (kind === 'special' && !topic.trim())) { setError('请填写专项练主题，并设置 1 至 50 道题的题型分布。'); return; }
    setLoading(true); setError('');
    try {
      const response = await generateWorkshopPaperWithAgents({
        fetcher: fetchJsonWithAuthFallback,
        topic: kind === 'free' ? '根据当前薄弱知识点、近期错题和待复习内容生成随心练' : topic.trim(),
        distribution: Object.fromEntries(Object.entries(distribution).filter(([, value]) => value > 0)),
        answerMode,
        durationMinutes: answerMode === 'test' ? duration : null,
      });
      if (response.error) throw new Error(response.error);
      const loaded = await loadPaper({ fetcher: fetchJsonWithAuthFallback, paperId: response.paperId });
      if (loaded.error) throw new Error(loaded.error);
      setActivePaperId(response.paperId);
    } catch (reason) { setError(reason.message || '生成试卷失败'); } finally { setLoading(false); }
  };
  return <div className="mt-5 space-y-3"><Fold title="历史存档" open={open === 'history'} onClick={() => setOpen(open === 'history' ? '' : 'history')}><PaperList papers={papers.filter((item) => item.status !== 'published')} onOpen={setActivePaperId} empty="暂无历史存档" /></Fold><Fold title="待办试卷" open={open === 'pending'} onClick={() => setOpen(open === 'pending' ? '' : 'pending')}><PaperList papers={papers.filter((item) => item.status === 'published')} onOpen={setActivePaperId} empty="暂无待办试卷" /></Fold><Fold title="智能合成" open={open === 'compose'} onClick={() => setOpen(open === 'compose' ? '' : 'compose')}><div className="grid gap-4 md:grid-cols-[3fr_2fr]"><section className={`border p-4 ${kind === 'special' ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200'}`}><h2 className="font-semibold text-slate-950">专项练</h2><div className="mt-3 flex border border-slate-300 bg-white"><input aria-label="专项练主题" value={topic} onFocus={() => setKind('special')} onChange={(event) => setTopic(event.target.value)} placeholder="输入想练习的主题" className="min-w-0 flex-1 px-3 py-2 text-sm outline-none" /><button type="button" onClick={() => setKind('special')} className="bg-emerald-700 px-3 text-sm font-medium text-white">开始生成</button></div><p className="mt-2 text-xs text-slate-600">输入你想练习的知识点吧</p></section><section className={`border p-4 ${kind === 'free' ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200'}`}><Sprout size={22} className="text-emerald-700" /><h2 className="mt-2 font-semibold text-slate-950">随心练</h2><button type="button" onClick={() => setKind('free')} className="mt-3 bg-slate-900 px-3 py-2 text-sm font-medium text-white">开始生成</button><p className="mt-2 text-xs text-slate-600">不知道练什么，开启随心练吧</p></section></div><fieldset className="mt-5"><legend className="text-sm font-medium text-slate-800">题型分布</legend><div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">{questionTypes.map(([key, label]) => <label key={key} className="text-sm text-slate-700">{label}<input aria-label={label} type="number" min="0" max="50" value={distribution[key]} onChange={(event) => setDistribution({ ...distribution, [key]: Math.max(0, Math.min(50, Number(event.target.value) || 0)) })} className="mt-1 w-full border border-slate-300 px-2 py-2" /></label>)}</div></fieldset><div className="mt-4 flex flex-wrap items-end gap-3"><label className="text-sm text-slate-700">作答模式<select aria-label="作答模式" value={answerMode} onChange={(event) => setAnswerMode(event.target.value)} className="ml-2 border border-slate-300 bg-white px-2 py-2"><option value="practice">练习模式</option><option value="test">测试模式</option></select></label>{answerMode === 'test' && <label className="text-sm text-slate-700">时长<input aria-label="测试时长" type="number" min="10" max="300" value={duration} onChange={(event) => setDuration(Math.max(10, Math.min(300, Number(event.target.value) || 60)))} className="ml-2 w-20 border border-slate-300 px-2 py-2" /> 分钟</label>}<button type="button" onClick={generate} disabled={loading} className="inline-flex items-center gap-2 bg-emerald-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{loading && <Loader2 size={16} className="animate-spin" />}{loading ? '正在生成' : '生成试卷'}</button></div></Fold>{error && <p role="alert" className="border border-rose-300 bg-rose-50 p-3 text-sm text-rose-800">{error}</p>}</div>;
}

function Fold({ title, open, onClick, children }) { return <section className="border border-slate-200"><button type="button" onClick={onClick} className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-semibold text-slate-900">{title}<ChevronDown size={18} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} /></button>{open && <div className="border-t border-slate-200 p-4">{children}</div>}</section>; }
function PaperList({ papers, onOpen, empty }) { return papers.length ? <div className="space-y-2">{papers.map((paper) => <div key={paper.paper_id} className="flex items-center justify-between gap-3 border border-slate-200 p-3"><span><strong className="block text-sm text-slate-900">{paper.title}</strong><span className="text-xs text-slate-500">{paper.status === 'published' ? '进行中' : '已完成'} · {paper.duration_minutes} 分钟</span></span><button type="button" onClick={() => onOpen(paper.paper_id)} className="border border-emerald-600 px-3 py-1.5 text-sm font-medium text-emerald-800">进入</button></div>)}</div> : <p className="text-sm text-slate-500">{empty}</p>; }
