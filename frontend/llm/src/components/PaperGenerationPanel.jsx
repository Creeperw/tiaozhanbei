import React, { useEffect, useMemo, useState } from 'react';
import { Loader2 } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { generateWorkshopPaperWithAgents, loadPaper, loadPapers, savePaperAnswers, setPaperTimerPaused, submitPaper } from '../pageDataLoaders';
import { groupPaperItems } from './paperQuestionGroups';

const questionTypes = [
  ['single_choice', '单选题'],
  ['multiple_choice', '多选题'],
  ['fill_blank', '填空题'],
  ['short_answer', '简答题'],
  ['case_quiz', '案例题'],
];
const paperStorageKey = 'training-paper-id';
const optionValue = (option, index) => {
  if (typeof option === 'string') return option.match(/^\s*([A-Z])(?:[.、．]|\s)/)?.[1] || option;
  return String(option?.key || option?.option_id || option?.id || String.fromCharCode(65 + index));
};

const optionText = (option, index) => {
  if (typeof option === 'string') return option;
  const key = option?.key || option?.option_id || option?.id || String.fromCharCode(65 + index);
  const value = option?.value || option?.content || option?.text || '';
  return `${key}. ${value}`;
};

const formatRemaining = (seconds) => {
  if (!Number.isFinite(seconds)) return '--:--';
  const safe = Math.max(0, seconds);
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const rest = safe % 60;
  return [hours, minutes, rest].map((value) => String(value).padStart(2, '0')).join(':');
};

const displayAnswer = (value) => Array.isArray(value) ? value.join('、') : String(value ?? '');

