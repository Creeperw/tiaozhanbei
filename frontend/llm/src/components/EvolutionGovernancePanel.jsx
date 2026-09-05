import React, { useCallback, useEffect, useState } from 'react';
import { Activity, CheckCircle2, FlaskConical, PauseCircle, PlayCircle, RefreshCw, ShieldCheck } from 'lucide-react';
import { MAIN_API_BASE, fetchWithAuth, readJsonResponse } from '../utils/api';

const getJson = async (path, options) => {
  const response = await fetchWithAuth(`${MAIN_API_BASE}${path}`, options);
  const data = await readJsonResponse(response, {});
  if (!response.ok) throw new Error(data.detail || `请求失败（${response.status}）`);
  return data;
};

const Pill = ({ children, tone = 'slate' }) => {
  const tones = {
    slate: 'border-slate-200 bg-slate-50 text-slate-600',
    green: 'border-emerald-200 bg-emerald-50 text-emerald-700',
    amber: 'border-amber-200 bg-amber-50 text-amber-700',
    red: 'border-rose-200 bg-rose-50 text-rose-700',
  };
  return <span className={`rounded-full border px-2 py-1 text-xs font-semibold ${tones[tone]}`}>{children}</span>;
};

const Empty = ({ children }) => <div className="rounded-2xl border border-dashed border-slate-200 px-4 py-10 text-center text-sm text-slate-400">{children}</div>;

const percent = value => (typeof value === 'number' ? `${(value * 100).toFixed(1)}%` : 'N/A');

const Metric = ({ label, value, note }) => <div className="rounded-2xl border border-slate-100 bg-slate-50 p-3">
  <div className="text-xs font-semibold text-slate-500">{label}</div>
  <div className="mt-1 text-xl font-bold text-slate-900">{value}</div>
  {note && <div className="mt-1 text-xs text-slate-400">{note}</div>}
</div>;

