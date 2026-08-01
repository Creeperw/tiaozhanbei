import React, { useCallback, useEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  History,
  Loader2,
  Maximize2,
  Minimize2,
  Plus,
  Send,
  Sparkles,
  Trash2,
  X,
} from 'lucide-react';
import {
  createPdfAiSession,
  deletePdfAiSession,
  loadPdfAiSessionMessages,
  loadPdfAiSessions,
  streamPdfAi,
} from './textbookPdfApi';

const SUMMARY_PROMPT = (bookTitle, pageNumber) => (
  `请总结《${bookTitle}》第 ${pageNumber} 页的电子教材内容，要求：\n`
  + '1. 用 150-300 字概括本页核心知识点；\n'
  + '2. 分点列出 2-4 个要点，方便记忆；\n'
  + '3. 结尾给出 1 个思考问题。\n'
  + '请直接输出总结内容，不要客套。'
);

const SYSTEM_PROMPT = (bookTitle, pageNumber) => (
  `你是《${bookTitle}》电子教材第 ${pageNumber} 页的 AI 助教，`
  + '负责帮助学习者理解本页内容。回答基于教材正文，保持中医学科严谨性，'
  + '不过度发挥；如正文未覆盖则说明。涉及诊疗内容须提示“不能替代专业诊断”。'
);

const DEFAULT_SIZE = { width: 560, height: 620 };
const MIN_SIZE = { width: 380, height: 400 };

function MarkdownMessage({ content }) {
  return (
    <div className="textbook-pdf-ai__markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
    </div>
  );
}