export default function PaperGenerationPanel({ enabled, paperId = '', onBack }) {
  const [topic, setTopic] = useState('围绕四君子汤与脾胃气虚证完成训练');
  const [distribution, setDistribution] = useState({
    single_choice: 1,
    multiple_choice: 0,
    fill_blank: 0,
    short_answer: 0,
    case_quiz: 0,
  });
  const [paper, setPaper] = useState(null);
  const [answers, setAnswers] = useState({});
  const [submissionRequestId, setSubmissionRequestId] = useState('');
  const [submitted, setSubmitted] = useState(null);
  const [error, setError] = useState('');
  const [noteDialogOpen, setNoteDialogOpen] = useState(false);
  const [noteDialogItem, setNoteDialogItem] = useState(null);
  const [noteDialogTitle, setNoteDialogTitle] = useState('');
  const [noteDialogContent, setNoteDialogContent] = useState('');
  const [loading, setLoading] = useState(false);
  const [remainingSeconds, setRemainingSeconds] = useState(null);
  const [paperLibrary, setPaperLibrary] = useState([]);

  const questionCount = useMemo(
    () => Object.values(distribution).reduce((total, count) => total + count, 0),
    [distribution],
  );
  const types = questionTypes.filter(([key]) => distribution[key] > 0).map(([key]) => key);
  const canGenerate = enabled && topic.trim() && questionCount > 0 && questionCount <= 50;
  const paperSubmitted = paper?.status === 'submitted' || Boolean(submitted);
  const timerPaused = Boolean(paper?.timing?.paused);
  const timeExpired = remainingSeconds === 0;
  const answerLocked = paperSubmitted || timeExpired;
  const timerActive = Boolean(paper) && !paperSubmitted && !timerPaused && Number.isFinite(remainingSeconds) && remainingSeconds > 0;
  const activePaperId = paper?.paper_id || '';
  const hasActivePaper = Boolean(activePaperId);
  const allAnswered = paper?.items?.every((item) => answers[item.paper_item_id]?.trim());
  const groupedItems = useMemo(() => groupPaperItems(paper?.items || []), [paper?.items]);

  const restorePaper = (loaded) => {
    setPaper(loaded.paper);
    setAnswers(Object.fromEntries(loaded.paper.items.map((item) => [item.paper_item_id, item.answer])));
    setSubmitted(loaded.paper.status === 'submitted' ? loaded.paper.result : null);
    setRemainingSeconds(loaded.paper.timing?.remaining_seconds ?? null);
    setSubmissionRequestId(`paper-${crypto.randomUUID()}`);
  };

  useEffect(() => {
    let active = true;
    const targetPaperId = paperId || sessionStorage.getItem(paperStorageKey);
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
  }, [paperId]);

  useEffect(() => {
    let active = true;
    if (hasActivePaper) return () => { active = false; };
    loadPapers({ fetcher: fetchJsonWithAuthFallback }).then((loaded) => {
      if (!active || loaded.error) return;
      setPaperLibrary(loaded.papers.items);
    });
    return () => { active = false; };
  }, [hasActivePaper]);

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
    if (!canGenerate) {
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
        topic: topic.trim(),
        distribution: Object.fromEntries(types.map((key) => [key, distribution[key]])),
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

  const returnToPaperLibrary = async () => {
    sessionStorage.removeItem(paperStorageKey);
    if (onBack) { onBack(); return; }
    setPaper(null);
    setAnswers({});
    setSubmitted(null);
    setSubmissionRequestId('');
    setRemainingSeconds(null);
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
      setPaper(saved.paper);
      setAnswers(Object.fromEntries(saved.paper.items.map((item) => [item.paper_item_id, item.answer])));
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

  if (!enabled) return <p className="mt-5 text-sm leading-6 text-slate-600">试卷生成暂未开放。</p>;

  return (
    <div className="mt-5 space-y-5">
      {!paper && <>
        {paperLibrary.length > 0 && <section className="space-y-3" aria-labelledby="paper-library-title">
          <div><h3 id="paper-library-title" className="text-sm font-semibold text-slate-900">待作答与历史试卷</h3><p className="mt-1 text-sm leading-6 text-slate-500">智能体审核通过的试卷会出现在这里。</p></div>
          <div className="grid gap-2">{paperLibrary.map((item) => <button key={item.paper_id} type="button" onClick={() => openPaper(item.paper_id)} disabled={loading} className="flex items-center justify-between gap-3 rounded-xl border border-slate-200 px-3 py-3 text-left text-sm transition hover:border-emerald-300 hover:bg-emerald-50 disabled:opacity-50"><span><strong className="block text-slate-900">{item.title}</strong><span className="mt-1 block text-xs text-slate-500">{item.status === 'published' ? '待作答' : '已提交'} · {item.duration_minutes} 分钟</span></span><span className="text-emerald-700">打开试卷</span></button>)}</div>
        </section>}
        <div className="border-t border-slate-200 pt-5"><h3 className="text-sm font-semibold text-slate-900">直接组卷</h3><p className="mt-1 text-sm leading-6 text-slate-500">也可以在智能问答中描述完整要求，审核通过后会提供“开始答题”按钮。</p></div>
        <label className="block text-sm font-medium text-slate-700">训练主题
          <textarea value={topic} onChange={(event) => setTopic(event.target.value)} disabled={loading} className="mt-2 min-h-20 w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm" />
        </label>
        <p className="text-sm text-slate-600">题量：{questionCount} 题；难度由智能体根据学习状态和组卷目标自动确定。</p>
        <fieldset>
          <legend className="text-sm font-medium text-slate-700">题型分布</legend>
          <p className="mt-1 text-xs leading-5 text-slate-500">可只保留一种题型，也可组合组卷；总题量不超过 50 题。</p>
          <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
          {questionTypes.map(([key, label]) => <label key={key} className="text-sm font-medium text-slate-700">{label}
            <input type="number" min="0" max="50" value={distribution[key]} onChange={(event) => setCount(key, event.target.value)} disabled={loading} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm" />
          </label>)}
          </div>
        </fieldset>
        <button type="button" onClick={generate} disabled={loading || !canGenerate} className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50">
          {loading && <Loader2 size={16} className="animate-spin" />}{loading ? '正在组卷并审核…' : '生成试卷'}
        </button>
      </>}
      {paper && <div className="space-y-4 border-t border-slate-200 pt-4">
        {!paperSubmitted && <nav aria-label="试题导航" className="flex flex-wrap gap-2 rounded-xl border border-slate-200 bg-slate-50 p-3">{paper.items.map((item) => <a key={item.paper_item_id} href={`#${item.paper_item_id}`} className={`grid size-9 place-items-center rounded-md border text-xs font-semibold ${answers[item.paper_item_id]?.trim() ? 'border-emerald-300 bg-emerald-50 text-emerald-800' : 'border-slate-200 bg-white text-slate-600'}`}>{item.position}</a>)}</nav>}
        <div className="sticky top-3 z-10 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-emerald-200 bg-white/95 px-4 py-3 shadow-sm backdrop-blur">
          <div className="flex items-center gap-3"><button type="button" onClick={returnToPaperLibrary} className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50">返回试卷列表</button><div><h3 className="text-base font-semibold text-slate-950">{paper.title}</h3><p className="mt-1 text-xs text-slate-500">已答 {paper.items.filter((item) => answers[item.paper_item_id]?.trim()).length} / {paper.items.length} 题 · 满分 {paper.total_score ?? submitted?.max_score ?? 100} 分</p></div></div>
          <div className="flex items-center gap-3">
            <div className={`font-mono text-lg font-semibold ${remainingSeconds === 0 ? 'text-rose-600' : timerPaused ? 'text-amber-700' : 'text-emerald-800'}`}>{paperSubmitted ? '已交卷' : formatRemaining(remainingSeconds)}</div>
            {!paperSubmitted && !timeExpired && <button type="button" onClick={toggleTimer} disabled={loading} className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50">{timerPaused ? '继续计时' : '暂停计时'}</button>}
          </div>
        </div>
        {!paperSubmitted && timerPaused && <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-800">计时已暂停。继续答题前请点击“继续计时”。</p>}
        {groupedItems.map((group) => <section key={group.key} aria-labelledby={`paper-group-${group.key}`} className="space-y-3">
          <div className="flex items-center gap-2 border-b border-slate-200 pb-2"><h4 id={`paper-group-${group.key}`} className="text-sm font-semibold text-slate-900">{group.label}</h4><span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">{group.items.length} 题</span></div>
          {group.items.map((item) => {
            const options = Array.isArray(item.options) ? item.options : [];
            const choice = options.length > 0 && ['single_choice', '单选题', '单项选择题', 'multiple_choice', '多选题', '多项选择题'].includes(item.question_type);
            const multiple = ['multiple_choice', '多选题', '多项选择题'].includes(item.question_type);
            const itemResult = submitted?.items?.find((entry) => entry.paper_item_id === item.paper_item_id);
            return <div id={item.paper_item_id} key={item.paper_item_id}>
              <fieldset className="rounded-2xl border border-slate-200 p-4" disabled={loading || answerLocked}>
                <legend className="px-1 text-sm font-medium leading-6 text-slate-800">{item.position}. {item.stem}</legend>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-slate-600">
                  {item.source_label && <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-1 text-emerald-800">{item.source_label}</span>}
                  {item.kp_names?.map((name) => <span key={name} className="rounded-full bg-slate-100 px-2 py-1">{name}</span>)}
                  {item.recommendation_reason && <span className="basis-full text-slate-500">{item.recommendation_reason}</span>}
                </div>
                {choice ? <div className="mt-3 space-y-2">{options.map((option, index) => {
                  const label = optionText(option, index);
                  const value = optionValue(option, index);
                  const checked = multiple
                    ? String(answers[item.paper_item_id] || '').split(',').map((entry) => entry.trim()).includes(value)
                    : answers[item.paper_item_id] === value;
                  return <label key={`${item.paper_item_id}-${index}`} className="flex cursor-pointer gap-3 rounded-xl bg-slate-50 px-3 py-2 text-sm text-slate-700"><input type={multiple ? 'checkbox' : 'radio'} name={item.paper_item_id} checked={checked} onChange={() => multiple ? toggleMultiple(item.paper_item_id, value) : setAnswers({ ...answers, [item.paper_item_id]: value })} />{label}</label>;
                })}</div> : <textarea aria-label={`第${item.position}题答案`} value={answers[item.paper_item_id] || ''} onChange={(event) => setAnswers({ ...answers, [item.paper_item_id]: event.target.value })} className="mt-3 min-h-24 w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm" />}
              </fieldset>
              {itemResult && <div className={`mt-2 rounded-xl border p-3 text-sm leading-6 ${itemResult.is_correct ? 'border-emerald-200 bg-emerald-50 text-emerald-900' : 'border-rose-200 bg-rose-50 text-rose-900'}`}>
                <strong>{itemResult.is_correct ? '回答正确' : '回答错误'} · {itemResult.score} / {itemResult.max_score} 分</strong>
                <p>参考答案：{displayAnswer(itemResult.standard_answer) || '待补充'}</p>
                <p>题目解析：{itemResult.explanation || '本题解析正在补充。'}</p>
                {itemResult.grading_analysis && itemResult.grading_analysis !== itemResult.explanation && <p>本次批改：{itemResult.grading_analysis}</p>}
                <div className="mt-3 flex gap-2">
                  <button type="button" onClick={function() {
                    var books = JSON.parse(localStorage.getItem('qp-collection-books') || '[]');
                    var choices = ['默认'].concat(books).concat(['+ 新建收藏簿']);
                    var choice = prompt('选择收藏簿：\n' + choices.map(function(c, i) { return (i+1) + '. ' + c; }).join('\n') + '\n\n输入序号或新收藏簿名称：');
                    if (!choice) return;
                    var bookName = choice.trim();
                    var idx = parseInt(choice);
                    if (idx >= 1 && idx <= choices.length) bookName = choices[idx - 1];
                    if (bookName === '+ 新建收藏簿') { bookName = prompt('请输入新收藏簿名称：'); if (!bookName || !bookName.trim()) return; bookName = bookName.trim(); if (books.indexOf(bookName) === -1) { books.push(bookName); localStorage.setItem('qp-collection-books', JSON.stringify(books)); } }
                    var key = 'qp-favorite-questions';
                    var favs = JSON.parse(localStorage.getItem(key) || '[]');
                    // Convert options to display format if needed
                    var opts = (item.options || []).map(function(opt, j) {
                      if (typeof opt === 'object' && opt !== null) return opt;
                      return { option_id: String.fromCharCode(65 + j), content: String(opt) };
                    });
                    if (!favs.find(function(f) { return f.question_id === item.paper_item_id && f.book === bookName; })) {
                      favs.unshift({ question_id: item.paper_item_id, question_content: item.stem, question_type: item.question_type, options: opts, my_answer: String(answers[item.paper_item_id] || ''), standard_answer: itemResult.standard_answer || [], explanation: itemResult.explanation || '', book: bookName, source: '智能组卷', saved_at: new Date().toISOString() });
                      localStorage.setItem(key, JSON.stringify(favs.slice(0, 200)));
                    }
                  }} className="rounded-lg border border-amber-300 bg-amber-50 px-3 py-1.5 text-xs font-medium text-amber-700 hover:bg-amber-100">加入收藏</button>
                  <button type="button" onClick={function() {
                    var paperTitle = paper.title || '智能组卷';
                    setNoteDialogItem(item);
                    setNoteDialogTitle(paperTitle + '：');
                    setNoteDialogContent('');
                    setNoteDialogOpen(true);
                  }} className="rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-1.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100">记笔记</button>
                </div>
              </div>}
            </div>;
          })}
        </section>)}
        {!paperSubmitted && timeExpired && <p role="status" className="rounded-xl border border-amber-200 bg-amber-50 px-3 py-2 text-sm leading-6 text-amber-800">答题时间已结束，答案已锁定，请提交当前作答。</p>}
        {!paperSubmitted && <div className="flex flex-wrap gap-2">{!timeExpired && <button type="button" onClick={save} disabled={loading} className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 disabled:opacity-50">保存答案</button>}<button type="button" onClick={() => submit({ allowIncomplete: timeExpired })} disabled={loading || (!timeExpired && !allAnswered)} className="rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">{timeExpired ? '按当前答案交卷' : '提交试卷'}</button></div>}
      </div>}
      {submitted && <div className="border-l-2 border-emerald-300 pl-3 text-sm leading-6 text-slate-700"><p>总分：{submitted.score} / {submitted.max_score}</p><p>已完成 {submitted.items?.length || 0} 道题的服务端评分。</p></div>}
      {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-700">{error}</p>}
      {noteDialogOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30" onClick={function() { setNoteDialogOpen(false); }}>
          <div className="bg-white rounded-2xl shadow-2xl p-6 w-full max-w-md mx-4" onClick={function(e) { e.stopPropagation(); }}>
            <h3 className="text-lg font-semibold text-slate-900 mb-4">记笔记</h3>
            <label className="block text-sm font-medium text-slate-700 mb-1">标题</label>
            <input type="text" value={noteDialogTitle} onChange={function(e) { setNoteDialogTitle(e.target.value); }} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-400 mb-3" />
            <label className="block text-sm font-medium text-slate-700 mb-1">内容</label>
            <textarea value={noteDialogContent} onChange={function(e) { setNoteDialogContent(e.target.value); }} rows={5} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-400 resize-none mb-4" placeholder="写下你的笔记…" />
            <div className="flex justify-end gap-3">
              <button type="button" onClick={function() { setNoteDialogOpen(false); }} className="rounded-lg border border-slate-300 px-4 py-2 text-sm text-slate-600 hover:bg-slate-50">取消</button>
              <button type="button" onClick={function() {
                if (!noteDialogTitle.trim()) return;
                var notes = JSON.parse(localStorage.getItem('study-notes') || '[]');
                var item = noteDialogItem;
                var result = submitted?.items?.find(function(e) { return e.paper_item_id === item.paper_item_id; });
                var opts = (item.options || []).map(function(opt, j) {
                  if (typeof opt === 'object' && opt !== null) return opt;
                  return { option_id: String.fromCharCode(65 + j), content: String(opt) };
                });
                notes.unshift({ id: 'note-' + Date.now(), title: noteDialogTitle.trim(), content: noteDialogContent || '', type: '题目笔记', source: '智能组卷', question_content: item.stem, options: opts, standard_answer: result ? result.standard_answer : [], explanation: result ? result.explanation : '', created_at: new Date().toISOString() });
                localStorage.setItem('study-notes', JSON.stringify(notes));
                setNoteDialogOpen(false);
              }} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700">保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
