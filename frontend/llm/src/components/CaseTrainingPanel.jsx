import React, { useEffect, useMemo, useState } from 'react';
import { Loader2, RefreshCw } from 'lucide-react';
import { fetchJsonWithAuthFallback } from '../utils/api';
import {
  loadCaseSession,
  loadCaseTypes,
  requestCaseHelp,
  sendCaseMessage,
  startCaseSession,
  submitCaseSession,
} from '../pageDataLoaders';

const terminalStatuses = new Set(['completed', 'needs_revision', 'rejected', 'failed', 'needs_human_review', 'abandoned', 'expired']);
const sessionStorageKey = 'training-case-session-id';

const initialAnswer = {
  syndrome: '',
  formula_name: '',
  formula_composition: '',
  inquiry: '',
};

function buildAnswer(mode, answer) {
  const inquiry = answer.inquiry.split(/[，,、\n]/).map((value) => value.trim()).filter(Boolean);
  if (mode === 'diagnosis_only') return { syndrome: answer.syndrome.trim(), inquiry };
  return {
    syndrome: answer.syndrome.trim(),
    formula_name: answer.formula_name.trim(),
    formula_composition: answer.formula_composition.split(/[，,、\n]/).map((value) => value.trim()).filter(Boolean),
    inquiry,
  };
}

function answerIsComplete(mode, answer) {
  const value = buildAnswer(mode, answer);
  return Object.values(value).every((item) => Array.isArray(item) ? item.length > 0 : Boolean(item));
}

