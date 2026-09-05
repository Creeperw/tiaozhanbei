import React, { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Activity, Archive, Check, CheckCircle2, ChevronDown, ChevronRight, Circle,
  ClipboardList, Clock3, FileSearch, GitBranch, LibraryBig, Loader2, RotateCcw,
  Search, ShieldCheck, Sparkles, Waypoints, X, XCircle,
} from 'lucide-react';

import { agentStatusLabel, buildAgentPresentation, STATUS_PRIORITY } from '../agentPresentationModel';
import { useLangGraphStore } from '../stores/useLangGraphStore';

const ROLE_ICONS = {
  planner: ClipboardList, memory: Archive, diagnosis: Activity,
  knowledge: LibraryBig, expert: Sparkles, audit: ShieldCheck,
};

const STATUS_STYLES = {
  running: 'agent-task__status--running', done: 'agent-task__status--done',
  success: 'agent-task__status--done', completed: 'agent-task__status--done',
  error: 'agent-task__status--error', failed: 'agent-task__status--error',
  retrying: 'agent-task__status--review', rollingBack: 'agent-task__status--review',
  waiting_human_review: 'agent-task__status--waiting', interrupted: 'agent-task__status--waiting',
  archived: 'agent-task__status--muted', skipped: 'agent-task__status--muted',
  pending: 'agent-task__status--muted',
};

const TOOL_LABELS = {
  web_search: '检索公开资料', search_rag: '检索本地知识库',
  get_kp_with_content: '读取知识点内容', search_food_web: '检索外部参考',
  read_current_page: '读取当前页面',
};

const TABS = [
  { id: 'process', label: '协作过程', icon: Waypoints },
  { id: 'evidence', label: '证据来源', icon: FileSearch },
  { id: 'audit', label: '审核返修', icon: ShieldCheck },
];

function StatusIcon({ status }) {
  if (status === 'running' || status === 'retrying') return <Loader2 size={14} className="agent-task__spinner" aria-hidden="true" />;
  if (status === 'error' || status === 'failed') return <XCircle size={14} aria-hidden="true" />;
  if (['done', 'success', 'completed'].includes(status)) return <CheckCircle2 size={14} aria-hidden="true" />;
  if (status === 'rollingBack') return <RotateCcw size={14} aria-hidden="true" />;
  return <Circle size={11} aria-hidden="true" />;
}

function durationLabel(role) {
  if (!Number.isFinite(role.startedAt)) return '';
  const duration = (Number.isFinite(role.endedAt) ? role.endedAt : Date.now()) - role.startedAt;
  if (duration < 0 || duration > 86_400_000) return '';
  return duration < 1000 ? '< 1 秒' : `${(duration / 1000).toFixed(1)} 秒`;
}

function totalDurationLabel(roles) {
  const starts = roles.map((role) => role.startedAt).filter(Number.isFinite);
  const ends = roles.map((role) => role.endedAt || Date.now()).filter(Number.isFinite);
  if (!starts.length || !ends.length) return '尚未开始';
  const duration = Math.max(...ends) - Math.min(...starts);
  if (duration < 0 || duration > 86_400_000) return '';
  return duration < 1000 ? '< 1 秒' : `${Math.round(duration / 1000)} 秒`;
}

function safeQuery(value) {
  const text = String(value || '').replace(/\s+/g, ' ').trim();
  return text.length > 180 ? `${text.slice(0, 180)}…` : text;
}

function contextRequest(role) {
  const summary = role.nodes.map((node) => node.inputSummary).find(Boolean);
  return safeQuery(summary?.original_user_request || summary?.user_request || '');
}