function D1V2EvaluationPanel({ overview }) {
  const manifest = overview?.manifest || {};
  const runs = Array.isArray(overview?.runs) ? overview.runs : [];
  const latest = runs[0];
  const score = latest?.score;
  const metrics = score?.core_metrics || {};
  const improvement = metrics.same_class_problem_improvement || {};
  const firstPass = metrics.first_audit_pass_lift || {};
  const boundary = metrics.compatible_boundary_harm || {};
  const repairs = metrics.average_repair_count_reduction || {};
  const blind = score?.blind_review || {};
  const repeats = score?.repeatability || {};
  const technical = score?.technical_execution || {};

  return <section className="rounded-3xl border border-indigo-100 bg-white p-5 shadow-sm" aria-label="D1 V2 行为评测">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <h2 className="font-bold text-slate-900">D1 V2 隔离行为评测</h2>
        <p className="mt-1 text-sm text-slate-500">冻结 100 对 A/B；评测完成只形成证据，不会自动批准、激活或写回生产规则。</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <Pill tone={manifest.human_review_passed ? 'green' : 'amber'}>人工抽检 {manifest.human_review_approved_count || 0}/{manifest.human_review_required_count || 10}</Pill>
        <Pill tone={manifest.runtime_mode === 'live' ? 'green' : 'red'}>模式：{manifest.runtime_mode || '未知'}</Pill>
        <Pill tone={manifest.formal_environment_write_allowed === false ? 'green' : 'red'}>正式写回：关闭</Pill>
      </div>
    </div>

    <div className={`mt-4 rounded-2xl border px-4 py-3 text-sm ${manifest.execution_allowed ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-amber-200 bg-amber-50 text-amber-800'}`}>
      {manifest.execution_allowed
        ? '技术执行门已打开；仍须按单轨迹 → 10 条预检 → 100 对正式评测顺序人工启动。'
        : manifest.execution_block_reason === 'live_mode_required'
          ? '执行已阻断：当前不是 live 模式。'
          : manifest.execution_block_reason === 'independent_human_review_rejected'
            ? `执行已阻断：抽检存在驳回案例（${(manifest.human_review_rejected_case_ids || []).join('、')}）。`
            : `执行已阻断：固定 10 条独立人工抽检尚未全部完成并通过（已完成 ${manifest.human_review_completed_count || 0} 条）。`}
    </div>

    {latest ? <div className="mt-4 rounded-2xl border border-slate-100 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div><b className="text-sm text-slate-800">最新批次：{latest.purpose}</b><div className="mt-1 text-xs text-slate-400">{latest.run_id}</div></div>
        <Pill tone={latest.status === 'completed' ? 'green' : latest.status === 'failed' ? 'red' : 'amber'}>{latest.status}</Pill>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-indigo-500" style={{ width: `${latest.total_case_count ? (latest.completed_case_count / latest.total_case_count) * 100 : 0}%` }} /></div>
      <div className="mt-2 flex flex-wrap gap-4 text-xs text-slate-500">
        <span>进度 {latest.completed_case_count}/{latest.total_case_count}</span>
        <span>技术失败 {latest.technical_failure_count}</span>
        <span>重试恢复 {latest.recovered_case_count}</span>
        <span>覆盖门禁 {latest.coverage_valid ? '通过' : '未通过'}</span>
      </div>
    </div> : <div className="mt-4"><Empty>尚未启动 V2 单轨迹、预检或正式批次</Empty></div>}

    {score && <>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Metric label="同类问题净改进" value={percent(improvement.rate)} note={`${improvement.numerator || 0}/60；回归 ${improvement.regression_count || 0}`} />
        <Metric label="首次审核通过提升" value={`${Number(firstPass.absolute_percentage_point_change || 0).toFixed(1)} pp`} note={`A ${percent(firstPass.baseline_rate)} → B ${percent(firstPass.candidate_rate)}`} />
        <Metric label="相容边界误伤" value={percent(boundary.rate)} note={`${boundary.numerator || 0}/10`} />
        <Metric label="平均返修数降低" value={repairs.relative_reduction_display === 'N/A' ? 'N/A' : percent(repairs.relative_reduction)} note={`A ${repairs.baseline_mean ?? 'N/A'} → B ${repairs.candidate_mean ?? 'N/A'}`} />
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <Metric label="技术执行" value={`${technical.recovered_case_count || 0} 条恢复`} note={`${technical.failed_case_count || 0} 条最终失败`} />
        <Metric label="盲审" value={`${blind.distinct_case_count || 0}/20`} note={blind.minimum_met ? `一致率 ${percent(blind.agreement_rate)}` : '尚未达到不同案例门槛'} />
        <Metric label="重复运行" value={`${repeats.distinct_case_count || 0}/20`} note={repeats.minimum_met ? `完全一致率 ${percent(repeats.exact_outcome_agreement_rate)}` : '尚未达到不同案例门槛'} />
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Pill tone={score.integrity?.passed ? 'green' : 'red'}>完整性：{score.integrity?.passed ? '通过' : '失败'}</Pill>
        <Pill tone={score.evidence_completeness_gate?.passed ? 'green' : 'amber'}>证据门：{score.evidence_completeness_gate?.passed ? '齐备' : '未齐备'}</Pill>
        <Pill tone="slate">规则状态变更：禁止</Pill>
      </div>
    </>}
  </section>;
}

