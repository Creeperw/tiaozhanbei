import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import PdfAiPanel from './PdfAiPanel';
import {
  createPdfAiSession,
  deletePdfAiSession,
  loadPdfAiSessionMessages,
  loadPdfAiSessions,
  streamPdfAi,
} from './textbookPdfApi';

vi.mock('./textbookPdfApi', () => ({
  createPdfAiSession: vi.fn(),
  deletePdfAiSession: vi.fn(),
  loadPdfAiSessionMessages: vi.fn(),
  loadPdfAiSessions: vi.fn(),
  streamPdfAi: vi.fn(),
}));

const baseProps = { bookId: 'TB_1', bookTitle: '中医学基础', pageNumber: 3, onClose: vi.fn() };

describe('PdfAiPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    loadPdfAiSessions.mockResolvedValue({ sessions: [] });
    loadPdfAiSessionMessages.mockResolvedValue({ messages: [] });
    createPdfAiSession.mockResolvedValue({ session_id: 'textbook-ai-new1' });
    deletePdfAiSession.mockResolvedValue({ ok: true });
  });

  it('shows a friendly header without the page number', () => {
    render(<PdfAiPanel {...baseProps} />);
    expect(screen.getByText('AI 助教')).toBeInTheDocument();
    expect(screen.getByText(/《中医学基础》/)).toBeInTheDocument();
    expect(screen.queryByText(/第 3 页 AI 助手/)).not.toBeInTheDocument();
  });

  it('streams a summary as a chat message with markdown rendering', async () => {
    streamPdfAi.mockImplementation(async (_b, _p, { onDelta }) => {
      onDelta('### 总结');
      onDelta('\n\n- 要点一');
    });
    render(<PdfAiPanel {...baseProps} />);
    await screen.findByRole('button', { name: /总结本页/ });

    fireEvent.click(screen.getByRole('button', { name: /总结本页/ }));

    expect(await screen.findByRole('heading', { level: 3 })).toHaveTextContent('总结');
    expect(screen.getByText(/要点一/)).toBeInTheDocument();
    expect(streamPdfAi).toHaveBeenCalledWith(
      'TB_1',
      3,
      expect.objectContaining({ mode: 'summary', sessionId: null }),
    );
  });

  it('sends a question and renders the streaming answer', async () => {
    streamPdfAi.mockImplementation(async (_b, _p, { mode, question, history, onDelta }) => {
      expect(mode).toBe('chat');
      expect(question).toBe('什么是阴阳？');
      expect(history).toEqual([]);
      onDelta('阴阳是');
      onDelta('对立统一的哲学概念');
    });
    render(<PdfAiPanel {...baseProps} />);

    fireEvent.change(screen.getByLabelText('向 AI 提问'), { target: { value: '什么是阴阳？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送提问' }));

    expect(await screen.findByText('什么是阴阳？')).toBeInTheDocument();
    expect(await screen.findByText('阴阳是对立统一的哲学概念')).toBeInTheDocument();
    expect(screen.getByLabelText('向 AI 提问')).toHaveValue('');
  });

  it('passes history on follow-up questions', async () => {
    const callbacks = [];
    streamPdfAi.mockImplementation(async (_b, _p, { question, history, onDelta }) => {
      callbacks.push({ question, history });
      onDelta(question === '第一问' ? '回答一' : '回答二');
    });
    render(<PdfAiPanel {...baseProps} />);

    const input = screen.getByLabelText('向 AI 提问');
    fireEvent.change(input, { target: { value: '第一问' } });
    fireEvent.click(screen.getByRole('button', { name: '发送提问' }));
    await screen.findByText('回答一');

    fireEvent.change(input, { target: { value: '第二问' } });
    fireEvent.click(screen.getByRole('button', { name: '发送提问' }));
    await screen.findByText('回答二');

    expect(callbacks[1].history).toEqual([
      { role: 'user', content: '第一问' },
      { role: 'assistant', content: '回答一' },
    ]);
  });

  it('creates a new session via the toolbar button', async () => {
    render(<PdfAiPanel {...baseProps} />);
    fireEvent.click(screen.getByRole('button', { name: /新建对话/ }));

    await waitFor(() => expect(createPdfAiSession).toHaveBeenCalledWith('新对话'));
    expect(loadPdfAiSessions).toHaveBeenCalled();
  });

  it('lists and opens a historical session', async () => {
    loadPdfAiSessions.mockResolvedValue({
      sessions: [{ id: 'textbook-ai-old1', title: '阴阳五行讨论' }],
    });
    loadPdfAiSessionMessages.mockResolvedValue({
      messages: [
        { role: 'user', content: '之前的问题' },
        { role: 'assistant', content: '之前的回答' },
      ],
    });
    render(<PdfAiPanel {...baseProps} />);

    fireEvent.click(screen.getByRole('button', { name: /对话记录/ }));
    const sessionButton = (await screen.findAllByRole('button', { name: /阴阳五行讨论/ }))[0];
    fireEvent.click(sessionButton);

    expect(await screen.findByText('之前的问题')).toBeInTheDocument();
    expect(screen.getByText('之前的回答')).toBeInTheDocument();
    expect(loadPdfAiSessionMessages).toHaveBeenCalledWith('textbook-ai-old1');
  });

  it('deletes a session from the history list', async () => {
    loadPdfAiSessions.mockResolvedValue({
      sessions: [
        { id: 'textbook-ai-current', title: '当前会话' },
        { id: 'textbook-ai-old1', title: '待删除' },
      ],
    });
    render(<PdfAiPanel {...baseProps} />);

    fireEvent.click(screen.getByRole('button', { name: /对话记录/ }));
    fireEvent.click(await screen.findByRole('button', { name: '删除会话：待删除' }));

    expect(deletePdfAiSession).toHaveBeenCalledWith('textbook-ai-old1');
    await waitFor(() => expect(screen.queryByText('待删除')).not.toBeInTheDocument());
  });

  it('toggles fullscreen mode and closes via the header button', () => {
    const onClose = vi.fn();
    render(<PdfAiPanel {...baseProps} onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: '全屏窗口' }));
    expect(screen.getByRole('button', { name: '还原窗口' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '关闭 AI 助手' }));
    expect(onClose).toHaveBeenCalled();
  });
});
