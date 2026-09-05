import React, { useState, useEffect, useRef, useMemo, lazy, Suspense } from 'react';
import { Virtuoso } from 'react-virtuoso';
import {
  Send, Bot, User, Plus, MessageSquare, Trash2,
  Edit2, X, Check, Cpu, Copy, Sparkles, MoreHorizontal,
  ChevronsLeft, ChevronsRight, SquarePen, Download,
  Paperclip, FileText, Loader2, FileJson, FileType, FileCode, UploadCloud,
  LogOut, Square, Globe, Search, BrainCircuit, Mic, MicOff, ArrowDown,
  Database, BookOpen, ChevronRight, Library, ExternalLink, Layout, ArrowLeft,
  ImageIcon, Film, Lightbulb, ThumbsUp, ThumbsDown, HeartPulse, RefreshCw, ShieldCheck,
  CalendarRange
} from 'lucide-react';
import { API_BASE, MAIN_API_BASE, fetchWithAuth } from '../utils/api';
import { getAppShellConfig } from '../appShell';
import { extractTraceEventsFromContent, hasExecutionDoneEvent, removeTraceEventsFromContent, stripAssistantVisibleContent } from '../chatProtocol';
import HomeButton from './HomeButton';

// --- Markdown Imports ---
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import rehypeKatex from 'rehype-katex';
import 'katex/dist/katex.min.css';
import AgentInlineTrace from './AgentInlineTrace';
import { buildAgentPresentation } from '../agentPresentationModel';
import { buildTraceFromEvents, useLangGraphStore } from '../stores/useLangGraphStore';
import {
  cancelWorkflowRun,
  createWorkflowRunId,
  getResumableWorkflowRunId,
  getWorkflowRun,
  getWorkflowRunWithStartupGrace,
  runtimeEventToTrace,
  streamWorkflowTurn,
} from '../workflowChatClient';
import { formatMessageTime } from '../chatTime';
import { workshopActionIntent } from '../pageIntent';
import { resolveAssistantSessionId } from '../assistantDockModel';

const CodeHighlighter = lazy(() => import('./CodeHighlighter'));

// --- Utils Helper Functions ---
const preprocessLaTeX = (content) => {
  if (typeof content !== 'string') return content;
  return content
    .replace(/\\\[([\s\S]*?)\\\]/g, '$$$1$$') 
    .replace(/\\\((.*?)\\\)/g, '$$$1$');       
};

const getCurrentTime = () => formatMessageTime(new Date());

const copyTextSafely = async (value) => {
  const text = String(value ?? '');
  if (!text) return false;

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Clipboard access is commonly denied in embedded/system browsers. Fall
    // back to the synchronous browser command instead of leaking an event
    // error that can leave the React surface blank.
  }

  if (typeof document === 'undefined' || typeof document.execCommand !== 'function') return false;
  const activeElement = document.activeElement;
  const textarea = document.createElement('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', '');
  textarea.setAttribute('aria-hidden', 'true');
  textarea.style.position = 'fixed';
  textarea.style.inset = '0 auto auto 0';
  textarea.style.opacity = '0';
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  let copied = false;
  try {
    copied = document.execCommand('copy');
  } catch {
    copied = false;
  } finally {
    textarea.remove();
    try {
      activeElement?.focus?.({ preventScroll: true });
    } catch {
      activeElement?.focus?.();
    }
  }
  return copied;
};

const keepNativeCopyShortcut = (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
    // Copy remains a native browser action, but it must not leak to shell-level
    // shortcuts that could replace the assistant route.
    event.stopPropagation();
  }
};

const isImageFile = (filename) => {
  const ext = filename?.split('.').pop().toLowerCase() || '';
  return ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'webp', 'tiff'].includes(ext);
};

const getFileIcon = (filename) => {
  const ext = filename?.split('.').pop().toLowerCase() || '';
  if (isImageFile(filename)) return <ImageIcon size={14} className="text-purple-500" />;
  if (ext === 'json') return <FileJson size={14} className="text-orange-500" />;
  if (ext === 'docx') return <FileText size={14} className="text-blue-500" />;
  if (['py', 'js', 'html', 'css'].includes(ext)) return <FileCode size={14} className="text-green-500" />;
  return <FileType size={14} className="text-gray-500" />;
};

const getDomain = (url) => {
  try {
    if (!url || url.startsWith('file')) return 'Local';
    return new URL(url).hostname.replace('www.', '');
  } catch {
    return 'Source';
  }
};

const isExternalUrl = (url) => /^https?:\/\//i.test(url || '');

// 教材证据的 source_id 形如 "中西医结合口腔科学_clean:00475"，提取教材名作标题。
const formatTextbookSourceTitle = (sourceId = '') => {
  const text = String(sourceId || '');
  const cleaned = text.replace(/_clean:\d+$/, '').replace(/\.md$/, '');
  return cleaned || '知识库教材';
};

const VideoPreviewCard = React.memo(({ video, index }) => {
  const snippet = (video?.snippet || video?.content || '').replace(/\s+/g, ' ').trim();

  return (
    <div className="w-52 shrink-0 rounded-2xl border border-sky-100 bg-white p-3 shadow-sm shadow-sky-100/60">
      <a href={video?.url} target="_blank" rel="noopener noreferrer" className="flex aspect-video items-center justify-center rounded-xl bg-sky-50 text-sky-700 transition hover:bg-sky-100">
        <Film size={28} />
      </a>
      <div className="px-1 py-2">
        <a href={video?.url} target="_blank" rel="noopener noreferrer" className="line-clamp-2 text-xs font-semibold leading-5 text-slate-800 hover:text-sky-700">
          {index + 1}. {video?.title || '演示视频'}
        </a>
        <div className="mt-1 line-clamp-1 text-[11px] leading-4 text-slate-500">
          {video?.author || getDomain(video?.url)}{snippet ? ` · ${snippet.slice(0, 36)}` : ''}
        </div>
      </div>
    </div>
  );
});

const VideoLinks = React.memo(({ videos }) => {
  if (!videos?.length) return null;

  return (
    <div className="mt-3 w-full max-w-full min-w-0 overflow-hidden border-t border-sky-100/80 pt-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div className="inline-flex items-center gap-1.5 text-xs font-semibold text-sky-700">
          <Film size={13} /> 推荐演示视频
        </div>
        {videos.length > 1 && <span className="text-[11px] text-slate-400">横向滑动查看更多</span>}
      </div>
      <div className="video-scrollbar w-full max-w-full min-w-0 overflow-x-auto overscroll-x-contain pb-1.5">
        <div className="inline-flex w-max max-w-none gap-3 pr-1">
          {videos.map((video, idx) => <VideoPreviewCard key={`${video?.url || idx}-${idx}`} video={video} index={idx} />)}
        </div>
      </div>
    </div>
  );
});

// --- Sub Components ---

function MarkdownCode({ inline, className, children, ...props }) {
  const match = /language-(\w+)/.exec(className || '');
  const codeString = String(children).replace(/\n$/, '');
  const [copied, setCopied] = useState(false);

  const handleCodeCopy = async () => {
    if (!(await copyTextSafely(codeString))) return;
    setCopied(true);
    window.setTimeout(() => setCopied(false), 2000);
  };

  if (!inline && match) {
    return (
      <div className="relative my-5 rounded-2xl overflow-hidden border border-emerald-900/30 shadow-lg shadow-emerald-950/10 bg-[#10201c] group/code">
        <div className="flex justify-between items-center px-4 py-2.5 bg-[#162b25] border-b border-emerald-800/40 text-xs text-emerald-100/70 font-mono select-none">
          <div className="flex gap-1.5 items-center">
            <span className="w-2.5 h-2.5 rounded-full bg-red-500/80"></span>
            <span className="w-2.5 h-2.5 rounded-full bg-yellow-500/80"></span>
            <span className="w-2.5 h-2.5 rounded-full bg-green-500/80"></span>
            <span className="ml-2 text-gray-300 font-medium uppercase tracking-wider">{match[1]}</span>
          </div>
          <button
            type="button"
            onClick={handleCodeCopy}
            className={`flex items-center gap-1.5 cursor-pointer transition-[color,opacity] ${copied ? 'text-green-400 opacity-100' : 'hover:text-white opacity-0 group-hover/code:opacity-100'}`}
          >
            {copied ? <Check size={14} /> : <Copy size={14} />}
            <span>{copied ? '已复制' : '复制代码'}</span>
          </button>
        </div>
        <Suspense fallback={<pre className="m-0 bg-transparent p-5 text-sm leading-relaxed text-emerald-50/90 overflow-x-auto"><code>{codeString}</code></pre>}>
          <CodeHighlighter language={match[1]} {...props}>
            {codeString}
          </CodeHighlighter>
        </Suspense>
      </div>
    );
  }

  return (
    <code className="bg-emerald-50 text-emerald-700 px-1.5 py-0.5 rounded-md text-[0.85em] font-mono border border-emerald-100 mx-0.5" {...props}>
      {children}
    </code>
  );
}

