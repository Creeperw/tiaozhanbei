import React, { useState, useMemo, useEffect } from 'react';
import {
  CheckCircle2, ChevronDown, ChevronUp, Filter, Home, Loader2,
  RotateCcw, Search, Send, Target, X, XCircle,
} from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import { loadMistakes } from '../pageDataLoaders';

const REVIEW_COUNTS_KEY = 'mistake-redo-counts';

function getReviewCounts() {
  try { return JSON.parse(localStorage.getItem(REVIEW_COUNTS_KEY) || '{}'); } catch { return {}; }
}
function saveReviewCounts(counts) {
  try { localStorage.setItem(REVIEW_COUNTS_KEY, JSON.stringify(counts)); } catch {}
}

// ── Answer checking ─────────────────────────────────────────
function checkAnswer(userAnswer, correctAnswer, options) {
  // If it's a single-letter answer (A, B, C, D...) compare directly
  if (/^[A-Za-z]$/.test(userAnswer) && options?.length > 0) {
    const correctOpt = options.find((o) => {
      const val = String(o.option_id || o.id || '');
      return String(correctAnswer || '').includes(val);
    });
    if (correctOpt) {
      const correctVal = String(correctOpt.option_id || correctOpt.id || '');
      return userAnswer.toUpperCase() === correctVal.toUpperCase();
    }
  }
  // Text comparison fallback
  const normalize = (s) => s.toLowerCase().replace(/[\s,，.。!！?？;；:：""''（）()\[\]]/g, '').trim();
  const user = normalize(userAnswer);
  const correct = normalize(correctAnswer);
  if (!user || !correct) return false;
  if (user === correct) return true;
  if (correct.includes(user) && user.length > 3) return true;
  if (user.includes(correct) && correct.length > 3) return true;
  const ck = correct.split(/[，,、]/).filter((k) => k.length >= 2);
  const uk = user.split(/[，,、]/).filter((k) => k.length >= 2);
  if (ck.length > 0) {
    const matched = ck.filter((c) => uk.some((u) => u.includes(c) || c.includes(u))).length;
    return matched / ck.length >= 0.7;
  }
  return false;
}

// ── Map backend mistake to UI format ────────────────────────
function deriveSource(m) {
  const qid = String(m.question_id || '');
  if (qid.includes('possible_new')) return '真题模拟';
  if (qid.includes('generated')) return '专项特训';
  if (qid.startsWith('Q_TCM')) return '知识点特训';
  if (qid.startsWith('paper_')) return '智能组卷';
  if (qid.startsWith('WEBQ_')) return '知识点特训'; // 网络搜题补充题：随每日任务/知识点特训下发
  return '练习';
}

function mapMistake(m, counts) {
  const key = String(m.mistake_id);
  const localCount = Number(counts[key] || 0);
  const reviewCount = Math.max(localCount, Number(m.review_count || m.reviewCount || 0));
  const localMastered = Boolean(counts['_mastered_' + key]);
  const isResolved = String(m.status) === 'resolved';
  return {
    id: String(m.mistake_id || m.id || ''),
    title: String(m.stem || m.title || '错题').slice(0, 120),
    source: deriveSource(m),
    status: isResolved || localMastered ? 'mastered' : (reviewCount > 0 ? 'reviewing' : 'pending'),
    wrongAnswer: String(m.student_answer || ''),
    correctAnswer: '',
    explanation: String(m.feedback || m.summary || ''),
    reviewCount,
    tags: Array.isArray(m.kp_names) ? m.kp_names : [],
    createdAt: String(m.created_at || '').slice(0, 10),
    questionId: String(m.question_id || ''),
    raw: m,
  };
}

// ── Sub-components ───────────────────────────────────────────

