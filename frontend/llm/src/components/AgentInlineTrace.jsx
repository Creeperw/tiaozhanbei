import React, { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Activity, Archive, BrainCircuit, Check, ChevronDown, ChevronRight,
  Circle, Clock3, Database, FileSearch, LibraryBig, Loader2, RotateCcw,
  Search, ShieldCheck, Sparkles, TerminalSquare, Wrench, X,
} from 'lucide-react';

const ROLE_ICONS = {
  planner: BrainCircuit,
  memory: Archive,
  diagnosis: Activity,
  knowledge: LibraryBig,
  expert: Sparkles,
  audit: ShieldCheck,
};

const TOOL_LABELS = {
  web_search: '搜索外部资料',
  search_rag: '检索本地知识库',
  get_kp_with_content: '读取知识点内容',
  search_food_web: '搜索外部参考',
  read_current_page: '读取当前页面',
};

const FORMAL_OUTPUT_LABELS = {
  planner: '任务规划结果',
  memory: '记忆筛选结果',
  diagnosis: '学情分析结果',
  knowledge: '知识整理结果',
  expert: '专家候选内容',
  audit: '审核结果',
};

const terminalStatuses = new Set(['done', 'success', 'completed', 'archived']);

function shortText(value, limit = 170) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

function uniqueModelCalls(calls = []) {
  const seen = new Set();
  return calls.filter((call) => {
    const key = call.callId || call.id;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function roleViewState(role) {
  if (role.workingOutputStreaming || role.formalOutputStreaming || role.publicOutputStreaming) return 'streaming';
  if (['running', 'retrying'].includes(role.status)) return 'running';
  if (role.status === 'rollingBack') return 'repairing';
  if (role.status === 'waiting_human_review') return 'review';
  if (role.status === 'interrupted') return 'waiting';
  if (['error', 'failed'].includes(role.status)) return 'error';
  return 'done';
}

function StageGlyph({ state }) {
  if (['running', 'streaming'].includes(state)) return <Loader2 size={13} />;
  if (state === 'repairing') return <RotateCcw size={13} />;
  if (state === 'review') return <ShieldCheck size={13} />;
  if (state === 'waiting') return <Circle size={8} fill="currentColor" />;
  if (state === 'error') return <X size={13} />;
  return <Check size={13} />;
}

function stageVerb(state) {
  if (state === 'streaming') return '正在输出';
  if (state === 'running') return '正在执行';
  if (state === 'repairing') return '正在返修';
  if (state === 'review') return '等待复核';
  if (state === 'waiting') return '等待补充';
  if (state === 'error') return '执行失败';
  return '已完成';
}

function waitingMessage(role, elapsedSeconds = 0) {
  const action = {
    planner: '正在分析当前请求并选择后续执行路径',
    memory: '正在筛选与本次任务有关的历史和用户信息',
    diagnosis: '正在分析学习状态与计划衔接关系',
    knowledge: '正在检索、筛选并整理可核验资料',
    expert: '正在根据上游结果生成候选内容',
    audit: '正在核对事实依据、任务要求与发布条件',
  }[role.key] || '正在处理当前阶段任务';
  if (elapsedSeconds >= 25) return `${action}。本阶段耗时较长，但任务仍在继续…`;
  if (elapsedSeconds >= 10) return `${action}。模型仍在生成阶段结果…`;
  return `${action}，结果生成后会立即逐段显示…`;
}

function OperationLine({ icon, title, detail, status = 'done', meta }) {
  const Icon = icon || Wrench;
  return (
    <div className="agent-stream-operation" data-status={status}>
      <span className="agent-stream-operation__glyph"><Icon size={13} aria-hidden="true" /></span>
      <span className="agent-stream-operation__copy">
        <strong>{title}</strong>
        {detail ? <span title={detail}>{detail}</span> : null}
      </span>
      <span className="agent-stream-operation__state">
        {status === 'running' ? <Loader2 size={11} /> : status === 'error' ? <X size={11} /> : <Check size={11} />}
        {meta || (status === 'running' ? '运行中' : status === 'error' ? '失败' : '完成')}
      </span>
    </div>
  );
}

function WorkingDisclosure({ content, streaming, waiting, formalReady }) {
  const [manualOpen, setManualOpen] = useState(null);
  const bodyRef = useRef(null);
  const active = streaming || Boolean(waiting);
  // Keep the genuine public stream visible while it is arriving. Once a
  // formal artifact exists, collapse automatically unless the learner has
  // explicitly chosen a state for this disclosure.
  const open = active ? true : (manualOpen ?? !formalReady);

  // While streaming, follow the tail of the output so the newest text stays
  // in view without the block growing unboundedly (fixed height + scroll).
  useEffect(() => {
    if (active && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [content, waiting, active]);

  if (!content && !streaming && !waiting) return null;
  const preview = shortText(content || waiting, 92);
  return (
    <section className={`agent-working ${active ? 'is-streaming' : ''} ${open ? 'is-open' : ''}`}>
      <button
        type="button"
        className="agent-working__toggle"
        aria-expanded={open}
        onClick={() => setManualOpen(!open)}
      >
        <span className="agent-working__icon"><BrainCircuit size={13} aria-hidden="true" /></span>
        <span className="agent-working__heading">
          <strong>{active ? '正在输出' : '工作过程'}</strong>
          {!open && preview ? <small>{preview}</small> : <small>公开工作过程</small>}
        </span>
        {active ? <span className="agent-working__live"><i /><i /><i /></span> : null}
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open ? (
        <div ref={bodyRef} className="agent-working__body" aria-live={active ? 'polite' : undefined}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || waiting}</ReactMarkdown>
          {active ? <i className="agent-working__caret" aria-hidden="true" /> : null}
        </div>
      ) : null}
    </section>
  );
}

function ReasoningDisclosure({ content, streaming }) {
  const [manualOpen, setManualOpen] = useState(null);
  const bodyRef = useRef(null);
  const active = Boolean(streaming);
  // While the model is thinking, keep the block open so the learner watches
  // the stream arrive. Once committed, collapse to a compact summary line.
  const open = active ? true : (manualOpen ?? false);

  // Follow the tail of the reasoning stream inside the fixed-height scroll
  // container, mirroring Copilot's chain-of-thought panel.
  useEffect(() => {
    if (active && bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [content, active]);

  if (!content && !active) return null;
  const preview = shortText(content, 92);
  return (
    <section className={`agent-inline-thought ${active ? 'is-streaming' : ''}`}>
      <button
        type="button"
        className="agent-inline-thought__label"
        aria-expanded={open}
        onClick={() => setManualOpen(!open)}
      >
        <BrainCircuit size={12} aria-hidden="true" />
        {active ? '正在思考' : '思考过程'}
        {!open && preview ? <small title={content}>{preview}</small> : null}
        {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
      </button>
      {open ? (
        <div ref={bodyRef} className="agent-inline-thought__body" aria-live={active ? 'polite' : undefined}>
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{content || ''}</ReactMarkdown>
          {active ? <i className="agent-inline-thought__caret" aria-hidden="true" /> : null}
        </div>
      ) : null}
    </section>
  );
}

function FormalOutputNode({ roleKey, content, streaming, ready }) {
  const [manualOpen, setManualOpen] = useState(null);
  const open = streaming ? true : (manualOpen ?? !ready);

  if (!content && !streaming && !ready) return null;
  const title = FORMAL_OUTPUT_LABELS[roleKey] || '阶段正式输出';
  const preview = shortText(content, 105);
  return (
    <section className={`agent-result-node ${open ? 'is-open' : ''} ${streaming ? 'is-streaming' : ''}`}>
      <button
        type="button"
        className="agent-result-node__toggle"
        aria-expanded={open}
        onClick={() => setManualOpen(!open)}
      >
        <span className="agent-result-node__icon">
          {streaming ? <Loader2 size={14} /> : <Check size={14} />}
        </span>
        <span className="agent-result-node__heading">
          <strong>{streaming ? `正在生成：${title}` : `已生成：${title}`}</strong>
          {!open && preview ? <small>{preview}</small> : null}
        </span>
        <span className="agent-result-node__badge">{streaming ? '流式输出' : '正式产出'}</span>
        {open ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
      </button>
      {open ? (
        <div className="agent-result-node__body" aria-live={streaming ? 'polite' : undefined}>
          {content ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown> : <p>正在整理经校验的阶段结果…</p>}
          {streaming ? <i className="agent-working__caret" aria-hidden="true" /> : null}
        </div>
      ) : null}
    </section>
  );
}

function AgentStage({ role, elapsedSeconds }) {
  const Icon = ROLE_ICONS[role.key] || Sparkles;
  const state = roleViewState(role);
  const modelCalls = useMemo(() => uniqueModelCalls(role.modelCalls), [role.modelCalls]);
  const fallback = role.activities?.at(-1)?.text || role.summary || role.description;
  const isLive = ['running', 'streaming', 'repairing'].includes(state);
  const legacyStreaming = Boolean(
    role.publicOutputStreaming && !role.workingOutput && !role.formalOutput
  );
  const workingOutput = role.workingOutput
    || (legacyStreaming ? role.publicOutput : '');
  const workingOutputStreaming = Boolean(role.workingOutputStreaming || legacyStreaming);
  const formalOutput = role.formalOutput
    || (!workingOutput && !legacyStreaming ? role.publicOutput : '');
  const formalOutputStreaming = Boolean(role.formalOutputStreaming);
  const formalReady = Boolean(role.formalOutputReady || (formalOutput && !formalOutputStreaming));
  const waiting = !workingOutput && !formalOutput && isLive
    ? waitingMessage(role, elapsedSeconds)
    : '';

  return (
    <li className={`agent-stream-stage agent-stream-stage--${role.key}`} data-status={state}>
      <span className="agent-stream-stage__rail" aria-hidden="true" />
      <div className="agent-stream-stage__headline">
        <span className="agent-stream-stage__status"><StageGlyph state={state} /></span>
        <span className="agent-stream-stage__agent"><Icon size={13} aria-hidden="true" />{role.label}</span>
        <strong>{stageVerb(state)}：{role.description.replace(/[。.]$/, '')}</strong>
        {isLive ? <span className="agent-stream-stage__pulse" aria-label="仍在运行"><i /><i /><i /></span> : null}
      </div>

      <div className="agent-stream-stage__details">
        {modelCalls.slice(-2).map((call) => {
          const callStatus = call.kind === 'output' ? 'done' : isLive ? 'running' : 'done';
          return (
            <OperationLine
              key={call.callId || call.id}
              icon={TerminalSquare}
              title={callStatus === 'running' ? '正在调用语言模型' : '已完成模型调用'}
              detail={`${role.label}正在根据本步骤上下文生成阶段结果`}
              status={callStatus}
            />
          );
        })}

        {(role.tools || []).map((tool) => (
          <OperationLine
            key={tool.id}
            icon={tool.name === 'web_search' ? Search : Wrench}
            title={TOOL_LABELS[tool.name] || '调用学习工具'}
            detail={shortText(tool.args?.query || tool.resultSnippet)}
            status={tool.status}
          />
        ))}

        {(role.retrievals || []).map((retrieval, index) => {
          const evidenceCount = retrieval.evidence_items?.length || 0;
          const query = retrieval.kp_query || retrieval.question_query;
          return (
            <OperationLine
              key={`${query}-${retrieval.retrieval_round || index}`}
              icon={Database}
              title={`知识库检索${retrieval.retrieval_round ? ` · 第 ${retrieval.retrieval_round} 轮` : ''}`}
              detail={shortText(query)}
              meta={`${evidenceCount} 条结果`}
            />
          );
        })}

        {(role.auditEvents || []).map((event, index) => (
          <OperationLine
            key={`${event.kind}-${event.ts || index}`}
            icon={event.kind === 'reaudit_started' ? RotateCcw : ShieldCheck}
            title={event.text || '审核处理'}
            detail={event.locations?.length ? `定位：${event.locations.join('、')}` : ''}
            status={['failed', 'error'].includes(event.status) ? 'error'
              : ['pass', 'success', 'completed', 'done', 'planned'].includes(event.status) ? 'done'
              : 'running'}
          />
        ))}

        <ReasoningDisclosure
          content={role.reasoning || ''}
          streaming={Boolean(role.reasoningStreaming)}
        />

        <WorkingDisclosure
          content={workingOutput}
          streaming={workingOutputStreaming}
          waiting={waiting}
          formalReady={formalReady}
        />

        <FormalOutputNode
          roleKey={role.key}
          content={formalOutput || (terminalStatuses.has(role.status) && !workingOutput ? fallback : '')}
          streaming={formalOutputStreaming}
          ready={formalReady}
        />
      </div>
    </li>
  );
}

/** Linear, Copilot-style execution stream rendered inside the assistant message. */
export default function AgentInlineTrace({ roles = [], status = 'idle', elapsedSeconds, evidenceCount = 0, isGenerating = false }) {
  const [expanded, setExpanded] = useState(true);
  const [liveSeconds, setLiveSeconds] = useState(() => Number(elapsedSeconds || 0));
  const hasStreamingOutput = roles.some((role) => (
    role.workingOutputStreaming || role.formalOutputStreaming || role.publicOutputStreaming
  ));
  const isRunning = isGenerating || status === 'running' || hasStreamingOutput;
  const isExpanded = isRunning || expanded;

  useEffect(() => {
    if (!isRunning) return undefined;
    const timer = window.setInterval(() => setLiveSeconds((value) => value + 1), 1000);
    return () => window.clearInterval(timer);
  }, [isRunning]);

  const completedCount = roles.filter((role) => roleViewState(role) === 'done').length;
  const currentRole = roles.find((role) => ['streaming', 'running', 'repairing'].includes(roleViewState(role)));
  const shownSeconds = Math.max(Number(elapsedSeconds || 0), liveSeconds);
  const label = isRunning
    ? `正在处理：${currentRole?.label || '多智能体协作'}`
    : status === 'review'
      ? '已转入人工复核'
      : status === 'failed'
        ? '执行已安全停止'
        : '多智能体协作完成';

  return (
    <section className="agent-inline" data-status={isRunning ? 'running' : status} aria-label="多智能体执行过程">
      <button type="button" className="agent-inline__toggle" onClick={() => setExpanded((value) => !value)} aria-expanded={isExpanded}>
        <span className="agent-inline__mark">{isRunning ? <Loader2 size={14} /> : <Check size={14} />}</span>
        <span className="agent-inline__title">
          <strong>{label}</strong>
          <small>{completedCount}/{roles.length} 个阶段完成</small>
        </span>
        <span className="agent-inline__stats">
          {evidenceCount > 0 ? <span><FileSearch size={11} />{evidenceCount} 条资料</span> : null}
          <span><Clock3 size={11} />{shownSeconds} 秒</span>
          {isExpanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </span>
      </button>
      {isExpanded ? (
        <div className="agent-inline__body">
          <ol className="agent-stream" aria-label="智能体实时活动">
            {roles.map((role, index) => (
              <AgentStage
                key={role.nodes?.[0]?.id || `${role.key}-${index}`}
                role={role}
                elapsedSeconds={shownSeconds}
              />
            ))}
          </ol>
          <footer><Circle size={5} fill="currentColor" />实时展示模型思考过程、公开工作过程、工具状态和经校验的阶段产出；系统提示词与编译器内部数据始终隐藏。</footer>
        </div>
      ) : null}
    </section>
  );
}
