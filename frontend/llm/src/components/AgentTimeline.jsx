import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Archive,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  ClipboardList,
  Clock3,
  LibraryBig,
  Loader2,
  RotateCcw,
  Search,
  ShieldCheck,
  Sparkles,
  Wrench,
  X,
  XCircle,
} from 'lucide-react';

import { buildAgentPresentation } from '../agentPresentationModel';
import { useLangGraphStore } from '../stores/useLangGraphStore';

const ROLE_ICONS = {
  planner: ClipboardList,
  memory: Archive,
  diagnosis: Activity,
  knowledge: LibraryBig,
  expert: Sparkles,
  audit: ShieldCheck,
};

const STATUS_STYLES = {
  running: 'agent-task__status--running',
  done: 'agent-task__status--done',
  success: 'agent-task__status--done',
  completed: 'agent-task__status--done',
  error: 'agent-task__status--error',
  failed: 'agent-task__status--error',
  retrying: 'agent-task__status--review',
  rollingBack: 'agent-task__status--review',
  waiting_human_review: 'agent-task__status--waiting',
  interrupted: 'agent-task__status--waiting',
  archived: 'agent-task__status--muted',
  skipped: 'agent-task__status--muted',
};

function StatusIcon({ status }) {
  if (status === 'running' || status === 'retrying') {
    return <Loader2 size={14} className="agent-task__spinner" aria-hidden="true" />;
  }
  if (status === 'error' || status === 'failed') return <XCircle size={14} aria-hidden="true" />;
  if (status === 'done' || status === 'success' || status === 'completed') {
    return <CheckCircle2 size={14} aria-hidden="true" />;
  }
  if (status === 'rollingBack') return <RotateCcw size={14} aria-hidden="true" />;
  return <Circle size={12} aria-hidden="true" />;
}

function durationLabel(role) {
  if (!Number.isFinite(role.startedAt)) return '';
  const end = Number.isFinite(role.endedAt) ? role.endedAt : Date.now();
  const duration = end - role.startedAt;
  if (duration < 0 || duration > 86_400_000) return '';
  return `${(duration / 1000).toFixed(1)} 秒`;
}

function formatModelPayload(value) {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

const MODEL_CALL_LABELS = { input: '模型输入', output: '模型输出', transport: '模型传输' };
function hasValue(value) {
  if (value == null) return false;
  if (typeof value === 'string') return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === 'object') return Object.keys(value).length > 0;
  return true;
}

function extractRequestMessages(requestPayload) {
  if (Array.isArray(requestPayload)) return requestPayload;
  if (requestPayload == null || typeof requestPayload !== 'object') return null;
  const body = requestPayload.body;
  if (body && Array.isArray(body.messages)) return body.messages;
  if (Array.isArray(requestPayload.messages)) return requestPayload.messages;
  return null;
}

/** Real natural-language request/reply captured at the model boundary. */
function TransportDetails({ call }) {
  const messages = extractRequestMessages(call.requestPayload);
  const structured = (call.structuredInput != null || call.structuredOutput != null);
  return (
    <>
      {messages && messages.length > 0 && (
        <div className="agent-task__transport">
          <div className="agent-task__technical-title">发送给模型的自然语言请求</div>
          {messages.map((message, index) => (
            <div className="agent-task__transport-message" key={`${message.role || 'msg'}-${index}`}>
              <code>{message.role || 'message'}</code>
              <pre>{formatModelPayload(message.content)}</pre>
            </div>
          ))}
        </div>
      )}
      {hasValue(call.responseText) && (
        <div className="agent-task__transport">
          <div className="agent-task__technical-title">模型原始输出</div>
          <pre>{String(call.responseText)}</pre>
        </div>
      )}
      {structured && (
        <details className="agent-task__transport-structured">
          <summary>查看结构化数据</summary>
          {call.structuredInput != null && (
            <pre>{formatModelPayload(call.structuredInput)}</pre>
          )}
          {call.structuredOutput != null && (
            <pre>{formatModelPayload(call.structuredOutput)}</pre>
          )}
        </details>
      )}
    </>
  );
}


function TechnicalDetails({ role }) {
  const internalAgents = [...new Set(role.nodes.map((node) => node.agent || node.name).filter(Boolean))];
  return (
    <div className="agent-task__technical">
      {internalAgents.length > 0 && (
        <div className="agent-task__internal-nodes">
          <span>内部执行节点</span>
          <div>{internalAgents.map((agent) => <code key={agent}>{agent}</code>)}</div>
        </div>
      )}
      {role.retrievals?.length > 0 && (
        <div className="agent-task__tools">
          <div className="agent-task__technical-title"><Search size={13} aria-hidden="true" />检索语句</div>
          {role.retrievals.map((retrieval, index) => (
            <details key={`${retrieval.ts || index}-${index}`} open={index === role.retrievals.length - 1}>
              <summary>
                <code>第 {index + 1} 轮检索</code>
                <span>{retrieval.ts ? new Date(retrieval.ts).toLocaleTimeString('zh-CN', { hour12: false }) : '已返回'}</span>
              </summary>
              {retrieval.kp_query && (
                <div className="agent-task__retrieval-line">
                  <span>知识点检索语句</span>
                  <pre>{retrieval.kp_query}</pre>
                </div>
              )}
              {retrieval.question_query && (
                <div className="agent-task__retrieval-line">
                  <span>题目检索语句</span>
                  <pre>{retrieval.question_query}</pre>
                </div>
              )}
            </details>
          ))}
        </div>
      )}
      {role.details.length > 0 && (
        <ol className="agent-task__event-list" aria-label={`${role.label}执行记录`}>
          {role.details.map((detail) => <li key={detail}>{detail}</li>)}
        </ol>
      )}
      {role.tools.length > 0 && (
        <div className="agent-task__tools">
          <div className="agent-task__technical-title"><Wrench size={13} aria-hidden="true" />工具调用详情</div>
          {role.tools.map((tool) => (
            <details key={tool.id}>
              <summary>
                <code>{tool.name}</code>
                <span>{tool.status === 'running' ? '调用中' : '已返回'}</span>
              </summary>
              <pre>{JSON.stringify(tool.args || {}, null, 2)}</pre>
              {tool.resultSnippet && <p>{tool.resultSnippet}</p>}
            </details>
          ))}
        </div>
      )}
      {role.modelCalls.length > 0 && (
        <div className="agent-task__tools">
          <div className="agent-task__technical-title"><Sparkles size={13} aria-hidden="true" />模型调用详情</div>
          {role.modelCalls.map((call) => (
            <details key={call.id}>
              <summary>
                <code>{call.agent || 'model'}</code>
                <span>{MODEL_CALL_LABELS[call.kind] || call.kind}</span>
              </summary>
              {call.kind === 'input' && <pre>{formatModelPayload(call.input)}</pre>}
              {call.kind === 'output' && <pre>{formatModelPayload(call.output)}</pre>}
              {call.kind === 'transport' && <TransportDetails call={call} />}
            </details>
          ))}
        </div>
      )}
    </div>
  );
}

