import React, { useEffect, useRef, useState } from 'react';
import {
  ArrowUp,
  Bot,
  Expand,
  History,
  Loader2,
  MessageSquarePlus,
  PanelRightClose,
  PanelRightOpen,
} from 'lucide-react';
import { buildAssistantGreeting, createNewAssistantState } from '../assistantDockModel';
import {
  compactAssistantContent,
  createAssistantSession,
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

export default function CompactAssistant({
  currentUser = 'User',
  dailyGoal = '',
  dailyFocus = '',
  preferredSessionId = null,
  initialContext = '',
  contextLabel = '当前学习任务',
  initiallyCollapsed = false,
  quickActions = defaultQuickActions,
  onCollapsedChange,
  onFloatingDockChange,
  onOpenFull,
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
  const dragRef = useRef(null);
  const suppressExpandRef = useRef(false);
  const sessionGenerationRef = useRef(0);
  const [dragging, setDragging] = useState(false);
  const [floatingPosition, setFloatingPosition] = useState(null);
  const [characterFailed, setCharacterFailed] = useState(false);
  const [characterPose, setCharacterPose] = useState('center');

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
        const preferred = preferredSessionId
          ? nextSessions.find((session) => session.id === preferredSessionId)
          : null;
        if (preferred) {
          setSessionId(preferred.id);
          localStorage.setItem('lastSessionId', preferred.id);
          const history = await loadAssistantMessages(preferred.id);
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

  const startNewConversation = () => {
    sessionGenerationRef.current += 1;
    abortRef.current?.abort();
    const fresh = createNewAssistantState();
    setSessionId(fresh.sessionId);
    setMessages(fresh.messages);
    setInput('');
    setError('');
    setHistoryOpen(false);
  };

  const selectSession = async (nextSessionId) => {
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

  useEffect(() => {
    if (!dragging) return undefined;

    const moveFloatingAssistant = (event) => {
      const drag = dragRef.current;
      if (!drag) return;
      const deltaX = event.clientX - drag.pointerX;
      const deltaY = event.clientY - drag.pointerY;
      if (Math.hypot(deltaX, deltaY) > 4) drag.moved = true;
      if (collapsed) {
        setCharacterPose(deltaX < -10 ? 'left' : deltaX > 10 ? 'right' : 'center');
      }
      const maxLeft = Math.max(8, window.innerWidth - drag.width - 8);
      const maxTop = Math.max(8, window.innerHeight - drag.height - 8);
      const nextLeft = Math.min(maxLeft, Math.max(8, drag.left + deltaX));
      const nextTop = Math.min(maxTop, Math.max(8, drag.top + deltaY));
      drag.lastLeft = nextLeft;
      drag.lastTop = nextTop;
      setFloatingPosition({
        left: nextLeft,
        top: nextTop,
      });
    };
    const finishFloatingDrag = () => {
      const drag = dragRef.current;
      suppressExpandRef.current = Boolean(drag?.moved);
      if (drag?.moved) {
        const rightGap = window.innerWidth - ((drag.lastLeft ?? drag.left) + drag.width);
        const bottomGap = window.innerHeight - ((drag.lastTop ?? drag.top) + drag.height);
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

  const startFloatingDrag = (event, allowInteractiveTarget = false) => {
    if (!allowInteractiveTarget && event.target.closest('button, a, input, textarea, select')) return;
    const rect = floatingRef.current?.getBoundingClientRect();
    if (!rect) return;
    suppressExpandRef.current = false;
    dragRef.current = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      left: rect.left,
      top: rect.top,
      width: rect.width || 56,
      height: rect.height || 56,
      moved: false,
    };
    setDragging(true);
  };

  const restoreAssistant = () => {
    if (suppressExpandRef.current) {
      suppressExpandRef.current = false;
      return;
    }
    setFloatingPosition((current) => {
      if (!current) return current;
      const expandedWidth = Math.min(320, Math.max(0, window.innerWidth - 16));
      const expandedHeight = Math.min(560, Math.max(0, window.innerHeight - 16));
      return {
        left: Math.min(Math.max(8, window.innerWidth - expandedWidth - 8), Math.max(8, current.left)),
        top: Math.min(Math.max(8, window.innerHeight - expandedHeight - 8), Math.max(8, current.top)),
      };
    });
    setCollapsed(false);
    onCollapsedChange?.(false);
  };

  const floatingStyle = floatingPosition ? {
    left: floatingPosition.left,
    top: floatingPosition.top,
    right: 'auto',
    bottom: 'auto',
  } : undefined;

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
      await streamAssistantMessage(activeSessionId, content, {
        signal: abortRef.current.signal,
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

  if (collapsed) {
    return (
      <aside
        ref={floatingRef}
        className={`compact-assistant is-collapsed${dragging ? ' is-dragging' : ''} ${className}`.trim()}
        aria-label="常驻智能助教"
        data-state="collapsed"
        data-floating="true"
        style={floatingStyle}
      >
        <button
          type="button"
          className="compact-assistant__restore"
          aria-label="展开智能助教"
          title="拖拽移动，点击展开智能助教"
          onPointerDown={(event) => startFloatingDrag(event, true)}
          onClick={restoreAssistant}
        >
          {!characterFailed ? (
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
              <span className="compact-assistant__character-hint">点击问我</span>
            </span>
          ) : (
            <span className="compact-assistant__character-fallback" data-testid="assistant-character-fallback" aria-hidden="true">
              <Bot size={21} />
              <PanelRightOpen size={15} />
            </span>
          )}
        </button>
      </aside>
    );
  }

  return (
    <aside
      ref={floatingRef}
      className={`compact-assistant${dragging ? ' is-dragging' : ''} ${className}`.trim()}
      aria-label="常驻智能助教"
      data-state="workspace"
      data-floating="true"
      data-history-open={String(historyOpen)}
      style={floatingStyle}
    >
      <header
        className="compact-assistant__header"
        data-drag-handle="true"
        onPointerDown={startFloatingDrag}
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
          <div><strong>智能助教</strong><small>李时珍 · 中医药专项助手</small></div>
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
            onClick={() => {
              setHistoryOpen(false);
              setCollapsed(true);
              onCollapsedChange?.(true);
            }}
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
