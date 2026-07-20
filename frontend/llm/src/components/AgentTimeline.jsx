import React, { useEffect, useRef, useState } from 'react';
import { Bot, BrainCircuit, CheckCircle2, ChevronDown, ChevronRight, Clock, Database, GitBranch, Loader2, RotateCcw, Search, ShieldAlert, Sparkles, Wrench, XCircle } from 'lucide-react';
import { useLangGraphStore } from '../stores/useLangGraphStore';

const iconMap = {
  InfoManager: Database,
  Planner: BrainCircuit,
  Executor: Bot,
  Feedback: ShieldAlert,
};

const statusStyle = {
  running: 'bg-blue-50 text-blue-700 border-blue-100',
  done: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  error: 'bg-rose-50 text-rose-700 border-rose-100',
  rollingBack: 'bg-amber-50 text-amber-700 border-amber-100',
  archived: 'bg-slate-100 text-slate-500 border-slate-200',
};

const nodeIconStyle = {
  InfoManager: 'bg-cyan-50 text-cyan-700 shadow-cyan-100/70',
  Planner: 'bg-teal-50 text-teal-700 shadow-teal-100/70',
  Executor: 'bg-emerald-50 text-emerald-700 shadow-emerald-100/70',
  Feedback: 'bg-slate-100 text-slate-600 shadow-slate-100/70',
};

const nodeStatusRing = {
  running: 'ring-cyan-100 border-cyan-300',
  done: 'ring-emerald-100 border-emerald-200',
  error: 'ring-rose-100 border-rose-200',
  rollingBack: 'ring-amber-100 border-amber-200',
  archived: 'ring-slate-100 border-slate-200',
};

const statusLabel = {
  running: 'running',
  done: 'done',
  error: 'error',
  rollingBack: 'review',
  archived: 'archived',
};

function elapsed(node) {
  const end = node.endTime || Date.now();
  return `${Math.max(0, ((end - node.startTime) / 1000)).toFixed(1)}s`;
}

function normalizeLog(log = '') {
  const text = String(log || '').trim();
  if (!text) return '';
  if (/参考信息整理完成。?\s*无\s*$/.test(text)) return '参考信息整理完成，暂无额外参考信息。';
  if (/个性化信息管理完成。?\s*抽取摘要：无\s*重要信息 → 7 天短期记忆：无\s*非重要信息 → 候选池：无\s*$/s.test(text)) {
    return '个性化信息管理完成，本轮未发现需要沉淀的新信息。';
  }
  return text;
}

function visibleLogs(logs = []) {
  return logs.map(normalizeLog).filter(Boolean);
}

/** @param {{tools: import('../stores/useLangGraphStore').ToolCall[]}} props */
export function ToolCallViewer({ tools = [] }) {
  if (!tools.length) return null;
  return (
    <div className="mt-3 space-y-2">
      <div className="text-xs font-bold text-gray-400 uppercase tracking-wide flex items-center gap-1"><Wrench size={12}/> Tool Calls</div>
      {tools.map(tool => (
        <details key={tool.id} className="rounded-xl border border-orange-100 bg-orange-50/40 p-3" open={tool.status === 'running'}>
          <summary className="cursor-pointer select-none flex items-center justify-between gap-2 text-sm font-semibold text-orange-700">
            <span>{tool.name}</span>
            <span className={`text-[10px] px-2 py-0.5 rounded-full border ${statusStyle[tool.status] || statusStyle.done}`}>{tool.status}</span>
          </summary>
          <div className="mt-2 text-xs text-gray-600 space-y-2">
            <pre className="bg-white/80 border border-orange-100 rounded-lg p-2 overflow-auto">{JSON.stringify(tool.args || {}, null, 2)}</pre>
            {tool.resultSnippet && <div className="bg-white/80 border border-orange-100 rounded-lg p-2 whitespace-pre-wrap max-h-40 overflow-auto">{tool.resultSnippet}</div>}
          </div>
        </details>
      ))}
    </div>
  );
}

