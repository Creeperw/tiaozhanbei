import React, { useEffect, useState } from 'react';
import {
  BrainCircuit,
  CheckCircle2,
  ChevronRight,
  Clock3,
  Download,
  FileCheck2,
  ListChecks,
  Loader2,
  Minus,
  Plus,
  Sparkles,
  Sprout,
} from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { generateWorkshopPaperWithAgents, loadPaper, loadPapers, loadReportsData } from '../pageDataLoaders';
import PaperGenerationPanel from './PaperGenerationPanel';

const questionTypes = [
  ['single_choice', '单选题'],
  ['multiple_choice', '多选题'],
  ['fill_blank', '填空题'],
  ['short_answer', '简答题'],
  ['case_quiz', '案例题'],
];

const sectionButton = 'inline-flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold transition duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:ring-offset-2 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-40';
const fallbackTopicRecommendations = [
  '四君子汤的组成、功效主治与配伍意义',
  '气血津液辨证与常见证候鉴别',
  '中药功效、归经与配伍禁忌',
];

function buildTopicRecommendations(report) {
  const weakPointNames = (Array.isArray(report?.weak_points) ? report.weak_points : [])
    .map((item) => item?.kp_name || item?.name || '')
    .map((name) => String(name).trim())
    .filter(Boolean);
  return [...new Set([...weakPointNames, ...fallbackTopicRecommendations])].slice(0, 4);
}