export default function CaseTrainingPanel({ enabled }) {
  const [caseTypes, setCaseTypes] = useState([]);
  const [mode, setMode] = useState('full');
  const [selection, setSelection] = useState('random');
  const [caseType, setCaseType] = useState('');
  const [session, setSession] = useState(null);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState(initialAnswer);
  const [notice, setNotice] = useState('');
  const [disclaimer, setDisclaimer] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const hasCases = caseTypes.length > 0;
  const terminal = terminalStatuses.has(session?.status);
  const canAsk = Boolean(session?.session_id) && !terminal && question.trim();
  const canSubmit = Boolean(session?.session_id) && !terminal && answerIsComplete(session.mode, answer);
  const patientMessages = useMemo(
    () => (session?.messages || []).filter((message) => message.role === 'patient'),
    [session],
  );

  useEffect(() => {
    let active = true;
    loadCaseTypes({ fetcher: fetchJsonWithAuthFallback }).then((result) => {
      if (!active) return;
      setCaseTypes(result.caseTypes.types);
      if (result.error) setError(result.error);
    });
    const savedSessionId = sessionStorage.getItem(sessionStorageKey);
    if (savedSessionId) {
      loadCaseSession({ fetcher: fetchJsonWithAuthFallback, sessionId: savedSessionId }).then((result) => {
        if (!active) return;
        if (result.error) {
          sessionStorage.removeItem(sessionStorageKey);
          return;
        }
        applySession(result.session);
      });
    }
    return () => { active = false; };
  }, []);

  const applySession = (nextSession) => {
    setSession(nextSession);
    if (terminalStatuses.has(nextSession.status)) sessionStorage.removeItem(sessionStorageKey);
    else sessionStorage.setItem(sessionStorageKey, nextSession.session_id);
  };

  const refreshSession = async (sessionId = session?.session_id) => {
    if (!sessionId) return false;
    const result = await loadCaseSession({ fetcher: fetchJsonWithAuthFallback, sessionId });
    if (result.error) {
      setError(result.error);
      return false;
    }
    applySession(result.session);
    return true;
  };

  const applyActionStatus = (result) => {
    if (typeof result?.status === 'string') applySession({ ...session, status: result.status });
  };

  const run = async (action) => {
    setLoading(true);
    setError('');
    setNotice('');
    try {
      await action();
    } finally {
      setLoading(false);
    }
  };

  const start = () => run(async () => {
    const result = await startCaseSession({
      fetcher: fetchJsonWithAuthFallback,
      selection,
      caseType,
      mode,
    });
    if (result.error) {
      setError(result.error);
      return;
    }
    applySession(result.session);
    setAnswer(initialAnswer);
    setQuestion('');
    setNotice('案例会话已开始。');
  });

  const ask = () => run(async () => {
    const result = await sendCaseMessage({
      fetcher: fetchJsonWithAuthFallback,
      sessionId: session.session_id,
      message: question,
    });
    if (result.error) {
      setError(result.error);
      return;
    }
    setQuestion('');
    if (typeof result.result.disclaimer === 'string' && result.result.disclaimer.trim()) {
      setDisclaimer(result.result.disclaimer.trim());
    }
    applyActionStatus(result.result);
    await refreshSession(session.session_id);
  });

  const help = (helpType) => run(async () => {
    const result = await requestCaseHelp({
      fetcher: fetchJsonWithAuthFallback,
      sessionId: session.session_id,
      helpType,
    });
    if (result.error) {
      setError(result.error);
      return;
    }
    setNotice(helpType === 'hint' ? '已记录提示请求。' : '已使用答案帮助，本次不再计入正式学习回填。');
    applyActionStatus(result.result);
    await refreshSession(session.session_id);
  });

  const submit = () => run(async () => {
    const result = await submitCaseSession({
      fetcher: fetchJsonWithAuthFallback,
      sessionId: session.session_id,
      answer: buildAnswer(session.mode, answer),
    });
    if (result.error) {
      setError(result.error);
      return;
    }
    applyActionStatus(result.result);
    await refreshSession(session.session_id);
  });

  if (!enabled) return <p className="mt-5 text-sm leading-6 text-slate-600">案例训练暂未开放。</p>;

  return (
    <div className="mt-5 space-y-5">
      {!session ? (
        <div className="grid gap-4 md:grid-cols-2">
          <label className="text-sm font-medium text-slate-700">训练模式
            <select value={mode} onChange={(event) => setMode(event.target.value)} disabled={loading} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm">
              <option value="full">完整辨证与方药</option>
              <option value="diagnosis_only">辨证训练</option>
            </select>
          </label>
          <label className="text-sm font-medium text-slate-700">案例来源
            <select value={selection} onChange={(event) => setSelection(event.target.value)} disabled={loading} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm">
              <option value="random">随机案例</option>
              <option value="by_type">按案例类型</option>
            </select>
          </label>
          {selection === 'by_type' && <label className="text-sm font-medium text-slate-700 md:col-span-2">案例类型
            <select value={caseType} onChange={(event) => setCaseType(event.target.value)} disabled={loading || !hasCases} className="mt-2 w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm">
              <option value="">请选择案例类型</option>
              {caseTypes.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>}
          {!hasCases && <p className="text-sm leading-6 text-slate-600 md:col-span-2">暂无可用案例。请先导入已审核的案例定义、版本和评分标准。</p>}
          <button type="button" onClick={start} disabled={loading || !hasCases || (selection === 'by_type' && !caseType)} className="inline-flex w-fit items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-50">
            {loading && <Loader2 size={16} className="animate-spin" />}开始案例训练
          </button>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-start justify-between gap-3 border-b border-slate-200 pb-4">
            <div>
              <h3 className="text-base font-semibold text-slate-950">{session.title}</h3>
              <p className="mt-1 text-sm text-slate-600">状态：{session.status} · 已问诊 {session.learner_messages} 轮</p>
              {session.visible_context?.chief_complaint && <p className="mt-2 text-sm leading-6 text-slate-700">主诉：{session.visible_context.chief_complaint}</p>}
            </div>
            <button type="button" onClick={() => run(() => refreshSession())} disabled={loading} title="刷新会话状态" className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-slate-200 text-slate-700 disabled:opacity-50"><RefreshCw size={16} /></button>
          </div>
          <div className="max-h-64 space-y-3 overflow-y-auto pr-1">
            {(session.messages || []).map((message) => <div key={message.sequence} className={`border-l-2 pl-3 text-sm leading-6 ${message.role === 'patient' ? 'border-cyan-300 text-slate-700' : 'border-emerald-300 text-slate-900'}`}><span className="font-semibold">{message.role === 'patient' ? '患者' : '学员'}</span><p>{message.content}</p></div>)}
            {patientMessages.length === 0 && <p className="text-sm text-slate-500">请先开始问诊。</p>}
          </div>
          {disclaimer && <p className="border-l-2 border-amber-300 pl-3 text-sm leading-6 text-amber-900">{disclaimer}</p>}
          {!terminal && <div className="space-y-3">
            <label className="block text-sm font-medium text-slate-700">问诊问题
              <textarea value={question} onChange={(event) => setQuestion(event.target.value)} disabled={loading} className="mt-2 min-h-24 w-full rounded-xl border border-slate-200 bg-slate-50 p-3 text-sm" />
            </label>
            <div className="flex flex-wrap gap-2">
              <button type="button" onClick={ask} disabled={loading || !canAsk} className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">发送问题</button>
              {session.status === 'help_available' && <><button type="button" onClick={() => help('hint')} disabled={loading} className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700">请求提示</button><button type="button" onClick={() => help('answer')} disabled={loading} className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700">查看答案帮助</button></>}
            </div>
          </div>}
          {!terminal && <div className="grid gap-3 border-t border-slate-200 pt-4 md:grid-cols-2">
            <label className="text-sm font-medium text-slate-700">证型<textarea value={answer.syndrome} onChange={(event) => setAnswer({ ...answer, syndrome: event.target.value })} disabled={loading} className="mt-2 min-h-20 w-full rounded-xl border border-slate-200 p-3 text-sm" /></label>
            <label className="text-sm font-medium text-slate-700">问诊依据（以逗号分隔）<textarea value={answer.inquiry} onChange={(event) => setAnswer({ ...answer, inquiry: event.target.value })} disabled={loading} className="mt-2 min-h-20 w-full rounded-xl border border-slate-200 p-3 text-sm" /></label>
            {session.mode === 'full' && <><label className="text-sm font-medium text-slate-700">方名<textarea value={answer.formula_name} onChange={(event) => setAnswer({ ...answer, formula_name: event.target.value })} disabled={loading} className="mt-2 min-h-20 w-full rounded-xl border border-slate-200 p-3 text-sm" /></label><label className="text-sm font-medium text-slate-700">方药组成（以逗号分隔）<textarea value={answer.formula_composition} onChange={(event) => setAnswer({ ...answer, formula_composition: event.target.value })} disabled={loading} className="mt-2 min-h-20 w-full rounded-xl border border-slate-200 p-3 text-sm" /></label></>}
            <button type="button" onClick={submit} disabled={loading || !canSubmit} className="w-fit rounded-xl bg-slate-900 px-4 py-2 text-sm font-medium text-white disabled:opacity-50">提交案例答案</button>
          </div>}
        </>
      )}
      {notice && <p className="text-sm leading-6 text-emerald-700">{notice}</p>}
      {error && <p role="alert" className="rounded-xl border border-rose-200 bg-rose-50 px-3 py-2 text-sm leading-6 text-rose-700">{error}</p>}
    </div>
  );
}