/** @param {{intents: import('../stores/useLangGraphStore').IntentCall[]}} props */
export function IntentClassifierViewer({ intents = [] }) {
  if (!intents.length) return null;
  return (
    <div className="mt-3 space-y-2">
      <div className="text-xs font-bold text-gray-400 uppercase tracking-wide flex items-center gap-1"><GitBranch size={12}/> Intent Classifier</div>
      {intents.map(intent => (
        <div key={intent.id} className="rounded-xl border border-purple-100 bg-purple-50/50 p-3">
          <div className="flex items-center justify-between gap-2 text-sm font-semibold text-purple-700">
            <span className="flex items-center gap-2"><BrainCircuit size={14}/>{intent.label || 'IntentClassifier'}</span>
            <span className={`text-[10px] px-2 py-0.5 rounded-full border ${statusStyle[intent.status] || statusStyle.done}`}>{intent.status}</span>
          </div>
          <div className="mt-2 rounded-lg border border-purple-100 bg-white/80 p-2 text-xs text-gray-700">
            <div className="font-semibold text-gray-500 mb-1">识别意图</div>
            <div className="text-purple-700 font-bold">{intent.intent || '其他'}</div>
            {intent.resultSnippet && <div className="mt-2 whitespace-pre-wrap text-gray-500">{intent.resultSnippet}</div>}
          </div>
        </div>
      ))}
    </div>
  );
}

/** @param {{node: import('../stores/useLangGraphStore').ExecutionNode, active:boolean}} props */
export function AgentStepCard({ node, active }) {
  const [open, setOpen] = useState(active || node.status === 'error' || node.status === 'rollingBack');
  const [previousActive, setPreviousActive] = useState(active);
  if (active !== previousActive) {
    setPreviousActive(active);
    if (active && !open) setOpen(true);
  }
  const Icon = iconMap[node.name] || BrainCircuit;
  const isError = node.status === 'error' || node.status === 'rollingBack';
  const iconStyle = nodeIconStyle[node.name] || nodeIconStyle.Planner;
  const logs = visibleLogs(node.logs || []);
  const statusIcon = node.status === 'running'
    ? <Loader2 size={14} className="animate-spin" />
    : isError
      ? <XCircle size={14} />
      : <CheckCircle2 size={14} />;
  return (
    <div className="relative pl-9 py-2">
      <div className="absolute left-[14px] top-0 bottom-0 w-px bg-gradient-to-b from-transparent via-emerald-100 to-transparent" />
      <div className={`absolute left-0 top-4 z-10 w-7 h-7 rounded-full inline-flex items-center justify-center border ring-4 shadow-sm ${iconStyle} ${nodeStatusRing[node.status] || nodeStatusRing.done} ${node.status === 'running' ? 'animate-pulse' : ''}`}>
        <Icon size={14} strokeWidth={2} />
      </div>
      <div className="group relative rounded-2xl px-1 py-1 transition-colors hover:bg-white/45">
        <button onClick={() => setOpen(!open)} className="w-full flex items-center justify-between gap-3 text-left">
          <div className="flex items-center min-w-0">
            <div className="min-w-0">
              <div className="font-semibold text-slate-800 flex items-center gap-2 leading-tight">
                {node.name}
                <span className="inline-flex items-center gap-1 text-[11px] font-medium text-slate-400"><Clock size={11}/>{elapsed(node)}</span>
                {node.archived && <span className="text-[10px] bg-slate-100 text-slate-500 px-2 py-0.5 rounded-full">archived</span>}
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <span className={`inline-flex items-center gap-1 text-[10px] px-2 py-0.5 rounded-full border font-semibold ${statusStyle[node.status] || statusStyle.done}`}>{statusIcon}{statusLabel[node.status] || node.status}</span>
            {open ? <ChevronDown size={15} className="text-slate-400 group-hover:text-slate-600"/> : <ChevronRight size={15} className="text-slate-400 group-hover:text-slate-600"/>}
          </div>
        </button>

        {open && (
          <div className="mt-3 ml-11 space-y-3 animate-in fade-in slide-in-from-top-1 duration-200">
            {node.error && <div className="rounded-xl bg-rose-50/80 border border-rose-100 text-rose-700 text-sm p-3 flex gap-2"><XCircle size={16} className="shrink-0 mt-0.5"/>{node.error}</div>}
            {logs.length > 0 && (
              <div className="space-y-2 border-l border-emerald-100 pl-4">
                {logs.map((log, idx) => (
                  <p key={idx} className="text-xs text-slate-600 whitespace-pre-wrap leading-6">
                    {log}
                  </p>
                ))}
              </div>
            )}
            <IntentClassifierViewer intents={node.intents || []} />
            <ToolCallViewer tools={node.tools} />
          </div>
        )}
      </div>
    </div>
  );
}

