import React, { useEffect, useState } from 'react';
import { ArrowLeft, ChevronDown, Loader2, Sprout } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { generateWorkshopPaperWithAgents, loadPaper, loadPapers } from '../pageDataLoaders';
import PaperGenerationPanel from './PaperGenerationPanel';

const questionTypes = [
  ['single_choice', '单选题'], ['multiple_choice', '多选题'], ['fill_blank', '填空题'], ['short_answer', '简答题'], ['case_quiz', '案例题'],
];

export default function SmartPaperPanel({ enabled, paperId = '', onBack }) {
  const [open, setOpen] = useState('compose');
  const [papers, setPapers] = useState([]);
  const [kind, setKind] = useState('special');
  const [topic, setTopic] = useState('');
  const [distribution, setDistribution] = useState({ single_choice: 5, multiple_choice: 0, fill_blank: 0, short_answer: 0, case_quiz: 0 });
  const [answerMode, setAnswerMode] = useState('practice');
  const [duration, setDuration] = useState(60);
  const [activePaperId, setActivePaperId] = useState(paperId);
  const [loading, setLoading] = useState(false);
  const [stage, setStage] = useState('');
  const [error, setError] = useState('');
  const refreshPapers = async () => {
    const result = await loadPapers({ fetcher: fetchJsonWithAuthFallback });
    if (result.error) {
      setError(result.error);
      return;
    }
    setPapers(result.papers.items);
  };

  useEffect(() => { refreshPapers(); }, []);
  useEffect(() => { setActivePaperId(paperId); }, [paperId]);
  if (activePaperId) return <PaperGenerationPanel enabled paperId={activePaperId} onBack={() => setActivePaperId('')} />;
  const total = Object.values(distribution).reduce((sum, value) => sum + value, 0);
  const canGenerate = !loading && total > 0 && total <= 50 && (kind === 'free' || topic.trim());
  const disabledReason = total < 1 || total > 50
    ? '请设置 1 至 50 道题。'
    : kind === 'special' && !topic.trim()
      ? '专项练需要填写主题。'
      : '';
  const generate = async () => {
    if (!canGenerate) { setError(disabledReason || '请检查组卷条件。'); return; }
    setLoading(true); setError(''); setStage('正在解析组卷要求');
    try {
      setStage('正在制定蓝图并检索题库');
      const response = await generateWorkshopPaperWithAgents({
        fetcher: fetchJsonWithAuthFallback,
        topic: kind === 'free' ? '根据当前薄弱知识点、近期错题和待复习内容生成随心练' : topic.trim(),
        distribution: Object.fromEntries(Object.entries(distribution).filter(([, value]) => value > 0)),
        answerMode,
        durationMinutes: answerMode === 'test' ? duration : null,
      });
      if (response.error) throw new Error(response.error);
      setStage('正在审核并发布试卷');
      const loaded = await loadPaper({ fetcher: fetchJsonWithAuthFallback, paperId: response.paperId });
      if (loaded.error) throw new Error(loaded.error);
      setActivePaperId(response.paperId);
    } catch (reason) { setError(reason.message || '生成试卷失败'); } finally { setLoading(false); setStage(''); }
  };
  return <div className="flex h-full flex-col smart-paper" aria-live="polite">
    <header className="flex items-center gap-4 border-b border-slate-200 px-5 py-4">
      {onBack && <button type="button" onClick={onBack} className="inline-flex items-center gap-2 rounded-lg border-2 border-emerald-600 bg-white px-3 py-2 text-sm font-semibold text-emerald-700 shadow-sm transition hover:bg-emerald-50"><ArrowLeft size={16} />返回训练工坊</button>}
      <div><span className="text-xs font-medium tracking-[0.2em] text-emerald-700">研习案卷</span><h2 className="text-lg font-semibold text-slate-950">智能组卷</h2></div>
    </header>
    <div className="flex-1 overflow-y-auto px-5 py-4">
      <div className="space-y-3">
        <div className="smart-paper__archive">
          <Fold title="历史存档" index={0} open={open === 'history'} onClick={() => setOpen(open === 'history' ? '' : 'history')}><PaperList papers={papers.filter((item) => item.status !== 'published')} onOpen={setActivePaperId} empty="暂无历史存档" /></Fold>
          <Fold title="待办试卷" index={1} open={open === 'pending'} onClick={() => setOpen(open === 'pending' ? '' : 'pending')}><PaperList papers={papers.filter((item) => item.status === 'published')} onOpen={setActivePaperId} empty="暂无待办试卷" /></Fold>
        </div>
        <Fold title="智能合成" index={2} open={open === 'compose'} onClick={() => setOpen(open === 'compose' ? '' : 'compose')}>
          <section className="smart-paper__composer">
            <div className="smart-paper__composer-heading"><span>01 / 组卷依据</span><h3>选择训练路径</h3><p>正式题库优先；练习模式会明确标注审核生成补题，测试模式不会以补题替代正式试题。</p></div>
            <div className="grid gap-4 md:grid-cols-[3fr_2fr]">
              <section className={`smart-paper__mode ${kind === 'special' ? 'is-selected' : ''}`}><h2>专项练</h2><div className="mt-3 flex border border-slate-300 bg-white"><input name="paper-topic" aria-label="专项练主题" value={topic} onFocus={() => setKind('special')} onChange={(event) => setTopic(event.target.value)} placeholder="输入想练习的主题" className="min-w-0 flex-1 px-3 py-2 text-sm outline-none" /><button type="button" onClick={() => setKind('special')} className="bg-emerald-700 px-3 text-sm font-medium text-white">选择专项练</button></div><p>围绕你指定的知识点、题型与题量组卷。</p></section>
              <section className={`smart-paper__mode ${kind === 'free' ? 'is-selected' : ''}`}><Sprout size={22} className="text-emerald-700" /><h2 className="mt-2">随心练</h2><button type="button" onClick={() => setKind('free')} className="mt-3 bg-slate-900 px-3 py-2 text-sm font-medium text-white">选择随心练</button><p>优先覆盖薄弱知识点、近期错题与待复习内容。</p></section>
            </div>
            <fieldset className="smart-paper__distribution"><legend>02 / 题型分布</legend><div>{questionTypes.map(([key, label]) => <label key={key}>{label}<input name={`question-count-${key}`} aria-label={label} type="number" min="0" max="50" value={distribution[key]} onChange={(event) => setDistribution({ ...distribution, [key]: Math.max(0, Math.min(50, Number(event.target.value) || 0)) })} /></label>)}</div></fieldset>
            <div className="smart-paper__actions"><div><label>作答模式<select name="answer-mode" aria-label="作答模式" value={answerMode} onChange={(event) => setAnswerMode(event.target.value)}><option value="practice">练习模式</option><option value="test">测试模式</option></select></label>{answerMode === 'test' && <label>时长<input name="test-duration" aria-label="测试时长" type="number" min="10" max="300" value={duration} onChange={(event) => setDuration(Math.max(10, Math.min(300, Number(event.target.value) || 60)))} /> 分钟</label>}</div><button type="button" onClick={generate} disabled={!canGenerate} aria-describedby={disabledReason ? 'paper-generate-reason' : undefined}>{loading && <Loader2 size={16} className="animate-spin" />}{loading ? stage : '生成并审核试卷'}</button></div>
            {disabledReason && <p id="paper-generate-reason" className="smart-paper__hint">{disabledReason}</p>}
          </section>
        </Fold>
        {error && <p role="alert" className="smart-paper__error">{error}<button type="button" onClick={refreshPapers}>刷新试卷库</button></p>}
      </div>
    </div>
  </div>;
}

