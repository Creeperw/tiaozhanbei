import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  Bot,
  Expand,
  History,
  Loader2,
  MessageSquarePlus,
  PanelRightClose,
  PanelRightOpen,
  Maximize2,
  Minimize2,
} from 'lucide-react';
import {
  buildAssistantGreeting,
  createNewAssistantState,
  resolveAssistantSessionId,
} from '../assistantDockModel';
import {
  compactAssistantContent,
  createAssistantSession,
  getAssistantPendingRun,
  listAssistantSessions,
  loadAssistantMessages,
  streamAssistantMessage,
} from '../chatSessionClient';

function messageId(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

const defaultQuickActions = [
  { label: '解释当前内容', prompt: '请解释当前内容，并结合学习目标说明关键概念。' },
  { label: '生成练习', prompt: '请根据当前内容生成一组由浅入深的练习题。' },
  { label: '对比资料', prompt: '请对比当前资料与知识库中的相关内容，并指出异同。' },
  { label: '总结重点', prompt: '请总结当前内容的重点、易错点和复习建议。' },
];

const assistantCharacterImages = {
  center: '/assistant-character/lizhizhen-center-cutout.png',
  left: '/assistant-character/lizhizhen-left-cutout.png',
  right: '/assistant-character/lizhizhen-right-cutout.png',
};

const CHARACTER_BASE_WIDTH = 40;
const CHARACTER_BASE_HEIGHT = 70;

const executionEventLabels = {
  handoff_prepared: '按需通信',
  handoff_blocked: '通信信息不足',
  repair_planned: '已生成局部修复链',
  repair_step_started: '局部修复执行中',
  repair_completed: '局部修复完成',
  repair_stopped: '局部修复已停止',
};

function eventStepSummary(event) {
  if (Array.isArray(event.rerun_step_ids) && event.rerun_step_ids.length > 0) {
    return event.rerun_step_ids.join(' → ');
  }
  return event.step_id || event.trigger_step_id || '';
}

export default function CompactAssistant({
  currentUser = 'User',
  dailyGoal = '',
  dailyFocus = '',
  preferredSessionId = null,
  initialContext = '',
  contextLabel = '当前学习任务',
  initiallyCollapsed = false,
  floating = true,
  quickActions = defaultQuickActions,
  onCollapsedChange,
  onFloatingDockChange,
  onOpenFull,
  characterHint = '点击问我',
  executionEvents = [],
  readPageContext = null,
  className = '',
}) {
  const [sessionId, setSessionId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [sessions, setSessions] = useState([]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(initiallyCollapsed);
  const [input, setInput] = useState(initialContext);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState('');
  const abortRef = useRef(null);
  const endRef = useRef(null);
  const floatingRef = useRef(null);
  const characterHitAreaRef = useRef(null);
  const dragRef = useRef(null);
  const suppressExpandRef = useRef(false);
  const collapsedPositionRef = useRef(null);
  const sessionGenerationRef = useRef(0);
  const [dragging, setDragging] = useState(false);
  const [floatingPosition, setFloatingPosition] = useState(null);
  const [characterFailed, setCharacterFailed] = useState(false);
  const [characterPose, setCharacterPose] = useState('center');
  const [characterScale, setCharacterScale] = useState(() => {
    const saved = localStorage.getItem('compactAssistantCharacterScale');
    const parsed = saved ? parseFloat(saved) : 1.0;
    return Number.isFinite(parsed) ? Math.max(0.5, Math.min(3.0, parsed)) : 1.0;
  });
  const scaleSaveTimerRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    const initialize = async () => {
      setLoading(true);
      setError('');
      try {
        const items = await listAssistantSessions();
        const nextSessions = Array.isArray(items) ? items : [];
        if (cancelled) return;
        setSessions(nextSessions);
        const restoredId = resolveAssistantSessionId(
          nextSessions,
          preferredSessionId,
          localStorage.getItem('lastSessionId'),
        );
        if (restoredId) {
          setSessionId(restoredId);
          localStorage.setItem('lastSessionId', restoredId);
          const history = await loadAssistantMessages(restoredId);
          if (!cancelled) setMessages(Array.isArray(history) ? history : []);
        } else {
          const fresh = createNewAssistantState();
          setSessionId(fresh.sessionId);
          setMessages(fresh.messages);
        }
      } catch (loadError) {
        if (!cancelled) setError(loadError.message || '聊天记录加载失败');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    initialize();
    return () => {
      cancelled = true;
      abortRef.current?.abort();
    };
  }, [preferredSessionId]);

  useEffect(() => {
    if (!sessionId) return undefined;
    let cancelled = false;
    let timer = null;
    const syncBackgroundRun = async () => {
      try {
        const pending = await getAssistantPendingRun(sessionId);
        if (cancelled || !pending) return;
        if (pending.status === 'running') {
          setSending(true);
          setError('当前会话任务正在后台运行，切换页面不会中断。');
          timer = window.setTimeout(syncBackgroundRun, 2000);
          return;
        }
        setSending(false);
        if (['completed', 'interrupted', 'waiting_human_review'].includes(pending.status)) {
          const history = await loadAssistantMessages(sessionId);
          if (!cancelled) {
            setMessages(Array.isArray(history) ? history : []);
            setError('');
          }
        }
      } catch (runError) {
        if (!cancelled) setError(runError.message || '后台任务状态恢复失败');
      }
    };
    void syncBackgroundRun();
    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, [sessionId]);

  const startNewConversation = () => {
    sessionGenerationRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    const fresh = createNewAssistantState();
    setSessionId(fresh.sessionId);
    setMessages(fresh.messages);
    setInput('');
    setError('');
    setHistoryOpen(false);
  };

  const selectSession = async (nextSessionId) => {
    abortRef.current?.abort();
    abortRef.current = null;
    setSending(false);
    const generation = sessionGenerationRef.current + 1;
    sessionGenerationRef.current = generation;
    setLoading(true);
    setError('');
    try {
      const items = await loadAssistantMessages(nextSessionId);
      if (sessionGenerationRef.current !== generation) return;
      setSessionId(nextSessionId);
      setMessages(Array.isArray(items) ? items : []);
      localStorage.setItem('lastSessionId', nextSessionId);
      setHistoryOpen(false);
    } catch (loadError) {
      if (sessionGenerationRef.current === generation) {
        setError(loadError.message || '聊天记录加载失败');
      }
    } finally {
      if (sessionGenerationRef.current === generation) setLoading(false);
    }
  };

  useEffect(() => {
    if (typeof endRef.current?.scrollIntoView === 'function') {
      endRef.current.scrollIntoView({ block: 'nearest' });
    }
  }, [messages, sending]);

  // Collapsed drag: pointerdown only records start position, does NOT set dragging
  useEffect(() => {
    if (!dragging || !collapsed) return undefined;
    const drag = dragRef.current;
    if (!drag) return undefined;

    const threshold = drag.pointerType === 'touch' ? 12 : 8;

    const moveFloatingAssistant = (event) => {
      const deltaX = event.clientX - drag.startX;
      const deltaY = event.clientY - drag.startY;
      const distance = Math.hypot(deltaX, deltaY);
      if (!drag.active && distance >= threshold) {
        drag.active = true;
        drag.originLeft = drag.left;
        drag.originTop = drag.top;
      }
      if (!drag.active) return;
      drag.moved = true;
      if (collapsed) {
        setCharacterPose(deltaX < -10 ? 'left' : deltaX > 10 ? 'right' : 'center');
      }
      const maxLeft = Math.max(8, window.innerWidth - drag.width - 8);
      const maxTop = Math.max(8, window.innerHeight - drag.height - 8);
      const nextLeft = Math.min(maxLeft, Math.max(8, drag.originLeft + deltaX));
      const nextTop = Math.min(maxTop, Math.max(8, drag.originTop + deltaY));
      drag.lastLeft = nextLeft;
      drag.lastTop = nextTop;
      setFloatingPosition({
        left: nextLeft,
        top: nextTop,
      });
    };
    const finishFloatingDrag = () => {
      suppressExpandRef.current = Boolean(drag?.moved && drag?.active);
      if (drag?.moved && drag?.active) {
        const rightGap = window.innerWidth - ((drag.lastLeft ?? drag.originLeft ?? drag.left) + drag.width);
        const bottomGap = window.innerHeight - ((drag.lastTop ?? drag.originTop ?? drag.top) + drag.height);
        onFloatingDockChange?.(rightGap <= 32 && bottomGap <= 32);
      }
      dragRef.current = null;
      setCharacterPose('center');
      setDragging(false);
    };

    window.addEventListener('pointermove', moveFloatingAssistant);
    window.addEventListener('pointerup', finishFloatingDrag);
    window.addEventListener('pointercancel', finishFloatingDrag);
    return () => {
      window.removeEventListener('pointermove', moveFloatingAssistant);
      window.removeEventListener('pointerup', finishFloatingDrag);
      window.removeEventListener('pointercancel', finishFloatingDrag);
    };
  }, [collapsed, dragging, onFloatingDockChange]);

  // Expanded header drag remains unchanged
  useEffect(() => {
    if (!dragging || collapsed) return undefined;
    const drag = dragRef.current;
    if (!drag) return undefined;

    const moveFloatingAssistant = (event) => {
      const deltaX = event.clientX - drag.startX;
      const deltaY = event.clientY - drag.startY;
      if (Math.hypot(deltaX, deltaY) > 4) drag.moved = true;
      const maxLeft = Math.max(8, window.innerWidth - drag.width - 8);
      const maxTop = Math.max(8, window.innerHeight - drag.height - 8);
      const nextLeft = Math.min(maxLeft, Math.max(8, drag.originLeft + deltaX));
      const nextTop = Math.min(maxTop, Math.max(8, drag.originTop + deltaY));
      drag.lastLeft = nextLeft;
      drag.lastTop = nextTop;
      setFloatingPosition({
        left: nextLeft,
        top: nextTop,
      });
    };
    const finishFloatingDrag = () => {
      suppressExpandRef.current = Boolean(drag?.moved);
      if (drag?.moved) {
        const rightGap = window.innerWidth - ((drag.lastLeft ?? drag.originLeft ?? drag.left) + drag.width);
        const bottomGap = window.innerHeight - ((drag.lastTop ?? drag.originTop ?? drag.top) + drag.height);
        onFloatingDockChange?.(rightGap <= 32 && bottomGap <= 32);
      }
      dragRef.current = null;
      setDragging(false);
    };

    window.addEventListener('pointermove', moveFloatingAssistant);
    window.addEventListener('pointerup', finishFloatingDrag);
    window.addEventListener('pointercancel', finishFloatingDrag);
    return () => {
      window.removeEventListener('pointermove', moveFloatingAssistant);
      window.removeEventListener('pointerup', finishFloatingDrag);
      window.removeEventListener('pointercancel', finishFloatingDrag);
    };
  }, [collapsed, dragging, onFloatingDockChange]);

  useEffect(() => {
    if (!floating) return undefined;
    const keepAssistantInViewport = () => {
      const rect = floatingRef.current?.getBoundingClientRect();
      if (!rect) return;
      const actualWidth = rect.width;
      const actualHeight = rect.height;
      setFloatingPosition((current) => {
        if (!current) return current;
        if (!collapsed) {
          const width = Math.min(320, Math.max(0, window.innerWidth - 16));
          const height = Math.min(560, Math.max(0, window.innerHeight - 16));
          return {
            left: Math.min(Math.max(8, window.innerWidth - width - 8), Math.max(8, current.left)),
            top: Math.min(Math.max(8, window.innerHeight - height - 8), Math.max(8, current.top)),
          };
        }
        return {
          left: Math.min(Math.max(8, window.innerWidth - actualWidth - 8), Math.max(8, current.left)),
          top: Math.min(Math.max(8, window.innerHeight - actualHeight - 8), Math.max(8, current.top)),
        };
      });
    };
    window.addEventListener('resize', keepAssistantInViewport);
    return () => window.removeEventListener('resize', keepAssistantInViewport);
  }, [collapsed, floating]);

  const startFloatingDrag = (event, allowInteractiveTarget = false) => {
    if (!allowInteractiveTarget && event.target.closest('button, a, input, textarea, select')) return;
    const rect = floatingRef.current?.getBoundingClientRect();
    if (!rect) return;
    suppressExpandRef.current = false;
    dragRef.current = {
      pointerType: event.pointerType || 'mouse',
      startX: event.clientX,
      startY: event.clientY,
      left: rect.left,
      top: rect.top,
      originLeft: rect.left,
      originTop: rect.top,
      width: rect.width || 56,
      height: rect.height || 56,
      moved: false,
      active: false,
    };
    setDragging(true);
  };

  const restoreAssistant = () => {
    if (suppressExpandRef.current) {
      suppressExpandRef.current = false;
      return;
    }
    const rect = floatingRef.current?.getBoundingClientRect();
    const collapsedPosition = floatingPosition || (rect ? {
      left: rect.left,
      top: rect.top,
    } : null);
    if (floating && collapsedPosition) {
      collapsedPositionRef.current = collapsedPosition;
    }
    setFloatingPosition(() => {
      if (!collapsedPosition) return null;
      const expandedWidth = Math.min(320, Math.max(0, window.innerWidth - 16));
      const expandedHeight = Math.min(560, Math.max(0, window.innerHeight - 16));
      return {
        left: Math.min(Math.max(8, window.innerWidth - expandedWidth - 8), Math.max(8, collapsedPosition.left)),
        top: Math.min(Math.max(8, window.innerHeight - expandedHeight - 8), Math.max(8, collapsedPosition.top)),
      };
    });
    setCollapsed(false);
    onCollapsedChange?.(false);
  };

  const collapseAssistant = () => {
    setHistoryOpen(false);
    if (floating && collapsedPositionRef.current) {
      setFloatingPosition(collapsedPositionRef.current);
    }
    setCollapsed(true);
    onCollapsedChange?.(true);
  };

  const clampViewportX = (left, width) => Math.max(8, Math.min(window.innerWidth - width - 8, left));
  const clampViewportY = (top, height) => Math.max(8, Math.min(window.innerHeight - height - 8, top));

  const saveCharacterScale = useCallback((scale) => {
    if (scaleSaveTimerRef.current) clearTimeout(scaleSaveTimerRef.current);
    scaleSaveTimerRef.current = setTimeout(() => {
      localStorage.setItem('compactAssistantCharacterScale', String(scale));
    }, 300);
  }, []);

  // Synchronous scale + position update: both state changes happen in the same updater,
  // so React 18 batching commits them together — no two-frame jump.
  const handleScaleChange = useCallback((delta, reset = false) => {
    setCharacterScale((prevScale) => {
      const nextScale = reset ? 1.0 : Math.max(0.5, Math.min(3.0, prevScale + delta));
      saveCharacterScale(nextScale);

      // Compute new position to keep bottom-center stable — no rAF, no getBoundingClientRect
      setFloatingPosition((pos) => {
        if (!pos) return pos;
        const oldW = CHARACTER_BASE_WIDTH * prevScale;
        const oldH = CHARACTER_BASE_HEIGHT * prevScale;
        const newW = CHARACTER_BASE_WIDTH * nextScale;
        const newH = CHARACTER_BASE_HEIGHT * nextScale;
        const left = pos.left + (oldW - newW) / 2;
        const top = pos.top + (oldH - newH);
        return {
          left: clampViewportX(left, newW),
          top: clampViewportY(top, newH),
        };
      });

      return nextScale;
    });
  }, [saveCharacterScale]);

  const handleCharacterWheel = useCallback((event) => {
    if (!event.deltaY) return;
    event.preventDefault();
    event.stopPropagation();
    const pageUnit = Math.max(window.innerHeight, 1);
    const deltaPixels = event.deltaMode === 1
      ? event.deltaY * 16
      : event.deltaMode === 2
        ? event.deltaY * pageUnit
        : event.deltaY;
    const magnitude = Math.min(0.12, Math.max(0.005, Math.abs(deltaPixels) * 0.0015));
    handleScaleChange(deltaPixels < 0 ? magnitude : -magnitude);
  }, [handleScaleChange]);

  useEffect(() => {
    const hitArea = characterHitAreaRef.current;
    if (!collapsed || !hitArea) return undefined;
    hitArea.addEventListener('wheel', handleCharacterWheel, { passive: false });
    return () => hitArea.removeEventListener('wheel', handleCharacterWheel);
  }, [collapsed, handleCharacterWheel]);

  useEffect(() => () => {
    if (scaleSaveTimerRef.current) clearTimeout(scaleSaveTimerRef.current);
  }, []);

  const floatingStyle = floatingPosition ? {
    left: floatingPosition.left,
    top: floatingPosition.top,
    right: 'auto',
    bottom: 'auto',
  } : undefined;

  const visualWidth = CHARACTER_BASE_WIDTH * characterScale;
  const visualHeight = CHARACTER_BASE_HEIGHT * characterScale;
  const hitWidth = Math.max(44, visualWidth);
  const hitHeight = Math.max(60, visualHeight);

  const send = async () => {
    const content = input.trim();
    if (!content || sending) return;
    setSending(true);
    setError('');
    let activeSessionId = sessionId;
    let assistantId = '';
    try {
      if (!activeSessionId) {
        const created = await createAssistantSession();
        activeSessionId = created.id;
        setSessionId(activeSessionId);
        localStorage.setItem('lastSessionId', activeSessionId);
      }
      const userMessage = { id: messageId('user'), role: 'user', content };
      assistantId = messageId('assistant');
      setMessages((current) => [...current, userMessage, {
        id: assistantId,
        role: 'assistant',
        content: '',
        pending: true,
      }]);
      setInput('');
      abortRef.current = new AbortController();
      let currentPage = null;
      try {
        currentPage = typeof readPageContext === 'function' ? readPageContext() : null;
      } catch {
        currentPage = null;
      }
      await streamAssistantMessage(activeSessionId, content, {
        signal: abortRef.current.signal,
        currentPage,
        onUpdate: (answer) => {
          setMessages((current) => current.map((message) => (
            message.id === assistantId
              ? { ...message, content: answer, pending: true }
              : message
          )));
        },
      });
      setMessages((current) => current.map((message) => (
        message.id === assistantId ? { ...message, pending: false } : message
      )));
    } catch (sendError) {
      if (assistantId) {
        setMessages((current) => current.map((message) => (
          message.id === assistantId ? { ...message, pending: false } : message
        )));
      }
      setError(sendError.name === 'AbortError' ? '回答已停止' : (sendError.message || '发送失败'));
    } finally {
      setSending(false);
      abortRef.current = null;
    }
  };

  const visibleMessages = messages
    .map((message) => ({
      ...message,
      content: message.role === 'assistant'
        ? compactAssistantContent(message.content)
        : message.content,
    }))
    .filter((message) => message.content || message.pending)
    .slice(-8);
  const greeting = buildAssistantGreeting({
    username: currentUser,
    goal: dailyGoal,
    focus: dailyFocus,
  });
  const visibleExecutionEvents = executionEvents
    .filter((event) => executionEventLabels[event?.event])
    .slice(-6);

  const handleCollapsedPointerDown = (event) => {
    startFloatingDrag(event, true);
  };

  if (collapsed) {
    return (
      <aside
        ref={floatingRef}
        className={`compact-assistant is-collapsed${dragging ? ' is-dragging' : ''} ${className}`.trim()}
        aria-label="常驻智能助教"
        data-state="collapsed"
        data-floating={String(floating)}
        style={{
          '--character-scale': characterScale,
          '--character-visual-width': `${visualWidth}px`,
          '--character-visual-height': `${visualHeight}px`,
          '--character-hit-width': `${hitWidth}px`,
          '--character-hit-height': `${hitHeight}px`,
          ...(floating ? floatingStyle : undefined),
        }}
      >
        <div className="compact-assistant__collapsed-controls">
          <div
            ref={characterHitAreaRef}
            className="compact-assistant__hit-area"
            onPointerDown={floating ? handleCollapsedPointerDown : undefined}
          >
            {!characterFailed ? (
              <button
                type="button"
                className="compact-assistant__restore"
                aria-label="展开智能助教"
                title="拖拽移动，滚轮缩放，点击展开智能助教"
                onClick={restoreAssistant}
              >
                <span
                  className="compact-assistant__character"
                  data-pose={characterPose}
                  data-testid="lizhizhen-assistant-character"
                  aria-hidden="true"
                >
                  <span className="compact-assistant__character-shadow" />
                  <span className="compact-assistant__character-figure">
                    {Object.entries(assistantCharacterImages).map(([pose, src]) => (
                      <img
                        key={pose}
                        src={src}
                        alt=""
                        data-pose={pose}
                        draggable="false"
                        onError={() => setCharacterFailed(true)}
                      />
                    ))}
                  </span>
                  <span className="compact-assistant__character-hint">{characterHint}</span>
                </span>
              </button>
            ) : (
              <button
                type="button"
                className="compact-assistant__restore"
                aria-label="展开智能助教"
                onClick={restoreAssistant}
              >
                <span className="compact-assistant__character-fallback" data-testid="assistant-character-fallback" aria-hidden="true">
                  <Bot size={21} />
                  <PanelRightOpen size={15} />
                </span>
              </button>
            )}
          </div>
        </div>
      </aside>
    );
  }

  return (
    <aside
      ref={floatingRef}
      className={`compact-assistant${dragging ? ' is-dragging' : ''} ${className}`.trim()}
      aria-label="常驻智能助教"
      data-state="workspace"
      data-floating={String(floating)}
      data-history-open={String(historyOpen)}
      style={floating ? floatingStyle : undefined}
    >
      <header
        className="compact-assistant__header"
        data-drag-handle="true"
        onPointerDown={floating ? startFloatingDrag : undefined}
      >
        <div className="compact-assistant__identity">
          <span className="compact-assistant__avatar">
            {characterFailed ? (
              <Bot aria-hidden="true" size={18} />
            ) : (
              <img
                src={assistantCharacterImages.center}
                alt=""
                aria-hidden="true"
                draggable="false"
                onError={() => setCharacterFailed(true)}
              />
            )}
          </span>
          <div className="flex flex-col gap-0.5">
            <strong className="text-lg font-bold text-slate-900">智能助教</strong>
            <small className="text-sm font-semibold text-[#2E7D32] bg-[#E8F5E9] px-2 py-0.5 rounded-full">多智能体按需协作</small>
          </div>
        </div>
        <div className="compact-assistant__controls">
          <button
            type="button"
            aria-label="查看历史对话"
            title="查看历史对话"
            aria-expanded={historyOpen}
            onClick={() => setHistoryOpen((value) => !value)}
          ><History aria-hidden="true" size={15} /></button>
          <button type="button" aria-label="新建对话" title="新建对话" onClick={startNewConversation}>
            <MessageSquarePlus aria-hidden="true" size={15} />
          </button>
          <button
            type="button"
            aria-label="折叠智能助教"
            title="折叠智能助教"
            onClick={collapseAssistant}
          ><PanelRightClose aria-hidden="true" size={15} /></button>
          <button
            type="button"
            aria-label="打开完整智能助教"
            title="打开完整智能助教"
            onClick={() => onOpenFull?.(sessionId)}
          ><Expand aria-hidden="true" size={15} /></button>
        </div>
      </header>

      <div className="compact-assistant__context" aria-label="助教当前上下文">
        <span>当前上下文</span>
        <strong>{contextLabel}</strong>
      </div>
      <div className="compact-assistant__orchestration" aria-label="多智能体协作能力">
        <span><i aria-hidden="true" />按任务自动组队</span>
        <span><i aria-hidden="true" />需要时检索与审核</span>
      </div>

      {visibleExecutionEvents.length > 0 && (
        <section className="border-b border-slate-100 px-3 py-2" aria-label="执行协调摘要">
          <div className="space-y-1.5">
            {visibleExecutionEvents.map((event, index) => (
              <div key={`${event.event}-${event.repair_id || event.step_id || index}`} className="flex items-center justify-between gap-2 text-xs">
                <span className="font-medium text-slate-700">{executionEventLabels[event.event]}</span>
                <span className="truncate text-slate-500">
                  {[eventStepSummary(event), event.status].filter(Boolean).join(' · ')}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {historyOpen && (
        <section className="compact-assistant__history" role="dialog" aria-label="历史对话">
          <header><strong>历史对话</strong><small>{sessions.length} 条</small></header>
          <div>
            {sessions.map((session) => (
              <button
                key={session.id}
                type="button"
                className={session.id === sessionId ? 'is-active' : undefined}
                onClick={() => selectSession(session.id)}
              >
                <span>{session.title || '未命名对话'}</span>
                {session.updated_at && <small>{session.updated_at}</small>}
              </button>
            ))}
            {sessions.length === 0 && <p>还没有历史对话</p>}
          </div>
        </section>
      )}

      <div className="compact-assistant__messages" aria-live="polite">
        {!loading && visibleMessages.length === 0 && (
          <div className="compact-assistant__message is-assistant">
            <span><Bot aria-hidden="true" size={12} /></span>
            <p>{greeting}</p>
          </div>
        )}
        {loading && <div className="compact-assistant__loading"><Loader2 aria-hidden="true" size={16} />正在恢复会话…</div>}
        {visibleMessages.map((message) => (
          <div key={message.id} className={`compact-assistant__message is-${message.role}`}>
            {message.role === 'assistant' && <span><Bot aria-hidden="true" size={12} /></span>}
            <p>{message.content || '正在思考…'}</p>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      {error && <div className="compact-assistant__error" role="alert">{error}</div>}
      <div className="compact-assistant__quick-actions" aria-label="助教快捷操作">
        {quickActions.map((action) => (
          <button
            key={action.label}
            type="button"
            onClick={() => setInput(action.prompt)}
          >
            {action.label}
          </button>
        ))}
      </div>
      <form
        className="compact-assistant__composer"
        onSubmit={(event) => { event.preventDefault(); send(); }}
      >
        <label>
          <span className="sr-only">向智能助教提问</span>
          <textarea
            aria-label="向智能助教提问"
            name="assistant-message"
            rows={1}
            placeholder="向李时珍提问…"
            value={input}
            disabled={sending}
            onChange={(event) => setInput(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault();
                send();
              }
            }}
          />
        </label>
        <button type="submit" aria-label="发送问题" disabled={!input.trim() || sending}>
          {sending ? <Loader2 aria-hidden="true" size={14} /> : <ArrowUp aria-hidden="true" size={14} />}
        </button>
      </form>
    </aside>
  );
}