function AgentTask({ role }) {
  const [open, setOpen] = useState(false);
  const Icon = ROLE_ICONS[role.key] || Sparkles;
  const duration = durationLabel(role);
  const hasDetails = role.nodes.length > 0 || role.tools.length > 0 || role.modelCalls.length > 0 || (role.retrievals?.length || 0) > 0;

  return (
    <article className="agent-task" data-status={role.status}>
      <div className="agent-task__rail" aria-hidden="true" />
      <div className="agent-task__icon"><Icon size={17} strokeWidth={1.9} aria-hidden="true" /></div>
      <div className="agent-task__body">
        <div className="agent-task__heading">
          <div>
            <h3>{role.label}</h3>
            <p>{role.summary}</p>
          </div>
          <span className={`agent-task__status ${STATUS_STYLES[role.status] || STATUS_STYLES.skipped}`}>
            <StatusIcon status={role.status} />
            {role.statusLabel}
          </span>
        </div>
        <div className="agent-task__meta">
          <span>{role.description}</span>
          {duration && <span><Clock3 size={12} aria-hidden="true" />{duration}</span>}
        </div>
        {hasDetails && (
          <button
            type="button"
            className="agent-task__details-toggle"
            aria-expanded={open}
            aria-label={`${open ? '收起' : '展开'}${role.label}技术详情`}
            onClick={() => setOpen((value) => !value)}
          >
            {open ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />}
            技术详情
          </button>
        )}
        {open && <TechnicalDetails role={role} />}
      </div>
    </article>
  );
}

/** User-facing six-agent execution desk backed by authoritative LangGraph events. */
export function AgentTimeline({
  isOpen = true,
  onClose,
  onInspectRefs,
  nodes: externalNodes,
  refs: externalRefs,
  title = '执行进度',
}) {
  const storeNodes = useLangGraphStore((state) => state.nodes);
  const storeRefs = useLangGraphStore((state) => state.references);
  const isRollingBack = useLangGraphStore((state) => state.isRollingBack);
  const nodes = externalNodes || storeNodes;
  const refs = externalRefs || storeRefs;
  const roles = useMemo(() => buildAgentPresentation(nodes), [nodes]);
  const statusSignature = roles.map((role) => role.status).join('|');
  const bottomRef = useRef(null);

  useEffect(() => {
    if (typeof bottomRef.current?.scrollIntoView === 'function') {
      bottomRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  }, [statusSignature]);

  const activeRole = roles.find((role) => role.status === 'running' || role.status === 'retrying');
  const completedCount = roles.filter((role) => ['done', 'success', 'completed'].includes(role.status)).length;

  return (
    <aside
      aria-label="执行进度"
      aria-hidden={isOpen ? undefined : true}
      inert={isOpen ? undefined : true}
      className={`agent-desk ${isOpen ? 'agent-desk--open' : 'agent-desk--closed'}`}
    >
      <header className="agent-desk__header">
        <div>
          <span className="agent-desk__eyebrow">多智能体协作</span>
          <h2>{title}</h2>
          <p>{activeRole ? `${activeRole.label}正在处理` : nodes.length ? `已完成 ${completedCount} 个环节` : '发送消息后显示实时进度'}</p>
        </div>
        <button type="button" className="agent-desk__close" onClick={onClose} aria-label="关闭执行进度">
          <X size={17} aria-hidden="true" />
        </button>
      </header>

      {isRollingBack && (
        <div className="agent-desk__review" role="status">
          <RotateCcw size={16} aria-hidden="true" />
          审核发现问题，正在重新生成并复核内容。
        </div>
      )}

      <div className="agent-desk__scroll" data-scroll-region="agent-details">
        <div className="agent-desk__roles">
          {roles.map((role) => <AgentTask key={role.key} role={role} />)}
        </div>
        {refs.length > 0 && (
          <button
            type="button"
            onClick={() => onInspectRefs?.(refs, refs[0]?.query || '')}
            className="agent-desk__sources"
          >
            <Search size={15} aria-hidden="true" />查看 {refs.length} 条参考来源
          </button>
        )}
        <div ref={bottomRef} />
      </div>
    </aside>
  );
}

export default AgentTimeline;