function ReviewSession({ mistake, currentIndex, totalCount, onSubmit, onExit, onEndEarly, onRestart }) {
  const [userAnswer, setUserAnswer] = useState('');
  const [showResult, setShowResult] = useState(false);
  const [isCorrect, setIsCorrect] = useState(null);
  const [showExplanation, setShowExplanation] = useState(false);
  const progress = ((currentIndex + 1) / totalCount) * 100;

  const handleSubmit = () => {
    if (!userAnswer.trim()) return;
    setIsCorrect(checkAnswer(userAnswer, mistake.correctAnswer, mistake.options));
    setShowResult(true);
  };

  return (
    <div className="flex flex-col" style={{height:'calc(100vh - 200px)'}}>
      <div className="mb-4 flex items-center justify-between shrink-0">
        <div className="flex flex-1 items-center gap-4">
          <span className="text-sm font-medium text-slate-500">{currentIndex + 1} / {totalCount}</span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-emerald-600 transition-all" style={{ width: `${progress}%` }} /></div>
        </div>
        <button onClick={onExit} className="ml-3 rounded-lg p-2 text-slate-400 hover:bg-slate-100"><X size={18} /></button>
      </div>
      <div className="flex-1 overflow-y-auto space-y-4">
        <div className="rounded-2xl border border-slate-200 bg-white p-6">
          <span className="mb-3 inline-block rounded-full bg-emerald-100 px-3 py-1 text-sm text-emerald-700">错题重做</span>
          <h2 className="text-lg font-semibold leading-relaxed text-slate-900">{mistake.title}</h2>
        </div>
        {!showResult ? (
          <div>
            <label className="mb-2 block text-sm font-medium text-slate-700">你的答案</label>
            {mistake.options?.length > 0 ? (
              <div className="space-y-2">
                {mistake.options.map((opt, i) => {
                  const label = String.fromCharCode(65 + i);
                  const val = opt.option_id || opt.id || '';
                  return (
                    <label key={i} className={`flex cursor-pointer items-center gap-3 rounded-xl border p-3 text-sm transition ${userAnswer === val ? 'border-emerald-500 bg-emerald-50' : 'border-slate-200 hover:bg-slate-50'}`}>
                      <input type="radio" name="answer" checked={userAnswer === val} onChange={() => setUserAnswer(val)} className="accent-emerald-600" />
                      <span><strong className="text-slate-600">{label}.</strong> {String(opt.content || opt.value || opt.text || '').replace(/^[A-Z][.．、)\s]\s*/, '')}</span>
                    </label>
                  );
                })}
              </div>
            ) : (
              <textarea value={userAnswer} onChange={(e) => setUserAnswer(e.target.value)} placeholder="请输入你的答案..." className="min-h-[120px] w-full resize-none rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm outline-none transition focus:border-emerald-400 focus:bg-white focus:ring-4 focus:ring-emerald-100" />
            )}
          </div>
        ) : (
          <div className="space-y-4">
            <div className={`rounded-xl border p-4 ${isCorrect ? 'border-emerald-200 bg-emerald-50' : 'border-rose-200 bg-rose-50'}`}>
              <div className={`mb-2 flex items-center gap-2 text-lg font-semibold ${isCorrect ? 'text-emerald-700' : 'text-rose-700'}`}>
                {isCorrect ? <><CheckCircle2 size={22} />回答正确</> : <><XCircle size={22} />回答错误</>}
              </div>
              <p className="text-sm text-slate-600">{isCorrect ? '恭喜你答对了！' : '查看解析巩固知识点'}</p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className={`rounded-xl p-4 ${isCorrect ? 'bg-emerald-50 border border-emerald-200' : 'bg-slate-50'}`}>
                <div className="mb-2 flex items-center gap-2 text-sm font-medium text-slate-500">本次作答</div>
                <div className={isCorrect ? 'text-emerald-700' : 'text-rose-600'}>{userAnswer || '未填写'}</div>
              </div>
              <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
                <div className="mb-2 flex items-center gap-2 text-sm font-medium text-amber-700">我的首次作答</div>
                <div className="text-amber-800">{mistake.wrongAnswer || '未填写'}</div>
              </div>
            </div>
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
              <div className="mb-2 flex items-center gap-2 text-sm font-medium text-emerald-700"><CheckCircle2 size={16} />正确答案</div>
              <div>{mistake.correctAnswer || '答案暂未收录'}</div>
            </div>
            {mistake.explanation && (
              <div className="rounded-xl border border-amber-200 bg-amber-50/50">
                <button type="button" onClick={() => setShowExplanation((prev) => !prev)} className="flex w-full items-center justify-between p-4 text-left">
                  <span className="flex items-center gap-2 font-medium text-slate-800">详细解析</span>
                  {showExplanation ? <ChevronUp size={18} className="text-slate-400" /> : <ChevronDown size={18} className="text-slate-400" />}
                </button>
                {showExplanation && <div className="border-t border-amber-200 px-4 pb-4 text-sm leading-relaxed text-slate-700">{mistake.explanation}</div>}
              </div>
            )}
          </div>
        )}
      </div>
      {!showResult && (
        <div className="shrink-0 pt-3">
          <button onClick={handleSubmit} disabled={!userAnswer.trim()} className="flex w-full items-center justify-center gap-2 rounded-xl bg-emerald-600 py-3 text-base font-semibold text-white transition hover:bg-emerald-700 disabled:opacity-50">
            <Send size={18} />提交答案
          </button>
        </div>
      )}
      {showResult && (
        <div className="flex gap-3 shrink-0 pt-3">
          {totalCount > 1 && (
            <button onClick={() => { onSubmit(isCorrect); onEndEarly(); }} className="flex flex-1 items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white py-3 text-base font-semibold text-slate-700 transition hover:bg-slate-50">
              <X size={18} />结束本次复盘
            </button>
          )}
          {currentIndex < totalCount - 1 ? (
            <button onClick={() => { onSubmit(isCorrect); setUserAnswer(''); setShowResult(false); setIsCorrect(null); setShowExplanation(false); }} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-emerald-600 py-3 text-base font-semibold text-white transition hover:bg-emerald-700">
              下一题
            </button>
          ) : totalCount === 1 ? (
            <div className="flex flex-1 gap-3">
              <button onClick={() => { onSubmit(isCorrect); setUserAnswer(''); setShowResult(false); setIsCorrect(null); setShowExplanation(false); }} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-emerald-600 py-3 text-base font-semibold text-white transition hover:bg-emerald-700">
                退出
              </button>
              <button onClick={() => { onSubmit(isCorrect); onRestart(); }} className="flex flex-1 items-center justify-center gap-2 rounded-xl border border-emerald-300 bg-emerald-50 py-3 text-base font-semibold text-emerald-700 transition hover:bg-emerald-100">
                开始今日复盘
              </button>
            </div>
          ) : (
            <button onClick={() => { onSubmit(isCorrect); setUserAnswer(''); setShowResult(false); setIsCorrect(null); setShowExplanation(false); }} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-emerald-600 py-3 text-base font-semibold text-white transition hover:bg-emerald-700">
              查看结果
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function ReviewResult({ results, onContinue, onFinish }) {
  const correctCount = results.filter((r) => r.isCorrect).length;
  const wrongCount = results.filter((r) => !r.isCorrect).length;
  const accuracy = results.length > 0 ? Math.round((correctCount / results.length) * 100) : 0;
  const wrongItems = results.filter((r) => !r.isCorrect);

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-8 text-center">
        <div className="mb-4 inline-flex h-20 w-20 items-center justify-center rounded-full bg-emerald-100"><Target size={36} className="text-emerald-600" /></div>
        <h2 className="mb-2 text-2xl font-semibold text-slate-900">本次重做完成</h2>
        <p className="text-slate-500">坚持练习，温故知新</p>
      </div>
      <div className="mb-8 grid grid-cols-3 gap-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-center"><div className="mb-1 text-3xl font-bold text-emerald-600">{correctCount}</div><div className="text-sm text-slate-500">做对</div></div>
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-center"><div className="mb-1 text-3xl font-bold text-rose-500">{wrongCount}</div><div className="text-sm text-slate-500">做错</div></div>
        <div className="rounded-xl border border-slate-200 bg-white p-4 text-center"><div className="mb-1 text-3xl font-bold text-emerald-700">{accuracy}%</div><div className="text-sm text-slate-500">正确率</div></div>
      </div>
      {wrongItems.length > 0 && (
        <div className="mb-8">
          <h3 className="mb-4 flex items-center gap-2 text-lg font-medium text-slate-900"><XCircle size={20} className="text-rose-500" />错题回顾</h3>
          <div className="space-y-3">
            {wrongItems.map(({ mistake }) => (
              <div key={mistake.id} className="rounded-xl border border-amber-200 bg-amber-50/50 p-4">
                <div className="mb-2 font-medium text-slate-900">{mistake.title}</div>
                <div className="mb-1 text-sm text-amber-700">我的首次作答：{mistake.wrongAnswer}</div>
                <div className="text-sm text-emerald-600">正确答案：{mistake.correctAnswer}</div>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="flex gap-4">
        <button onClick={onFinish} className="flex flex-1 items-center justify-center gap-2 rounded-xl border border-slate-300 bg-white py-3 font-semibold text-slate-700 transition hover:bg-slate-50"><Home size={16} />返回首页</button>
        {wrongItems.length > 0 && (
          <button onClick={onContinue} className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-emerald-600 py-3 font-semibold text-white transition hover:bg-emerald-700"><RotateCcw size={16} />继续复盘</button>
        )}
      </div>
    </div>
  );
}

// ── Main Component ───────────────────────────────────────────
export default function MistakeRedoPanel() {
  const [mistakes, setMistakes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [reviewQueue, setReviewQueue] = useState([]);
  const [currentReviewIndex, setCurrentReviewIndex] = useState(0);
  const [isReviewing, setIsReviewing] = useState(false);
  const [showResult, setShowResult] = useState(false);
  const [reviewResults, setReviewResults] = useState([]);
  const [settings] = useState({ masteryThreshold: 1, shuffleQuestions: true });
  const [searchQuery, setSearchQuery] = useState('');
  const [libStatusFilter, setLibStatusFilter] = useState('all');
  const [libSourceFilter, setLibSourceFilter] = useState('all');
  const [viewingMistake, setViewingMistake] = useState(null);

  useEffect(() => {
    let active = true;
    const loaded = [];
    const loadAll = async (offset = 0) => {
      const response = await loadMistakes({ fetcher: fetchJsonWithAuthFallback, limit: 100, offset });
      if (!active) return;
      loaded.push(...(response.mistakes?.items || []));
      if (response.mistakes?.has_more && loaded.length < (response.mistakes?.total || 0)) {
        await loadAll(offset + 100);
      } else {
        // Enrich with question details (options + correct answer)
        const counts = getReviewCounts();
        const enriched = await Promise.all(loaded.map(async (m) => {
          const base = mapMistake(m, counts);
          try {
            const res = await fetchJsonWithAuthFallback({
              paths: [`/v1/workshop/practice/question-detail/${encodeURIComponent(base.questionId)}`],
              fallback: {},
            });
            const d = res.data || {};
            base.options = Array.isArray(d.options) ? d.options : [];
            base.correctAnswer = Array.isArray(d.answer) ? d.answer.map((a, i) => {
              const opt = base.options.find((o) => String(o.option_id || o.id) === String(a));
              return opt ? `${opt.option_id || ''}. ${opt.content || ''}` : String(a);
            }).join('；') : String(d.answer || '');
            base.explanation = d.explanation || base.explanation;
          } catch {}
          return base;
        }));
        if (active) { setMistakes(enriched); setLoading(false); }
      }
    };
    loadAll().catch(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const filteredMistakes = useMemo(() => mistakes.filter((m) => {
    if (searchQuery && !m.title.toLowerCase().includes(searchQuery.toLowerCase())) return false;
    if (libStatusFilter !== 'all' && m.status !== libStatusFilter) return false;
    if (libSourceFilter !== 'all' && m.source !== libSourceFilter) return false;
    return true;
  }), [mistakes, searchQuery, libStatusFilter, libSourceFilter]);

  const pendingMistakes = useMemo(() => mistakes.filter((m) => m.status === 'pending' || m.status === 'reviewing'), [mistakes]);

  const startReviewSession = (selected) => {
    const queue = selected || pendingMistakes;
    if (queue.length === 0) return;
    setReviewQueue(settings.shuffleQuestions ? [...queue].sort(() => Math.random() - 0.5) : queue);
    setCurrentReviewIndex(0);
    setReviewResults([]);
    setIsReviewing(true);
    setShowResult(false);
  };

  const submitAnswer = (isCorrect) => {
    const current = reviewQueue[currentReviewIndex];
    // Save to localStorage
    const counts = getReviewCounts();
    const key = String(current.raw?.mistake_id || current.id);
    counts[key] = (counts[key] || 0) + 1;
    // Also store consecutive correct count for mastery tracking
    const correctKey = 'mistake-redo-correct';
    const correctCounts = JSON.parse(localStorage.getItem(correctKey) || '{}');
    if (isCorrect) {
      correctCounts[key] = (correctCounts[key] || 0) + 1;
      if (correctCounts[key] >= settings.masteryThreshold) {
        counts['_mastered_' + key] = 1;
      }
    } else {
      correctCounts[key] = 0;
      delete counts['_mastered_' + key];
    }
    localStorage.setItem(correctKey, JSON.stringify(correctCounts));
    saveReviewCounts(counts);
    // Update local state
    setMistakes((prev) => prev.map((m) => {
      if (m.id === current.id) {
        const newCount = m.reviewCount + 1;
        const mastered = isCorrect && (correctCounts[key] || 0) >= settings.masteryThreshold;
        return { ...m, reviewCount: newCount, status: mastered ? 'mastered' : newCount > 0 ? 'reviewing' : 'pending' };
      }
      return m;
    }));
    setReviewResults((prev) => [...prev, { mistake: current, isCorrect }]);
    if (currentReviewIndex < reviewQueue.length - 1) {
      setCurrentReviewIndex((p) => p + 1);
    } else {
      setIsReviewing(false);
      setShowResult(true);
    }
  };

  const endReviewSession = () => {
    setIsReviewing(false); setShowResult(false);
    setReviewQueue([]); setReviewResults([]); setCurrentReviewIndex(0);
  };

  return (
    <div className="mx-auto max-w-[87.5%] space-y-5">
      {loading ? (
        <div className="flex items-center justify-center py-12"><Loader2 className="animate-spin text-emerald-600" size={24} /></div>
      ) : isReviewing ? (
        <ReviewSession mistake={reviewQueue[currentReviewIndex]} currentIndex={currentReviewIndex} totalCount={reviewQueue.length} onSubmit={submitAnswer} onExit={endReviewSession} onEndEarly={() => { setIsReviewing(false); setShowResult(true); }} onRestart={() => startReviewSession()} />
      ) : showResult ? (
        <ReviewResult results={reviewResults} onContinue={() => startReviewSession()} onFinish={endReviewSession} />
      ) : (<>
        <div className="grid grid-cols-[3fr_1fr] gap-4 mb-4">
          <div className="rounded-2xl bg-gradient-to-br from-emerald-50 to-teal-50 p-8 text-center flex flex-col justify-center">
            <h2 className="mb-2 text-2xl font-semibold text-slate-900">开始错题重做</h2>
            <p className="mb-6 text-slate-500">今日待复习 {pendingMistakes.length} 道错题，坚持练习，温故知新</p>
            <div className="flex items-center justify-center gap-4">
              <button onClick={() => startReviewSession()} disabled={pendingMistakes.length === 0} className="rounded-xl bg-emerald-600 px-8 py-3 font-medium text-white shadow-lg shadow-emerald-200 transition hover:bg-emerald-700 disabled:opacity-50">开始重做</button>
            </div>
          </div>
          <div className="grid grid-rows-3 gap-3">
            <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 text-center flex flex-col justify-center"><div className="text-2xl font-bold text-emerald-600">{mistakes.filter((m) => m.status === 'mastered').length}</div><div className="text-xs text-emerald-700">已掌握</div></div>
            <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-center flex flex-col justify-center"><div className="text-2xl font-bold text-amber-600">{mistakes.filter((m) => m.status === 'reviewing').length}</div><div className="text-xs text-amber-700">复习中</div></div>
            <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-center flex flex-col justify-center"><div className="text-2xl font-bold text-rose-500">{mistakes.filter((m) => m.status === 'pending').length}</div><div className="text-xs text-rose-700">待复习</div></div>
          </div>
        </div>
        <div className="space-y-4">
          <div className="rounded-xl border border-slate-200 bg-white p-4 space-y-3">
            <div className="relative">
              <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
              <input placeholder="搜索错题..." value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} className="w-full rounded-lg border border-slate-200 bg-slate-50 py-2 pl-10 pr-3 text-sm outline-none transition focus:border-emerald-400 focus:bg-white" />
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Filter size={14} className="text-slate-400" />
              <span className="text-xs text-slate-400">状态:</span>
              {[{k:'all',l:'全部'},{k:'pending',l:'待复习'},{k:'reviewing',l:'复习中'},{k:'mastered',l:'已掌握'}].map(({k,l}) => (
                <button key={k} onClick={() => setLibStatusFilter(k)} className={`rounded-md px-2 py-1 text-xs font-medium transition ${libStatusFilter === k ? 'bg-emerald-600 text-white' : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`}>{l}</button>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-slate-400">来源:</span>
              {[{k:'all',l:'全部'},{k:'真题模拟',l:'真题模拟'},{k:'知识点特训',l:'知识点特训'},{k:'专项特训',l:'专项特训'},{k:'智能组卷',l:'智能组卷'},{k:'练习',l:'练习'}].map(({k,l}) => (
                <button key={k} onClick={() => setLibSourceFilter(k)} className={`rounded-md px-2 py-1 text-xs font-medium transition ${libSourceFilter === k ? 'bg-emerald-600 text-white' : 'border border-slate-200 bg-white text-slate-600 hover:bg-slate-50'}`}>{l}</button>
              ))}
            </div>
          </div>
          {loading ? (
            <div className="flex items-center justify-center py-12"><Loader2 className="animate-spin text-emerald-600" size={24} /></div>
          ) : filteredMistakes.length === 0 ? (
            <p className="py-12 text-center text-sm text-slate-400">暂无错题记录</p>
          ) : (
            <div className="space-y-3">
              {filteredMistakes.map((m) => (
                <div key={m.id} className="rounded-xl border border-slate-200 bg-white p-4 transition hover:border-emerald-300">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0 flex-1">
                      <h3 className="mb-2 text-sm font-medium leading-relaxed text-slate-900">{m.title}</h3>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${m.status === 'mastered' ? 'bg-emerald-100 text-emerald-700' : m.status === 'reviewing' ? 'bg-amber-100 text-amber-700' : 'bg-rose-100 text-rose-700'}`}>{m.status === 'mastered' ? '已掌握' : m.status === 'reviewing' ? '复习中' : '待复习'}</span>
                        {m.reviewCount > 0 && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-500">复习 {m.reviewCount} 次</span>}
                        {m.tags.slice(0, 3).map((tag) => (
                          <span key={tag} className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-600">{tag}</span>
                        ))}
                      </div>
                    </div>
                    <div className="flex shrink-0 items-center gap-1.5">
                      <button onClick={() => setViewingMistake(m)} className="flex items-center gap-1 rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm font-semibold text-slate-700 transition hover:bg-slate-50">查看</button>
                      <button onClick={() => startReviewSession([m])} className="flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-sm font-semibold text-white transition hover:bg-emerald-700"><RotateCcw size={14} />重做</button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </> )}

      {viewingMistake && (
        <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 pt-12" onClick={() => setViewingMistake(null)}>
          <div className="w-full max-w-2xl rounded-2xl border border-slate-200 bg-white shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between border-b border-slate-200 p-4">
              <h2 className="text-lg font-semibold text-slate-900">错题详情</h2>
              <button onClick={() => setViewingMistake(null)} className="rounded-lg p-2 text-slate-400 hover:bg-slate-100"><X size={18} /></button>
            </div>
            <div className="max-h-[70vh] overflow-y-auto p-5 space-y-4">
              <div><h3 className="mb-2 text-sm font-medium text-slate-500">题目</h3><p className="text-slate-900">{viewingMistake.title}</p></div>
              {viewingMistake.options?.length > 0 && (
                <div>
                  <h3 className="mb-2 text-sm font-medium text-slate-500">选项</h3>
                  <div className="space-y-1.5">
                    {viewingMistake.options.map((opt, i) => {
                      const label = String.fromCharCode(65 + i);
                      const val = opt.option_id || opt.id || '';
                      const text = opt.content || opt.value || opt.text || String(opt);
                      const isCorrect = String(viewingMistake.correctAnswer || '').includes(val);
                      const isWrong = String(viewingMistake.wrongAnswer || '').includes(val);
                      let bg = '';
                      if (isCorrect) bg = 'bg-emerald-100 border-emerald-300';
                      else if (isWrong) bg = 'bg-rose-100 border-rose-300';
                      return <div key={i} className={`rounded-lg border px-3 py-2 text-sm ${bg || 'border-slate-200'}`}>{label}. {String(text).replace(/^[A-Z][.．、)\s]\s*/, '')}{isCorrect ? ' ✓' : ''}{isWrong && !isCorrect ? ' ✗' : ''}</div>;
                    })}
                  </div>
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-xl border border-amber-200 bg-amber-50 p-3"><div className="mb-1 text-xs font-medium text-amber-700">我的首次作答</div><div className="text-sm text-amber-800">{viewingMistake.wrongAnswer || '未填写'}</div></div>
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3"><div className="mb-1 text-xs font-medium text-emerald-600">正确答案</div><div className="text-sm text-emerald-700">{viewingMistake.correctAnswer || '未收录'}</div></div>
              </div>
              {viewingMistake.explanation && <div><h3 className="mb-2 text-sm font-medium text-slate-500">解析</h3><div className="rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm leading-relaxed text-slate-700">{viewingMistake.explanation}</div></div>}
              {viewingMistake.tags?.length > 0 && <div className="flex flex-wrap gap-1.5">{viewingMistake.tags.map((tag) => <span key={tag} className="rounded-full bg-blue-50 px-2 py-0.5 text-xs text-blue-600">{tag}</span>)}</div>}
            </div>
            <div className="flex items-center gap-2 border-t border-slate-200 bg-slate-50 p-4">
              <button onClick={() => setViewingMistake(null)} className="flex-1 rounded-lg border border-slate-300 bg-white py-2 text-sm font-semibold text-slate-700 hover:bg-slate-100">关闭</button>
              <button onClick={() => { const m = viewingMistake; setViewingMistake(null); startReviewSession([m]); }} className="flex flex-1 items-center justify-center gap-1 rounded-lg bg-emerald-600 py-2 text-sm font-semibold text-white hover:bg-emerald-700"><RotateCcw size={14} />重做本题</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