export default function SmartPaperPanel({ paperId = '', taskItemId = '' }) {
  const [papers, setPapers] = useState([]);
  const [topicRecommendations, setTopicRecommendations] = useState(fallbackTopicRecommendations);
  const [recommendationsLoading, setRecommendationsLoading] = useState(true);
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

  useEffect(() => {
    let active = true;
    loadReportsData({ fetcher: fetchJsonWithAuthFallback })
      .then((result) => {
        if (!active) return;
        setTopicRecommendations(buildTopicRecommendations(result.report));
      })
      .catch(() => {})
      .finally(() => {
        if (active) setRecommendationsLoading(false);
      });
    return () => { active = false; };
  }, []);

  const total = Object.values(distribution).reduce((sum, value) => sum + value, 0);
  const selectedTypes = questionTypes.filter(([key]) => distribution[key] > 0);

  const handleDownloadPaper = async (paperId, title, format) => {
    try {
      const result = await loadPaper({ fetcher: fetchJsonWithAuthFallback, paperId });
      if (result.error || !result.paper?.items?.length) return;
      const p = result.paper;
      const safeName = (title || '试卷').replace(/[\\/:*?"<>|]/g, '_');
      const lines = [];
      lines.push('# ' + (title || p.title || '试卷'));
      if (p.total_score) lines.push('**满分**：' + p.total_score + ' 分  ·  **题量**：' + p.items.length + ' 题');
      lines.push('---');
      p.items.forEach((item) => {
        lines.push('## ' + (item.position || '') + '. ' + (item.question_type || '题目'));
        lines.push(String(item.stem || '').replace(/<[^>]+>/g, ''));
        (Array.isArray(item.options) ? item.options : []).forEach((opt, i) => {
          const label = String.fromCharCode(65 + i);
          let text = typeof opt === 'string' ? opt : (opt.content || opt.value || opt.text || '');
          text = String(text || '').replace(/<[^>]+>/g, '').trim().replace(/^[A-Z][.．、)\s]\s*/, '');
          lines.push('- ' + label + '. ' + text);
        });
        lines.push('---');
      });
      const md = lines.join('\n');
      if (format === 'md') {
        const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
        const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = safeName + '.md'; a.click(); URL.revokeObjectURL(url);
      } else if (format === 'doc') {
        const html = '<!DOCTYPE html><html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="utf-8"><title>'+title+'</title><style>@page{margin:1.5cm}body{font-family:"Microsoft YaHei",sans-serif;max-width:760px;margin:0 auto;padding:10px;font-size:11pt;line-height:1.4}h1{font-size:13pt}h2{font-size:10.5pt;margin:12px 0 3px}hr{border:0;border-top:1px solid #e5e7eb;margin:6px 0}li{margin:1px 0;font-size:11pt}</style></head><body>'
          + md.split('\n').map(l=>{if(l.startsWith('# '))return'<h1>'+l.slice(2)+'</h1>';if(l.startsWith('## '))return'<h2>'+l.slice(3)+'</h2>';if(l.startsWith('- '))return'<li>'+l.slice(2)+'</li>';if(l==='---')return'<hr>';if(l==='')return'';return'<p>'+l+'</p>';}).join('\n')+'</body></html>';
        const blob = new Blob([html], { type: 'application/msword;charset=utf-8' });
        const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = safeName + '.doc'; a.click(); URL.revokeObjectURL(url);
      } else if (format === 'png') {
        const html = '<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{font-family:"Microsoft YaHei",sans-serif;max-width:760px;margin:0 auto;padding:20px;font-size:13px;line-height:1.45;color:#1a1a1a}h1{font-size:1.15em}h2{font-size:.95em}hr{border:0;border-top:1px solid #e5e7eb;margin:8px 0}li{margin:1px 0;font-size:13px}</style></head><body>'
          + md.split('\n').map(l=>{if(l.startsWith('# '))return'<h1>'+l.slice(2)+'</h1>';if(l.startsWith('## '))return'<h2>'+l.slice(3)+'</h2>';if(l.startsWith('- '))return'<li>'+l.slice(2)+'</li>';if(l==='---')return'<hr>';if(l==='')return'';return'<p>'+l+'</p>';}).join('\n')+'</body></html>';
        const container = document.createElement('div');
        container.style.cssText = 'position:fixed;left:-9999px;top:0;width:800px;background:#fff;padding:40px;font-family:"Microsoft YaHei",sans-serif;font-size:13px;z-index:-1';
        container.innerHTML = html;
        document.body.appendChild(container);
        const { default: h2c } = await import('html2canvas');
        try { const canvas = await h2c(container, { scale: 2, backgroundColor: '#ffffff', logging: false });
          canvas.toBlob((blob) => { if (blob) { const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = safeName + '.png'; a.click(); URL.revokeObjectURL(url); } }, 'image/png');
        } finally { document.body.removeChild(container); }
      } else if (format === 'pdf') {
        const html = '<!DOCTYPE html><html><head><meta charset="utf-8"><style>body{font-family:"Microsoft YaHei",sans-serif;max-width:760px;margin:0 auto;padding:20px;font-size:13px;line-height:1.45}h1{font-size:1.15em}h2{font-size:.95em}hr{border:0;border-top:1px solid #e5e7eb;margin:8px 0}li{margin:1px 0}@media print{@page{margin:1cm}}</style></head><body>'
          + md.split('\n').map(l=>{if(l.startsWith('# '))return'<h1>'+l.slice(2)+'</h1>';if(l.startsWith('## '))return'<h2>'+l.slice(3)+'</h2>';if(l.startsWith('- '))return'<li>'+l.slice(2)+'</li>';if(l==='---')return'<hr>';if(l==='')return'';return'<p>'+l+'</p>';}).join('\n')+'</body></html>';
        const w = window.open('', '_blank', 'width=800,height=600');
        if (w) { w.document.write(html); w.document.close(); w.focus(); w.print(); }
      }
    } catch {}
  };

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
        topic: kind === 'plus'
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
      const refreshed = await loadPapers({ fetcher: fetchJsonWithAuthFallback });
      if (!refreshed.error) setPapers(refreshed.papers.items);
      setActivePaperId(response.paperId);
    } catch (reason) {
      setError(reason.message || '生成试卷失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="smart-paper-panel mt-5 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <header className="relative overflow-hidden border-b border-emerald-100 bg-[radial-gradient(circle_at_top_right,rgba(167,243,208,0.45),transparent_42%),linear-gradient(135deg,#f7fcf8,#eef8f1)] px-5 pb-6 pt-5 sm:px-7">
        <div className="relative z-[1] max-w-2xl">
          <span className="inline-flex items-center gap-2 text-xs font-semibold tracking-wide text-emerald-700"><BrainCircuit size={16} />智能组卷</span>
          <h2 className="mt-2 text-2xl font-semibold tracking-tight text-slate-950">把学习目标变成一张可作答的试卷</h2>
          <p className="mt-2 max-w-xl text-[15px] leading-6 text-slate-600">选择练习主题、题型和作答方式，系统完成检索、补题与审核后再发布试卷。</p>
        </div>
        <div className="pointer-events-none absolute -right-10 -top-16 h-44 w-44 rounded-full border-[28px] border-white/50" aria-hidden="true" />
      </header>

      <div className="grid gap-0 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <main className="min-w-0 space-y-7 p-5 sm:p-7">
          <section aria-labelledby="paper-source-title">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div><h3 id="paper-source-title" className="text-base font-semibold text-slate-950">1. 选择出题范围</h3><p className="mt-1 text-[15px] leading-6 text-slate-500">明确主题，或交给系统依据学习状态选择。</p></div>
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <button type="button" aria-pressed={kind === 'special'} onClick={() => setKind('special')} className={`group min-h-32 rounded-2xl border p-5 text-left transition duration-200 ${kind === 'special' ? 'border-emerald-500 bg-emerald-50 shadow-sm' : 'border-slate-200 bg-white hover:-translate-y-0.5 hover:border-emerald-300 hover:shadow-sm'}`}>
                <span className="flex items-center gap-3 text-base font-semibold text-slate-950"><span className={`grid h-10 w-10 place-items-center rounded-xl ${kind === 'special' ? 'bg-emerald-100' : 'bg-slate-100 group-hover:bg-emerald-50'}`}><FileCheck2 size={19} className="text-emerald-700" /></span>专项练</span>
                <span className="mt-3 block text-sm leading-6 text-slate-600">围绕指定教材章节、知识点或能力目标出题。</span>
              </button>
              <button type="button" aria-pressed={kind === 'plus'} onClick={() => setKind('plus')} className={`group min-h-32 rounded-2xl border p-5 text-left transition duration-200 ${kind === 'plus' ? 'border-emerald-500 bg-emerald-50 shadow-sm' : 'border-slate-200 bg-white hover:-translate-y-0.5 hover:border-emerald-300 hover:shadow-sm'}`}>
                <span className="flex items-center gap-3 text-base font-semibold text-slate-950"><span className={`grid h-10 w-10 place-items-center rounded-xl ${kind === 'plus' ? 'bg-emerald-100' : 'bg-slate-100 group-hover:bg-emerald-50'}`}><Sprout size={19} className="text-emerald-700" /></span>随心练</span>
                <span className="mt-3 block text-sm leading-6 text-slate-600">结合薄弱知识点、近期错题和复习队列智能选题。</span>
              </button>
            </div>
            {kind === 'special' && <label className="mt-4 block text-sm font-semibold text-slate-800">练习主题
              <textarea aria-label="专项练主题" value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="例如：四君子汤的组成、功效主治与配伍意义" className="mt-2 min-h-24 w-full resize-y rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm leading-6 outline-none transition focus:border-emerald-500 focus:bg-white focus:ring-4 focus:ring-emerald-100" />
            </label>}
            {kind === 'special' && <div className="mt-4 rounded-2xl border border-emerald-100 bg-white/75 p-4 shadow-sm shadow-emerald-100/60" role="region" aria-label="AI推荐主题">
              <div className="flex items-start gap-3">
                <span className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-emerald-100 text-emerald-700"><Sparkles size={17} /></span>
                <div className="min-w-0">
                  <h4 className="text-sm font-semibold text-slate-900">AI 推荐主题</h4>
                  <p className="mt-1 text-xs leading-5 text-slate-500">根据近期练习表现和薄弱知识点生成，点击标签即可填入主题。</p>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap gap-2" aria-live="polite">
                {recommendationsLoading
                  ? <span className="text-xs text-slate-400">正在分析近期学情…</span>
                  : topicRecommendations.map((recommendation) => (
                    <button key={recommendation} type="button" onClick={() => setTopic(recommendation)} className={`rounded-full border px-3 py-2 text-left text-xs font-medium leading-5 transition hover:-translate-y-0.5 hover:shadow-sm ${topic === recommendation ? 'border-emerald-500 bg-emerald-100 text-emerald-800' : 'border-emerald-200 bg-emerald-50/60 text-emerald-800 hover:border-emerald-400'}`}>
                      {recommendation}
                    </button>
                  ))}
              </div>
            </div>}
            {kind === 'plus' && <div className="mt-4 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm leading-6 text-emerald-900">系统会读取当前学习计划、掌握度、错题和待复习知识点，组合本次试卷范围。</div>}
          </section>

          <section aria-labelledby="paper-types-title">
            <div><h3 id="paper-types-title" className="text-base font-semibold text-slate-950">2. 设置题型与题量</h3><p className="mt-1 text-[15px] leading-6 text-slate-500">支持单一题型和混合组卷，总题量不超过 50 题。</p></div>
            <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
              {questionTypes.map(([key, label]) => (
                <div key={key} className={`rounded-xl border p-3 transition ${distribution[key] > 0 ? 'border-emerald-300 bg-emerald-50/60' : 'border-slate-200 bg-white'}`}>
                  <label htmlFor={`paper-count-${key}`} className="block text-sm font-semibold text-slate-800">{label}</label>
                  <div className="mt-3 flex items-center rounded-lg border border-slate-200 bg-white">
                    <button type="button" aria-label={`减少${label}`} onClick={() => setCount(key, distribution[key] - 1)} disabled={distribution[key] === 0} className="inline-flex h-9 w-9 items-center justify-center text-slate-500 transition hover:bg-slate-50 hover:text-slate-900 disabled:opacity-30"><Minus size={14} /></button>
                    <input id={`paper-count-${key}`} aria-label={label} type="text" inputMode="numeric" pattern="[0-9]*" value={distribution[key]} onChange={(event) => setCount(key, event.target.value)} className="h-9 min-w-0 flex-1 border-x border-slate-200 bg-transparent text-center text-sm font-semibold tabular-nums text-slate-900 outline-none" />
                    <button type="button" aria-label={`增加${label}`} onClick={() => setCount(key, distribution[key] + 1)} disabled={total >= 50} className="inline-flex h-9 w-9 items-center justify-center text-slate-500 transition hover:bg-slate-50 hover:text-slate-900 disabled:opacity-30"><Plus size={14} /></button>
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section aria-labelledby="paper-mode-title">
            <div><h3 id="paper-mode-title" className="text-base font-semibold text-slate-950">3. 选择作答方式</h3><p className="mt-1 text-[15px] leading-6 text-slate-500">练习模式适合巩固，测试模式适合阶段验收。</p></div>
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
              <div className="flex items-start justify-between gap-3"><dt className="text-slate-500">范围</dt><dd className="max-w-40 text-right font-medium text-slate-800">{kind === 'plus' ? '基于学情智能选择' : topic.trim() || '尚未填写主题'}</dd></div>
              <div className="flex items-center justify-between gap-3"><dt className="text-slate-500">题型</dt><dd className="font-medium text-slate-800">{selectedTypes.length || 0} 种</dd></div>
              <div className="flex items-center justify-between gap-3"><dt className="text-slate-500">模式</dt><dd className="font-medium text-slate-800">{answerMode === 'test' ? `测试 · ${duration} 分钟` : '练习'}</dd></div>
              <div className="flex items-center justify-between gap-3"><dt className="text-slate-500">发布门禁</dt><dd className="font-medium text-emerald-700">智能体审核</dd></div>
            </dl>
            <button type="button" onClick={generate} disabled={loading || !total || total > 50 || (kind === 'special' && !topic.trim())} className={`${sectionButton} mt-5 w-full border-emerald-700 bg-emerald-700 px-4 py-3 text-white shadow-[0_10px_24px_rgba(22,101,52,0.16)] hover:-translate-y-0.5 hover:bg-emerald-800`}>
              {loading ? <Loader2 size={17} className="animate-spin" /> : <Sparkles size={17} />}
              {loading ? '正在组卷并审核' : '生成试卷'}
            </button>
            <p className="mt-3 text-[15px] leading-5 text-slate-500">审核通过后进入单题作答界面；不在对话区展开试卷正文。</p>
          </div>
          <section className="smart-paper__archive-grid mt-6 border-t border-slate-200 pt-5" role="region" aria-label="试卷列表">
            <header className="flex items-center justify-between gap-3">
              <div>
                <h3 className="flex items-center gap-2 text-sm font-semibold text-slate-900"><ListChecks size={16} className="text-emerald-700" />试卷列表</h3>
                <p className="mt-1 text-xs leading-5 text-slate-500">已生成的试卷会在这里显示完成状态。</p>
              </div>
              <span className="rounded-md bg-emerald-100 px-2 py-0.5 text-xs font-semibold text-emerald-800">{papers.length}</span>
            </header>
            <PaperList papers={papers} onOpen={setActivePaperId} onDownload={handleDownloadPaper} empty="生成试卷后会显示在这里" compact />
          </section>
        </aside>
      </div>
      {error && <p role="alert" className="mx-5 mb-5 rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm leading-6 text-rose-800">{error}</p>}
    </div>
  );
}

function PaperList({ papers, onOpen, onDownload, empty, compact = false }) {
  const [downloadMenu, setDownloadMenu] = useState(null);
  if (!papers.length) {
    return <div className={`grid place-items-center px-5 text-center ${compact ? 'min-h-32 py-6' : 'min-h-64 py-12'}`}><div><FileCheck2 className="mx-auto text-slate-300" size={compact ? 26 : 34} /><h3 className="mt-3 text-sm font-semibold text-slate-800">{empty}</h3><p className="mt-1 text-xs leading-5 text-slate-500">生成并审核通过的试卷会显示在这里。</p></div></div>;
  }
  return (
    <div className={`grid gap-3 ${compact ? 'max-h-52 overflow-y-auto p-3' : 'p-5 sm:p-7 md:grid-cols-2'}`}>
      {papers.map((paper) => (
        <article key={paper.paper_id} className={`group flex flex-col rounded-xl border border-slate-200 bg-white transition duration-200 hover:-translate-y-0.5 hover:border-emerald-300 hover:shadow-md ${compact ? 'min-h-24 p-3' : 'min-h-32 p-4'}`}>
          <div className="flex items-start justify-between gap-3">
            <span className={`rounded-md px-2 py-1 text-xs font-semibold ${paper.status === 'published' ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800'}`}>{paper.status === 'published' ? '未完成' : '已完成'}</span>
            <div className="flex items-center gap-2">
              <span className="inline-flex items-center gap-1 text-xs text-slate-500"><Clock3 size={13} />{paper.duration_minutes} 分钟</span>
              {onDownload && (
                <div className="relative">
                  <button type="button" title="下载试卷" onClick={(e) => { e.stopPropagation(); setDownloadMenu(downloadMenu === paper.paper_id ? null : paper.paper_id); }} className="inline-flex h-10 w-10 items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-emerald-600">
                    <Download size={28} />
                  </button>
                  {downloadMenu === paper.paper_id && <><button type="button" aria-label="关闭下载菜单" onClick={(e) => { e.stopPropagation(); setDownloadMenu(null); }} className="fixed inset-0 z-10" />
                  <div className="absolute right-0 top-full z-20 mt-1 w-36 rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
                    {[{k:'md',l:'Markdown'},{k:'doc',l:'Word 文档'},{k:'png',l:'图片'},{k:'pdf',l:'打印 PDF'}].map(f => (
                      <button key={f.k} type="button" onClick={(e) => { e.stopPropagation(); setDownloadMenu(null); onDownload(paper.paper_id, paper.title, f.k); }} className="flex w-full items-center gap-2 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50">{f.l}</button>
                    ))}
                  </div></>}
                </div>
              )}
            </div>
          </div>
          <h3 className="mt-3 text-sm font-semibold leading-6 text-slate-950">{paper.title}</h3>
          {paper.status !== 'published' && paper.score !== undefined && paper.score !== null && (
            <p className="mt-1 text-xs text-slate-500">得分：<span className="font-semibold text-slate-700">{paper.score}</span> / {paper.max_score || 100} 分</p>
          )}
          <button type="button" onClick={() => onOpen(paper.paper_id)} className="mt-auto inline-flex items-center gap-1 self-end pt-3 text-sm font-semibold text-emerald-700 transition group-hover:gap-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-600">
            {paper.status === 'published' ? '开始答题' : '查看试卷'}<ChevronRight size={16} />
          </button>
        </article>
      ))}
    </div>
  );
}