/** Right observability sidebar for LangGraph execution events. */
export function AgentTimeline({ isOpen = true, onClose, onInspectRefs, nodes: externalNodes, refs: externalRefs, title = 'Agent Observability' }) {
  const storeNodes = useLangGraphStore(s => s.nodes);
  const storeActiveId = useLangGraphStore(s => s.currentActiveNodeId);
  const storeRefs = useLangGraphStore(s => s.references);
  const isRollingBack = useLangGraphStore(s => s.isRollingBack);
  const nodes = externalNodes || storeNodes;
  const refs = externalRefs || storeRefs;
  const activeId = externalNodes ? null : storeActiveId;
  const bottomRef = useRef(null);
  const [autoFollow, setAutoFollow] = useState(true);

  useEffect(() => { if (autoFollow) bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [nodes.length, activeId, autoFollow]);

  return (
    <aside
      aria-hidden={isOpen ? undefined : true}
      inert={isOpen ? undefined : true}
      className={`${isOpen ? 'w-[420px] translate-x-0 opacity-100 duration-[var(--motion-drawer-in)]' : 'w-0 translate-x-4 opacity-0 duration-[var(--motion-drawer-out)]'} shrink-0 border-l border-emerald-100 bg-gradient-to-b from-white/95 via-emerald-50/45 to-slate-50/95 backdrop-blur-xl transition-[transform,opacity] overflow-hidden flex flex-col`}
    >
      <div className="h-16 px-5 border-b border-emerald-100 bg-white/85 flex items-center justify-between shadow-sm shadow-emerald-50">
        <div>
          <div className="font-bold text-slate-900 flex items-center gap-2"><Sparkles size={16} className="text-emerald-500" />{title}</div>
        </div>
        <button onClick={onClose} className="w-8 h-8 rounded-full hover:bg-emerald-50 text-slate-400 hover:text-emerald-700 transition-colors">×</button>
      </div>
      {isRollingBack && <div className="mx-4 mt-4 rounded-2xl border border-amber-200 bg-amber-50 text-amber-700 p-3 text-sm flex items-center gap-2"><RotateCcw size={16}/>审核失败或回滚中，主答案正在重新评估。</div>}
      <div onScroll={(e) => {
        const { scrollTop, scrollHeight, clientHeight } = e.currentTarget;
        setAutoFollow(scrollHeight - scrollTop - clientHeight < 80);
      }} className="flex-1 overflow-y-auto p-4 space-y-4 custom-scrollbar">
        {nodes.length === 0 && <div className="mt-20 text-center text-gray-400 text-sm">发送消息后，这里会实时显示所有 LangGraph 节点执行过程。</div>}
        {nodes.map(node => <AgentStepCard key={node.id} node={node} active={node.id === activeId} />)}
        {refs.length > 0 && (
          <button onClick={() => onInspectRefs?.(refs, refs[0]?.query || '')} className="w-full rounded-2xl border border-blue-100 bg-blue-50 text-blue-700 p-3 text-sm flex items-center justify-center gap-2 hover:bg-blue-100 transition-colors">
            <Search size={15}/> 查看 {refs.length} 个检索来源
          </button>
        )}
        <div ref={bottomRef} />
      </div>
    </aside>
  );
}

export default AgentTimeline;