function PublicDetails({ role }) {
  const request = contextRequest(role);
  const actions = [...role.activities, ...role.details.map((text, index) => ({ kind: 'log', text, ts: index }))]
    .filter((item, index, all) => item.text && all.findIndex((candidate) => candidate.text === item.text) === index);

  return (
    <div className="agent-task__public-details">
      <section>
        <h4>收到的信息</h4>
        {role.contextSignals.length > 0 ? (
          <div className="agent-context-signals">
            {role.contextSignals.map((signal) => <span key={signal}><Check size={11} />{signal}</span>)}
          </div>
        ) : <p className="agent-detail-empty">仅接收了完成本步骤所需的信息。</p>}
        {request && <p className="agent-task__request" title={request}>当前任务：{request}</p>}
      </section>
      <section>
        <h4>执行记录</h4>
        <ol className="agent-task__event-list">
          {actions.slice(-6).map((activity, index) => <li key={`${activity.kind || 'activity'}-${activity.ts || index}-${index}`}>{activity.text}</li>)}
          {role.tools.map((tool) => (
            <li key={tool.id}>
              {TOOL_LABELS[tool.name] || '调用学习工具'}
              {safeQuery(tool.args?.query) ? `：${safeQuery(tool.args.query)}` : ''}
              {tool.status === 'running' ? '（进行中）' : '（已完成）'}
            </li>
          ))}
          {actions.length === 0 && role.tools.length === 0 && <li>按既定职责处理本步骤。</li>}
        </ol>
      </section>
      <section className="agent-task__artifact">
        <h4>智能体完整业务输出 {role.publicOutputStreaming && <span className="agent-task__live-label">LIVE</span>}</h4>
        {role.publicOutput ? (
          <div className={`agent-task__stream-output ${role.publicOutputStreaming ? 'is-streaming' : ''}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{role.publicOutput}</ReactMarkdown>
            {role.publicOutputStreaming && <i aria-hidden="true" />}
          </div>
        ) : <p>{role.status === 'running'
          ? '正在生成本步骤产出，完成后将交给下游智能体。'
          : role.status === 'waiting_human_review'
            ? '产出已被安全拦截，等待人工复核。'
            : ['error', 'failed'].includes(role.status)
              ? '本步骤未形成可发布产出。'
              : '产出已完成，并已进入后续处理或审核。'}</p>}
      </section>
    </div>
  );
}

function AgentTask({ role, isLast }) {
  const [open, setOpen] = useState(role.status === 'running');

  useEffect(() => {
    if (role.status === 'running' || role.status === 'rollingBack') {
      setOpen(true);
    }
  }, [role.status]);
  const Icon = ROLE_ICONS[role.key] || Sparkles;
  const duration = durationLabel(role);
  return (
    <article className={`agent-task agent-task--${role.key}`} data-status={role.status}>
      {!isLast && <div className="agent-task__rail" aria-hidden="true"><i /></div>}
      <div className="agent-task__icon"><Icon size={17} strokeWidth={1.9} aria-hidden="true" /></div>
      <div className="agent-task__body">
        <div className="agent-task__heading">
          <div><h3>{role.label}</h3><p>{role.summary}</p></div>
          <span className={`agent-task__status ${STATUS_STYLES[role.status] || STATUS_STYLES.pending}`}><StatusIcon status={role.status} />{role.statusLabel}</span>
        </div>
        <div className="agent-task__meta">
          {role.dependencies.length > 0 && <span><GitBranch size={12} />接续 {role.dependencies.length} 个上游环节</span>}
          {duration && <span><Clock3 size={12} />{role.status === 'pending' ? `已等待 ${duration}` : duration}</span>}
        </div>
        <button type="button" className="agent-task__details-toggle" aria-expanded={open} aria-label={`${open ? '收起' : '展开'}${role.label}公开记录`} onClick={() => setOpen((value) => !value)}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}{open ? '收起公开记录' : '查看公开记录'}
        </button>
        {open && <PublicDetails role={role} />}
      </div>
    </article>
  );
}

function collectEvidence(roles, refs) {
  const collected = [];
  refs.forEach((item) => collected.push({ ...item, origin: item.source_url ? '外部资料' : '本地知识库' }));
  roles.forEach((role) => role.retrievals.forEach((retrieval) => (
    (retrieval.evidence_items || []).forEach((item) => collected.push({
      ...item,
      origin: item.source_url || item.url ? '外部资料' : '本地知识库',
      retrievalQuery: retrieval.kp_query || retrieval.question_query,
    }))
  )));
  const seen = new Set();
  return collected.filter((item) => {
    const key = String(item.evidence_id || item.id || item.source_url || item.url || item.title || item.name || '');
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function evidenceTitle(item, index) {
  if (item.title || item.name || item.source_title) return item.title || item.name || item.source_title;
  if (item.source_url || item.url) {
    try {
      return new URL(item.source_url || item.url).hostname.replace(/^www\./, '');
    } catch {
      // Fall through to a semantic source label for malformed legacy URLs.
    }
  }
  if (item.resource_type === 'textbook' || item.origin === '本地知识库') return '教材知识库证据';
  if (item.resource_type === 'question') return '题目资料';
  return `外部参考资料 ${index + 1}`;
}

function EvidencePanel({ evidence, roles, onInspectRefs, refs }) {
  const retrievalCount = roles.reduce((sum, role) => sum + role.retrievals.length, 0);
  if (evidence.length === 0 && retrievalCount === 0) {
    return <div className="agent-desk__empty"><FileSearch size={24} /><strong>本次没有调用资料检索</strong><span>直接回答或流程类任务无需额外证据。</span></div>;
  }
  return (
    <div className="agent-evidence">
      <div className="agent-evidence__summary"><strong>{evidence.length}</strong><span>条可追溯资料</span><i /><strong>{retrievalCount}</strong><span>轮检索</span></div>
      {evidence.slice(0, 12).map((item, index) => (
        <article key={item.evidence_id || item.id || index} className="agent-evidence__item">
          <span>{item.origin || '参考资料'}</span>
          <h3>{evidenceTitle(item, index)}</h3>
          <p>{item.knowledge_point || item.topic || item.retrievalQuery || item.source_name || '已用于本次内容生成与核验'}</p>
          <div>
            {item.difficulty_label && <em>难度：{item.difficulty_label}</em>}
            {Number.isFinite(Number(item.match_score)) && <em>匹配度 {Math.round(Number(item.match_score) * (Number(item.match_score) <= 1 ? 100 : 1))}%</em>}
          </div>
        </article>
      ))}
      {refs.length > 0 && <button type="button" onClick={() => onInspectRefs?.(refs, refs[0]?.query || '')} className="agent-desk__sources"><Search size={15} />打开完整来源列表</button>}
    </div>
  );
}

function AuditPanel({ roles, isRollingBack }) {
  const auditRoles = roles.filter((role) => role.key === 'audit');
  const events = auditRoles.flatMap((role) => role.auditEvents || []);
  if (auditRoles.length === 0) return <div className="agent-desk__empty"><ShieldCheck size={24} /><strong>本次流程无需独立审核</strong><span>问候、读取已有内容等轻量任务可直接返回。</span></div>;
  const passed = auditRoles.some((role) => ['done', 'success', 'completed'].includes(role.status)) && !isRollingBack;
  const auditStatus = auditRoles.reduce((selected, role) => (
    (STATUS_PRIORITY[role.status] ?? 0) > (STATUS_PRIORITY[selected] ?? 0) ? role.status : selected
  ), auditRoles[0]?.status || 'pending');
  return (
    <div className="agent-audit">
      <div className="agent-audit__verdict" data-status={passed ? 'pass' : auditStatus}>
        <ShieldCheck size={22} />
        <div><strong>{passed ? '审核已通过' : isRollingBack ? '正在局部返修' : agentStatusLabel(auditStatus)}</strong><span>{passed ? '最终内容已通过发布门禁。' : '未通过审核的内容不会作为正式回答发布。'}</span></div>
      </div>
      <ol className="agent-audit__timeline">
        {events.map((event, index) => (
          <li key={`${event.kind}-${event.ts || index}`} data-status={event.status || event.kind}>
            <i /><div><strong>{event.text}</strong>
              {event.locations.length > 0 && <span>定位：{event.locations.join('、')}</span>}
              {event.targetStepIds.length > 0 && <span>仅返修 {event.targetStepIds.length} 个受影响环节</span>}
              {event.preservedStepIds.length > 0 && <span>保留 {event.preservedStepIds.length} 个无关环节</span>}
            </div>
          </li>
        ))}
        {events.length === 0 && <li><i /><div><strong>完成事实、质量与发布条件检查</strong><span>{auditRoles[0]?.summary}</span></div></li>}
      </ol>
    </div>
  );
}

/** Learner-facing collaboration desk. It exposes verifiable actions, not hidden chain-of-thought. */
export function AgentTimeline({ isOpen = true, onClose, onInspectRefs, nodes: externalNodes, refs: externalRefs, live = false, title = '执行进度' }) {
  const storeNodes = useLangGraphStore((state) => state.nodes);
  const storeRefs = useLangGraphStore((state) => state.references);
  const isRollingBack = useLangGraphStore((state) => state.isRollingBack);
  const [activeTab, setActiveTab] = useState('process');
  const nodes = live && storeNodes.length > 0 ? storeNodes : (externalNodes || storeNodes);
  const refs = live && storeRefs.length > 0 ? storeRefs : (externalRefs || storeRefs);
  const allRoles = useMemo(() => buildAgentPresentation(nodes), [nodes]);
  const roles = useMemo(() => allRoles
    .filter((role) => role.nodes.length > 0 && !['skipped', 'idle'].includes(role.status))
    .sort((left, right) => (left.startedAt || Number.MAX_SAFE_INTEGER) - (right.startedAt || Number.MAX_SAFE_INTEGER)), [allRoles]);
  const evidence = useMemo(() => collectEvidence(roles, refs), [roles, refs]);
  const statusSignature = roles.map((role) => `${role.key}:${role.status}`).join('|');
  const bottomRef = useRef(null);

  useEffect(() => {
    if (activeTab === 'process' && typeof bottomRef.current?.scrollIntoView === 'function') bottomRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [statusSignature, activeTab]);

  const activeRoles = roles.filter((role) => ['running', 'retrying', 'rollingBack'].includes(role.status));
  const completedCount = roles.filter((role) => ['done', 'success', 'completed'].includes(role.status)).length;
  const auditRoles = roles.filter((role) => role.key === 'audit');
  const auditRole = auditRoles.length
    ? {
        ...auditRoles[0],
        status: auditRoles.reduce((selected, role) => (
          (STATUS_PRIORITY[role.status] ?? 0) > (STATUS_PRIORITY[selected] ?? 0) ? role.status : selected
        ), auditRoles[0].status),
      }
    : null;
  const headerText = activeRoles.length > 1 ? `${activeRoles.length} 个环节正在并行处理`
    : activeRoles.length === 1 ? `${activeRoles[0].label}正在处理`
      : roles.length ? `已完成 ${completedCount}/${roles.length} 个参与环节` : '发送消息后显示实时协作过程';

  return (
    <aside aria-label="执行进度" aria-hidden={isOpen ? undefined : true} inert={isOpen ? undefined : true} className={`agent-desk ${isOpen ? 'agent-desk--open' : 'agent-desk--closed'}`}>
      <header className="agent-desk__header">
        <div><span className="agent-desk__eyebrow">MULTI-AGENT LIVE TRACE</span><h2>{title}</h2><p>{headerText}</p></div>
        <button type="button" className="agent-desk__close" onClick={onClose} aria-label="关闭执行进度"><X size={17} /></button>
      </header>
      <div className="agent-desk__pulsebar" aria-label="本次协作摘要">
        <span><strong>{roles.length}</strong> 个参与者</span><span><strong>{evidence.length}</strong> 条资料</span>
        <span><strong>{totalDurationLabel(roles)}</strong> 用时</span><span data-state={auditRole ? auditRole.status : 'none'}><strong>{auditRole ? auditRole.statusLabel : '无需审核'}</strong></span>
      </div>
      {isRollingBack && <div className="agent-desk__review" role="status"><RotateCcw size={16} />审核已定位问题，系统只返修受影响环节，完成后会再次审核。</div>}
      <nav className="agent-desk__tabs" aria-label="协作详情视图">
        {TABS.map(({ id, label, icon }) => <button key={id} type="button" className={activeTab === id ? 'is-active' : ''} aria-current={activeTab === id ? 'page' : undefined} onClick={() => setActiveTab(id)}>{React.createElement(icon, { size: 14 })}{label}{id === 'evidence' && evidence.length > 0 && <small>{evidence.length}</small>}{id === 'audit' && isRollingBack && <i />}</button>)}
      </nav>
      <div className="agent-desk__scroll" data-scroll-region="agent-details">
        {activeTab === 'process' && <div className="agent-desk__roles">
          {roles.map((role, index) => <AgentTask key={role.nodes?.[0]?.id || `${role.key}-${index}`} role={role} isLast={index === roles.length - 1} />)}
          {roles.length === 0 && <div className="agent-desk__empty"><Waypoints size={24} /><strong>等待任务开始</strong><span>本次实际参与的智能体会按执行顺序出现在这里。</span></div>}
          <div ref={bottomRef} />
        </div>}
        {activeTab === 'evidence' && <EvidencePanel evidence={evidence} roles={roles} onInspectRefs={onInspectRefs} refs={refs} />}
        {activeTab === 'audit' && <AuditPanel roles={roles} isRollingBack={isRollingBack} />}
      </div>
      <footer className="agent-desk__privacy"><Circle size={6} fill="currentColor" />展示公开执行记录；系统提示词、隐私数据与内部推理不会显示。</footer>
    </aside>
  );
}

export default AgentTimeline;