const foldIcons = { '历史存档': '📋', '待办试卷': '📝', '智能合成': '✨' };
const foldGradients = [
  'linear-gradient(135deg, #fff 0%, #f9fdfa 100%)',
  'linear-gradient(135deg, #f9fdfa 0%, #f0faf4 100%)',
  'linear-gradient(135deg, #f0faf4 0%, #e8f7ef 100%)',
];
function Fold({ title, open, onClick, children, index }) { return <section className="rounded-xl border border-transparent shadow-md overflow-hidden" style={{ background: foldGradients[index % 3] }}><button type="button" onClick={onClick} className="flex w-full items-center gap-3 px-5 py-4 text-left text-base font-bold text-slate-900"><span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl text-lg" style={{ background: 'linear-gradient(135deg, #ecfdf5, #d1fae5)', boxShadow: '0 2px 8px rgba(5,150,105,.1)' }}>{foldIcons[title] || '📁'}</span><span className="flex-1">{title}</span><ChevronDown size={18} className={open ? 'rotate-180 transition-transform' : 'transition-transform'} /></button>{open && <div className="border-t border-emerald-100 px-5 pb-4">{children}</div>}</section>; }
function PaperList({ papers, onOpen, empty }) { return papers.length ? <div className="space-y-2">{papers.map((paper) => <button key={paper.paper_id} type="button" onClick={() => onOpen(paper.paper_id)} className="w-full flex items-center justify-between gap-3 border border-slate-200 p-3 rounded-lg text-left hover:border-emerald-300 hover:bg-emerald-50 transition cursor-pointer"><span><strong className="block text-sm text-slate-900">{paper.title}</strong><span className="text-xs text-slate-500">{paper.status === 'published' ? '进行中' : '已完成'} · {paper.duration_minutes} 分钟</span></span></button>)}</div> : <p className="text-sm text-slate-500">{empty}</p>; }