export function EvolutionGovernancePanel() {
  const [status, setStatus] = useState(null);
  const [feedback, setFeedback] = useState([]);
  const [feedbackClassifications, setFeedbackClassifications] = useState([]);
  const [feedbackClassificationSelections, setFeedbackClassificationSelections] = useState({});
  const [signatures, setSignatures] = useState([]);
  const [rules, setRules] = useState([]);
  const [evaluations, setEvaluations] = useState({});
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [trainingFeedback, setTrainingFeedback] = useState(null);
  const [trainingDraft, setTrainingDraft] = useState({ prompt: '', rejected: '', chosen: '', rationale: '' });
  const [reviewNotes, setReviewNotes] = useState({});
  const [v2Overview, setV2Overview] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setMessage('');
    try {
      const [nextStatus, nextFeedback, nextClassifications, nextSignatures, nextRules] = await Promise.all([
        getJson('/evolution/status'),
        getJson('/evolution/feedback/admin?limit=100'),
        getJson('/evolution/feedback/admin/classifications'),
        getJson('/evolution/signatures?limit=100'),
        getJson('/evolution/rules?limit=100'),
      ]);
      setStatus(nextStatus);
      setFeedback(Array.isArray(nextFeedback) ? nextFeedback : []);
      setFeedbackClassifications(Array.isArray(nextClassifications) ? nextClassifications : []);
      setSignatures(Array.isArray(nextSignatures) ? nextSignatures : []);
      setRules(Array.isArray(nextRules) ? nextRules : []);
      try {
        setV2Overview(await getJson('/evolution/evaluation/d1-v2/overview'));
      } catch {
        setV2Overview(null);
      }
      const evaluationEntries = await Promise.all(
        (Array.isArray(nextRules) ? nextRules : []).map(async rule => {
          try {
            return [rule.rule_id, await getJson(`/evolution/rules/${rule.rule_id}/evaluations`)];
          } catch {
            return [rule.rule_id, []];
          }
        }),
      );
      setEvaluations(Object.fromEntries(evaluationEntries));
    } catch (error) {
      setMessage(error.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => { void refresh(); }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const mutate = async (path, body) => {
    try {
      await getJson(path, { method: 'POST', ...(body ? { body: JSON.stringify(body) } : {}) });
      await refresh();
    } catch (error) { setMessage(error.message); }
  };

  const validateFeedback = async (item, nextStatus) => {
    const requiresClassification = nextStatus === 'validated'
      && ['user', 'human_review'].includes(item.source_type)
      && !item.field_path;
    const classificationId = feedbackClassificationSelections[item.feedback_id] || '';
    if (requiresClassification && !classificationId) {
      setMessage('请先选择与执行轨迹核对一致的规则分类。');
      return;
    }
    try {
      await getJson(`/evolution/feedback/admin/${item.feedback_id}`, {
        method: 'PATCH',
        body: JSON.stringify({
          status: nextStatus,
          classification_id: classificationId || null,
          issue_type: item.issue_type,
          target_agent: item.target_agent,
          owner_step_id: item.owner_step_id,
          reviewer_note: '',
        }),
      });
      await refresh();
    } catch (error) { setMessage(error.message); }
  };

  const transition = (rule, action) => mutate(`/evolution/rules/${rule.rule_id}/transition`, {
    action,
    reviewer_domain: 'mixed',
    note: '管理员在治理台确认',
  });

  const createTrainingSample = async () => {
    if (!trainingFeedback) return;
    try {
      await getJson('/preference-training/samples', {
        method: 'POST',
        body: JSON.stringify({
          source_type: 'human_review',
          source_id: trainingFeedback.feedback_id,
          task_type: trainingFeedback.task_type,
          ...trainingDraft,
        }),
      });
      setTrainingFeedback(null);
      setTrainingDraft({ prompt: '', rejected: '', chosen: '', rationale: '' });
      setMessage('训练样本草稿已创建，仍需在偏好训练页人工批准。');
    } catch (error) { setMessage(error.message); }
  };

  const resolvePaperReview = async (item, action) => {
    const note = String(reviewNotes[item.feedback_id] || '').trim();
    if (!item.conversation_id || note.length < 3) {
      setMessage('请先填写至少 3 个字的人工复核说明。');
      return;
    }
    try {
      await getJson(`/workshop/smart-papers/reviews/${encodeURIComponent(item.conversation_id)}`, {
        method: 'POST',
        body: JSON.stringify({ action, note }),
      });
      await refresh();
      setMessage(action === 'approve_publish' ? '人工复核通过，试卷已正式发布。' : '待复核试卷已驳回。');
    } catch (error) { setMessage(error.message); }
  };

  return (
    <div className="space-y-5">
      <section className="rounded-3xl border border-emerald-100 bg-white p-5 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 font-bold text-slate-900"><ShieldCheck className="text-emerald-600" size={20}/>失败驱动策略进化</div>
            <p className="mt-1 text-sm text-slate-500">失败签名、回放、人工批准、定向生效与回滚均留痕；规则采用封闭模板，默认不影响正式链路。</p>
          </div>
          <button onClick={refresh} className="inline-flex items-center gap-2 rounded-xl border border-emerald-200 px-3 py-2 text-sm text-emerald-700 hover:bg-emerald-50"><RefreshCw size={15} className={loading ? 'animate-spin' : ''}/>刷新</button>
        </div>
        {status && <div className="mt-4 flex flex-wrap gap-2">
          <Pill tone={status.governance_enabled ? 'green' : 'amber'}>治理：{status.governance_enabled ? '已启用' : '未启用'}</Pill>
          <Pill tone={status.runtime_rules_enabled ? 'green' : 'slate'}>运行时规则：{status.runtime_rules_enabled ? '已启用' : '关闭'}</Pill>
          <Pill tone="green">安全边界：封闭模板 + 管理员批准 + 故障回退</Pill>
        </div>}
        {message && <div className="mt-4 rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">{message}</div>}
      </section>

      {v2Overview && <D1V2EvaluationPanel overview={v2Overview} />}

      <section className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
        <h2 className="mb-3 font-bold text-slate-900">待审核反馈</h2>
        <div className="space-y-2">
          {feedback.filter(item => item.status === 'pending').map(item => <div key={item.feedback_id} className="rounded-2xl border border-slate-100 p-4">
            <div className="flex flex-wrap items-center gap-2"><Pill tone="amber">{item.source_type}</Pill><Pill>{item.trust_level}</Pill><b className="text-sm text-slate-800">{item.issue_type}</b></div>
            <p className="mt-2 whitespace-pre-wrap text-sm text-slate-600">{item.summary}</p>
            {['user', 'human_review'].includes(item.source_type) && !item.field_path && <div className="mt-3">
              <label className="mb-1 block text-xs font-semibold text-slate-600" htmlFor={`feedback-classification-${item.feedback_id}`}>规则分类（须人工核对执行轨迹）</label>
              <select id={`feedback-classification-${item.feedback_id}`} value={feedbackClassificationSelections[item.feedback_id] || ''} onChange={event => setFeedbackClassificationSelections(current => ({ ...current, [item.feedback_id]: event.target.value }))} className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700">
                <option value="">请选择代码注册的规则分类</option>
                {feedbackClassifications.map(option => <option key={option.classification_id} value={option.classification_id}>{option.label}</option>)}
              </select>
              <p className="mt-1 text-xs text-slate-400">系统只采用所选分类对应的固定 Agent、步骤和字段，不从反馈文字自动推断。</p>
            </div>}
            {item.source_type === 'human_review' && item.task_type === 'paper_generation' && <input value={reviewNotes[item.feedback_id] || ''} onChange={event => setReviewNotes(current => ({ ...current, [item.feedback_id]: event.target.value }))} placeholder="人工复核说明（发布或驳回必填）" className="mt-3 w-full rounded-lg border border-slate-200 px-3 py-2 text-xs" />}
            <div className="mt-3 flex gap-2">
              {item.source_type === 'human_review' && item.task_type === 'paper_generation' && <button onClick={() => resolvePaperReview(item, 'approve_publish')} className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs font-semibold text-white">确认安全并发布</button>}
              {item.source_type === 'human_review' && item.task_type === 'paper_generation' && <button onClick={() => resolvePaperReview(item, 'reject')} className="rounded-lg border border-rose-200 px-3 py-1.5 text-xs text-rose-700">驳回试卷</button>}
              <button onClick={() => validateFeedback(item, 'validated')} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white">{item.source_type === 'human_review' ? '纳入规则优化' : '批准入库'}</button>
              {item.source_type === 'human_review' && <button onClick={() => { setTrainingFeedback(item); setTrainingDraft({ prompt: '', rejected: '', chosen: '', rationale: item.summary || '' }); }} className="rounded-lg border border-emerald-200 px-3 py-1.5 text-xs font-semibold text-emerald-700">整理为训练样本</button>}
              <button onClick={() => validateFeedback(item, 'rejected')} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600">驳回</button>
            </div>
          </div>)}
          {!feedback.some(item => item.status === 'pending') && <Empty>暂无待审核反馈</Empty>}
        </div>
      </section>

      {trainingFeedback && <section className="rounded-3xl border border-emerald-200 bg-white p-5 shadow-sm" aria-label="整理人工复核训练样本">
        <div className="flex items-start justify-between gap-3"><div><h2 className="font-bold text-slate-900">整理为偏好训练样本</h2><p className="mt-1 text-sm text-slate-500">人工填写原输入、未通过输出和修正后输出。系统不会从审核意见臆造训练答案，保存后仍需二次批准。</p></div><button onClick={() => setTrainingFeedback(null)} className="text-slate-400">×</button></div>
        <div className="mt-4 grid gap-3">
          <textarea value={trainingDraft.prompt} onChange={event => setTrainingDraft(current => ({ ...current, prompt: event.target.value }))} placeholder="原始输入" className="min-h-20 rounded-xl border border-slate-200 px-3 py-2 text-sm" />
          <textarea value={trainingDraft.rejected} onChange={event => setTrainingDraft(current => ({ ...current, rejected: event.target.value }))} placeholder="未通过审核的输出" className="min-h-28 rounded-xl border border-slate-200 px-3 py-2 text-sm" />
          <textarea value={trainingDraft.chosen} onChange={event => setTrainingDraft(current => ({ ...current, chosen: event.target.value }))} placeholder="人工确认的正确输出" className="min-h-28 rounded-xl border border-slate-200 px-3 py-2 text-sm" />
          <textarea value={trainingDraft.rationale} onChange={event => setTrainingDraft(current => ({ ...current, rationale: event.target.value }))} placeholder="修正依据" className="min-h-20 rounded-xl border border-slate-200 px-3 py-2 text-sm" />
        </div>
        <div className="mt-4 flex justify-end"><button disabled={!trainingDraft.prompt.trim() || !trainingDraft.rejected.trim() || !trainingDraft.chosen.trim()} onClick={createTrainingSample} className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-40">创建待审训练样本</button></div>
      </section>}

      <section className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
        <h2 className="mb-3 font-bold text-slate-900">重复失败签名</h2>
        <div className="grid gap-3 lg:grid-cols-2">
          {signatures.map(item => <div key={item.signature_id} className="rounded-2xl border border-slate-100 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2"><b className="text-sm text-slate-800">{item.issue_type}</b><Pill tone={item.effective_candidate_ready ? 'green' : item.threshold_ready ? 'amber' : 'slate'}>{item.effective_candidate_ready ? '可生成候选' : item.threshold_ready ? '门禁拦截' : '观察中'}</Pill></div>
            <div className="mt-2 text-xs leading-6 text-slate-500">{item.task_type} · {item.target_agent} · {item.owner_step_id}<br/>案例 {item.case_count} / 执行 {item.execution_count} / 高可信 {item.high_trust_count}</div>
            {item.threshold_ready && !item.effective_candidate_ready && <div className="mt-2 text-xs text-amber-700">当前代码已覆盖、字段已失效，或没有匹配的封闭模板；不会调用模型生成无效规则。</div>}
            {item.effective_candidate_ready && <button onClick={() => mutate('/evolution/rules/generate', { signature_id: item.signature_id })} className="mt-3 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs font-semibold text-white">生成规则候选</button>}
          </div>)}
          {!signatures.length && <Empty>尚未形成失败签名</Empty>}
        </div>
      </section>

      <section className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
        <h2 className="mb-3 font-bold text-slate-900">规则生命周期</h2>
        <div className="space-y-3">
          {rules.map(rule => <div key={`${rule.rule_id}-${rule.version}`} className="rounded-2xl border border-slate-100 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2"><b className="text-sm text-slate-800">{rule.contract.template_id}</b><Pill tone={rule.status === 'active' ? 'green' : rule.status === 'rejected' ? 'red' : 'amber'}>{rule.status}</Pill></div>
            <p className="mt-2 text-sm text-slate-600">{rule.strategy_text}</p>
            {evaluations[rule.rule_id]?.[0] && <div className="mt-3 grid gap-2 rounded-xl bg-slate-50 p-3 text-xs text-slate-600 sm:grid-cols-3">
              <span>目标集改进：<b>{evaluations[rule.rule_id][0].paired_improvements}</b></span>
              <span>目标集回归：<b>{evaluations[rule.rule_id][0].paired_regressions}</b></span>
              <span>控制集回归：<b>{evaluations[rule.rule_id][0].control_regressions}</b></span>
              <span>行为评估：<Pill tone={evaluations[rule.rule_id][0].behavior_eval_passed ? 'green' : 'red'}>{evaluations[rule.rule_id][0].behavior_eval_passed ? '通过' : '未通过'}</Pill></span>
              <span>回归门禁：<Pill tone={evaluations[rule.rule_id][0].regression_gate_passed ? 'green' : 'red'}>{evaluations[rule.rule_id][0].regression_gate_passed ? '通过' : '未通过'}</Pill></span>
              <span>数据集：{evaluations[rule.rule_id][0].dataset_id} v{evaluations[rule.rule_id][0].dataset_version}</span>
            </div>}
            <div className="mt-3 flex flex-wrap gap-2">
              {rule.status === 'draft' && <button onClick={() => mutate(`/evolution/rules/${rule.rule_id}/replay`)} className="inline-flex items-center gap-1 rounded-lg bg-slate-800 px-3 py-1.5 text-xs text-white"><FlaskConical size={13}/>安全回放</button>}
              {rule.status === 'safety_replay_passed' && <span className="rounded-lg border border-amber-200 px-3 py-1.5 text-xs text-amber-700">待行为评估与回归门禁</span>}
              {rule.status === 'regression_gate_passed' && <button onClick={() => transition(rule, 'approve')} className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs text-white"><CheckCircle2 size={13}/>人工批准</button>}
              {rule.status === 'approved' && <button onClick={() => transition(rule, 'activate')} className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-3 py-1.5 text-xs text-white"><PlayCircle size={13}/>激活</button>}
              {rule.status === 'active' && <button onClick={() => transition(rule, 'pause')} className="inline-flex items-center gap-1 rounded-lg border border-amber-200 px-3 py-1.5 text-xs text-amber-700"><PauseCircle size={13}/>暂停</button>}
              {rule.status === 'paused' && <button onClick={() => transition(rule, 'resume')} className="rounded-lg bg-emerald-600 px-3 py-1.5 text-xs text-white">恢复</button>}
              {!['active', 'paused', 'retired', 'rejected'].includes(rule.status) && <button onClick={() => transition(rule, 'reject')} className="rounded-lg border border-rose-200 px-3 py-1.5 text-xs text-rose-700">驳回</button>}
              {['active', 'paused'].includes(rule.status) && <button onClick={() => transition(rule, 'retire')} className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs text-slate-600">退役</button>}
            </div>
          </div>)}
          {!rules.length && <Empty>暂无规则候选</Empty>}
        </div>
      </section>
    </div>
  );
}

export function PreferenceTrainingPanel() {
  const [status, setStatus] = useState(null);
  const [samples, setSamples] = useState([]);
  const [datasets, setDatasets] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [message, setMessage] = useState('');

  const refresh = useCallback(async () => {
    try {
      const [nextStatus, nextSamples, nextDatasets, nextJobs] = await Promise.all([
        getJson('/evolution/status'), getJson('/preference-training/samples'),
        getJson('/preference-training/datasets'), getJson('/preference-training/jobs'),
      ]);
      setStatus(nextStatus); setSamples(nextSamples); setDatasets(nextDatasets); setJobs(nextJobs); setMessage('');
    } catch (error) { setMessage(error.message); }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => { void refresh(); }, 0);
    return () => window.clearTimeout(timer);
  }, [refresh]);

  const approved = samples.filter(item => item.status === 'approved' && !item.contains_sensitive_data);
  const freeze = async () => {
    try {
      await getJson('/preference-training/datasets/freeze', { method: 'POST', body: JSON.stringify({ name: `人工审核偏好集-${new Date().toLocaleDateString()}`, sample_ids: approved.map(item => item.sample_id) }) });
      await refresh();
    } catch (error) { setMessage(error.message); }
  };
  const dryRun = async (dataset) => {
    try {
      await getJson('/preference-training/jobs', { method: 'POST', body: JSON.stringify({ dataset_id: dataset.dataset_id, backend: 'dry_run', base_model: '待部署模型', epochs: 1, learning_rate: 0.000005, batch_size: 1, lora_rank: 8 }) });
      await refresh();
    } catch (error) { setMessage(error.message); }
  };

  return <div className="space-y-5">
    <section className="rounded-3xl border border-emerald-100 bg-white p-5 shadow-sm">
      <div className="flex items-center gap-2 font-bold text-slate-900"><Activity size={20} className="text-emerald-600"/>一键偏好训练平台（附加能力）</div>
      <p className="mt-1 text-sm text-slate-500">训练效果不作为当前创新点验收指标。平台只负责人工审核、不可变数据集、完整性校验与受限任务创建，不会自动替换正式模型。</p>
      <div className="mt-3 flex gap-2"><Pill tone={status?.preference_training_enabled ? 'green' : 'amber'}>平台：{status?.preference_training_enabled ? '已启用' : '未启用'}</Pill><Pill tone={status?.trl_dpo_enabled ? 'green' : 'slate'}>DPO Worker：{status?.trl_dpo_enabled ? '允许' : '禁用'}</Pill></div>
      {message && <div className="mt-3 rounded-xl bg-rose-50 px-3 py-2 text-sm text-rose-700">{message}</div>}
    </section>
    <section className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between"><h2 className="font-bold text-slate-900">已审核样本</h2><button disabled={!approved.length} onClick={freeze} className="rounded-xl bg-emerald-600 px-3 py-2 text-xs font-semibold text-white disabled:opacity-40">冻结所选样本</button></div>
      <div className="mt-3 text-sm text-slate-500">共 {samples.length} 条，符合冻结条件 {approved.length} 条。含敏感信息的样本不能批准或训练。</div>
    </section>
    <section className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm">
      <h2 className="font-bold text-slate-900">冻结数据集</h2>
      <div className="mt-3 space-y-2">{datasets.map(item => <div key={`${item.dataset_id}-${item.version}`} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-slate-100 p-3"><div><b className="text-sm">{item.name}</b><div className="text-xs text-slate-400">{item.sample_count} 条 · {item.sha256?.slice(0, 12)}…</div></div><button onClick={() => dryRun(item)} className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs text-white">一键校验</button></div>)}{!datasets.length && <Empty>暂无冻结数据集</Empty>}</div>
    </section>
    <section className="rounded-3xl border border-slate-100 bg-white p-5 shadow-sm"><h2 className="font-bold text-slate-900">训练任务</h2><div className="mt-3 space-y-2">{jobs.map(item => <div key={item.job_id} className="flex items-center justify-between rounded-xl bg-slate-50 px-3 py-2 text-sm"><span>{item.backend} · {item.base_model}</span><Pill tone={item.status === 'succeeded' ? 'green' : item.status === 'blocked' || item.status === 'failed' ? 'red' : 'amber'}>{item.status}</Pill></div>)}{!jobs.length && <Empty>暂无训练任务</Empty>}</div></section>
  </div>;
}