export default function PdfAiPanel({ bookId, bookTitle, pageNumber, onClose }) {
  const [sessionId, setSessionId] = useState(null);
  const [sessions, setSessions] = useState([]);
  const [messages, setMessages] = useState([]);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [position, setPosition] = useState(null);
  const [size, setSize] = useState(DEFAULT_SIZE);
  const panelRef = useRef(null);
  const dragRef = useRef(null);
  const resizeRef = useRef(null);
  const controllerRef = useRef(null);
  const messagesEndRef = useRef(null);
  const sessionRef = useRef(null);
  sessionRef.current = sessionId;

  useEffect(() => () => {
    controllerRef.current?.abort();
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'end' });
  }, [messages]);

  const openSession = useCallback(async (targetId) => {
    controllerRef.current?.abort();
    setBusy(false);
    setError('');
    setSessionId(targetId);
    setMessages([]);
    if (!targetId) return;
    try {
      const payload = await loadPdfAiSessionMessages(targetId);
      setMessages((payload.messages || []).filter(
        (item) => item.role === 'user' || item.role === 'assistant',
      ));
    } catch (reason) {
      setError(reason.message || '会话记录加载失败');
    }
  }, []);

  const refreshSessions = useCallback(async (preferredId = null) => {
    setSessionsLoading(true);
    try {
      const payload = await loadPdfAiSessions();
      setSessions(payload.sessions || []);
      if (preferredId) setSessionId(preferredId);
    } catch (reason) {
      setError(reason.message || '会话列表加载失败');
    } finally {
      setSessionsLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  const newSession = async () => {
    controllerRef.current?.abort();
    setError('');
    setBusy(false);
    setQuestion('');
    setMessages([]);
    setSessionsOpen(false);
    try {
      const payload = await createPdfAiSession('新对话');
      setSessionId(payload.session_id);
      await refreshSessions(payload.session_id);
    } catch (reason) {
      setError(reason.message || '新建会话失败');
    }
  };

  const removeSession = async (event, targetId) => {
    event.stopPropagation();
    try {
      await deletePdfAiSession(targetId);
      const next = sessions.filter((item) => item.id !== targetId);
      setSessions(next);
      if (sessionId === targetId) {
        setSessionId(null);
        setMessages([]);
      }
    } catch (reason) {
      setError(reason.message || '删除会话失败');
    }
  };

  const runStream = async (mode, questionText, history, onDelta) => {
    const controller = new AbortController();
    controllerRef.current?.abort();
    controllerRef.current = controller;
    let text = '';
    try {
      await streamPdfAi(bookId, pageNumber, {
        mode,
        question: questionText,
        history,
        sessionId: sessionRef.current,
        onDelta: (delta) => { text += delta; onDelta(text); },
        signal: controller.signal,
      });
      return text;
    } catch (reason) {
      if (reason?.name !== 'AbortError') throw reason;
      return text;
    }
  };

  const sendSummary = async () => {
    if (busy) return;
    setError('');
    setMessages((current) => [
      ...current,
      { role: 'user', content: `请总结《${bookTitle}》第 ${pageNumber} 页的内容` },
    ]);
    setMessages((current) => [...current, { role: 'assistant', content: '' }]);
    setBusy(true);
    try {
      await runStream(
        'summary',
        SUMMARY_PROMPT(bookTitle, pageNumber),
        [],
        (next) => setMessages((current) => current.map(
          (item, index) => (index === current.length - 1 ? { ...item, content: next } : item),
        )),
      );
      refreshSessions();
    } catch (reason) {
      setError(reason.message || '总结生成失败，请稍后重试');
    } finally {
      setBusy(false);
    }
  };

  const send = async () => {
    const value = question.trim();
    if (!value || busy) return;
    const history = messages
      .slice(-8)
      .map((item) => ({ role: item.role === 'user' ? 'user' : 'assistant', content: item.content }));
    setMessages((current) => [...current, { role: 'user', content: value }]);
    setQuestion('');
    setError('');
    setMessages((current) => [...current, { role: 'assistant', content: '' }]);
    setBusy(true);
    try {
      await runStream(
        'chat',
        value,
        history,
        (next) => setMessages((current) => current.map(
          (item, index) => (index === current.length - 1 ? { ...item, content: next } : item),
        )),
      );
      refreshSessions();
    } catch (reason) {
      setError(reason.message || '回答失败，请稍后重试');
    } finally {
      setBusy(false);
    }
  };

  // ── 拖动与缩放 ──
  const startDrag = (event) => {
    const panel = panelRef.current;
    if (!panel || event.button !== 0) return;
    const bounds = panel.getBoundingClientRect();
    const offsetX = event.clientX - bounds.left;
    const offsetY = event.clientY - bounds.top;
    const onMove = (moveEvent) => {
      setPosition({
        left: Math.max(8, Math.min(window.innerWidth - 120, moveEvent.clientX - offsetX)),
        top: Math.max(8, Math.min(window.innerHeight - 80, moveEvent.clientY - offsetY)),
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const startResize = (event) => {
    event.preventDefault();
    event.stopPropagation();
    const panel = panelRef.current;
    if (!panel) return;
    const startX = event.clientX;
    const startY = event.clientY;
    const startWidth = panel.offsetWidth;
    const startHeight = panel.offsetHeight;
    const onMove = (moveEvent) => {
      setSize({
        width: Math.max(MIN_SIZE.width, Math.min(window.innerWidth - 40, startWidth + (moveEvent.clientX - startX))),
        height: Math.max(MIN_SIZE.height, Math.min(window.innerHeight - 40, startHeight + (moveEvent.clientY - startY))),
      });
    };
    const onUp = () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
  };

  const currentSessions = sessions.filter((item) => item.id !== sessionId);

  return (
    <aside
      ref={panelRef}
      className={`textbook-pdf-ai${expanded ? ' is-expanded' : ''}`}
      role="dialog"
      aria-modal="false"
      aria-labelledby="textbook-pdf-ai-title"
      style={{
        ...(position ? { left: position.left, top: position.top } : {}),
        width: size.width,
        height: size.height,
      }}
    >
      <header onPointerDown={startDrag}>
        <span><Sparkles aria-hidden="true" size={18} /></span>
        <div>
          <strong id="textbook-pdf-ai-title">AI 助教</strong>
          <small>《{bookTitle}》 · 总结与问答</small>
        </div>
        <div className="textbook-pdf-ai__actions">
          <button type="button" aria-label={expanded ? '还原窗口' : '全屏窗口'} title={expanded ? '还原窗口' : '全屏窗口'} onClick={() => setExpanded((current) => !current)}>
            {expanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
          </button>
          <button type="button" aria-label="关闭 AI 助手" onClick={onClose}><X size={17} /></button>
        </div>
      </header>
      <div className="textbook-pdf-ai__toolbar">
        <button
          type="button"
          className={sessionsOpen ? 'is-active' : ''}
          aria-expanded={sessionsOpen}
          onClick={() => setSessionsOpen((current) => !current)}
        >
          <History size={14} />对话记录
        </button>
        <button type="button" onClick={newSession}><Plus size={14} />新建对话</button>
      </div>
      {sessionsOpen && (
        <div className="textbook-pdf-ai__session-list" aria-label="对话记录列表">
          {sessionsLoading ? (
            <p className="textbook-pdf-ai__loading"><Loader2 size={14} />加载会话列表…</p>
          ) : currentSessions.length > 0 ? (
            currentSessions.map((item) => (
              <button
                key={item.id}
                type="button"
                className={sessionId === item.id ? 'is-active' : ''}
                onClick={() => openSession(item.id)}
              >
                <span>{item.title || '新对话'}</span>
                <i
                  role="button"
                  aria-label={`删除会话：${item.title || '新对话'}`}
                  onClick={(event) => removeSession(event, item.id)}
                >
                  <Trash2 size={13} />
                </i>
              </button>
            ))
          ) : (
            <p className="textbook-pdf-ai__hint">暂无历史对话，点击「新建对话」开始。</p>
          )}
        </div>
      )}
      <div className="textbook-pdf-ai__messages" aria-live="polite">
        {messages.length === 0 && (
          <p className="textbook-pdf-ai__hint">
            对教材内容有疑问？点「总结本页」生成总结，或直接输入问题。
          </p>
        )}
        {messages.map((item, index) => (
          <div key={index} className={`textbook-pdf-ai__message is-${item.role === 'user' ? 'user' : 'ai'}`}>
            {item.role === 'user' ? (
              <p>{item.content}</p>
            ) : item.content ? (
              <MarkdownMessage content={item.content} />
            ) : (
              <p className="textbook-pdf-ai__typing" role="status"><Loader2 className="is-spinning" size={13} />正在思考…</p>
            )}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>
      {error && <p className="textbook-pdf-ai__error" role="alert">{error}</p>}
      <div className="textbook-pdf-ai__footer">
        <button type="button" className="textbook-pdf-ai__summary-btn" onClick={sendSummary} disabled={busy}>
          {busy ? <Loader2 className="is-spinning" size={14} /> : <Sparkles size={14} />}
          总结本页
        </button>
        <form className="textbook-pdf-ai__chat-form" onSubmit={(event) => { event.preventDefault(); send(); }}>
          <input
            aria-label="向 AI 提问"
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="围绕教材内容提问…"
            disabled={busy}
          />
          <button type="submit" aria-label="发送提问" disabled={busy || !question.trim()}>
            <Send size={15} />
          </button>
        </form>
      </div>
      <span
        className="textbook-pdf-ai__resize"
        aria-hidden="true"
        onPointerDown={startResize}
      />
    </aside>
  );
}
