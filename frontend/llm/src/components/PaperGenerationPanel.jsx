import React, { useEffect, useMemo, useState } from 'react';
import {
  ArrowLeft,
  Bookmark,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Clock3,
  Download,
  FileText,
  Grid3X3,
  Loader2,
  PanelRightClose,
  PanelRightOpen,
  Pause,
  Play,
  Printer,
  X,
} from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { generateWorkshopPaperWithAgents, loadPaper, loadPapers, savePaperAnswers, setPaperTimerPaused, submitPaper } from '../pageDataLoaders';
import { groupPaperItems } from './paperQuestionGroups';
import { FavoriteQuestionButton, NoteQuestionButton } from './WorkshopSaveActions';

const questionTypes = [
  ['single_choice', '单选题'],
  ['multiple_choice', '多选题'],
  ['fill_blank', '填空题'],
  ['short_answer', '简答题'],
  ['case_quiz', '案例题'],
];
const paperStorageKey = 'training-paper-id';
const optionText = (option, index) => {
  if (typeof option === 'string') return option;
  const key = option?.key || option?.option_id || option?.id || String.fromCharCode(65 + index);
  const value = option?.value || option?.content || option?.text || '';
  return `${key}. ${value}`;
};

const legacyImagePattern = /<img\b[^>]*?(?:src|layer-src)=(['"])(https?:\/\/[^'"<>]+)\1[^>]*>/gi;
const stripLegacyTags = (value) => value.replace(/<[^>]*>/g, '').trim();

export function PaperQuestionContent({ content }) {
  const source = String(content ?? '');
  const parts = [];
  let cursor = 0;
  for (const match of source.matchAll(legacyImagePattern)) {
    const text = stripLegacyTags(source.slice(cursor, match.index));
    if (text) parts.push({ type: 'text', value: text });
    parts.push({ type: 'image', value: match[2] });
    cursor = match.index + match[0].length;
  }
  const tail = stripLegacyTags(source.slice(cursor));
  if (tail) parts.push({ type: 'text', value: tail });
  if (!parts.length) return <span>{stripLegacyTags(source)}</span>;
  return <span className="space-y-2">{parts.map((part, index) => part.type === 'image'
    ? <img key={`${part.value}-${index}`} src={part.value} alt="题目配图" loading="lazy" referrerPolicy="no-referrer" className="block max-h-80 max-w-full object-contain" />
    : <span key={`${part.value}-${index}`} className="block">{part.value}</span>)}</span>;
}

const formatRemaining = (seconds) => {
  if (!Number.isFinite(seconds)) return '--:--';
  const safe = Math.max(0, seconds);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const rest = safe % 60;
  return [hours, minutes, rest].map((value) => String(value).padStart(2, '0')).join(':');
};

const displayAnswer = (value) => Array.isArray(value) ? value.join('、') : String(value ?? '');
const normalizePaperOrder = (paper) => ({
  ...paper,
  items: [...(paper?.items || [])].sort(
    (left, right) => Number(left.position || 0) - Number(right.position || 0),
  ),
});
const paperButton = 'inline-flex items-center justify-center gap-2 rounded-lg border px-3 py-2 text-sm font-semibold transition duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-emerald-600 focus-visible:ring-offset-2 active:translate-y-px disabled:cursor-not-allowed disabled:opacity-40';
const questionTypeLabel = (value) => ({
  single_choice: '单选题',
  multiple_choice: '多选题',
  fill_blank: '填空题',
  short_answer: '简答题',
  case_quiz: '案例题',
  单项选择题: '单选题',
  多项选择题: '多选题',
}[value] || value || '题目');

export default function PaperGenerationPanel({ enabled, paperId = '', taskItemId = '', onExit }) {
  const [topic, setTopic] = useState('围绕四君子汤与脾胃气虚证完成训练');
  const [distribution, setDistribution] = useState({
    single_choice: 1,
    multiple_choice: 0,
    fill_blank: 0,
    short_answer: 0,
    case_quiz: 0,
  });
  const [difficultyFilter, setDifficultyFilter] = useState(null);
  const [paper, setPaper] = useState(null);
  const [answers, setAnswers] = useState({});
  const [submissionRequestId, setSubmissionRequestId] = useState('');
  const [submitted, setSubmitted] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [remainingSeconds, setRemainingSeconds] = useState(null);
  const [paperLibrary, setPaperLibrary] = useState([]);
  const [boundOpenStarted, setBoundOpenStarted] = useState(false);
  const [position, setPosition] = useState(1);
  const [markedPositions, setMarkedPositions] = useState([]);
  const [answerCardOpen, setAnswerCardOpen] = useState(false);
  const [saveMenuOpen, setSaveMenuOpen] = useState(false);

  const questionCount = useMemo(
    () => Object.values(distribution).reduce((total, count) => total + count, 0),
    [distribution],
  );
  const types = questionTypes.filter(([key]) => distribution[key] > 0).map(([key]) => key);
  const canGenerate = enabled && topic.trim() && questionCount > 0 && questionCount <= 50;
  const boundPaper = Boolean(taskItemId);
  const paperSubmitted = paper?.status === 'submitted' || Boolean(submitted);
  const timerPaused = Boolean(paper?.timing?.paused);
  const timeExpired = remainingSeconds === 0;
  const answerLocked = paperSubmitted || timeExpired;
  const timerActive = Boolean(paper) && !paperSubmitted && !timerPaused && Number.isFinite(remainingSeconds) && remainingSeconds > 0;
  const activePaperId = paper?.paper_id || '';
  const hasActivePaper = Boolean(activePaperId);
  const allAnswered = Boolean(paper?.items?.length) && paper.items.every((item) => answers[item.paper_item_id]?.trim());
  const groupedItems = useMemo(() => groupPaperItems(paper?.items || []), [paper?.items]);
  const currentItem = paper?.items?.[Math.max(0, Math.min((paper?.items?.length || 1) - 1, position - 1))] || null;
  const currentResult = currentItem
    ? submitted?.items?.find((entry) => entry.paper_item_id === currentItem.paper_item_id)
    : null;
  const answeredIds = useMemo(
    () => new Set(Object.entries(answers).filter(([, value]) => String(value || '').trim()).map(([key]) => key)),
    [answers],
  );

  const restorePaper = (loaded) => {
    const normalizedPaper = normalizePaperOrder(loaded.paper);
    setPaper(normalizedPaper);
    setAnswers(Object.fromEntries(normalizedPaper.items.map((item) => [item.paper_item_id, item.answer])));
    setSubmitted(normalizedPaper.status === 'submitted' ? normalizedPaper.result : null);
    setRemainingSeconds(normalizedPaper.timing?.remaining_seconds ?? null);
    setSubmissionRequestId(`paper-${crypto.randomUUID()}`);
    setPosition(1);
    setMarkedPositions([]);
    setAnswerCardOpen(false);
  };

  useEffect(() => {
    let active = true;
    const targetPaperId = paperId || (!taskItemId ? sessionStorage.getItem(paperStorageKey) : '');
    if (!targetPaperId) return () => { active = false; };
    loadPaper({ fetcher: fetchJsonWithAuthFallback, paperId: targetPaperId }).then((loaded) => {
      if (!active) return;
      if (loaded.error) {
        sessionStorage.removeItem(paperStorageKey);
        return;
      }
      sessionStorage.setItem(paperStorageKey, targetPaperId);
      restorePaper(loaded);
    });
    return () => { active = false; };
  }, [paperId, taskItemId]);

  useEffect(() => {
    let active = true;
    if (hasActivePaper || boundPaper) return () => { active = false; };
    loadPapers({ fetcher: fetchJsonWithAuthFallback }).then((loaded) => {
      if (!active || loaded.error) return;
      setPaperLibrary(loaded.papers.items);
    });
    return () => { active = false; };
  }, [boundPaper, hasActivePaper]);

  useEffect(() => {
    if (!timerActive) return undefined;
    const timer = window.setInterval(() => {
      setRemainingSeconds((value) => Math.max(0, (value ?? 0) - 1));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [timerActive]);

  const setCount = (key, value) => {
    const count = Math.max(0, Math.min(50, Number.parseInt(value, 10) || 0));
    setDistribution({ ...distribution, [key]: count });
  };

  const generate = async () => {
    if (!boundPaper && !canGenerate) {
      setError('请填写主题，并设置 1 至 50 道题的题型分布。');
      return;
    }
    setLoading(true);
    setError('');
    setPaper(null);
    setSubmitted(null);
    try {
      const response = await generateWorkshopPaperWithAgents({
        fetcher: fetchJsonWithAuthFallback,
        topic: boundPaper ? '打开今日任务绑定试卷' : topic.trim(),
        distribution: boundPaper ? {} : Object.fromEntries(types.map((key) => [key, distribution[key]])),
        taskItemId,
        difficulty: boundPaper ? null : difficultyFilter,
      });
      if (response.error) {
        setError(response.error);
        return;
      }
      const generatedPaperId = response.paperId;
      if (!generatedPaperId) {
        setError('试卷已经生成，但后端没有返回试卷 ID，请稍后从试卷列表打开。');
        return;
      }
      const loaded = await loadPaper({ fetcher: fetchJsonWithAuthFallback, paperId: generatedPaperId });
      if (loaded.error) {
        setError(loaded.error);
        return;
      }
      sessionStorage.setItem(paperStorageKey, generatedPaperId);
      restorePaper(loaded);
    } catch (generationError) {
      setError(generationError?.message || '试卷生成失败，请稍后重试。');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!boundPaper || boundOpenStarted || paper || loading) return undefined;
    setBoundOpenStarted(true);
    generate();
    return undefined;
  }, [boundPaper, boundOpenStarted, paper, loading]);

  const openPaper = async (targetPaperId) => {
    setLoading(true);
    setError('');
    try {
      const loaded = await loadPaper({ fetcher: fetchJsonWithAuthFallback, paperId: targetPaperId });
      if (loaded.error) {
        setError(loaded.error);
        return;
      }
      sessionStorage.setItem(paperStorageKey, targetPaperId);
      restorePaper(loaded);
    } finally {
      setLoading(false);
    }
  };

  const buildPaperMarkdown = () => {
    if (!paper?.items?.length) return '';
    const typeLabel = (type) => ({single_choice:'单选题',multiple_choice:'多选题',fill_blank:'填空题',short_answer:'简答题',case_quiz:'案例题',true_false:'判断题'}[type]||type||'题目');
    const lines = [];
    lines.push(`# ${paper.title || '智能组卷'}`);
    if (paper.total_score) lines.push(`**满分**：${paper.total_score} 分  ·  **题量**：${paper.items.length} 题`);
    lines.push('---');
    paper.items.forEach((item) => {
      lines.push(`## ${item.position || ''}. ${typeLabel(item.question_type)}`);
      lines.push(String(item.stem || '').replace(/<[^>]+>/g, ''));
      const options = Array.isArray(item.options) ? item.options : [];
      options.forEach((opt, i) => {
        const label = String.fromCharCode(65 + i);
        let text = typeof opt === 'string' ? opt : (opt.content || opt.value || opt.text || '');
        text = String(text || '').replace(/<[^>]+>/g, '').trim().replace(/^[A-Z][.．、)\s]\s*/, '');
        lines.push(`- ${label}. ${text}`);
      });
      lines.push('---');
    });
    return lines.join('\n');
  };

  const buildPaperHtml = () => {
    const md = buildPaperMarkdown();
    return '<!DOCTYPE html><html><head><meta charset="utf-8"><title>'+(paper?.title||'试卷')+'</title>'
    +'<style>body{font-family:"Microsoft YaHei",sans-serif;max-width:760px;margin:0 auto;padding:0 8px;color:#1a1a1a;font-size:13px;line-height:1.45}'
    +'h1{font-size:1.15em;border-bottom:1px solid #d1d5db;padding-bottom:6px;margin:0 0 10px 0}'
    +'h2{font-size:.95em;margin:14px 0 4px 0;font-weight:700}p{margin:0 0 4px 0}'
    +'hr{border:0;border-top:1px solid #e5e7eb;margin:8px 0}li{margin:1px 0;font-size:13px}'
    +'@media print{body{margin:0;padding:0 4px}@page{margin:1cm}}</style></head><body>'
    +md.split('\n').map(l=>{if(l.startsWith('# '))return'<h1>'+l.slice(2)+'</h1>';if(l.startsWith('## '))return'<h2>'+l.slice(3)+'</h2>';if(l.startsWith('**'))return'<p><strong>'+l.replace(/\*\*/g,'')+'</strong></p>';if(l.startsWith('- '))return'<li>'+l.slice(2)+'</li>';if(l==='---')return'<hr>';if(l==='')return'';return'<p>'+l+'</p>';}).join('\n')
    +'</body></html>';
  };

  const downloadMarkdown = () => {
    const md = buildPaperMarkdown();
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url;
    a.download = `${(paper?.title || '试卷').replace(/[\\/:*?"<>|]/g, '_')}.md`;
    a.click(); URL.revokeObjectURL(url); setSaveMenuOpen(false);
  };

  const downloadDocx = () => {
    const html = buildPaperHtml();
    const docxHtml = '<!DOCTYPE html><html xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:w="urn:schemas-microsoft-com:office:word" xmlns="http://www.w3.org/TR/REC-html40"><head><meta charset="utf-8"><title>'+(paper?.title||'试卷')+'</title><!--[if gte mso 9]><xml><w:WordDocument><w:View>Print</w:View></w:WordDocument></xml><![endif]--><style>@page{margin:1.5cm}body{font-family:"Microsoft YaHei",sans-serif;max-width:760px;margin:0 auto;padding:10px;color:#1a1a1a;font-size:11pt;line-height:1.4}h1{font-size:13pt;border-bottom:1px solid #d1d5db;padding-bottom:6px;margin:0 0 10px 0}h2{font-size:10.5pt;margin:12px 0 3px 0;font-weight:700}hr{border:0;border-top:1px solid #e5e7eb;margin:6px 0}li{margin:1px 0;font-size:11pt}p{margin:0 0 3px 0}</style></head><body>'+html.replace(/^[\s\S]*<body>/, '').replace(/<\/body>[\s\S]*$/, '')+'</body></html>';
    const blob = new Blob([docxHtml], { type: 'application/msword;charset=utf-8' });
    const url = URL.createObjectURL(blob); const a = document.createElement('a');
    a.href = url; a.download = `${(paper?.title || '试卷').replace(/[\\/:*?"<>|]/g, '_')}.doc`;
    a.click(); URL.revokeObjectURL(url); setSaveMenuOpen(false);
  };

  const downloadImage = async () => {
    const html = buildPaperHtml();
    const container = document.createElement('div');
    container.style.cssText = 'position:fixed;left:-9999px;top:0;width:800px;background:#fff;padding:40px;font-family:"Microsoft YaHei",sans-serif;color:#1a1a1a;line-height:1.45;font-size:13px;z-index:-1';
    container.innerHTML = html.replace(/^[\s\S]*<body>/, '').replace(/<\/body>[\s\S]*$/, '');
    document.body.appendChild(container);
    const { default: html2canvas } = await import('html2canvas');
    try {
      const canvas = await html2canvas(container, { scale: 2, backgroundColor: '#ffffff', logging: false });
      canvas.toBlob((blob) => {
        if (blob) { const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = `${(paper?.title || '试卷').replace(/[\\/:*?"<>|]/g, '_')}.png`; a.click(); URL.revokeObjectURL(url); }
      }, 'image/png');
    } finally { document.body.removeChild(container); }
    setSaveMenuOpen(false);
  };

  const printPaper = () => {
    const html = buildPaperHtml();
    const w = window.open('', '_blank', 'width=800,height=600');
    if (w) { w.document.write(html); w.document.close(); w.focus(); w.print(); }
    setSaveMenuOpen(false);
  };

  const returnToPaperLibrary = async () => {
    if (boundPaper) return;
    sessionStorage.removeItem(paperStorageKey);
    setPaper(null);
    setAnswers({});
    setSubmitted(null);
    setSubmissionRequestId('');
    setRemainingSeconds(null);
    setPosition(1);
    setMarkedPositions([]);
    setAnswerCardOpen(false);
    setError('');
    const loaded = await loadPapers({ fetcher: fetchJsonWithAuthFallback });
    if (loaded.error) {
      setError(loaded.error);
      return;
    }
    setPaperLibrary(loaded.papers.items);
  };

  const save = async () => {
    setLoading(true);
    setError('');
    try {
      const saved = await savePaperAnswers({ fetcher: fetchJsonWithAuthFallback, paperId: paper.paper_id, answers });
      if (saved.error) {
        setError(saved.error);
        return false;
      }
      const normalizedPaper = normalizePaperOrder(saved.paper);
      setPaper(normalizedPaper);
      setAnswers(Object.fromEntries(normalizedPaper.items.map((item) => [item.paper_item_id, item.answer])));
      return true;
    } finally {
      setLoading(false);
    }
  };

  const toggleTimer = async () => {
    if (!paper || paperSubmitted || timeExpired) return;
    setLoading(true);
    setError('');
    try {
      const updated = await setPaperTimerPaused({
        fetcher: fetchJsonWithAuthFallback,
        paperId: paper.paper_id,
        paused: !timerPaused,
      });
      if (updated.error) {
        setError(updated.error);
        return;
      }
      setPaper((current) => current ? { ...current, timing: updated.paper.timing } : current);
      setRemainingSeconds(updated.paper.timing?.remaining_seconds ?? null);
    } finally {
      setLoading(false);
    }
  };

  const submit = async ({ allowIncomplete = false } = {}) => {
    const saved = await save();
    if (!saved || (!allAnswered && !allowIncomplete)) return;
    setLoading(true);
    setError('');
    try {
      const response = await submitPaper({ fetcher: fetchJsonWithAuthFallback, paperId: paper.paper_id, requestId: submissionRequestId });
      if (response.error) {
        setError(response.error);
        return;
      }
      setSubmitted(response.result);
      setPaper((current) => current ? { ...current, status: 'submitted', result: response.result } : current);
      setRemainingSeconds(null);
    } finally {
      setLoading(false);
    }
  };

  const toggleMultiple = (itemId, option) => {
    const values = String(answers[itemId] || '').split(',').map((value) => value.trim()).filter(Boolean);
    const next = values.includes(option) ? values.filter((value) => value !== option) : [...values, option];
    setAnswers({ ...answers, [itemId]: next.join(',') });
  };

  const exitAndSave = async () => {
    if (!paperSubmitted) {
      const saved = await save();
      if (!saved) return;
    }
    if (onExit) {
      sessionStorage.removeItem(paperStorageKey);
      onExit();
      return;
    }
    await returnToPaperLibrary();
  };

  const goToPosition = (nextPosition) => {
    const safePosition = Math.max(1, Math.min(paper?.items?.length || 1, nextPosition));
    setPosition(safePosition);
    setAnswerCardOpen(false);
  };

  const toggleMarked = () => {
    setMarkedPositions((current) => current.includes(position)
      ? current.filter((item) => item !== position)
      : [...current, position]);
  };

  const currentOptions = Array.isArray(currentItem?.options) ? currentItem.options : [];
  const currentIsChoice = currentOptions.length > 0 && [
    'single_choice', '单选题', '单项选择题',
    'multiple_choice', '多选题', '多项选择题',
  ].includes(currentItem?.question_type);
  const currentIsMultiple = ['multiple_choice', '多选题', '多项选择题'].includes(currentItem?.question_type);
  const currentSavedQuestion = currentItem && currentResult ? {
    resource_id: currentItem.paper_item_id,
    title: `第 ${currentItem.position} 题 · ${stripLegacyTags(String(currentItem.stem || '')).slice(0, 80)}`,
    defaultTitle: `${paper?.title || '智能组卷'} · 第 ${currentItem.position} 题`,
    content: {
      question_content: currentItem.stem,
      question_type: currentItem.question_type,
      options: currentOptions,
      my_answer: answers[currentItem.paper_item_id] || '',
      standard_answer: currentResult.standard_answer || [],
      explanation: currentResult.explanation || '',
    },
  } : null;

  if (!enabled) return <p className="mt-5 text-[15px] leading-6 text-slate-600">试卷生成暂未开放。</p>;

  return (
    <div className="mt-5 space-y-5">
      {!paper && <>
        {!boundPaper && paperLibrary.length > 0 && <section className="space-y-3" aria-labelledby="paper-library-title">
          <div><h3 id="paper-library-title" className="text-sm font-semibold text-slate-900">待作答与历史试卷</h3><p className="mt-1 text-[15px] leading-6 text-slate-500">智能体审核通过的试卷会出现在这里。</p></div>
          <div className="grid gap-2">{paperLibrary.map((item) => <button key={item.paper_id} type="button" onClick={() => openPaper(item.paper_id)} disabled={loading} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 px-3 py-3 text-left text-sm transition hover:border-emerald-300 hover:bg-emerald-50 disabled:opacity-50"><span><strong className="block text-slate-900">{item.title}</strong><span className="mt-1 block text-xs text-slate-500">{item.status === 'published' ? '待作答' : '已提交'} · {item.duration_minutes} 分钟</span></span><span className="text-emerald-700">打开试卷</span></button>)}</div>
        </section>}
        {!boundPaper && <div className="border-t border-slate-200 pt-5"><h3 className="text-sm font-semibold text-slate-900">直接组卷</h3><p className="mt-1 text-[15px] leading-6 text-slate-500">也可以在智能问答中描述完整要求，审核通过后会提供“开始答题”按钮。</p></div>}
        {!boundPaper && <label className="block text-sm font-medium text-slate-700">训练主题
          <textarea value={topic} onChange={(event) => setTopic(event.target.value)} disabled={loading} className="mt-2 min-h-20 w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm" />
        </label>}
        {!boundPaper && <p className="text-[15px] text-slate-600">题量：{questionCount} 题；系统按主题、题型和知识点覆盖情况组卷。</p>}
        {!boundPaper && <fieldset>
          <legend className="text-sm font-medium text-slate-700">题型分布</legend>
          <p className="mt-1 text-[15px] leading-5 text-slate-500">可只保留一种题型，也可组合组卷；总题量不超过 50 题。</p>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {questionTypes.map(([key, label]) => <label key={key} className="text-sm font-medium text-slate-700">{label}
            <input type="number" min="0" max="50" value={distribution[key]} onChange={(event) => setCount(key, event.target.value)} disabled={loading} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" />
          </label>)}
          </div>
        </fieldset>}
        {!boundPaper && <fieldset>
          <legend className="text-sm font-medium text-slate-700">难度要求 <span className="font-normal text-slate-400">（可选，仅使用真实难度标注）</span></legend>
          <p className="mt-1 text-[15px] leading-5 text-slate-500">指定难度时优先选用该难度的正式题；不足时依次补入未标注难度的正式题、网络参考题，最后才生成补充题。系统会如实标注每题来源，不会把补充题伪装成指定难度。</p>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button type="button" onClick={() => setDifficultyFilter(null)} disabled={loading} title="不按难度筛选，全部题目均可组卷" className={difficultyFilter === null ? 'rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white' : 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700'}>全部</button>
            {[1, 2, 3, 4, 5].map((level) => <button key={level} type="button" onClick={() => setDifficultyFilter(level)} disabled={loading} className={difficultyFilter === level ? 'rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-medium text-white' : 'rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700'}>难度 {level}</button>)}
          </div>
        </fieldset>}
        {!boundPaper && <button type="button" onClick={generate} disabled={loading || !canGenerate} className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50">
          {loading && <Loader2 size={16} className="animate-spin" />}{loading ? '正在组卷并审核…' : '生成试卷'}
        </button>}
        {boundPaper && <p role="status" className="rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-2 text-[15px] text-emerald-900">正在打开今日任务绑定试卷，题目范围和组卷约束由服务端冻结。</p>}
      </>}
      {paper && currentItem && <section className="relative min-h-[620px] overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <header className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-slate-50/80 px-4 py-3 sm:px-5">
          <div className="flex min-w-0 items-center gap-3">
            {!boundPaper && <button type="button" onClick={exitAndSave} disabled={loading} className={`${paperButton} border-slate-300 bg-white text-slate-700 shadow-sm hover:border-rose-300 hover:text-rose-700`}>
              <ArrowLeft size={16} />退出并保存
            </button>}
            <div className="min-w-0">
              <h3 className="truncate text-sm font-semibold text-slate-950 sm:text-base">{paper.title}</h3>
              <p className="mt-1 text-xs text-slate-500">第 {position} / {paper.items.length} 题 · 已答 {answeredIds.size} 题 · 满分 {paper.total_score ?? submitted?.max_score ?? 100} 分</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <span className={`inline-flex min-h-9 items-center gap-2 rounded-lg border bg-white px-3 font-mono text-sm font-semibold tabular-nums shadow-sm ${timeExpired ? 'border-rose-200 text-rose-700' : timerPaused ? 'border-amber-200 text-amber-700' : 'border-slate-200 text-emerald-800'}`}>
              <Clock3 size={15} aria-hidden="true" />{paperSubmitted ? '已交卷' : formatRemaining(remainingSeconds)}
            </span>
            {!paperSubmitted && !timeExpired && <button type="button" onClick={toggleTimer} disabled={loading} className={`${paperButton} border-slate-300 bg-white text-slate-700 hover:border-emerald-400 hover:text-emerald-800`}>
              {timerPaused ? <Play size={16} /> : <Pause size={16} />}<span className="hidden sm:inline">{timerPaused ? '继续计时' : '暂停计时'}</span>
            </button>}
            <button type="button" aria-label={answerCardOpen ? '收起答题卡' : '展开答题卡'} aria-expanded={answerCardOpen} aria-controls="smart-paper-answer-card" onClick={() => setAnswerCardOpen(!answerCardOpen)} className={`${paperButton} border-slate-300 bg-white text-slate-700 hover:border-emerald-400 hover:text-emerald-800`}>
              {answerCardOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}<span className="hidden sm:inline">答题卡</span>
            </button>
            <div className="relative">
              <button type="button" onClick={() => setSaveMenuOpen(!saveMenuOpen)} className={`${paperButton} border-slate-300 bg-white text-slate-700 hover:border-emerald-400 hover:text-emerald-800`}>
                <Download size={16} /><span className="hidden sm:inline">保存试卷</span>
              </button>
              {saveMenuOpen && <>
                <button type="button" aria-label="关闭保存菜单" onClick={() => setSaveMenuOpen(false)} className="fixed inset-0 z-10" />
                <div className="absolute right-0 top-full z-20 mt-1 w-48 rounded-lg border border-slate-200 bg-white py-1 shadow-lg">
                  <button type="button" onClick={downloadMarkdown} className="flex w-full items-center gap-2 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"><FileText size={15} />Markdown (.md)</button>
                  <button type="button" onClick={downloadDocx} className="flex w-full items-center gap-2 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"><FileText size={15} />Word 文档 (.doc)</button>
                  <button type="button" onClick={downloadImage} className="flex w-full items-center gap-2 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"><FileText size={15} />图片 (.png)</button>
                  <button type="button" onClick={printPaper} className="flex w-full items-center gap-2 px-3 py-2 text-sm text-slate-700 hover:bg-slate-50"><Printer size={15} />打印为 PDF</button>
                </div>
              </>}
            </div>
          </div>
        </header>

        {paperSubmitted && <section className="border-b border-emerald-200 bg-emerald-50 px-5 py-4" aria-label="试卷得分">
          <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-4">
            <div><p className="text-xs font-semibold tracking-wide text-emerald-700">本次作答</p><p className="mt-1 text-2xl font-semibold tracking-tight text-emerald-950">{submitted?.score ?? paper.result?.score ?? 0} <span className="text-sm font-medium text-emerald-700">/ {submitted?.max_score ?? paper.result?.max_score ?? paper.total_score ?? 100} 分</span></p><p className="mt-1 text-sm text-emerald-800">得分率 {submitted?.score_rate ?? paper.result?.score_rate ?? 0}%</p></div>
            <div className="flex items-center gap-2 text-sm text-emerald-900"><CheckCircle2 size={18} /><span>已完成 {submitted?.items?.length || paper.items.length} 道题评分</span></div>
          </div>
        </section>}

        {!paperSubmitted && timerPaused && <p role="status" className="border-b border-amber-200 bg-amber-50 px-5 py-3 text-sm leading-6 text-amber-800">计时已暂停，答案保留在当前页面。继续作答时请恢复计时。</p>}
        {!paperSubmitted && timeExpired && <p role="status" className="border-b border-amber-200 bg-amber-50 px-5 py-3 text-sm leading-6 text-amber-800">答题时间已结束，答案已锁定，请按当前答案交卷。</p>}

        {paper?.difficulty_source_summary && (
          <section className="border-b border-slate-200 bg-slate-50 px-5 py-4" aria-label="题目来源与难度说明">
            <div className="mx-auto max-w-3xl space-y-2 text-sm leading-6 text-slate-700">
              <p className="font-medium text-slate-900">题目来源与难度说明</p>
              <ul className="list-inside list-disc space-y-1">
                {Number(paper.difficulty_source_summary.exact_difficulty_count || 0) > 0 && (
                  <li>符合指定难度（{paper.difficulty_source_summary.target_difficulty}星）的正式题：<strong>{paper.difficulty_source_summary.exact_difficulty_count}</strong> 题</li>
                )}
                {Number(paper.difficulty_source_summary.unlabeled_official_count || 0) > 0 && (
                  <li>未标注难度的正式题（补充）：<strong>{paper.difficulty_source_summary.unlabeled_official_count}</strong> 题</li>
                )}
                {Number(paper.difficulty_source_summary.web_reference_count || 0) > 0 && (
                  <li>网络参考题（补充）：<strong>{paper.difficulty_source_summary.web_reference_count}</strong> 题</li>
                )}
                {Number(paper.difficulty_source_summary.generated_count || 0) > 0 && (
                  <li>系统生成补充题（无真实难度标注）：<strong>{paper.difficulty_source_summary.generated_count}</strong> 题</li>
                )}
                {Number(paper.difficulty_source_summary.unmet_count || 0) > 0 && (
                  <li>未能满足的指定难度数量：<strong>{paper.difficulty_source_summary.unmet_count}</strong> 题</li>
                )}
              </ul>
              {paper.difficulty_source_summary.notice && <p className="text-slate-500">{paper.difficulty_source_summary.notice}</p>}
            </div>
          </section>
        )}

        <article className="mx-auto max-w-3xl px-5 py-8 sm:py-10">
          <div className="mb-5 flex flex-wrap items-center gap-2 text-xs font-semibold">
            <span className="rounded-md bg-emerald-100 px-2.5 py-1 text-emerald-800">{questionTypeLabel(currentItem.question_type)}</span>
            {markedPositions.includes(position) && <span className="rounded-md border border-amber-200 bg-amber-100 px-2.5 py-1 text-amber-800">已标记</span>}
            {paperSubmitted && currentResult && <span className={`rounded-md px-2.5 py-1 ${currentResult.is_correct ? 'bg-emerald-100 text-emerald-800' : 'bg-rose-100 text-rose-800'}`}>{currentResult.is_correct ? '回答正确' : '需要复盘'}</span>}
          </div>
          <div className="flex items-start gap-3 text-lg font-medium leading-8 text-slate-950">
            <span className="mt-0.5 flex h-7 min-w-7 items-center justify-center rounded-full bg-emerald-100 px-2 text-sm font-bold text-emerald-800">{position}</span>
            <span className="min-w-0 flex-1"><PaperQuestionContent content={currentItem.stem} /></span>
          </div>

          <fieldset className="mt-6" disabled={loading || answerLocked}>
            <legend className="sr-only">第 {position} 题答案</legend>
            {currentIsChoice ? <div className="space-y-3">{currentOptions.map((option, index) => {
              const value = optionText(option, index);
              const checked = currentIsMultiple
                ? String(answers[currentItem.paper_item_id] || '').split(',').map((entry) => entry.trim()).includes(value)
                : answers[currentItem.paper_item_id] === value;
              return <label key={`${currentItem.paper_item_id}-${index}`} className={`flex cursor-pointer items-start gap-3 rounded-xl border p-3.5 text-sm leading-6 transition duration-200 ${checked ? 'border-emerald-500 bg-emerald-50 shadow-sm' : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50'}`}>
                <input className="mt-1 accent-emerald-700" type={currentIsMultiple ? 'checkbox' : 'radio'} name={currentItem.paper_item_id} checked={checked} onChange={() => currentIsMultiple ? toggleMultiple(currentItem.paper_item_id, value) : setAnswers({ ...answers, [currentItem.paper_item_id]: value })} />
                <PaperQuestionContent content={value} />
              </label>;
            })}</div> : <textarea aria-label={`第${currentItem.position}题答案`} value={answers[currentItem.paper_item_id] || ''} onChange={(event) => setAnswers({ ...answers, [currentItem.paper_item_id]: event.target.value })} placeholder="请在这里输入答案" className="min-h-36 w-full rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm leading-6 outline-none transition focus:border-emerald-500 focus:bg-white focus:ring-4 focus:ring-emerald-100" />}
          </fieldset>

          {currentResult && <section className={`mt-6 rounded-xl border p-4 text-sm leading-6 ${currentResult.is_correct ? 'border-emerald-200 bg-emerald-50 text-emerald-950' : 'border-rose-200 bg-rose-50 text-rose-950'}`} aria-label={`第 ${position} 题解析`}>
            <div className="flex flex-wrap items-center justify-between gap-2"><strong>{currentResult.is_correct ? '回答正确' : '回答错误'}</strong><span className="font-semibold tabular-nums">{currentResult.score} / {currentResult.max_score} 分</span></div>
            <dl className="mt-3 space-y-2">
              <div><dt className="inline font-semibold">你的答案：</dt><dd className="inline">{displayAnswer(answers[currentItem.paper_item_id]) || '未作答'}</dd></div>
              <div><dt className="inline font-semibold">参考答案：</dt><dd className="inline">{displayAnswer(currentResult.standard_answer) || '待补充'}</dd></div>
              <div><dt className="font-semibold">题目解析</dt><dd className="mt-1 text-slate-700">{currentResult.explanation || '本题解析正在补充。'}</dd></div>
              {currentResult.grading_analysis && currentResult.grading_analysis !== currentResult.explanation && <div><dt className="font-semibold">本次批改</dt><dd className="mt-1 text-slate-700">{currentResult.grading_analysis}</dd></div>}
            </dl>
            <div className="mt-4 flex flex-wrap gap-2"><FavoriteQuestionButton question={currentSavedQuestion} source="智能组卷" /><NoteQuestionButton question={currentSavedQuestion} source="智能组卷" /></div>
          </section>}
        </article>

        <footer className="mx-auto flex max-w-3xl flex-wrap items-center gap-3 border-t border-slate-200 px-5 py-4">
          <button type="button" disabled={position === 1} onClick={() => goToPosition(position - 1)} className={`${paperButton} border-slate-300 bg-white text-slate-700 hover:border-emerald-400 hover:text-emerald-800`}><ChevronLeft size={16} />上一题</button>
          {!paperSubmitted && <button type="button" onClick={toggleMarked} className={`${paperButton} ${markedPositions.includes(position) ? 'border-amber-300 bg-amber-100 text-amber-800' : 'border-slate-300 bg-white text-slate-700 hover:border-slate-500'}`}><Bookmark size={16} />{markedPositions.includes(position) ? '取消标记' : '标记本题'}</button>}
          <button type="button" disabled={position === paper.items.length} onClick={() => goToPosition(position + 1)} className={`${paperButton} border-slate-300 bg-white text-slate-700 hover:border-emerald-400 hover:text-emerald-800`}>下一题<ChevronRight size={16} /></button>
          {!paperSubmitted && <button type="button" onClick={() => submit({ allowIncomplete: timeExpired })} disabled={loading || (!timeExpired && !allAnswered)} className={`${paperButton} ml-auto border-slate-900 bg-slate-900 text-white hover:bg-slate-800`}><ClipboardList size={16} />{timeExpired ? '按当前答案交卷' : '提交试卷'}</button>}
        </footer>

        {answerCardOpen && <><button type="button" aria-label="关闭答题卡遮罩" onClick={() => setAnswerCardOpen(false)} className="absolute inset-0 z-10 bg-slate-950/10 backdrop-blur-[1px]" /><aside id="smart-paper-answer-card" role="complementary" aria-label="答题卡" className="absolute bottom-0 right-0 top-0 z-20 flex w-[min(22rem,90vw)] flex-col border-l border-slate-200 bg-white shadow-2xl">
          <div className="flex items-center justify-between border-b border-slate-200 px-4 py-4">
            <div><div className="flex items-center gap-2"><Grid3X3 size={17} className="text-emerald-700" /><h3 className="font-semibold text-slate-900">答题卡</h3></div><p className="mt-1 text-xs text-slate-500">已答 {answeredIds.size} / {paper.items.length} · 已标记 {markedPositions.length}</p></div>
            <button type="button" aria-label="收起答题卡" onClick={() => setAnswerCardOpen(false)} className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-300 text-slate-600 transition hover:border-emerald-400 hover:text-emerald-800"><X size={17} /></button>
          </div>
          <div data-testid="smart-paper-answer-card-grid" className="min-h-0 flex-1 space-y-5 overflow-y-auto overscroll-contain p-4">
            {groupedItems.map((group) => <section key={group.key} aria-labelledby={`paper-card-group-${group.key}`}>
              <div className="mb-2 flex items-center justify-between gap-2"><h4 id={`paper-card-group-${group.key}`} className="text-xs font-semibold text-slate-700">{group.label}</h4><span className="text-xs text-slate-400">{group.items.length} 题</span></div>
              <div className="grid grid-cols-5 gap-2">{group.items.map((item) => {
                const itemPosition = paper.items.findIndex((entry) => entry.paper_item_id === item.paper_item_id) + 1;
                const result = submitted?.items?.find((entry) => entry.paper_item_id === item.paper_item_id);
                const color = itemPosition === position
                  ? 'ring-2 ring-emerald-700 ring-offset-2'
                  : result
                    ? result.is_correct ? 'bg-emerald-200 text-emerald-800' : 'bg-rose-600 text-white'
                    : markedPositions.includes(itemPosition)
                      ? 'border border-amber-300 bg-amber-100 text-amber-800'
                      : answeredIds.has(item.paper_item_id)
                        ? 'bg-emerald-200 text-emerald-800'
                        : 'border border-slate-300 bg-white text-slate-700';
                return <button key={item.paper_item_id} type="button" aria-label={`第 ${itemPosition} 题`} onClick={() => goToPosition(itemPosition)} className={`h-9 rounded-full text-xs font-semibold transition hover:scale-105 ${color}`}>{itemPosition}</button>;
              })}</div>
            </section>)}
          </div>
          <div className="border-t border-slate-200 px-4 py-3 text-xs leading-5 text-slate-500">绿色为已答，浅绿色为标记；交卷后绿色与红色分别表示正确和错误。</div>
        </aside></>}
      </section>}
      {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-700">{error}</p>}
    </div>
  );
}