const MarkdownRenderer = React.memo(({ content, className }) => {
  const formattedContent = preprocessLaTeX(content);

  return (
    <div className={`markdown-body text-[15px] leading-relaxed break-words text-slate-700 ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          code: MarkdownCode,

          // --- 基础排版优化 ---
          p: ({children}) => <p className="mb-4 last:mb-0 text-slate-700 leading-7">{children}</p>,
          
          // 🔥 优化：列表的 Marker（原点/数字）使用主题色
          ul: ({children}) => <ul className="list-disc pl-6 mb-5 space-y-2 marker:text-emerald-400 text-slate-700">{children}</ul>,
          ol: ({children}) => <ol className="list-decimal pl-6 mb-5 space-y-2 marker:text-emerald-600 marker:font-semibold text-slate-700">{children}</ol>,
          li: ({children}) => {
            const text = React.Children.toArray(children).map(child => (typeof child === 'string' ? child : '')).join('').trim();
            if (!text && React.Children.toArray(children).length === 0) return null;
            return <li className="pl-1 leading-7 empty:hidden">{children}</li>;
          },
          
          // 🔥 优化：各级标题层次分明，加入主题色和渐变
          h1: ({children}) => <h1 className="text-2xl font-extrabold text-transparent bg-clip-text bg-gradient-to-r from-emerald-700 via-teal-600 to-cyan-600 mb-5 mt-8 pb-2 border-b border-emerald-100">{children}</h1>,
          h2: ({children}) => <h2 className="text-xl font-bold text-emerald-950 mb-4 mt-7 pb-1.5 border-b border-emerald-50">{children}</h2>,
          h3: ({children}) => <h3 className="text-lg font-bold text-slate-800 mb-3 mt-6 flex items-center gap-2"><span className="w-1.5 h-4 bg-gradient-to-b from-emerald-400 to-teal-500 rounded-full inline-block"></span>{children}</h3>,
          h4: ({children}) => <h4 className="text-base font-bold text-slate-700 mb-2 mt-4">{children}</h4>,
          
          // 🔥 优化：引用块加上淡色背景和柔和的边框
          blockquote: ({children}) => (
            <blockquote className="border-l-4 border-emerald-400 bg-gradient-to-r from-emerald-50 via-teal-50/50 to-transparent py-3 px-5 rounded-r-2xl my-5 text-slate-600 italic shadow-sm shadow-emerald-100/40">
              {children}
            </blockquote>
          ),
          
          // 🔥 优化：链接样式，加入下划线动画
          a: ({children, href}) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-teal-700 hover:text-emerald-800 underline decoration-emerald-300/60 hover:decoration-emerald-600 decoration-2 underline-offset-4 transition-[color,text-decoration-color] duration-200">{children}</a>,
          
          // 🔥 优化：支持 Markdown 表格的精美渲染 (remarkGfm 提供)
          table: ({children}) => <div className="overflow-x-auto my-5 rounded-2xl border border-emerald-100 shadow-sm shadow-emerald-100/50 bg-white"><table className="w-full text-sm text-left text-slate-600">{children}</table></div>,
          thead: ({children}) => <thead className="text-xs text-emerald-900 uppercase bg-gradient-to-r from-emerald-50 to-teal-50 border-b border-emerald-100">{children}</thead>,
          th: ({children}) => <th className="px-4 py-3 font-semibold text-emerald-950">{children}</th>,
          td: ({children}) => <td className="px-4 py-3 border-b border-emerald-50 last:border-0 align-top">{children}</td>,

          // --- 保留你的 Agent 动作核心逻辑 ---
          strong: ({children}) => {
            const textNode = Array.isArray(children) ? children[0] : children;
            if (typeof textNode === 'string') {
              if (textNode.includes('[意图分析]')) return <span className="text-purple-600 font-bold inline-flex items-center gap-1.5 bg-purple-50 px-2 py-0.5 rounded-md border border-purple-100"><BrainCircuit size={16} className="animate-pulse"/> {children}</span>;
              if (textNode.includes('[决策执行]')) return <span className="text-rose-500 font-bold inline-flex items-center gap-1.5 bg-rose-50 px-2 py-0.5 rounded-md border border-rose-100"><Cpu size={16} className="animate-pulse"/> {children}</span>;
              if (textNode.includes('[任务规划]')) return <span className="text-yellow-600 font-bold inline-flex items-center gap-1.5 bg-yellow-50 px-2 py-0.5 rounded-md border border-yellow-100"><Lightbulb size={16} className="animate-pulse"/> {children}</span>;
              if (textNode.includes('[信息检索与整合]')) return <span className="text-orange-600 font-bold inline-flex items-center gap-1.5 bg-orange-50 px-2 py-0.5 rounded-md border border-orange-100"><Database size={16} className="animate-bounce"/> {children}</span>;
              if (textNode.includes('[主模型接管]')) return <span className="text-emerald-700 font-bold inline-flex items-center gap-1.5 bg-emerald-50 px-2 py-0.5 rounded-md border border-emerald-100"><Sparkles size={16} className="animate-pulse"/> {children}</span>;
            }
            // 🔥 普通的加粗文本：不再是死板的黑色，而是深灰偏蓝，更加精致
            return <strong className="font-bold text-slate-800 tracking-wide">{children}</strong>;
          },
          
          // 优化 Agent 横线分隔符
          hr: () => <hr className="my-6 border-gray-200/60 border-dashed" />
        }}
      >
        {formattedContent}
      </ReactMarkdown>
    </div>
  );
});

const mergeTraceEvents = (inlineEvents, persistedEvents) => {
  const all = [...(Array.isArray(inlineEvents) ? inlineEvents : []), ...(Array.isArray(persistedEvents) ? persistedEvents : [])];
  // 去重：同一 callId 的 model_input/output/transport 是同一个模型边界的三条
  // 事件，kind 不同必须都保留，交给 mergeModelCalls 按 callId 合并展示。
  // 运行时 inline 只有轻量 step/tool 事件，两者合并后节点完整。
  const seen = new Set();
  const merged = [];
  for (const event of all) {
    if (!event) continue;
    // reasoning_stream delta 是顺序拼接的增量文本：同一次模型调用的
    // 每条 delta 共享同一个 type|kind|stepId（没有 callId 可区分），
    // 若按通用 key 去重会只剩第一条（如"好的"），把完整思考过程截成
    // 几个字。必须全部按序保留，由下游 reduce 依次拼接。
    if (event.type === 'reasoning_stream' && event.kind === 'delta') {
      merged.push(event);
      continue;
    }
    if (event.type === 'knowledge_retrieval') {
      merged.push(event);
      continue;
    }
    const key = Number.isFinite(event.seq)
      ? `seq:${event.seq}`
      : `${event.type || ''}|${event.kind || ''}|${event.stepId || event.step_id || ''}|${event.callId || event.call_id || ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(event);
  }
  return merged;
};

const evidenceIdentity = (item = {}) => {
  const evidenceId = String(item.evidence_id || '').trim();
  if (evidenceId) return `evidence:${evidenceId}`;
  const sourceUrl = String(item.source_url || '').trim();
  if (sourceUrl) return `url:${sourceUrl}`;
  const sourceId = String(item.source_id || '').trim();
  if (sourceId) return `source:${sourceId}`;
  return `content:${String(item.content_summary || item.content || '').trim()}`;
};

const deduplicateKnowledgeRetrievals = (retrievals = []) => {
  const seen = new Set();
  return retrievals.map(retrieval => ({
    ...retrieval,
    evidence_items: (Array.isArray(retrieval?.evidence_items) ? retrieval.evidence_items : [])
      .filter(item => {
        const key = evidenceIdentity(item);
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      }),
  }));
};

const hasExecutionDoneTrace = (message) => (
  hasExecutionDoneEvent(message?.content || '')
  || (Array.isArray(message?.traceEvents)
    && message.traceEvents.some(event => event?.type === 'execution_done'))
);

const ChatBubble = React.memo(({ role, content, files, timestamp, messageId, feedbackStatus, branch, actions, traceEvents: persistedTraceEvents, publicationStatus, onAction, onInspectKnowledge, onFeedback, onRegenerate, onSwitchBranch, isGenerating, isReviewing }) => {
  const isUser = role === 'user';
  const [isCopied, setIsCopied] = useState(false);
  
  let rawContent = content;
  let videos = [];
  const inlineTraceEvents = extractTraceEventsFromContent(rawContent);
  const traceEvents = mergeTraceEvents(inlineTraceEvents, persistedTraceEvents);
  if (inlineTraceEvents.length > 0) {
    rawContent = removeTraceEventsFromContent(rawContent).trim();
  }
  // 知识库管理智能体的检索轨迹：每轮检索语句 + 证据列表 + 模型对证据的
  // 检索总结。实时 SSE 与持久化回执中的 knowledge_retrieval 事件都会经
  // runtimeEventToTrace 转换为 {type:'knowledge_retrieval'}，model_output
  // 转换为 {type:'model_call', kind:'output'}，供"检索详情"抽屉回放检索内容。
  // 证据展示：本地教材（无 source_url）与外部来源（视频/论文/网页）都展示，
  // 分别以知识库卡片 / 网页卡片呈现。
  const knowledgeRetrievals = traceEvents.filter(e => e.type === 'knowledge_retrieval' && e.agent === 'knowledge_base_agent');
  const uniqueKnowledgeRetrievals = deduplicateKnowledgeRetrievals(knowledgeRetrievals);
  const knowledgeEvidenceCount = uniqueKnowledgeRetrievals.reduce(
    (sum, kr) => sum + (kr.evidence_items || []).length,
    0,
  );
  const knowledgeWebCount = uniqueKnowledgeRetrievals.reduce(
    (sum, kr) => sum + (kr.evidence_items || []).filter(item => item.source_url).length,
    0,
  );
  const knowledgeRagCount = knowledgeEvidenceCount - knowledgeWebCount;
  // 只统计最终采用的总结：知识库管理智能体最终确认后发出 knowledge_summary
  // 事件（补充检索的中间轮次总结会被覆盖，不单独计数）。审计打回重跑时
  // 会产生多个 knowledge_summary 事件，只有最后一次（最终交付）才算数。
  // 旧会话回执没有该事件时，回退到最后一次带 retrieval_summary 的模型输出。
  const summaryEvents = traceEvents.filter(
    e => e.type === 'knowledge_summary' && e.agent === 'knowledge_base_agent' && e.summary,
  );
  const fallbackSummaries = traceEvents
    .filter(e => e.type === 'model_call' && e.kind === 'output' && e.agent === 'knowledge_base_agent' && e.output)
    .map(e => {
      try {
        const parsed = typeof e.output === 'string' ? JSON.parse(e.output) : e.output;
        const summary = parsed?.retrieval_summary;
        return typeof summary === 'string' && summary.trim() ? summary.trim() : null;
      } catch {
        return null;
      }
    })
    .filter(Boolean);
  const knowledgeSummaries = summaryEvents.length > 0
    ? [summaryEvents[summaryEvents.length - 1].summary]
    : (fallbackSummaries.length > 0 ? [fallbackSummaries[fallbackSummaries.length - 1]] : []);
  const hasKnowledgeRetrieval = knowledgeEvidenceCount > 0;
  const refMatch = rawContent.match(/<<REFS:(.*?)>>/);
  if (refMatch) {
    rawContent = rawContent.replace(refMatch[0], '').trim();
  }
  const videoMatch = rawContent.match(/<<VIDEOS:(.*?)>>/);
  if (videoMatch) {
    rawContent = rawContent.replace(videoMatch[0], '').trim();
    try {
      videos = JSON.parse(videoMatch[1]);
    } catch (e) {
      console.error("Parsed videos error", e);
    }
  }
  const hasTerminalTraceEvent = traceEvents.some((event) => [
    'workflow_done',
    'workflow_failed',
    'workflow_interrupted',
    'human_review_waiting',
  ].includes(event.type));
  // A model_output/step_completed event only closes one stage.  Local repair,
  // forced re-audit and persistence may still be running afterwards.  Keep the
  // collaboration transcript live until the backend emits a run-level terminal
  // event instead of declaring success from per-agent status alone.
  const traceIsLive = Boolean(
    isGenerating || (traceEvents.length > 0 && !hasTerminalTraceEvent)
  );
  const traceNodes = traceEvents.length > 0 ? buildTraceFromEvents(traceEvents, { historical: !traceIsLive }) : [];
  const traceRoles = traceNodes.length > 0 ? buildAgentPresentation(traceNodes) : [];
  const participatingTraceRoles = traceRoles.filter((role) => (
    role.nodes?.length > 0 && !['pending', 'skipped', 'idle'].includes(role.status)
  ));
  const traceStartedAt = Math.min(...traceNodes.map(node => node.startTime).filter(Number.isFinite), Number.POSITIVE_INFINITY);
  const traceEndedAt = Math.max(...traceNodes.map(node => node.endTime || node.startTime).filter(Number.isFinite), 0);
  const traceElapsedSeconds = Number.isFinite(traceStartedAt) && traceEndedAt >= traceStartedAt
    ? Math.max(0, Math.round((traceEndedAt - traceStartedAt) / 1000))
    : null;
  const hasRunningTraceNode = traceNodes.some(n => n.status === 'running' || n.status === 'rollingBack');
  const hasFailedTraceNode = traceNodes.some(n => n.status === 'error' || n.status === 'failed');
  const hasReviewTraceNode = traceNodes.some(n => n.status === 'waiting_human_review');
  const traceStatus = hasFailedTraceNode
    ? 'failed'
    : hasRunningTraceNode
      ? 'running'
      : hasReviewTraceNode
        ? 'review'
        : traceIsLive
          ? 'running'
          : (traceNodes.length ? 'success' : 'idle');

  // 🔥 简化版解析逻辑：依赖后端保证 <think> 的存在
  const parseThinkContent = (text) => {
    const thinkStartTag = "<think>";
    const thinkEndTag = "</think>";
    
    const startIdx = text.indexOf(thinkStartTag);
    const endIdx = text.indexOf(thinkEndTag);
    
    if (startIdx === -1 && endIdx !== -1) {
      return {
        think: text.substring(0, endIdx),
        main: text.substring(endIdx + thinkEndTag.length).trimStart(),
        isThinking: false
      };
    }

    // 如果没有 <think> 标签，按普通正文处理。
    if (startIdx === -1) {
      return { think: null, main: text, isThinking: false };
    }

    // 有 <think> 但没有 </think> -> 正在思考
    if (endIdx === -1) {
      return { 
        think: text.substring(startIdx + thinkStartTag.length), 
        main: "", 
        isThinking: true 
      };
    }

    // <think> 和 </think> 都有 -> 思考结束
    return {
      think: text.substring(startIdx + thinkStartTag.length, endIdx),
      main: text.substring(endIdx + thinkEndTag.length).trimStart(),
      isThinking: false
    };
  };

  const { think, main } = isUser ? { think: null, main: rawContent } : parseThinkContent(rawContent);

  const isDecisionPhase = !isUser && (!main || main.trim() === '');

  const handleCopy = async () => {
    if (!(await copyTextSafely(main || think))) return;
    setIsCopied(true);
    window.setTimeout(() => setIsCopied(false), 2000);
  };

  return (
    <article aria-label={isUser ? '我的消息' : '智能助教回复'} className={`assistant-message relative flex gap-4 ${isUser ? 'mb-8 flex-row-reverse' : 'mb-12 flex-row'} group animate-fade-in-up`}>
      {/* 🔥 修改头像外框颜色和图标：决策阶段用紫色大脑，正式阶段用蓝色机器人 */}
      <div className={`w-9 h-9 rounded-full flex items-center justify-center shrink-0 shadow-sm mt-1 transition-transform hover:scale-110 ${
        isUser 
            ? 'bg-gradient-to-br from-emerald-500 to-teal-600 text-white' 
          : (isDecisionPhase
              ? 'bg-teal-50 border border-teal-200 text-teal-700' 
              : 'bg-white border border-emerald-100 text-emerald-700')
      }`}>
        {isUser ? <User size={18} /> : (isDecisionPhase ? <BrainCircuit size={18} className="animate-pulse" /> : <Bot size={18} />)}
      </div>

      <div className={`relative max-w-[90%] sm:max-w-[80%] min-w-0 flex flex-col ${isUser ? 'items-end' : 'items-start'}`}>
        <div className={`assistant-message__meta flex items-center gap-2 mb-1 text-xs text-gray-400 ${isUser ? 'flex-row-reverse' : 'flex-row'}`}>
          <span className="font-medium opacity-80">{isUser ? '我' : '智能助教'}</span>
          <span>{formatMessageTime(timestamp)}</span>
        </div>

        {files && files.length > 0 && (
          <div className={`mb-2 flex flex-wrap gap-2 ${isUser ? 'justify-end' : 'justify-start'}`}>
            {files.map((file, idx) => (
              <React.Fragment key={idx}>
                {isImageFile(file.name) ? (
                  <div className="flex items-center gap-2 bg-white border border-gray-200 pl-2 pr-3 py-1.5 rounded-xl shadow-sm text-xs text-gray-700 animate-in zoom-in duration-200">
                    <ImageIcon size={14} className="text-purple-500" />
                    <span className="font-medium max-w-[150px] truncate">{file.name}</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 bg-white border border-gray-200 pl-2 pr-3 py-1.5 rounded-xl shadow-sm text-xs text-gray-700 animate-in zoom-in duration-200">
                    {getFileIcon(file.name)}
                    <span className="font-medium max-w-[150px] truncate">{file.name}</span>
                  </div>
                )}
              </React.Fragment>
            ))}
          </div>
        )}

        {!isUser && traceNodes.length > 0 && (
          <AgentInlineTrace
            roles={participatingTraceRoles}
            status={traceStatus}
            elapsedSeconds={traceElapsedSeconds}
            evidenceCount={knowledgeEvidenceCount}
            isGenerating={traceIsLive}
          />
        )}

        {!isUser && isGenerating && publicationStatus && (
          <div className="assistant-publication-state" role="status">
            <ShieldCheck size={13} aria-hidden="true" />
            {publicationStatus === 'approved'
              ? '已通过发布门禁，正在输出正式回答'
              : publicationStatus === 'human_review'
                ? '正在输出人工复核说明'
                : '正在输出需要你补充的信息'}
          </div>
        )}

        <div className={`assistant-message__surface relative max-w-full px-5 py-4 rounded-2xl transition-shadow ${
          isUser 
            ? 'bg-gradient-to-br from-emerald-600 to-teal-600 text-white rounded-tr-sm shadow-emerald-200/60' 
            : 'bg-white/95 border border-emerald-100 text-slate-800 rounded-tl-sm shadow-emerald-100/70'
        }`}>
          {isUser ? (
            <p className="whitespace-pre-wrap leading-relaxed">{content}</p>
          ) : (
            <div className="assistant-message__body flex w-full min-w-0 max-w-full flex-col gap-2">
               {main ? (
                 <MarkdownRenderer content={main} />
               ) : isGenerating ? (
                 <div className="space-y-2 min-w-[260px] py-1">
                   <div className="h-3 w-40 rounded-full bg-gray-100 animate-pulse" />
                   <div className="h-3 w-56 rounded-full bg-gray-100 animate-pulse" />
                   <div className="h-3 w-32 rounded-full bg-gray-100 animate-pulse" />
                 </div>
               ) : (
                 <MarkdownRenderer content={rawContent} />
               )}
               
               {hasKnowledgeRetrieval && (
                <div className="mt-2 pt-2 border-t border-gray-100/50">
                  <div
                    onClick={() => onInspectKnowledge?.({ retrievals: uniqueKnowledgeRetrievals, summaries: knowledgeSummaries })}
                    className="flex items-center gap-2 flex-wrap cursor-pointer group/kb p-1.5 -ml-1.5 rounded-lg hover:bg-blue-50 transition-colors select-none"
                    title="点击查看参考来源"
                  >
                    <div className="text-[10px] font-semibold text-blue-500 uppercase tracking-wider flex items-center gap-1">
                      <Library size={12} /> 参考来源 {knowledgeEvidenceCount} 条
                    </div>
                    <div className="flex items-center gap-1.5 pl-2 border-l border-blue-100">
                      {knowledgeRagCount > 0 && (
                        <div className="w-5 h-5 rounded flex items-center justify-center bg-orange-50 border border-orange-100 text-orange-500" title={`${knowledgeRagCount} 条教材/知识库检索内容`}>
                          <BookOpen size={10} />
                        </div>
                      )}
                      {knowledgeWebCount > 0 && (
                        <div className="w-5 h-5 rounded flex items-center justify-center bg-blue-50 border border-blue-100 text-blue-500" title={`${knowledgeWebCount} 条外部来源（视频/论文/网页）`}>
                          <Globe size={10} />
                        </div>
                      )}
                      {knowledgeSummaries.length > 0 && (
                        <span className="text-[10px] text-gray-400 font-medium bg-gray-100 px-1 rounded">{knowledgeSummaries.length} 份总结</span>
                      )}
                      <ChevronRight size={14} className="text-gray-300 group-hover/kb:text-blue-500 transition-colors ml-1" />
                    </div>
                  </div>
                </div>
              )}
               <VideoLinks videos={videos} />
               {Array.isArray(actions) && actions.length > 0 && (
                 <div className="assistant-message__actions mt-3 flex flex-wrap gap-2 border-t border-emerald-100 pt-3">
                   {actions.map((action, index) => (
                     <button key={`${action.destination || 'action'}-${index}`} type="button" onClick={() => onAction?.(action)} className="rounded-xl bg-emerald-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-emerald-700">
                       {action.label || '继续'}
                     </button>
                   ))}
                 </div>
               )}
            </div>
          )}

          <div className={`absolute -bottom-8 z-30 ${isUser ? 'right-0' : 'left-0'} flex items-center gap-3 text-slate-400`}>
            {!isUser && branch?.count > 1 && (
              <div className="inline-flex items-center gap-1 text-xs font-semibold text-slate-600">
                <button
                  type="button"
                  onClick={() => onSwitchBranch?.('prev', messageId, branch)}
                  disabled={!branch?.prev_id}
                  className={`inline-flex h-6 w-5 items-center justify-center transition-colors ${branch?.prev_id ? 'hover:text-slate-900' : 'opacity-35 cursor-not-allowed'}`}
                  title="切换到上一分支"
                >
                  ‹
                </button>
                <span className="min-w-7 text-center tabular-nums">{branch.index}/{branch.count}</span>
                <button
                  type="button"
                  onClick={() => onSwitchBranch?.('next', messageId, branch)}
                  disabled={!branch?.next_id}
                  className={`inline-flex h-6 w-5 items-center justify-center transition-colors ${branch?.next_id ? 'hover:text-slate-900' : 'opacity-35 cursor-not-allowed'}`}
                  title="切换到下一分支"
                >
                  ›
                </button>
              </div>
            )}
            {!isUser && main && (
              <>
                <button
                  onClick={() => onFeedback?.('like', main, messageId, feedbackStatus)}
                  disabled={!messageId}
                  className={`inline-flex h-6 w-5 items-center justify-center transition-colors ${feedbackStatus === 'like' ? 'text-emerald-600' : 'text-slate-400 hover:text-emerald-600'} ${!messageId ? 'cursor-not-allowed opacity-50' : ''}`}
                  title={!messageId ? '回答保存后才能反馈' : feedbackStatus === 'like' ? '取消点赞' : feedbackStatus === 'dislike' ? '改为点赞' : '点赞：放入优秀反馈数据'}
                >
                  <ThumbsUp size={16} />
                </button>
                <button
                  onClick={() => onFeedback?.('dislike', main, messageId, feedbackStatus)}
                  disabled={!messageId}
                  className={`inline-flex h-6 w-5 items-center justify-center transition-colors ${feedbackStatus === 'dislike' ? 'text-rose-600' : 'text-slate-400 hover:text-rose-600'} ${!messageId ? 'cursor-not-allowed opacity-50' : ''}`}
                  title={!messageId ? '回答保存后才能反馈' : feedbackStatus === 'dislike' ? '取消点踩' : feedbackStatus === 'like' ? '改为点踩' : '点踩：记录问题反馈'}
                >
                  <ThumbsDown size={16} />
                </button>
                <button
                  onClick={() => onRegenerate?.(messageId)}
                  disabled={!messageId || isGenerating}
                  className={`inline-flex h-6 w-5 items-center justify-center transition-colors ${isGenerating ? 'text-sky-600 cursor-wait' : 'text-slate-400 hover:text-sky-600'} ${!messageId ? 'cursor-not-allowed opacity-50' : ''}`}
                  title={!messageId ? '回答保存后才能重新生成' : '重新生成这条回复'}
                >
                  <RefreshCw size={16} className={isGenerating ? 'animate-spin' : ''} />
                </button>
                {isReviewing && (
                  <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
                    <ShieldCheck size={11} /> 审核中
                  </span>
                )}
              </>
            )}
            <button 
              type="button"
              onClick={handleCopy}
              className={`inline-flex h-6 items-center gap-1 text-xs transition-colors ${
                isCopied 
                  ? 'text-emerald-600' 
                  : 'text-slate-400 hover:text-indigo-600'
              }`}
            >
              {isCopied ? <Check size={16} /> : <Copy size={16} />} 
              {isCopied ? '已复制' : '复制'}
            </button>
          </div>
        </div>
      </div>
    </article>
  );
}, (prevProps, nextProps) => {
  return (
    prevProps.content === nextProps.content &&
    prevProps.timestamp === nextProps.timestamp &&
    prevProps.role === nextProps.role &&
    prevProps.isGenerating === nextProps.isGenerating &&
    prevProps.messageId === nextProps.messageId &&
    prevProps.feedbackStatus === nextProps.feedbackStatus &&
    prevProps.isReviewing === nextProps.isReviewing &&
    prevProps.onRegenerate === nextProps.onRegenerate &&
    prevProps.onAction === nextProps.onAction &&
    JSON.stringify(prevProps.branch) === JSON.stringify(nextProps.branch) &&
    JSON.stringify(prevProps.actions) === JSON.stringify(nextProps.actions) &&
    JSON.stringify(prevProps.traceEvents) === JSON.stringify(nextProps.traceEvents) &&
    JSON.stringify(prevProps.files) === JSON.stringify(nextProps.files)
  );
});

// --- Retrieval Sidebar ---
const RetrievalSidebar = ({ isOpen, onClose, refs, query, knowledge }) => {
  const [selectedRef, setSelectedRef] = useState(null);
  const knowledgeRounds = deduplicateKnowledgeRetrievals(knowledge?.retrievals || []);

  const visibleSelectedRef = isOpen ? selectedRef : null;

  const handleClose = () => {
    setSelectedRef(null);
    onClose();
  };

  // 知识库管理智能体的检索证据映射为引用卡片，复用来源列表/详情视图。
  // 本地教材（无 source_url）映射为知识库卡片（type:'rag'），带原始链接的
  // 外部来源（视频/论文/网页）映射为网页卡片（type:'web'）。
  const knowledgeRefs = knowledgeRounds.flatMap(kr =>
    (kr.evidence_items || []).map(item => {
      const lines = String(item.content_summary || item.content || '').split('\n');
      const isTextbook = !item.source_url;
      return {
        type: isTextbook ? 'rag' : 'web',
        title: item.source_label || (isTextbook ? formatTextbookSourceTitle(item.source_id) : lines[0]) || item.source_id || '未命名来源',
        content: item.content_summary || item.content || '',
        score: typeof item.confidence === 'number' ? item.confidence : undefined,
        url: item.source_url,
        authority: item.authority,
        resourceType: item.resource_type,
      };
    }),
  );
  const knowledgeSummaries = knowledge?.summaries || [];
  const allRefs = [...knowledgeRefs, ...(Array.isArray(refs) ? refs : [])];

  return (
    <div
      aria-hidden={isOpen ? undefined : true}
      inert={isOpen ? undefined : true}
      className={`
        fixed inset-y-0 right-0 z-40 flex w-[400px] max-w-[92vw] transform flex-col border-l border-gray-200 bg-white shadow-2xl transition-transform ease-out
        ${isOpen ? 'translate-x-0 duration-[220ms]' : 'pointer-events-none translate-x-full duration-160'}
      `}
    >
      {visibleSelectedRef ? (
        // --- Detail View ---
        <div className="flex flex-col h-full bg-white animate-in slide-in-from-right duration-200 fade-in">
           <div className="h-14 flex items-center gap-3 px-4 border-b border-gray-100 bg-gray-50/80 backdrop-blur-sm shrink-0">
             <button 
               onClick={() => setSelectedRef(null)} 
               className="p-2 -ml-2 rounded-full hover:bg-white hover:shadow-sm hover:text-indigo-600 transition-[color,background-color,box-shadow] text-gray-500"
               title="返回列表"
             >
               <ArrowLeft size={18} />
             </button>
             <span className="font-semibold text-gray-700">来源详情</span>
           </div>

           <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
              <div className="flex items-center gap-3 mb-4">
                 <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 border ${selectedRef.type === 'rag' ? 'bg-orange-50 border-orange-100 text-orange-500' : 'bg-blue-50 border-blue-100 text-blue-600'}`}>
                    {selectedRef.type === 'rag' ? <BookOpen size={20} /> : <Globe size={20} />}
                 </div>
                 <div className="flex-1 min-w-0">
                    <h3 className="font-bold text-gray-800 leading-tight break-words">{selectedRef.title || '未命名来源'}</h3>
                    <div className="flex items-center gap-2 mt-1">
                      <span className="text-xs text-gray-500 font-mono bg-gray-100 px-1.5 rounded">{selectedRef.type === 'rag' ? '知识库' : getDomain(selectedRef.url)}</span>
                      {selectedRef.score && <span className="text-xs text-indigo-600 font-medium">相似度: {(selectedRef.score * 100).toFixed(1)}%</span>}
                    </div>
                 </div>
              </div>

              <div className="prose prose-sm prose-indigo max-w-none">
                 <div className="bg-gray-50 p-4 rounded-xl border border-gray-100 text-gray-700 leading-relaxed whitespace-pre-wrap font-sans text-sm">
                   {selectedRef.content}
                 </div>
              </div>
              
              {selectedRef.type !== 'rag' && isExternalUrl(selectedRef.url) && (
                <a 
                  href={selectedRef.url} 
                  target="_blank" 
                  rel="noopener noreferrer" 
                  className="mt-6 flex items-center justify-center gap-2 w-full py-3 bg-white border border-gray-200 shadow-sm rounded-xl text-sm font-medium text-gray-600 hover:text-indigo-600 hover:border-indigo-200 hover:shadow-md transition-[color,border-color,box-shadow]"
                >
                  访问原始网页 <ExternalLink size={14} />
                </a>
              )}
           </div>
        </div>
      ) : (
        // --- List View ---
        <div className="flex flex-col h-full">
          <div className="h-16 flex items-center justify-between px-5 border-b border-gray-100 bg-gray-50/50 shrink-0">
             <div className="flex items-center gap-2 font-semibold text-gray-700">
               <Library size={18} className="text-indigo-600" />
               <span>检索详情</span>
             </div>
             <button onClick={handleClose} aria-label="关闭检索详情" className="p-2 hover:bg-gray-200/50 rounded-full text-gray-400 hover:text-gray-700 transition-colors">
               <X size={18} />
             </button>
          </div>

          <div className="flex-1 overflow-y-auto p-5 custom-scrollbar">
             {query && (
               <div className="mb-6 animate-in fade-in slide-in-from-top-2 duration-200">
                  <div className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-2 flex items-center gap-1"><Search size={12}/> 检索语句</div>
                  <div className="p-3 bg-indigo-50 rounded-lg border border-indigo-100 text-sm text-indigo-900 font-medium leading-relaxed">
                     “{query}”
                  </div>
               </div>
             )}

             {knowledgeRounds.length > 0 && (
               <div className="mb-6 space-y-4">
                 <div className="flex items-center gap-2 font-semibold text-gray-700">
                   <Library size={16} className="text-indigo-500" />
                   <span>参考来源</span>
                   <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-indigo-50 text-indigo-600 border border-indigo-100">{knowledgeRefs.length} 条</span>
                 </div>

                 {knowledgeRounds.map((kr, idx) => {
                   const roundItems = (kr.evidence_items || []).map(item => ({
                     ...item,
                     isTextbook: !item.source_url,
                   }));
                   const roundQueries = [
                     ...(kr.kp_query ? [{ label: '知识点检索语句', value: kr.kp_query }] : []),
                     ...(kr.question_query ? [{ label: '题目检索语句', value: kr.question_query }] : []),
                   ];
                   if (roundItems.length === 0 && roundQueries.length === 0) return null;
                   return (
                     <div key={idx} className="rounded-xl border border-blue-100 bg-blue-50/40 p-3 animate-in fade-in slide-in-from-bottom-2 duration-200">
                       <div className="text-[10px] font-bold text-blue-400 uppercase tracking-wider mb-2">第 {idx + 1} 轮检索</div>
                       {roundQueries.length > 0 && (
                         <div className="mb-2 space-y-1">
                           {roundQueries.map((queryItem, qi) => (
                             <div key={qi} className="flex items-start gap-1.5">
                               <span className="shrink-0 mt-px inline-flex items-center gap-0.5 text-[10px] font-semibold text-indigo-500 bg-white border border-indigo-100 rounded px-1 py-0.5">
                                 <Search size={9} /> {queryItem.label}
                               </span>
                               <span className="text-xs text-indigo-900 font-medium leading-snug break-words">“{queryItem.value}”</span>
                             </div>
                           ))}
                         </div>
                       )}
                       {roundItems.map((item, i) => {
                         const lines = String(item.content_summary || item.content || '').split('\n');
                         const title = item.source_label || (item.isTextbook ? formatTextbookSourceTitle(item.source_id) : lines[0]) || item.source_id || '未命名来源';
                         const summary = lines.slice(1).join('\n').trim() || item.content_summary || item.content || '';
                         return (
                           <div key={i} className={`${i > 0 ? 'mt-2 pt-2 border-t border-blue-100' : ''}`}>
                             <div className="flex items-center gap-1.5">
                               {item.isTextbook && (
                                 <span className="inline-flex items-center gap-0.5 text-[10px] font-semibold text-orange-500 bg-orange-50 border border-orange-100 rounded px-1 py-0.5">
                                   <BookOpen size={10} /> 教材
                                 </span>
                               )}
                               <div className="text-xs font-semibold text-gray-800 leading-snug break-words">{title}</div>
                             </div>
                             {summary && (
                               <div className="mt-1 text-xs text-gray-600 leading-relaxed line-clamp-3 whitespace-pre-wrap">{summary}</div>
                             )}
                             {isExternalUrl(item.source_url) && (
                               <a
                                 href={item.source_url}
                                 target="_blank"
                                 rel="noopener noreferrer"
                                 className="mt-2 inline-flex items-center gap-1.5 text-xs font-semibold text-blue-600 hover:text-blue-700 hover:underline"
                                 title="打开原始网页"
                               >
                                 访问原始网页 <ExternalLink size={12} />
                               </a>
                             )}
                           </div>
                         );
                       })}
                     </div>
                   );
                 })}

                 {knowledgeSummaries.length > 0 && (
                   <div className="rounded-xl border border-emerald-100 bg-emerald-50/40 p-3 animate-in fade-in slide-in-from-bottom-2 duration-200">
                     <div className="text-[10px] font-bold text-emerald-500 uppercase tracking-wider mb-2 flex items-center gap-1">
                       <Sparkles size={11} /> 检索总结（知识库智能体提炼）
                     </div>
                     {knowledgeSummaries.map((summary, idx) => (
                       <div key={idx} className={`text-xs text-gray-700 leading-relaxed whitespace-pre-wrap ${idx > 0 ? 'mt-2 pt-2 border-t border-emerald-100' : ''}`}>
                         {summary}
                       </div>
                     ))}
                   </div>
                 )}
               </div>
             )}

             <div>
                <div className="text-xs font-bold text-gray-400 uppercase tracking-wider mb-3 flex items-center justify-between">
                  <span>来源列表 ({allRefs.length})</span>
                </div>
                
                <div className="space-y-3">
                  {allRefs.map((ref, idx) => (
                    <div 
                      key={idx} 
                      onClick={() => setSelectedRef(ref)}
                      className="group bg-white border border-gray-200 rounded-xl p-3 hover:shadow-md hover:border-indigo-300 cursor-pointer transition-[border-color,box-shadow] duration-200 animate-in fade-in slide-in-from-bottom-2"
                      style={{ animationDelay: `${idx * 50}ms` }}
                    >
                       <div className="flex items-start gap-3 mb-2">
                          <div className={`w-8 h-8 rounded-lg shrink-0 flex items-center justify-center border transition-colors ${ref.type === 'rag' ? 'bg-orange-50 border-orange-100 text-orange-500 group-hover:bg-orange-100' : 'bg-blue-50 border-blue-100 text-blue-600 group-hover:bg-blue-100'}`}>
                             {ref.type === 'rag' ? <BookOpen size={16} /> : <Globe size={16} />}
                          </div>
                          <div className="flex-1 min-w-0">
                             <div className="flex items-center justify-between">
                                <span className={`block text-sm font-semibold text-gray-800 truncate group-hover:text-indigo-600 transition-colors`}>
                                  {ref.title || '未命名来源'}
                                </span>
                                <ChevronRight size={14} className="text-gray-300 group-hover:text-indigo-400 transition-colors transform group-hover:translate-x-0.5" />
                             </div>
                             <div className="flex items-center gap-2 mt-0.5">
                               <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 font-medium">{ref.type === 'rag' ? '知识库' : getDomain(ref.url)}</span>
                               {ref.score && <span className="text-[10px] text-gray-400">相似度: {(ref.score * 100).toFixed(0)}%</span>}
                             </div>
                          </div>
                       </div>
                       <div className="text-xs text-gray-600 leading-relaxed line-clamp-3 bg-gray-50 p-2 rounded-lg border border-gray-100/50 group-hover:bg-white group-hover:border-indigo-100 transition-colors">
                          {ref.content}
                       </div>
                    </div>
                  ))}
                  {allRefs.length === 0 && (
                     <div className="text-center py-10 text-gray-400 text-sm">暂无引用内容</div>
                  )}
                </div>
             </div>
          </div>
        </div>
      )}
    </div>
  );
};

// --- Main Chat Logic Component ---

const CHAT_STORAGE_KEYS = {
  draftInput: 'health_chat_draft_input',
  toolsEnabled: 'health_chat_tools_enabled',
};

const DEFAULT_SESSION_TITLES = new Set(['', '新对话', 'New Chat']);

const isDefaultSessionTitle = (title = '') => DEFAULT_SESSION_TITLES.has(String(title || '').trim());
const PENDING_RUNS_STORAGE_KEY = 'assistantPendingWorkflowRuns';

const readPendingRuns = () => {
  try {
    const value = JSON.parse(localStorage.getItem(PENDING_RUNS_STORAGE_KEY) || '{}');
    return value && typeof value === 'object' ? value : {};
  } catch {
    return {};
  }
};

const ChatInterface = ({ currentUser, currentUserRole = 'user', onLogout, onBackHome, onOpenKnowledge, onOpenPersonalization, onOpenAdminFeedback, onNavigate, preferredSessionId = null, initialContext = '', embedded = false, forceNewConversation = false, onNewConversationConsumed }) => {
  const shellConfig = getAppShellConfig({
    currentPage: 'assistant',
    selectedSessionId: preferredSessionId,
  });
  const [sessions, setSessions] = useState([]);
  const [currentSessionId, setCurrentSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [activeSessionRuns, setActiveSessionRuns] = useState({});
  const [input, setInput] = useState(() => localStorage.getItem(CHAT_STORAGE_KEYS.draftInput) || initialContext || '');
  const [isLoading, setIsLoading] = useState(false);
  const [loadingSessionId, setLoadingSessionId] = useState(null);
  const newConversationStartedRef = useRef(false);
  
  // --- Auto Scroll ---
  const [autoScroll, setAutoScroll] = useState(true);
  const [showScrollButton, setShowScrollButton] = useState(false);
  const scrollContainerRef = useRef(null);
  const virtuosoRef = useRef(null);

  // --- Voice ---
  const [isRecording, setIsRecording] = useState(false);
  const [isProcessingVoice, setIsProcessingVoice] = useState(false);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  // --- Layout State ---
  const [isSidebarOpen, setIsSidebarOpen] = useState(() => (typeof window === 'undefined' ? true : window.innerWidth >= 768));
  const [editingSessionId, setEditingSessionId] = useState(null);
  const [editTitle, setEditTitle] = useState('');
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isUserMenuOpen, setIsUserMenuOpen] = useState(false);
  const [isToolMenuOpen, setIsToolMenuOpen] = useState(false);
  
  const [uploadedFiles, setUploadedFiles] = useState([]); 
  const [isUploading, setIsUploading] = useState(false);
  const [isDragging, setIsDragging] = useState(false);

  // --- Tool Calling State ---
  const [isToolsEnabled] = useState(() => localStorage.getItem(CHAT_STORAGE_KEYS.toolsEnabled) === 'true');
  const isWebSearchEnabled = isToolsEnabled;
  const isRagEnabled = isToolsEnabled;
  const [feedbackDialog, setFeedbackDialog] = useState({ isOpen: false, type: '', answer: '', messageId: null, reason: '', status: 'idle' });
  
  // --- Right Sidebar State ---
  const [isRightSidebarOpen, setIsRightSidebarOpen] = useState(false);
  const [rightSidebarContent, setRightSidebarContent] = useState({ refs: [], query: '' });
  const [messageBranches, setMessageBranches] = useState({});
  const resetWorkflow = useLangGraphStore(s => s.resetWorkflow);
  const dispatchGraphEvent = useLangGraphStore(s => s.dispatchEvent);
  const dispatchGraphEvents = useLangGraphStore(s => s.dispatchEvents);
  const appendGraphAnswer = useLangGraphStore(s => s.appendAnswer);
  const setGraphReferences = useLangGraphStore(s => s.setReferences);
  const markNetworkInterrupted = useLangGraphStore(s => s.markNetworkInterrupted);

  const resetSessionScopedView = () => {
    setIsRightSidebarOpen(false);
    setRightSidebarContent({ refs: [], query: '' });
    setMessageBranches({});
    setFeedbackDialog({ isOpen: false, type: '', answer: '', messageId: null, reason: '', status: 'idle' });
    resetWorkflow();
  };
  
  const dragCounter = useRef(0);
  const fileInputRef = useRef(null);
  const messagesEndRef = useRef(null);
  const textareaRef = useRef(null);
  const menuRef = useRef(null);
  const userMenuRef = useRef(null);
  const toolMenuRef = useRef(null);
  const abortControllerRef = useRef(null);
  const workflowControllersRef = useRef({});
  // A newly generated run ID is persisted before the streaming request has
  // necessarily reached the backend registry.  Session-switch effects must
  // not poll that ID during this short local startup window, otherwise every
  // new turn produces a misleading GET 404 before POST /stream registers it.
  const locallyStartingWorkflowRunsRef = useRef(new Set());
  const sessionCreationPromiseRef = useRef(null);
  const inputContainerRef = useRef(null);
  const currentSessionIdRef = useRef(currentSessionId);
  const liveSessionCacheRef = useRef({});
  const sessionMessageCacheRef = useRef({});
  const isCurrentSessionLoading = Boolean(currentSessionId && activeSessionRuns[currentSessionId])
    || (isLoading && loadingSessionId === currentSessionId);
  const currentAssistantMessage = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      if (messages[i]?.role === 'assistant') return messages[i];
    }
    return null;
  }, [messages]);
  const currentAssistantHasExecutionDone = useMemo(
    () => hasExecutionDoneTrace(currentAssistantMessage),
    [currentAssistantMessage]
  );
  const isAnswerStreamingCurrentSession = isCurrentSessionLoading && !!currentAssistantMessage && !currentAssistantHasExecutionDone;
  const isReviewingCurrentSession = isCurrentSessionLoading && !!currentAssistantMessage && currentAssistantHasExecutionDone;

  useEffect(() => {
    currentSessionIdRef.current = currentSessionId;
  }, [currentSessionId]);

  // 🔥 Initialize: Load sessions and restore last active session ID
  useEffect(() => { 
    const loadSessions = async () => {
        await fetchSessions({ skipRestore: forceNewConversation });
        if (forceNewConversation && !newConversationStartedRef.current) {
          newConversationStartedRef.current = true;
          try {
            await createSession();
          } finally {
            onNewConversationConsumed?.();
          }
        }
    };
    loadSessions();
  }, []);

  useEffect(() => {
    const reloadExamWorkspace = async () => {
      Object.values(workflowControllersRef.current).forEach(controller => controller?.abort());
      workflowControllersRef.current = {};
      locallyStartingWorkflowRunsRef.current.clear();
      abortControllerRef.current?.abort();
      liveSessionCacheRef.current = {};
      sessionMessageCacheRef.current = {};
      currentSessionIdRef.current = null;
      resetSessionScopedView();
      setActiveSessionRuns({});
      setCurrentSessionId(null);
      setMessages([]);
      setUploadedFiles([]);
      await fetchSessions();
    };
    window.addEventListener('shizhen:learning-target-changed', reloadExamWorkspace);
    return () => window.removeEventListener('shizhen:learning-target-changed', reloadExamWorkspace);
  }, []);

  useEffect(() => {
    const loadCurrentSession = async () => {
      if (currentSessionId) { 
        const liveCache = liveSessionCacheRef.current[currentSessionId];
        if (liveCache?.messages) {
          setMessages(liveCache.messages);
          setAutoScroll(true);
          // 2026-08-16: live 缓存可能缺最终回复（SSE 中断时被截断）；
          // 后台强制拉一次最新落库结果，命中时 fetchMessages 会直接
          // 更新界面（restorePendingRun 随后还会针对进行中/刚完成的
          // run 做重试拉取）。
          await fetchMessages(currentSessionId, { force: true });
        } else if (sessionMessageCacheRef.current[currentSessionId]) {
          setMessages(sessionMessageCacheRef.current[currentSessionId]);
          setAutoScroll(true);
          await fetchMessages(currentSessionId, { force: true });
        } else {
          await fetchMessages(currentSessionId);
        }
        // 🔥 Persist current session ID
        localStorage.setItem('lastSessionId', currentSessionId);
      } else { 
        setMessages([]); 
        setUploadedFiles([]); 
      }

      if (currentSessionId) void restorePendingRun(currentSessionId);

    };

    loadCurrentSession();
  }, [currentSessionId]);

  useEffect(() => {
    // 仅在非流式场景（会话切换/历史恢复）时补一次定位：流式输出期间由
    // Virtuoso 的 followOutput 负责跟随，这里再跳底会与平滑滚动打架，
    // 造成“一输出就强制拉到最底部”。
    if (!isCurrentSessionLoading && autoScroll) {
      if (virtuosoRef.current) {
        virtuosoRef.current.scrollToIndex({ index: 'LAST', align: 'end', behavior: 'auto' });
      } else {
        scrollToBottom();
      }
    }
  }, [messages, isLoading, autoScroll, isCurrentSessionLoading]);

  useEffect(() => {
    localStorage.setItem(CHAT_STORAGE_KEYS.draftInput, input);
  }, [input]);

  useEffect(() => {
    localStorage.setItem(CHAT_STORAGE_KEYS.toolsEnabled, String(isToolsEnabled));
  }, [isToolsEnabled]);

  // Click outside menu
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (menuRef.current && !menuRef.current.contains(event.target)) {
        setIsMenuOpen(false);
      }
      if (userMenuRef.current && !userMenuRef.current.contains(event.target)) {
        setIsUserMenuOpen(false);
      }
      if (toolMenuRef.current && !toolMenuRef.current.contains(event.target)) {
        setIsToolMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Textarea resizing
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      textarea.style.height = 'auto'; 
      textarea.style.height = `${textarea.scrollHeight}px`;
    }
  }, [input, uploadedFiles]);

  const scrollToBottom = () => {
    if (virtuosoRef.current) {
      virtuosoRef.current.scrollToIndex({ index: 'LAST', align: 'end', behavior: 'smooth' });
      return;
    }
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  };

  const handleAtBottomChange = (atBottom) => {
    setAutoScroll(atBottom);
    setShowScrollButton(!atBottom);
  };

  const fetchSessions = async ({ skipRestore = false } = {}) => {
    try {
      const res = await fetchWithAuth(`${MAIN_API_BASE}/conversations`);
      if (!res.ok) return [];
      const data = await res.json();
      setSessions(data);
      if (skipRestore) return data;
      
      // 🔥 Restore last session if exists
      const savedId = localStorage.getItem('lastSessionId');
      const restoredId = resolveAssistantSessionId(data, preferredSessionId, savedId);
      if (restoredId && currentSessionIdRef.current !== restoredId) {
          resetSessionScopedView();
          currentSessionIdRef.current = restoredId;
          setCurrentSessionId(restoredId);
      }
      return data;
    } catch (e) { console.error(e); }
    return [];
  };

  const refreshSessionTitleUntilSettled = async (sessionId, options = {}) => {
    const { maxAttempts = 24, initialDelay = 700 } = options;
    for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
      const delay = Math.min(initialDelay + attempt * 150, 1800);
      await new Promise(resolve => window.setTimeout(resolve, delay));
      // Refresh list metadata only. Re-applying preferredSessionId here would
      // pull the UI back to the route's original conversation after the user
      // has explicitly created or selected another one.
      const latestSessions = await fetchSessions({ skipRestore: true });
      const target = latestSessions.find(session => session.id === sessionId);
      if (target && !isDefaultSessionTitle(target.title)) return target.title;
    }
    return null;
  };

  const createSession = async () => {
    // Clicking “新对话” and immediately pressing Enter used to race React's
    // state update: the send handler still saw the previous session and could
    // resume its interrupted checkpoint.  Keep one synchronous promise/ref
    // barrier so every immediate send waits for and binds to the new session.
    if (sessionCreationPromiseRef.current) {
      return sessionCreationPromiseRef.current;
    }
    const creation = (async () => {
      try {
        const res = await fetchWithAuth(`${MAIN_API_BASE}/conversations`, {
          method: 'POST',
          body: JSON.stringify({ title: "新对话" })
        });
        const newSession = await res.json();
        setSessions(prev => [newSession, ...prev]);
        resetSessionScopedView();
        currentSessionIdRef.current = newSession.id;
        setCurrentSessionId(newSession.id);
        setMessages([]);
        setUploadedFiles([]);
        localStorage.setItem('lastSessionId', newSession.id);
        if (window.innerWidth < 640) setIsSidebarOpen(false);
        return newSession.id;
      } catch (e) {
        console.error(e);
        return null;
      }
    })();
    sessionCreationPromiseRef.current = creation;
    try {
      return await creation;
    } finally {
      if (sessionCreationPromiseRef.current === creation) {
        sessionCreationPromiseRef.current = null;
      }
    }
  };

  const deleteSession = async (e, id) => {
    e.stopPropagation();
    if (!confirm('确定要删除吗？')) return;
    try {
      const res = await fetchWithAuth(`${MAIN_API_BASE}/conversations/${id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('删除失败');
      setSessions(prev => prev.filter(s => s.id !== id));
      if (currentSessionId === id) { 
          resetSessionScopedView();
          currentSessionIdRef.current = null;
          setCurrentSessionId(null); 
          setMessages([]); 
          setUploadedFiles([]); 
          localStorage.removeItem('lastSessionId'); // Clean persistence
      }
      fetchSessions({ skipRestore: currentSessionId !== id });
    } catch (e) { console.error(e); alert('删除失败，请稍后重试'); }
  };

  const updateSessionTitle = async (e, id) => {
    e.stopPropagation();
    if (!editTitle.trim()) return;
    try {
      await fetchWithAuth(`${MAIN_API_BASE}/conversations/${id}`, {
        method: 'PATCH',
        body: JSON.stringify({ title: editTitle })
      });
      setSessions(sessions.map(s => s.id === id ? { ...s, title: editTitle } : s));
      setEditingSessionId(null);
    } catch (e) { console.error(e); }
  };

  const fetchMessages = async (id, options = {}) => {
    const { force = false } = options;
    const liveCache = liveSessionCacheRef.current[id];
    if (!force && liveCache?.messages) {
      if (currentSessionIdRef.current === id) {
        setMessages(liveCache.messages);
        setAutoScroll(true);
      }
      return liveCache.messages;
    }
    if (!force && sessionMessageCacheRef.current[id]) {
      const cachedMessages = sessionMessageCacheRef.current[id];
      if (currentSessionIdRef.current === id) {
        setMessages(cachedMessages);
        setAutoScroll(true);
      }
      return cachedMessages;
    }
    try {
      const res = await fetchWithAuth(`${MAIN_API_BASE}/conversations/${id}/messages`);
      if (!res.ok) return [];
      const data = await res.json();
      const branches = {};
      data.forEach(msg => {
        if (msg?.branch) branches[msg.id] = msg.branch;
      });
      setMessageBranches(branches);
      const enrichedData = data.map(msg => ({
        ...msg,
        timestamp: msg.timestamp || getCurrentTime(),
        searchQuery: null,
        traceEvents: Array.isArray(msg.trace_events)
          ? msg.trace_events.map(runtimeEventToTrace).filter(Boolean)
          : [],
      }));
      sessionMessageCacheRef.current[id] = enrichedData;
      if (currentSessionIdRef.current === id) {
        setMessages(enrichedData);
        setAutoScroll(true);
      }
      return enrichedData;
    } catch (e) { console.error(e); return []; }
  };

  const fetchMessagesUntilSaved = async (id, expectedAssistantCount, attempts = 6) => {
    let latest = [];
    for (let i = 0; i < attempts; i += 1) {
      latest = await fetchMessages(id, { force: true });
      const assistantCount = latest.filter(msg => msg.role === 'assistant' && msg.id).length;
      if (assistantCount >= expectedAssistantCount) return latest;
      await new Promise(resolve => setTimeout(resolve, 300));
    }
    // 超时仍未等到：后端可能因重复失败回执去重（旧固定 message_id）而没
    // 新增 assistant 消息。此时本地 live cache 可能已显示失败气泡；若
    // 后端消息数少于本地，保留本地（force 拉取是在 fetchMessages 内
    // setMessages 的，但这里可以恢复 live cache 到 UI）。
    const live = liveSessionCacheRef.current[id];
    if (live?.messages && latest.length < live.messages.length
      && currentSessionIdRef.current === id) {
      setMessages(live.messages);
      setAutoScroll(true);
    }
    return latest;
  };

  const startSessionSwitch = (session) => {
    if (!session || session.id === currentSessionId) return;
    const cachedMessages = liveSessionCacheRef.current[session.id]?.messages || sessionMessageCacheRef.current[session.id];
    if (cachedMessages) setMessages(cachedMessages);
    resetSessionScopedView();
    currentSessionIdRef.current = session.id;
    setCurrentSessionId(session.id);
    if (window.innerWidth < 640) setIsSidebarOpen(false);
  };

  const processFiles = async (files) => {
    if (!files || files.length === 0) return;
    setIsUploading(true);
    // 🔥 Added image extensions
    const allowedExts = ['.txt', '.json', '.md', '.docx', '.py', '.js', '.csv', '.jpg', '.jpeg', '.png', '.bmp', '.webp', '.tiff'];
    
    for (const file of files) {
      const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
      if (!allowedExts.includes(ext)) {
        alert(`不支持的文件类型: ${file.name}`);
        continue;
      }
      const formData = new FormData();
      formData.append('file', file);
      try {
        const res = await fetchWithAuth(`${API_BASE}/upload`, { method: 'POST', body: formData });
        if (!res.ok) throw new Error("Upload failed");
        const data = await res.json();
        setUploadedFiles(prev => [...prev, { id: data.file_id, name: data.filename }]);
      } catch (error) { console.error(error); alert(`上传 ${file.name} 失败`); }
    }
    setIsUploading(false);
    dragCounter.current = 0;
    setIsDragging(false);
  };

  const toggleRecording = async () => {
    if (isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      setIsProcessingVoice(true);
    } else {
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        mediaRecorderRef.current = new MediaRecorder(stream);
        audioChunksRef.current = [];

        mediaRecorderRef.current.ondataavailable = (event) => {
          if (event.data.size > 0) {
            audioChunksRef.current.push(event.data);
          }
        };

        mediaRecorderRef.current.onstop = async () => {
          const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
          const formData = new FormData();
          formData.append('file', audioBlob, 'recording.webm');

          try {
            const res = await fetchWithAuth(`${API_BASE}/voice/transcribe`, {
              method: 'POST',
              body: formData
            });
            
            if (res.ok) {
              const data = await res.json();
              if (data.text) {
                setInput(prev => {
                    const separator = prev.trim() ? " " : "";
                    return prev + separator + data.text;
                });
              }
            } else {
                console.error("Transcription failed");
            }
          } catch (error) {
            console.error("Error sending audio:", error);
            alert("语音识别失败");
          } finally {
            setIsProcessingVoice(false);
            stream.getTracks().forEach(track => track.stop());
          }
        };

        mediaRecorderRef.current.start();
        setIsRecording(true);
      } catch (err) {
        console.error("Error accessing microphone:", err);
        alert("无法访问麦克风，请检查权限。");
      }
    }
  };

  const handleFileSelect = (e) => {
    processFiles(Array.from(e.target.files));
    e.target.value = null; 
  };

  const handleDragEnter = (e) => {
    e.preventDefault(); e.stopPropagation();
    dragCounter.current += 1;
    if (e.dataTransfer.items?.length > 0) setIsDragging(true);
  };
  const handleDragLeave = (e) => {
    e.preventDefault(); e.stopPropagation();
    dragCounter.current -= 1;
    if (dragCounter.current === 0) setIsDragging(false);
  };
  const handleDragOver = (e) => { e.preventDefault(); e.stopPropagation(); };
  const handleDrop = (e) => {
    e.preventDefault(); e.stopPropagation();
    setIsDragging(false); dragCounter.current = 0;
    if (e.dataTransfer.files?.length > 0) {
      processFiles(Array.from(e.dataTransfer.files));
      e.dataTransfer.clearData();
    }
  };
  const removeFile = (id) => setUploadedFiles(prev => prev.filter(f => f.id !== id));

  const exportSession = () => {
    if (!currentSessionId || messages.length === 0) return;
    const sessionTitle = sessions.find(s => s.id === currentSessionId)?.title || "对话";
    let content = `# ${sessionTitle}\n\n`;
    messages.forEach(msg => {
      const role = msg.role === 'user' ? 'User' : 'Assistant';
      const time = msg.timestamp || '';
      content += `### ${role} (${time})\n${msg.content.replace(/<<REFS:.*?>>/, '')}\n\n`;
    });
    const blob = new Blob([content], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = `${sessionTitle}.md`;
    document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
    setIsMenuOpen(false);
  };

  const handleStop = async () => {
    const sessionId = currentSessionIdRef.current || currentSessionId;
    const runId = activeSessionRuns[sessionId] || readPendingRuns()[sessionId];
    const controller = workflowControllersRef.current[sessionId]
      || abortControllerRef.current;
    if (!runId) {
      controller?.abort();
      return;
    }
    try {
      const cancellation = await cancelWorkflowRun(runId);
      if (cancellation.status === 'cancelled') {
        controller?.abort();
        rememberPendingRun(sessionId, null);
        markSessionRunActive(sessionId, null);
      }
      // For cancellation_requested keep the SSE attached. The server emits
      // run_cancelled only after the worker task has actually stopped.
    } catch (error) {
      console.error('cancel workflow run failed', error);
    }
  };

  const handleInspectKnowledge = (knowledge) => {
      setRightSidebarContent({ refs: [], query: null, knowledge });
      setIsRightSidebarOpen(true);
  };

  const markMessageFeedback = (messageId, status) => {
    if (!messageId) return;
    setMessages(prev => prev.map(msg => (
      msg.id === messageId ? { ...msg, feedback_status: status, feedbackStatus: status } : msg
    )));
  };

  const handleSwitchBranch = async (direction, messageId, branch) => {
    if (!currentSessionId || !branch) return;
    const targetId = direction === 'prev' ? branch.prev_id : branch.next_id;
    if (!targetId) return;
    try {
      const res = await fetchWithAuth(`${API_BASE}/sessions/${currentSessionId}/branch`, {
        method: 'POST',
        body: JSON.stringify({ message_id: targetId })
      });
      if (!res.ok) throw new Error('switch branch failed');
      await fetchMessages(currentSessionId, { force: true });
    } catch (e) {
      console.error(e);
    }
  };

  const getTurnQuestionForAssistant = (messageId) => {
    const idx = messages.findIndex(msg => msg.id === messageId);
    if (idx <= 0) return '';
    for (let i = idx - 1; i >= 0; i -= 1) {
      if (messages[i]?.role === 'user') return messages[i].content || '';
    }
    return '';
  };

  const submitFeedback = async ({ type, answer, messageId, reason = '' }) => {
    try {
      const question = getTurnQuestionForAssistant(messageId);
      const res = await fetchWithAuth(`${API_BASE}/feedback`, {
        method: 'POST',
        body: JSON.stringify({
          session_id: currentSessionId,
          message_id: messageId,
          feedback_type: type,
          reason,
          question,
          answer,
          metadata: { source: 'chat_bubble' }
        })
      });
      if (!res.ok) throw new Error('feedback request failed');
      const data = await res.json().catch(() => ({}));
      const nextStatus = Object.prototype.hasOwnProperty.call(data, 'feedback_status') ? data.feedback_status : type;
      if (nextStatus === 'like' || nextStatus === 'dislike') {
        // Governance ingestion is additive and best-effort. The established
        // chat feedback endpoint remains authoritative for the UI, so a
        // disabled evolution module can never break learner feedback.
        fetchWithAuth(`${MAIN_API_BASE}/evolution/feedback`, {
          method: 'POST',
          body: JSON.stringify({
            feedback_type: nextStatus,
            issue_type: nextStatus === 'dislike' ? 'other' : null,
            comment: reason,
            conversation_id: currentSessionId,
            message_id: String(messageId),
            task_type: 'general_learning_support',
          }),
        }).catch(() => {});
      }
      markMessageFeedback(messageId, nextStatus);
      setFeedbackDialog({ isOpen: false, type: '', answer: '', messageId: null, reason: '', status: 'idle' });
    } catch (e) {
      console.error('feedback failed', e);
      setFeedbackDialog(prev => ({ ...prev, status: 'error' }));
    }
  };

  const handleFeedback = async (type, answer, messageId, currentStatus = null) => {
    if (!messageId) return;
    if (type === 'dislike' && currentStatus !== 'dislike') {
      setFeedbackDialog({ isOpen: true, type, answer, messageId, reason: '', status: 'idle' });
      return;
    }
    await submitFeedback({ type, answer, messageId });
  };

  const finalizeInterruptedAssistantContent = (content = '', label = '生成已中断') => {
    const text = String(content || '');
    if (!text.trim() || text.trim() === '<think>') {
      return `*（${label}，尚未收到可展示内容）*`;
    }
    if (text.includes('<think>') && !text.includes('</think>')) {
      const withoutOpenThink = text.replace(/<think>\s*$/i, '').trim();
      if (!withoutOpenThink) return `*（${label}，尚未收到可展示内容）*`;
      return `${text}\n</think>\n\n*（${label}）*`;
    }
    return `${text}\n\n*（${label}）*`;
  };

  const handleRegenerate = async (messageId) => {
    if (!currentSessionId || !messageId || isCurrentSessionLoading) return;
    if (import.meta.env.VITE_USE_LEGACY_CHAT !== 'true') {
      const question = getTurnQuestionForAssistant(messageId);
      if (question) await handleMainWorkflowSend(question);
      return;
    }
    const sessionId = currentSessionId;
    const targetIndex = messages.findIndex(msg => msg.id === messageId);
    if (targetIndex <= 0) return;
    const sourceUserIndex = targetIndex - 1;
    const tempBranchId = `regen-${messageId}-${Date.now()}`;
    const branchBaseMessages = [
      ...messages.slice(0, sourceUserIndex + 1),
      {
        id: tempBranchId,
        role: 'assistant',
        content: '',
        files: [],
        timestamp: getCurrentTime(),
        isPlaceholder: true,
        isRegenerating: true,
        searchQuery: null,
        branch: null,
      }
    ];
    liveSessionCacheRef.current[sessionId] = { messages: branchBaseMessages, isRunning: true };
    setMessages(branchBaseMessages);
    setIsLoading(true);
    setLoadingSessionId(sessionId);
    setAutoScroll(true);
    resetWorkflow();

    try {
      const res = await fetchWithAuth(`${API_BASE}/chat/${sessionId}/messages/${messageId}/regenerate`, {
        method: 'POST',
        body: JSON.stringify({
          tools_enabled: isToolsEnabled,
          web_search: isWebSearchEnabled,
          rag_search: isRagEnabled,
        }),
      });
      if (!res.ok) throw new Error('Regenerate request failed');
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let textQueue = '';
      let lastUpdateTime = 0;

      const appendToBranch = (chunk) => {
        const cache = liveSessionCacheRef.current[sessionId] || { messages: branchBaseMessages };
        const updated = (cache.messages || []).map(msg => {
          if (msg.id !== tempBranchId) return msg;
          return { ...msg, isPlaceholder: false, content: (msg.content || '') + chunk };
        });
        liveSessionCacheRef.current[sessionId] = { ...cache, messages: updated, isRunning: true };
        setMessages(updated);
      };

      const rollbackBranchAnswer = () => {
        const cache = liveSessionCacheRef.current[sessionId] || { messages: branchBaseMessages };
        const updated = (cache.messages || []).map(msg => (
          msg.id === tempBranchId ? { ...msg, content: stripAssistantVisibleContent(msg.content || '') } : msg
        ));
        liveSessionCacheRef.current[sessionId] = { ...cache, messages: updated, isRunning: true };
        setMessages(updated);
      };

      const drain = async () => {
        if (!textQueue) return;
        while (textQueue.length > 0) {
          const isProcessTag = textQueue.startsWith('<<PLAN:') || textQueue.startsWith('<<EXEC:') || textQueue.startsWith('<<EV:') || textQueue.startsWith('<<REFS:') || textQueue.startsWith('<<VIDEOS:') || textQueue.startsWith('<think>') || textQueue.startsWith('</think>');
          const chunkSize = isProcessTag ? Math.max(textQueue.indexOf('>>') + 2, textQueue.length) : textQueue.length;
          const chunk = textQueue.slice(0, chunkSize);
          textQueue = textQueue.slice(chunkSize);
          appendToBranch(chunk);
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        while (true) {
          const statusMatch = buffer.match(/<<STATUS:(.*?):(.*?)(?:>>)/);
          const refsMatch = buffer.match(/<<REFS:(.*?)(?:>>)/);
          const videosMatch = buffer.match(/<<VIDEOS:(.*?)(?:>>)/);
          const rollbackMatch = buffer.match(/<<ROLLBACK:(.*?)(?:>>)/);
          const processMatch = buffer.match(/<<(PLAN|EXEC|EV):(.*?)(?:>>)/s);
          let matched = false;
          if (statusMatch) {
            const [fullTag] = statusMatch;
            buffer = buffer.replace(fullTag, '');
            matched = true;
          }
          if (refsMatch) {
            const [fullTag] = refsMatch;
            const idx = buffer.indexOf(fullTag);
            textQueue += buffer.slice(0, idx);
            await drain(true);
            try { setGraphReferences(JSON.parse(refsMatch[1])); } catch (e) { console.error(e); }
            appendToBranch(fullTag);
            buffer = buffer.slice(idx + fullTag.length);
            matched = true;
          }
          if (videosMatch) {
            const [fullTag] = videosMatch;
            const idx = buffer.indexOf(fullTag);
            textQueue += buffer.slice(0, idx);
            await drain(true);
            appendToBranch(fullTag);
            buffer = buffer.slice(idx + fullTag.length);
            matched = true;
          }
          if (rollbackMatch) {
            const [fullTag] = rollbackMatch;
            const idx = buffer.indexOf(fullTag);
            textQueue += buffer.slice(0, idx);
            await drain(true);
            rollbackBranchAnswer();
            buffer = buffer.slice(idx + fullTag.length);
            matched = true;
          }
          if (processMatch) {
            const [fullTag] = processMatch;
            const idx = buffer.indexOf(fullTag);
            textQueue += buffer.slice(0, idx);
            await drain(true);
            if (processMatch[1] === 'EV') {
              try { dispatchGraphEvent(JSON.parse(processMatch[2])); } catch (e) { console.error('event parse failed', e); }
              appendToBranch(fullTag);
            }
            buffer = buffer.slice(idx + fullTag.length);
            matched = true;
          }
          if (!matched) break;
        }
        const tagStartIndex = buffer.indexOf('<<');
        if (tagStartIndex === -1) {
          textQueue += buffer.endsWith('<') ? buffer.slice(0, -1) : buffer;
          buffer = buffer.endsWith('<') ? '<' : '';
        } else if (tagStartIndex > 0) {
          textQueue += buffer.slice(0, tagStartIndex);
          buffer = buffer.slice(tagStartIndex);
        }
        const now = Date.now();
        if (textQueue.length > 0 && now - lastUpdateTime > 50) {
          lastUpdateTime = now;
          await drain(false);
        }
      }
      if (textQueue.length > 0 || buffer.length > 0) {
        const finalContent = textQueue + (buffer.startsWith('<<') ? '' : buffer);
        if (finalContent) {
          textQueue = finalContent;
          buffer = '';
          await drain(true);
        }
      }
      await fetchMessages(sessionId, { force: true });
      fetchSessions({ skipRestore: true });
      void refreshSessionTitleUntilSettled(sessionId);
    } catch (e) {
      console.error(e);
      const cache = liveSessionCacheRef.current[sessionId] || { messages: branchBaseMessages };
      const failedMessages = (cache.messages || []).map(msg => (
        msg.id === tempBranchId
          ? { ...msg, content: finalizeInterruptedAssistantContent(msg.content, `重新生成失败：${e.message || '请求异常'}`), isPlaceholder: false, isRegenerating: false }
          : msg
      ));
      liveSessionCacheRef.current[sessionId] = { messages: failedMessages, isRunning: false };
      setMessages(failedMessages);
    } finally {
      setIsLoading(false);
      setLoadingSessionId(null);
      delete liveSessionCacheRef.current[sessionId];
    }
  };

  const rememberPendingRun = (sessionId, runId) => {
    // Persist synchronously.  A React state effect is too late here: session
    // recovery can run between the terminal SSE event and the next render and
    // would otherwise poll an already-finished local run or resurrect stale
    // pending data.
    const next = { ...readPendingRuns() };
    if (runId) next[sessionId] = runId;
    else delete next[sessionId];
    localStorage.setItem(PENDING_RUNS_STORAGE_KEY, JSON.stringify(next));
  };

  const markSessionRunActive = (sessionId, runId) => {
    if (!sessionId) return;
    setActiveSessionRuns(prev => {
      const next = { ...prev };
      if (runId) next[sessionId] = runId;
      else delete next[sessionId];
      return next;
    });
  };

  async function restorePendingRun(sessionId) {
    const runId = readPendingRuns()[sessionId];
    if (!runId) return;
    if (locallyStartingWorkflowRunsRef.current.has(runId)) return;
    // This browser tab already owns a live SSE subscription for the session.
    // Recovery polling is only for refresh, tab switches, or disconnected
    // streams; polling while the local stream is active creates redundant
    // lookups and a terminal-event race before pending storage is cleared.
    if (workflowControllersRef.current[sessionId]) return;
    try {
      const run = await getWorkflowRunWithStartupGrace(runId);
      // 期望的 assistant 回复数 = 当前可见 user 消息数（每次发送至少
      // 产生一条 assistant 回复）；重试拉取直到数量匹配或超时。
      const existing = liveSessionCacheRef.current[sessionId]?.messages
        || sessionMessageCacheRef.current[sessionId] || [];
      const userCount = existing.filter(m => m.role === 'user').length;
      if (!run) {
        // 后端已无该 run：强制拉一次，避免残留缓存掩盖最新落库结果。
        await fetchMessages(sessionId, { force: true });
        rememberPendingRun(sessionId, null);
        markSessionRunActive(sessionId, null);
        return;
      }
      if (run.status === 'failed') {
        // 2026-08-16: 失败回执已由后端落库为 assistant 消息（含
        // error_code），重试拉取展示，避免刷新后对话“直接没了”。
        await fetchMessagesUntilSaved(sessionId, userCount);
        rememberPendingRun(sessionId, null);
        markSessionRunActive(sessionId, null);
        return;
      }
      if (run.status === 'cancelled') {
        rememberPendingRun(sessionId, null);
        markSessionRunActive(sessionId, null);
        return;
      }
      if (
        run.status === 'completed'
        || run.status === 'interrupted'
        || run.status === 'waiting_human_review'
      ) {
        // 2026-08-16: SSE 断连后 run 状态可能先于消息落库更新（或本地
        // 缓存仍缺最终回复），单次 force 拉取可能拿到旧数据；用重试
        // 拉取等待 assistant 回复落库（最多 6 次 × 300ms）。
        await fetchMessagesUntilSaved(sessionId, userCount);
      }
      if (run.status === 'completed' || run.status === 'waiting_human_review') {
        rememberPendingRun(sessionId, null);
        markSessionRunActive(sessionId, null);
      } else if (run.status === 'interrupted') {
        markSessionRunActive(sessionId, null);
      }
      if (run.status === 'running' || run.status === 'cancellation_requested') {
        markSessionRunActive(sessionId, runId);
        if (currentSessionIdRef.current === sessionId) {
          const events = Array.isArray(run.progress_events) ? run.progress_events : [];
          if (events.length > 0) rebuildRunningAssistantTrace(sessionId, events);
          window.setTimeout(() => restorePendingRun(sessionId), 2000);
        }
      }
    } catch (error) {
      console.warn('restore workflow run failed', error);
      // 查询失败（后端无响应等）时也不能永久锁死输入：解除运行标记，
      // 用户可重试发送；若任务实际仍在运行，下次发送会自动探测并恢复。
      markSessionRunActive(sessionId, null);
    }
  }

  // 刷新/断连后恢复进行中任务的协作回执：轨迹独立存放在 traceEvents，
  // 不再反复编码进 assistant 正文；历史 <<EV:>> 消息仍由读取路径兼容。
  const rebuildRunningAssistantTrace = (sessionId, events) => {
    const traceEvents = events.map(runtimeEventToTrace).filter(Boolean);
    if (traceEvents.length === 0) return;
    const restoredId = `restored-assistant-${sessionId}`;
    const current = (liveSessionCacheRef.current[sessionId]?.messages
      || sessionMessageCacheRef.current[sessionId] || []);
    const existing = current.find(m => m.id === restoredId);
    if (existing && JSON.stringify(existing.traceEvents) === JSON.stringify(traceEvents)) return;
    const base = current.filter(m => !(m.isPlaceholder && m.role === 'assistant'));
    const assistant = {
      id: restoredId,
      role: 'assistant',
      content: '',
      traceEvents,
      isPlaceholder: true,
      timestamp: getCurrentTime(),
    };
    const updated = [...base, assistant];
    liveSessionCacheRef.current[sessionId] = { messages: updated, isRunning: true };
    if (currentSessionIdRef.current === sessionId) setMessages(updated);
  };

  const handleWorkflowAction = (action) => {
    const intent = workshopActionIntent(action, {
      returnTo: {
        page: 'assistant',
        params: currentSessionId ? { sessionId: currentSessionId } : {},
      },
    });
    if (intent) onNavigate?.(intent);
  };

  const handleMainWorkflowSend = async (answerOverride = null) => {
    const answer = String(answerOverride ?? input).trim();
    if ((!answer && uploadedFiles.length === 0) || isCurrentSessionLoading) return;
    let useRenderedSessionMessages = !sessionCreationPromiseRef.current;
    let sessionId = sessionCreationPromiseRef.current
      ? await sessionCreationPromiseRef.current
      : (currentSessionIdRef.current || currentSessionId);
    if (!sessionId) {
      sessionId = await createSession();
      useRenderedSessionMessages = false;
    }
    if (!sessionId) return;

    const storedRunId = readPendingRuns()[sessionId] || null;
    let resumeRunId = null;
    if (storedRunId) {
      try {
        const storedRun = await getWorkflowRun(storedRunId);
        if (storedRun?.status === 'running') {
          markSessionRunActive(sessionId, storedRunId);
          void restorePendingRun(sessionId);
          return;
        }
        resumeRunId = storedRun?.status === 'interrupted'
          ? await getResumableWorkflowRunId(storedRunId)
          : null;
      } catch (error) {
        console.error('validate pending workflow run failed', error);
        markSessionRunActive(sessionId, storedRunId);
        void restorePendingRun(sessionId);
        return;
      }
      if (!resumeRunId) rememberPendingRun(sessionId, null);
    }
    const runId = resumeRunId || createWorkflowRunId();
    if (!resumeRunId) locallyStartingWorkflowRunsRef.current.add(runId);
    rememberPendingRun(sessionId, runId);
    const workflowController = new AbortController();
    workflowControllersRef.current[sessionId] = workflowController;
    markSessionRunActive(sessionId, runId);

    const filesMetadata = uploadedFiles.map(file => ({ id: file.id, name: file.name }));
    const requestText = filesMetadata.length
      ? `${answer}${answer ? '\n\n' : ''}附件：${filesMetadata.map(file => file.name).join('、')}`
      : answer;
    const userMessage = {
      id: `local-user-${Date.now()}`,
      role: 'user',
      content: answer,
      files: filesMetadata,
      timestamp: getCurrentTime(),
    };
    const assistantId = `local-assistant-${Date.now()}`;
    const baseMessages = [
      ...(useRenderedSessionMessages && currentSessionIdRef.current === sessionId
        ? messages
        : (
            liveSessionCacheRef.current[sessionId]?.messages
            || sessionMessageCacheRef.current[sessionId]
            || []
          )),
      userMessage,
      {
        id: assistantId,
        role: 'assistant',
        content: '',
        timestamp: getCurrentTime(),
        isPlaceholder: true,
      },
    ];
    liveSessionCacheRef.current[sessionId] = { messages: baseMessages, isRunning: true };
    if (currentSessionIdRef.current === sessionId) setMessages(baseMessages);
    setInput('');
    setUploadedFiles([]);
    setAutoScroll(true);
    if (currentSessionIdRef.current === sessionId) resetWorkflow();
    if (textareaRef.current) textareaRef.current.style.height = 'auto';

    const activeTraceEvents = [];
    let streamedAnswer = '';
    const updateAssistant = (content, placeholder = false, actions = undefined, metadata = {}) => {
      const cache = liveSessionCacheRef.current[sessionId] || { messages: baseMessages };
      const updated = (cache.messages || []).map(message => (
        message.id === assistantId
          ? {
              ...message,
              content,
              traceEvents: activeTraceEvents.slice(),
              isPlaceholder: placeholder,
              ...(actions === undefined ? {} : { actions }),
              ...metadata,
            }
          : message
      ));
      liveSessionCacheRef.current[sessionId] = { ...cache, messages: updated, isRunning: placeholder };
      if (currentSessionIdRef.current === sessionId) setMessages(updated);
      return updated;
    };

    let terminalReceived = false;
    try {
      const outcome = await streamWorkflowTurn({
        conversationId: sessionId,
        runId,
        answer: requestText,
        messages: baseMessages.filter(message => !message.isPlaceholder),
        signal: workflowController.signal,
        resume: Boolean(resumeRunId),
        onEvents: (eventBatch) => {
          // The SSE reader batches provider bursts so one model token cannot
          // trigger a full message/trace/Markdown render.  Apply the batch in
          // arrival order to preserve the exact stream semantics.
          const traceEvents = [];
          let shouldUpdateAssistant = false;
          let publicationStatus;
          for (const [runtimeEvent, traceEvent] of eventBatch) {
          // The first SSE event proves that the server has registered this
          // run, so later refresh/session recovery may safely query it.
          locallyStartingWorkflowRunsRef.current.delete(runId);
          if (runtimeEvent?.event === 'answer_started') {
            streamedAnswer = '';
            publicationStatus = runtimeEvent.publication_status || 'approved';
            shouldUpdateAssistant = true;
            continue;
          }
          if (runtimeEvent?.event === 'answer_delta') {
            streamedAnswer += String(runtimeEvent.delta || '');
            shouldUpdateAssistant = true;
            continue;
          }
          if (runtimeEvent?.event === 'answer_committed') continue;
          if (!traceEvent) continue;
          traceEvents.push(traceEvent);
          activeTraceEvents.push(traceEvent);
          shouldUpdateAssistant = true;
          }
          if (currentSessionIdRef.current === sessionId && traceEvents.length > 0) {
            dispatchGraphEvents(traceEvents);
          }
          if (shouldUpdateAssistant) {
            updateAssistant(streamedAnswer, true, undefined, publicationStatus
              ? { publicationStatus }
              : undefined);
          }
        },
      });
      const finalContent = String(
        outcome.message
        || streamedAnswer
        || (outcome.status === 'cancelled' ? '已停止生成。' : '')
      ).trim();
      const finalMessages = updateAssistant(finalContent, false, outcome.result?.ui_actions || []);
      sessionMessageCacheRef.current[sessionId] = finalMessages;
      if (outcome.status === 'interrupted') rememberPendingRun(sessionId, runId);
      else rememberPendingRun(sessionId, null);
      terminalReceived = true;
      markSessionRunActive(sessionId, null);
      // Reconcile the live placeholder with the canonical saved message once
      // the terminal event arrives.  In real streaming responses React could
      // otherwise retain the final trace-only placeholder until a manual page
      // refresh even though the backend had already saved the full answer.
      // If persistence is momentarily behind, restore the complete SSE result
      // instead of replacing it with an incomplete history response.
      if (currentSessionIdRef.current === sessionId && outcome.status !== 'cancelled') {
        const expectedAssistantCount = finalMessages.filter(
          message => message.role === 'assistant',
        ).length;
        const persistedMessages = await fetchMessages(sessionId, { force: true });
        const persistedAssistantCount = persistedMessages.filter(
          message => message.role === 'assistant',
        ).length;
        if (persistedAssistantCount < expectedAssistantCount) {
          sessionMessageCacheRef.current[sessionId] = finalMessages;
          setMessages(finalMessages);
        } else {
          delete liveSessionCacheRef.current[sessionId];
        }
      }
      fetchSessions({ skipRestore: true });
      void refreshSessionTitleUntilSettled(sessionId, { maxAttempts: 3, initialDelay: 150 });
    } catch (error) {
      if (workflowControllersRef.current[sessionId] === workflowController) {
        delete workflowControllersRef.current[sessionId];
      }
      if (error.name === 'AbortError') {
        updateAssistant('*（连接已中断，后台任务状态将在重连后恢复）*', false);
        void restorePendingRun(sessionId);
      } else {
        console.error(error);
        markNetworkInterrupted(error.message);
        // 在协作回执里标记失败：feedback_done approved:false 会把轨迹节点
        // 置为 error，回执按钮随之显示“已参与协作 / 查看复核轨迹”。
        activeTraceEvents.push({
          type: 'feedback_done',
          approved: false,
          text: error.message || '执行失败',
        });
        updateAssistant(`*（${error.message || '执行失败'}）*`, false);
        // 立即解锁发送按钮：失败/超时后不应继续锁死界面。后台任务若
        // 仍在运行（如仅 SSE 断连），restorePendingRun 会重新标记并恢复。
        markSessionRunActive(sessionId, null);
        void restorePendingRun(sessionId);
      }
    } finally {
      locallyStartingWorkflowRunsRef.current.delete(runId);
      if (workflowControllersRef.current[sessionId] === workflowController) {
        delete workflowControllersRef.current[sessionId];
      }
      if (terminalReceived) markSessionRunActive(sessionId, null);
    }
  };

  const handleSend = async () => {
    if (import.meta.env.VITE_USE_LEGACY_CHAT !== 'true') {
      await handleMainWorkflowSend();
      return;
    }
    if ((!input.trim() && uploadedFiles.length === 0) || isLoading) return;
    let useRenderedSessionMessages = !sessionCreationPromiseRef.current;
    let sessionId = sessionCreationPromiseRef.current
      ? await sessionCreationPromiseRef.current
      : (currentSessionIdRef.current || currentSessionId);
    if (!sessionId) {
      sessionId = await createSession();
      useRenderedSessionMessages = false;
    }
    if (!sessionId) return;

    abortControllerRef.current = new AbortController();
    setLoadingSessionId(sessionId);

    const filesMetadata = uploadedFiles.map(f => ({ id: f.id, name: f.name }));
    const displayMessage = { 
      role: 'user', content: input, files: filesMetadata, timestamp: getCurrentTime() 
    };

    setInput(''); setUploadedFiles([]); setIsLoading(true);
    setAutoScroll(true);
    resetWorkflow();
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
    const initialMessages = [
      ...(useRenderedSessionMessages && currentSessionIdRef.current === sessionId
        ? messages
        : (
            liveSessionCacheRef.current[sessionId]?.messages
            || sessionMessageCacheRef.current[sessionId]
            || []
          )),
      displayMessage,
      { role: 'assistant', content: '', timestamp: getCurrentTime(), isPlaceholder: true }
    ];
    liveSessionCacheRef.current[sessionId] = { messages: initialMessages, isRunning: true };
    if (currentSessionIdRef.current === sessionId) {
      setMessages(initialMessages);
    }
    
    // 🔥 Temporary store for search query extracted from stream
    let currentSearchQuery = '';

    try {
      const res = await fetchWithAuth(`${API_BASE}/chat/${sessionId}`, {
        method: 'POST',
        body: JSON.stringify({ 
            role: 'user', 
            content: displayMessage.content, 
            files: filesMetadata, 
            tools_enabled: isToolsEnabled,
            web_search: isWebSearchEnabled,
            rag_search: isRagEnabled // 🔥 Send RAG flag
        }),
        signal: abortControllerRef.current.signal
      });

      if (!res.ok) throw new Error("Backend Error");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      
      let buffer = '';       
      let textQueue = '';    
      let lastUpdateTime = 0; 

      const updateLiveSessionMessages = (updater) => {
        const cache = liveSessionCacheRef.current[sessionId] || { messages: [], isRunning: true };
        const updatedMessages = updater(cache.messages || []);
        liveSessionCacheRef.current[sessionId] = { ...cache, messages: updatedMessages, isRunning: true };
        if (currentSessionIdRef.current === sessionId) {
          setMessages(updatedMessages);
        }
        return updatedMessages;
      };

      const appendAssistantContent = (contentToAppend) => {
        appendGraphAnswer(contentToAppend);
        updateLiveSessionMessages(prev => {
          const newMsgs = [...prev];
          const lastMsg = newMsgs[newMsgs.length - 1];
          if (lastMsg.role === 'user') {
            return [...newMsgs, { role: 'assistant', content: contentToAppend, timestamp: getCurrentTime(), searchQuery: currentSearchQuery }];
          }
          newMsgs[newMsgs.length - 1] = {
            ...lastMsg,
            isPlaceholder: false,
            content: lastMsg.isPlaceholder ? contentToAppend : lastMsg.content + contentToAppend,
            searchQuery: currentSearchQuery
          };
          return newMsgs;
        });
      };

      const rollbackAssistantContent = () => {
        updateLiveSessionMessages(prev => {
          const newMsgs = [...prev];
          const lastMsg = newMsgs[newMsgs.length - 1];
          if (lastMsg?.role === 'assistant') {
            newMsgs[newMsgs.length - 1] = {
              ...lastMsg,
              isPlaceholder: false,
              content: stripAssistantVisibleContent(lastMsg.content || ''),
            };
          }
          return newMsgs;
        });
      };

      const drainTextQueue = async () => {
        if (!textQueue) return;
        while (textQueue.length > 0) {
          const isProcessTag = textQueue.startsWith('<<PLAN:') || textQueue.startsWith('<<EXEC:') || textQueue.startsWith('<<EV:') || textQueue.startsWith('<<REFS:') || textQueue.startsWith('<<VIDEOS:') || textQueue.startsWith('<think>') || textQueue.startsWith('</think>');
          const chunkSize = isProcessTag ? Math.max(textQueue.indexOf('>>') + 2, textQueue.length) : textQueue.length;
          const chunk = textQueue.slice(0, chunkSize);
          textQueue = textQueue.slice(chunkSize);
          appendAssistantContent(chunk);
        }
      };

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        
        buffer += decoder.decode(value, { stream: true });
        
        while (true) {
          const statusMatch = buffer.match(/<<STATUS:(.*?):(.*?)(?:>>)/);
          const refsMatch = buffer.match(/<<REFS:(.*?)(?:>>)/);
          const videosMatch = buffer.match(/<<VIDEOS:(.*?)(?:>>)/);
          const rollbackMatch = buffer.match(/<<ROLLBACK:(.*?)(?:>>)/);
          const processMatch = buffer.match(/<<(PLAN|EXEC|EV):(.*?)(?:>>)/s);
          
          let matched = false;

          if (statusMatch) {
             const [fullTag, mode, text] = statusMatch;
             buffer = buffer.replace(fullTag, '');
             matched = true;
             
             // 🔥 Extract search query from status
             if (mode === 'searching') {
                const queryMatch = text.match(/(?:搜索网络|检索知识库)：(.*)\.\.\.$/);
                if (queryMatch) {
                   currentSearchQuery = queryMatch[1];
                }
             }
          }

           if (refsMatch) {
             const [fullTag] = refsMatch;
             const idx = buffer.indexOf(fullTag);
             textQueue += buffer.slice(0, idx);
             await drainTextQueue(true);
             try {
               const refs = JSON.parse(refsMatch[1]);
               setGraphReferences(refs);
               currentSearchQuery = refs?.[0]?.query || refs?.[0]?.url || currentSearchQuery;
             } catch (e) { console.error('refs parse failed', e); }
             appendAssistantContent(fullTag);
             buffer = buffer.slice(idx + fullTag.length);
             matched = true;
          }

            if (videosMatch) {
             const [fullTag] = videosMatch;
             const idx = buffer.indexOf(fullTag);
             textQueue += buffer.slice(0, idx);
             await drainTextQueue(true);
             appendAssistantContent(fullTag);
             buffer = buffer.slice(idx + fullTag.length);
             matched = true;
           }

           if (rollbackMatch) {
             const [fullTag] = rollbackMatch;
             const idx = buffer.indexOf(fullTag);
             textQueue += buffer.slice(0, idx);
             await drainTextQueue(true);
             rollbackAssistantContent();
             buffer = buffer.slice(idx + fullTag.length);
             matched = true;
           }

           if (processMatch) {
             const [fullTag] = processMatch;
             const idx = buffer.indexOf(fullTag);
             textQueue += buffer.slice(0, idx);
             await drainTextQueue(true);
             if (processMatch[1] === 'EV') {
               try { dispatchGraphEvent(JSON.parse(processMatch[2])); } catch (e) { console.error('event parse failed', e); }
               appendAssistantContent(fullTag);
             }
             buffer = buffer.slice(idx + fullTag.length);
             matched = true;
           }
          
          if (!matched) break;
        }

        const tagStartIndex = buffer.indexOf('<<');

        if (tagStartIndex === -1) {
            if (buffer.endsWith('<')) {
                textQueue += buffer.slice(0, -1);
                buffer = '<';
            } else {
                textQueue += buffer;
                buffer = '';
            }
        } else if (tagStartIndex > 0) {
            textQueue += buffer.slice(0, tagStartIndex);
            buffer = buffer.slice(tagStartIndex);
        } else if (tagStartIndex === 0 && !buffer.includes('>>')) {
          // Protocol tag is incomplete; keep it buffered and never render partial REFS/EV text.
          // Leave the incomplete tag in buffer until more stream data arrives.
        }

        const now = Date.now();
        if (textQueue.length > 0 && (now - lastUpdateTime > 50)) {
            lastUpdateTime = now;
            await drainTextQueue(false);
        }
      }

      // Flush remaining
        if (textQueue.length > 0 || buffer.length > 0) {
          const safeRemainder = buffer.startsWith('<<') ? '' : buffer;
          const finalContent = textQueue + safeRemainder; 
          if (finalContent) {
              textQueue = finalContent;
              buffer = '';
              await drainTextQueue(true);
          }
      }

      const expectedAssistantCount = (liveSessionCacheRef.current[sessionId]?.messages || messages).filter(msg => msg.role === 'assistant').length;
      fetchSessions({ skipRestore: true });
      void refreshSessionTitleUntilSettled(sessionId);
      liveSessionCacheRef.current[sessionId] = { ...(liveSessionCacheRef.current[sessionId] || {}), isRunning: false };
      if (currentSessionIdRef.current === sessionId) {
        await fetchMessagesUntilSaved(sessionId, Math.max(1, expectedAssistantCount));
      }
      delete liveSessionCacheRef.current[sessionId];
    } catch (e) {
      if (e.name === 'AbortError') {
        // 🔥 修复：中断时自动闭合 <think> 标签，停止无限思考动画
        const interruptedMessages = (() => {
          const newMsgs = [...(liveSessionCacheRef.current[sessionId]?.messages || [])];
          const lastMsg = newMsgs[newMsgs.length - 1];
          if (lastMsg) {
            if (lastMsg.role === 'assistant') {
              const finalContent = finalizeInterruptedAssistantContent(lastMsg.content, '生成已中断');
              newMsgs[newMsgs.length - 1] = { ...lastMsg, content: finalContent, isPlaceholder: false };
            } else if (lastMsg.role === 'user') {
              newMsgs.push({ role: 'assistant', content: "*（生成已中断）*", timestamp: getCurrentTime() });
            }
          }
          return newMsgs;
        })();
        liveSessionCacheRef.current[sessionId] = { messages: interruptedMessages, isRunning: false };
        if (currentSessionIdRef.current === sessionId) setMessages(interruptedMessages);
        return; 
      }
      
      console.error(e);
      const errorReason = e?.message ? `事件流读取异常：${e.message}` : '事件流读取异常，连接可能已关闭。';
      markNetworkInterrupted(errorReason);
      const failedMessages = (() => {
        const base = liveSessionCacheRef.current[sessionId]?.messages || [];
        const lastMsg = base[base.length - 1];
        if (lastMsg?.role === 'assistant') {
          const patched = [...base];
          patched[patched.length - 1] = {
            ...lastMsg,
            content: finalizeInterruptedAssistantContent(lastMsg.content, errorReason),
            isPlaceholder: false,
          };
          return patched;
        }
        if (base.length > 0 && base[base.length - 1].role === 'user') {
          return [...base, { role: 'assistant', content: `⚠️ ${errorReason}`, timestamp: getCurrentTime() }];
        }
        return base;
      })();
      liveSessionCacheRef.current[sessionId] = { messages: failedMessages, isRunning: false };
      if (currentSessionIdRef.current === sessionId) setMessages(failedMessages);
    } finally { 
      setIsLoading(false); 
      setLoadingSessionId(null);
      abortControllerRef.current = null;
    }
  };

  return (
    <div
      className={`assistant-workspace flex min-h-0 bg-[radial-gradient(circle_at_top_left,#dcfce7_0,#f0fdfa_32%,#f8fafc_72%)] text-slate-800 font-sans overflow-hidden ${embedded ? 'h-full' : 'h-screen'}`}
      onKeyDownCapture={keepNativeCopyShortcut}
    >
      
      <style>{`
        @keyframes fade-in-up {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
        .animate-fade-in-up {
          animation: fade-in-up 0.3s ease-out forwards;
        }
        .custom-scrollbar::-webkit-scrollbar { width: 4px; }
        .custom-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .custom-scrollbar::-webkit-scrollbar-thumb { background: linear-gradient(180deg, #a7f3d0, #99f6e4); border-radius: 20px; }
        .video-scrollbar { scrollbar-width: thin; scrollbar-color: rgba(45, 212, 191, 0.45) transparent; }
        .video-scrollbar::-webkit-scrollbar { height: 3px; }
        .video-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .video-scrollbar::-webkit-scrollbar-thumb { background: rgba(45, 212, 191, 0.45); border-radius: 999px; }
        .video-scrollbar::-webkit-scrollbar-thumb:hover { background: rgba(20, 184, 166, 0.65); }
        .input-scrollbar::-webkit-scrollbar { width: 3px; }
        .input-scrollbar::-webkit-scrollbar-track { background: transparent; }
        .input-scrollbar::-webkit-scrollbar-thumb { background-color: #cbd5e1; border-radius: 20px; }
        .input-scrollbar::-webkit-scrollbar-thumb:hover { background-color: #94a3b8; }
        
        @keyframes bounce-delay {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-3px); }
        }
        .animate-bounce-delay-1 { animation: bounce-delay 1s infinite 0.1s; }
        .animate-bounce-delay-2 { animation: bounce-delay 1s infinite 0.2s; }
        .animate-bounce-delay-3 { animation: bounce-delay 1s infinite 0.3s; }
        
        @keyframes spin-slow {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        .animate-spin-slow {
          animation: spin-slow 3s linear infinite;
        }

        @keyframes ping-slow {
          75%, 100% { transform: scale(1.5); opacity: 0; }
        }
        .animate-ping-slow {
          animation: ping-slow 2s cubic-bezier(0, 0, 0.2, 1) infinite;
        }
        
        @keyframes pulse-ring {
          0% { transform: scale(0.8); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7); }
          70% { transform: scale(1); box-shadow: 0 0 0 10px rgba(239, 68, 68, 0); }
          100% { transform: scale(0.8); box-shadow: 0 0 0 0 rgba(239, 68, 68, 0); }
        }
        .animate-pulse-ring {
          animation: pulse-ring 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite;
        }

        @keyframes upload-float {
          0% { transform: translateY(4px); opacity: 0.78; }
          100% { transform: translateY(0); opacity: 1; }
        }
        .upload-float { animation: upload-float 180ms cubic-bezier(0.16, 1, 0.3, 1) both; }
      `}</style>

      {/* Hidden File Input */}
      <input type="file" multiple ref={fileInputRef} onChange={handleFileSelect} className="hidden" accept=".txt,.md,.json,.docx,.py,.js,.csv,.jpg,.jpeg,.png,.bmp,.webp,.tiff" />

      <button
        type="button"
        className="assistant-session-backdrop"
        aria-label="关闭会话列表"
        aria-hidden={isSidebarOpen ? undefined : true}
        tabIndex={isSidebarOpen ? 0 : -1}
        data-open={String(isSidebarOpen)}
        onClick={() => setIsSidebarOpen(false)}
      />

      {/* Sidebar */}
      <aside aria-label="会话列表" aria-hidden={isSidebarOpen ? undefined : true} inert={isSidebarOpen ? undefined : true} data-open={String(isSidebarOpen)} className={`assistant-session-rail ${isSidebarOpen ? 'w-[244px] translate-x-0' : 'w-0 -translate-x-full opacity-0'} bg-white/70 backdrop-blur-xl border-r border-emerald-100 flex flex-col flex-shrink-0 z-20 transition-[transform,opacity] duration-200 ease-out overflow-hidden shadow-xl shadow-emerald-100/40`}>
        {/* Sidebar Header */}
        <div className="assistant-session-rail__header h-16 flex items-center justify-between px-4 pt-2 flex-shrink-0">
           <div className="font-semibold text-gray-700 flex items-center gap-2">
             <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-emerald-500 to-teal-500 flex items-center justify-center text-white shadow-sm shadow-emerald-200 border border-emerald-100">
               <HeartPulse size={18} />
             </div>
             <span>时珍智训智能助教</span>
           </div>
           <button onClick={() => setIsSidebarOpen(false)} className="group p-2 rounded-2xl text-emerald-600 bg-emerald-50/70 border border-emerald-100 hover:text-white hover:bg-gradient-to-br hover:from-emerald-500 hover:to-teal-500 hover:shadow-md hover:shadow-emerald-200/70 transition-[color,background-color,border-color,box-shadow]" title="收起侧边栏">
             <ChevronsLeft size={19} className="transition-transform group-hover:-translate-x-0.5" />
           </button>
        </div>

        {/* New Chat Button */}
        <div className="assistant-session-rail__new px-4 pb-2">
          <button onClick={createSession} className="flex h-9 w-full items-center justify-center gap-1.5 rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 px-3 text-[13px] font-medium text-white shadow-sm shadow-emerald-200 transition-[transform,box-shadow,background-color] hover:-translate-y-0.5 hover:from-emerald-700 hover:to-teal-700">
            <Plus size={16} /><span>新对话</span>
          </button>
        </div>

        {/* Session List */}
        <div className="assistant-session-list flex-1 overflow-y-auto px-3 py-2 space-y-1 custom-scrollbar">
          <div className="px-2 py-2 text-xs font-semibold text-gray-400 uppercase tracking-wider">历史记录</div>
          {sessions.map(session => (
            <div key={session.id} onClick={() => startSessionSwitch(session)} className={`group flex items-center justify-between px-3 py-2.5 rounded-xl cursor-pointer transition-[color,background-color,border-color,box-shadow] duration-200 text-sm border border-transparent ${currentSessionId === session.id ? 'bg-white text-emerald-800 shadow-sm border-emerald-100 font-medium' : 'text-slate-600 hover:bg-emerald-50/80 hover:text-emerald-900'}`}>
              <div className="flex items-center gap-3 overflow-hidden">
                {editingSessionId === session.id ? (
                  <input type="text" value={editTitle} onChange={(e) => setEditTitle(e.target.value)} onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.key === 'Enter' && updateSessionTitle(e, session.id)} className="bg-white border border-emerald-300 rounded px-1 py-0.5 text-xs w-full focus:outline-none focus:ring-2 focus:ring-emerald-500/40" autoFocus />
                ) : (
                  <span className="truncate">{session.title}</span>
                )}
              </div>
              <div className={`flex gap-1 ${currentSessionId === session.id ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'} transition-opacity`}>
                {editingSessionId === session.id ? (
                  <><button onClick={(e) => updateSessionTitle(e, session.id)} className="p-1 hover:text-green-600"><Check size={14}/></button><button onClick={(e) => { e.stopPropagation(); setEditingSessionId(null); }} className="p-1 hover:text-red-500"><X size={14}/></button></>
                ) : (
                  <><button onClick={(e) => { e.stopPropagation(); setEditingSessionId(session.id); setEditTitle(session.title); }} className="p-1 text-gray-400 hover:text-gray-700"><Edit2 size={14} /></button><button onClick={(e) => deleteSession(e, session.id)} className="p-1 text-gray-400 hover:text-red-500"><Trash2 size={14} /></button></>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* User Info Footer */}
        {!embedded && <div className="p-4 border-t border-emerald-100 bg-emerald-50/40 flex-shrink-0 relative" ref={userMenuRef}>
          {isUserMenuOpen && (
            <div className="absolute left-4 right-4 bottom-[72px] bg-white rounded-2xl shadow-2xl border border-gray-100 py-2 z-50 animate-in fade-in zoom-in-95 duration-100 origin-bottom-left overflow-hidden">
              <button onClick={() => { setIsUserMenuOpen(false); onOpenPersonalization?.(); }} className="w-full text-left px-4 py-3 text-sm text-gray-700 hover:bg-emerald-50 hover:text-emerald-700 flex items-center gap-3 transition-colors">
                <Database size={17} /> 学习记忆
              </button>
              <button onClick={() => { setIsUserMenuOpen(false); onOpenKnowledge?.(); }} className="w-full text-left px-4 py-3 text-sm text-gray-700 hover:bg-indigo-50 hover:text-indigo-700 flex items-center gap-3 transition-colors">
                <BookOpen size={17} /> 知识库管理
              </button>
              {currentUserRole === 'admin' && (
                <button onClick={() => { setIsUserMenuOpen(false); onOpenAdminFeedback?.(); }} className="w-full text-left px-4 py-3 text-sm text-gray-700 hover:bg-teal-50 hover:text-teal-700 flex items-center gap-3 transition-colors">
                  <ShieldCheck size={17} /> 反馈数据管理
                </button>
              )}
              <div className="h-px bg-gray-100 my-1" />
              <button onClick={() => { setIsUserMenuOpen(false); onLogout?.(); }} className="w-full text-left px-4 py-3 text-sm text-red-500 hover:bg-red-50 flex items-center gap-3 transition-colors">
                <LogOut size={17} /> 退出登录
              </button>
            </div>
          )}
          <button onClick={() => setIsUserMenuOpen(v => !v)} className={`w-full flex items-center justify-between text-sm text-slate-500 whitespace-nowrap rounded-2xl p-2 transition-[color,background-color,box-shadow] ${isUserMenuOpen ? 'bg-white shadow-sm text-emerald-700' : 'hover:bg-white hover:shadow-sm'}`}>
            <div className="flex items-center gap-3 min-w-0">
              <div className="w-8 h-8 rounded-full bg-gradient-to-br from-emerald-100 to-teal-100 flex items-center justify-center text-emerald-700 font-bold border border-emerald-200 shrink-0">
                {currentUser ? currentUser[0].toUpperCase() : 'U'}
              </div>
              <div className="flex flex-col text-left min-w-0">
                <span className="font-semibold text-gray-700 truncate max-w-[120px]">{currentUser}</span>
                <span className="text-xs text-gray-400">菜单 · 在线</span>
              </div>
            </div>
            <MoreHorizontal size={18} className="text-gray-400" />
          </button>
        </div>}
      </aside>

      {/* Main Chat Area */}
      <div className="assistant-chat-pane flex-1 flex flex-col h-full bg-white/55 backdrop-blur-sm relative min-w-0">
        {currentSessionId ? (
          <>
            {/* Chat Header */}
            <header className="assistant-chat-header sticky top-0 z-10 grid h-11 min-h-11 grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)] items-center gap-2 border-b border-emerald-100 bg-white/75 px-3 shadow-sm shadow-emerald-50 backdrop-blur-xl sm:px-5">
              <div className="flex min-w-0 items-center gap-2">
                 {!isSidebarOpen && (
                  <button onClick={() => setIsSidebarOpen(true)} aria-label="展开侧边栏" className="group rounded-xl border border-emerald-100 bg-white/90 p-1.5 text-emerald-600 shadow-sm shadow-emerald-100 transition-[color,background-color,border-color,box-shadow] hover:bg-gradient-to-br hover:from-emerald-500 hover:to-teal-500 hover:text-white hover:shadow-md hover:shadow-emerald-200/70" title="展开侧边栏"><ChevronsRight size={18} className="transition-transform group-hover:translate-x-0.5" /></button>
                 )}
                 {!embedded && shellConfig.assistantHomeAction && onBackHome ? (
                   <HomeButton onClick={onBackHome} label={shellConfig.assistantHomeAction.label} className="h-8 justify-center px-3 py-1.5" />
                 ) : null}
              </div>

              <div className="flex min-w-0 items-center justify-center gap-2 px-1 text-center">
                <span className="h-2 w-2 shrink-0 rounded-full bg-green-500"></span>
                <span className="truncate font-semibold text-gray-800">{sessions.find(s => s.id === currentSessionId)?.title || '对话'}</span>
              </div>

              <div className="relative justify-self-end" ref={menuRef}>
                <div className="flex items-center gap-2">
                  <button aria-label="更多对话操作" onClick={() => setIsMenuOpen(!isMenuOpen)} className={`text-gray-400 hover:text-gray-600 cursor-pointer transition-colors p-2 rounded-full hover:bg-gray-100 ${isMenuOpen ? 'bg-gray-100 text-gray-600' : ''}`}><MoreHorizontal size={20} /></button>
                </div>
                {isMenuOpen && (
                  <div className="absolute right-0 mt-2 w-48 bg-white rounded-xl shadow-xl border border-gray-100 py-1 z-50 animate-in fade-in zoom-in-95 duration-100 origin-top-right">
                    <button onClick={exportSession} className="w-full text-left px-4 py-2.5 text-sm text-gray-700 hover:bg-gray-50 flex items-center gap-2 transition-colors"><Download size={16} />导出为 Markdown</button>
                  </div>
                )}
              </div>
            </header>
            
            {/* Messages Area */}
            <div 
              ref={scrollContainerRef}
              data-scroll-region="messages"
              className="assistant-messages flex-1 overflow-hidden p-3 sm:p-6 md:p-8 relative"
            >
              <div className="assistant-messages__content mx-auto w-full max-w-5xl pb-6 h-full">
                {messages.length === 0 ? (
                   <div className="assistant-starters mx-auto mt-14 flex max-w-2xl flex-col items-center text-center animate-fade-in-up sm:mt-24">
                      <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-3xl border border-emerald-100 bg-gradient-to-br from-emerald-50 to-teal-50 text-emerald-600 shadow-sm shadow-emerald-100"><HeartPulse size={32} /></div>
                      <h2 className="mb-2 text-2xl font-bold text-emerald-950">从这里开始</h2>
                      <p className="max-w-xl text-sm leading-6 text-slate-500 sm:text-base">说说你当前想完成的学习任务。智能助教会结合学习状态，调度六个智能体协同处理。</p>
                      <div className="mt-6 grid w-full gap-3 sm:grid-cols-3">
                        <button type="button" onClick={() => setInput('请结合我的学习状态，为我制定一份学习计划。')} className="assistant-starter-card">
                          <CalendarRange size={18} />
                          <span>制定学习计划</span>
                        </button>
                        <button type="button" onClick={() => setInput('请结合教材证据讲解一个知识点，并给我一道练习题。')} className="assistant-starter-card">
                          <BookOpen size={18} />
                          <span>讲解知识点</span>
                        </button>
                        <button type="button" onClick={() => setInput('请根据我的学习进度生成一份练习试卷。')} className="assistant-starter-card">
                          <FileText size={18} />
                          <span>生成练习试卷</span>
                        </button>
                      </div>
                   </div>
                ) : (
                  <Virtuoso
                    ref={virtuosoRef}
                    data={messages}
                    computeItemKey={(index, item) => item.id || `msg-${index}`}
                    // 仅在用户停留在底部时平滑跟随新内容；用户向上滚动查看
                    // 正在输出的内容后立即停止跟随，不再强制拉底。
                    followOutput={(isAtBottom) => (isAtBottom ? 'smooth' : false)}
                    atBottomStateChange={handleAtBottomChange}
                    increaseViewportBy={{ top: 200, bottom: 200 }}
                    className="assistant-messages__virtuoso"
                    style={{ height: '100%' }}
                    itemContent={(index, msg) => {
                      const isLast = index === messages.length - 1;
                      const messageHasExecutionDone = msg.role === 'assistant' ? hasExecutionDoneTrace(msg) : false;
                      const isGenerating = isCurrentSessionLoading && isLast && msg.role === 'assistant' && !messageHasExecutionDone;
                      const isReviewing = isCurrentSessionLoading && isLast && msg.role === 'assistant' && messageHasExecutionDone;

                      return (
                        <ChatBubble
                          role={msg.role}
                          content={msg.content}
                          files={msg.files}
                          timestamp={msg.timestamp}
                          messageId={msg.id}
                          feedbackStatus={msg.feedback_status || msg.feedbackStatus}
                          actions={msg.actions}
                          traceEvents={msg.traceEvents}
                          publicationStatus={msg.publicationStatus}
                          branch={msg.branch || messageBranches[msg.id]}
                          onInspectKnowledge={handleInspectKnowledge}
                          onFeedback={handleFeedback}
                          onRegenerate={handleRegenerate}
                          onSwitchBranch={handleSwitchBranch}
                          onAction={handleWorkflowAction}
                          isGenerating={isGenerating}
                          isReviewing={isReviewing}
                        />
                      );
                    }}
                  />
                )}
              </div>
              
              {showScrollButton && (
                <button 
                  aria-label="回到最新消息"
                  onClick={() => { setAutoScroll(true); scrollToBottom(); }}
                  className="fixed bottom-32 right-8 p-3 bg-white border border-emerald-100 shadow-lg shadow-emerald-100 rounded-full text-slate-500 hover:text-emerald-700 hover:border-emerald-200 transition-[color,border-color,box-shadow] animate-in fade-in zoom-in z-30"
                >
                  <ArrowDown size={20} />
                </button>
              )}
            </div>

            {/* Input Area */}
            <div className="assistant-composer border-t-0 p-3 sm:p-4">
              <div className="assistant-composer__inner mx-auto w-full max-w-5xl relative group">
                <div 
                  ref={inputContainerRef}
                  className={`
                    relative flex flex-col rounded-3xl border border-emerald-100/80 bg-white/90 shadow-[0_12px_32px_rgba(16,185,129,0.08)] transition-[background-color,border-color,box-shadow] duration-200
                    ${isDragging
                      ? 'border-emerald-300 bg-emerald-50 shadow-md shadow-emerald-100'
                      : 'focus-within:border-emerald-200 focus-within:bg-white focus-within:shadow-[0_14px_36px_rgba(16,185,129,0.12)]'
                    }
                  `}
                  onDragEnter={handleDragEnter} onDragLeave={handleDragLeave} onDragOver={handleDragOver} onDrop={handleDrop}
                >
                  {isDragging && (
                    <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-white/90 backdrop-blur-sm rounded-3xl animate-in fade-in duration-200 pointer-events-none">
                       <UploadCloud size={48} className="mb-2 text-emerald-600 upload-float" />
                       <span className="font-semibold text-emerald-700 text-lg">释放文件以上传</span>
                    </div>
                  )}

                  {uploadedFiles.length > 0 && (
                    <div className="flex flex-wrap gap-2 px-6 pt-5 pb-1">
                      {uploadedFiles.map(file => (
                        <div key={file.id} className="group/file flex items-center gap-2 bg-white border border-gray-200 pl-2 pr-1 py-1 rounded-lg shadow-sm text-sm hover:border-indigo-200 transition-colors select-none animate-in zoom-in duration-200 overflow-hidden">
                          {isImageFile(file.name) ? <ImageIcon size={16} className="text-purple-500" /> : getFileIcon(file.name)}
                          <span className="max-w-[120px] truncate font-medium text-gray-700" title={file.name}>{file.name}</span>
                          <button onClick={() => removeFile(file.id)} className="rounded-md p-1 text-slate-500 transition-colors hover:bg-red-50 hover:text-red-500"><X size={14} /></button>
                        </div>
                      ))}
                    </div>
                  )}
                  
                  <textarea 
                    id="assistant-composer-input"
                    aria-label="向智能助教提问"
                    ref={textareaRef} 
                    value={input} 
                    onChange={(e) => setInput(e.target.value)} 
                    onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && (e.preventDefault(), handleSend())} 
                    placeholder="描述你想学习、练习或规划的内容…"
                    className="min-h-[52px] w-full max-h-[160px] resize-none overflow-y-auto border-none bg-transparent px-5 py-3 text-gray-700 outline-none placeholder-gray-500 focus:ring-0 focus-visible:outline-none input-scrollbar"
                    rows="1" 
                  />
                  
                  <div className="flex items-center justify-between px-3 pb-2.5">
                    <div className="flex items-center gap-2">
                      <div className="relative" ref={toolMenuRef}>
                        <button
                          aria-label="工具与文件"
                          onClick={() => setIsToolMenuOpen(v => !v)}
                          disabled={isCurrentSessionLoading}
                          className={`rounded-full border p-2.5 transition-[color,background-color,border-color,box-shadow,transform] active:scale-95 ${isToolMenuOpen || isToolsEnabled || uploadedFiles.length > 0 ? 'border-emerald-200 bg-emerald-50 text-emerald-700 shadow-sm shadow-emerald-100' : 'border-transparent text-slate-600 hover:border-emerald-100 hover:bg-emerald-50/70 hover:text-emerald-700'}`}
                          title="工具与文件"
                        >
                          {isUploading ? <Loader2 size={20} className="animate-spin" /> : <Plus size={20} />}
                        </button>
                        {isToolMenuOpen && (
                          <div className="absolute bottom-full left-0 mb-2 w-64 rounded-2xl border border-emerald-100 bg-white shadow-xl shadow-emerald-100/60 p-2 z-30 animate-in fade-in zoom-in-95 duration-150">
                            <div className="px-3 py-2 text-[11px] font-bold text-gray-400 uppercase tracking-wider">文件上传</div>
                            <button onClick={() => { fileInputRef.current?.click(); setIsToolMenuOpen(false); }} disabled={isUploading || isCurrentSessionLoading} className="w-full flex items-center justify-between gap-3 px-3 py-2.5 rounded-xl hover:bg-emerald-50 text-sm transition-colors">
                              <span className="flex items-center gap-2 text-slate-700"><Paperclip size={16} className="text-emerald-500" />上传文件/图片</span>
                              {uploadedFiles.length > 0 && <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-100">{uploadedFiles.length} 个</span>}
                            </button>
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        aria-label={isRecording ? '停止语音输入' : '开始语音输入'}
                        onClick={toggleRecording}
                        disabled={isCurrentSessionLoading || isProcessingVoice}
                        className={`
                           p-2.5 rounded-full transition-[color,background-color,box-shadow,transform] duration-200 flex items-center justify-center
                           ${isRecording 
                             ? 'bg-red-500 text-white animate-pulse-ring' 
                             : 'text-gray-500 hover:bg-gray-200/60 hover:text-gray-900'}
                        `}
                      >
                         {isProcessingVoice ? <Loader2 size={18} className="animate-spin text-gray-500" /> : isRecording ? <Square size={16} fill="currentColor" /> : <Mic size={20} />}
                      </button>

                      <button 
                        aria-label={isReviewingCurrentSession ? '回答已完成，正在审核' : isAnswerStreamingCurrentSession ? '停止生成' : '发送消息'}
                        onClick={isAnswerStreamingCurrentSession ? handleStop : handleSend}
                        disabled={isReviewingCurrentSession || (!isAnswerStreamingCurrentSession && ((!input.trim() && uploadedFiles.length === 0) || isCurrentSessionLoading))}
                        className={`
                          p-2.5 rounded-full transition-[color,background-color,box-shadow,transform] duration-200 flex items-center justify-center
                          ${isReviewingCurrentSession
                              ? 'bg-slate-100 text-slate-500 cursor-not-allowed'
                              : isAnswerStreamingCurrentSession 
                              ? 'bg-gray-900 text-white hover:bg-gray-700' 
                              : (input.trim() || uploadedFiles.length > 0) 
                                  ? 'bg-gray-900 text-white hover:bg-gray-700 transform hover:-translate-y-0.5 shadow-sm' 
                                  : 'bg-gray-200 text-gray-400 cursor-not-allowed'
                          }
                        `}
                        title={isReviewingCurrentSession ? '回答已完成，正在审核' : isAnswerStreamingCurrentSession ? '停止生成' : '发送消息'}
                      >
                        {isReviewingCurrentSession ? <ShieldCheck size={16} /> : isAnswerStreamingCurrentSession ? <Square size={16} fill="currentColor" /> : <Send size={18} />} 
                      </button>
                    </div>
                  </div>

                </div>
                <div className="mt-2 text-center text-xs font-medium text-gray-400">AI生成内容仅供学习参考，请结合教材与专业意见进行判断。</div>
              </div>
            </div>
          </>
        ) : (
          <div className="relative flex-1 bg-white text-gray-400">
            <div className="absolute left-4 right-4 top-4 z-50 flex flex-wrap items-center justify-between gap-2 sm:left-6 sm:right-6 sm:top-6">
              <div className="flex items-center gap-2">
                {!isSidebarOpen && (
                  <button aria-label="展开侧边栏" onClick={() => setIsSidebarOpen(true)} className="group p-2 rounded-2xl bg-white/90 shadow-sm shadow-emerald-100 border border-emerald-100 text-emerald-600 hover:text-white hover:bg-gradient-to-br hover:from-emerald-500 hover:to-teal-500 hover:shadow-md hover:shadow-emerald-200/70 transition-[color,background-color,border-color,box-shadow]"><ChevronsRight size={19} className="transition-transform group-hover:translate-x-0.5" /></button>
                )}
                {!embedded && shellConfig.assistantHomeAction && onBackHome ? (
                  <HomeButton onClick={onBackHome} label={shellConfig.assistantHomeAction.label} className="h-10 justify-center px-3 py-2" />
                ) : null}
              </div>
            </div>
            <div className="flex h-full flex-col items-center justify-center px-6 text-center">
              <div className="w-24 h-24 bg-gray-50 rounded-full flex items-center justify-center mb-6 border border-gray-100"><MessageSquare size={40} className="text-gray-300" /></div>
              <p className="text-lg font-medium text-gray-500">开始一个新的对话</p>
            </div>
          </div>
        )}
      </div>

      {feedbackDialog.isOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 backdrop-blur-sm px-4">
          <div className="w-full max-w-md rounded-2xl bg-white shadow-2xl border border-gray-100 p-5 animate-in fade-in zoom-in-95 duration-150">
            <div className="font-bold text-gray-800 mb-1">反馈这条回答</div>
            <div className="text-sm text-gray-500 mb-4">可以告诉我哪里不满意，后续会用于优化回答与个性化偏好。</div>
            <textarea
              value={feedbackDialog.reason}
              onChange={(e) => setFeedbackDialog(prev => ({ ...prev, reason: e.target.value, status: 'idle' }))}
              placeholder="例如：没有回答重点、建议不适合我、引用不准确……（可为空）"
              className="w-full h-28 rounded-xl border border-gray-200 bg-gray-50 p-3 text-sm outline-none focus:ring-2 focus:ring-indigo-100 focus:border-indigo-300 resize-none"
            />
            {feedbackDialog.status === 'error' && <div className="mt-2 text-xs text-red-500">提交失败，请稍后再试。</div>}
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={() => setFeedbackDialog({ isOpen: false, type: '', answer: '', messageId: null, reason: '', status: 'idle' })}
                className="px-4 py-2 rounded-xl text-sm text-gray-500 hover:bg-gray-100 transition-colors"
              >取消</button>
              <button
                onClick={() => {
                  setFeedbackDialog(prev => ({ ...prev, status: 'submitting' }));
                  submitFeedback({ type: feedbackDialog.type, answer: feedbackDialog.answer, messageId: feedbackDialog.messageId, reason: feedbackDialog.reason });
                }}
                disabled={feedbackDialog.status === 'submitting'}
                className="px-4 py-2 rounded-xl text-sm bg-gray-900 text-white hover:bg-gray-700 disabled:opacity-60 transition-colors"
              >{feedbackDialog.status === 'submitting' ? '提交中...' : '提交反馈'}</button>
            </div>
          </div>
        </div>
      )}

      {/* 🔥 Retrieval Sidebar Overlay */}
      <RetrievalSidebar 
        isOpen={isRightSidebarOpen} 
        onClose={() => setIsRightSidebarOpen(false)} 
        refs={rightSidebarContent.refs}
        query={rightSidebarContent.query}
        knowledge={rightSidebarContent.knowledge}
      />
    </div>
  );
};

export default ChatInterface;
