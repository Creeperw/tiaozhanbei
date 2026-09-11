import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import LegacyConversation from './LegacyConversation';
import { loadLegacySessionMessages } from '../legacyLearningClient';

vi.mock('../legacyLearningClient', () => ({ loadLegacySessionMessages: vi.fn() }));
beforeEach(() => vi.resetAllMocks());

it('renders complete historical messages without send, feedback or regeneration', async () => {
  loadLegacySessionMessages.mockResolvedValue({ items: [
    { id: 1, role: 'user', content: '原问题', created_at: '2026-08-01' },
    { id: 2, role: 'assistant', content: '原回答', created_at: '2026-08-01' },
  ] });
  const create = vi.fn();
  render(<LegacyConversation session={{ id: 'old', title: '原会话' }} onNewConversation={create} />);
  expect(await screen.findByText('原回答')).toBeInTheDocument();
  expect(screen.getAllByRole('article', { name: '历史消息' })).toHaveLength(2);
  expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /发送|点赞|重新生成/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '另开新对话' }));
  expect(create).toHaveBeenCalledTimes(1);
});

it('does not present failed history requests as empty and supports retry', async () => {
  loadLegacySessionMessages.mockRejectedValueOnce(new Error('404')).mockResolvedValueOnce({ items: [] });
  render(<LegacyConversation session={{ id: 'old' }} onNewConversation={() => {}} />);
  expect(await screen.findByRole('alert')).toHaveTextContent('404');
  expect(screen.queryByText('此历史会话暂无消息。')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重试历史消息' }));
  expect(await screen.findByText('此历史会话暂无消息。')).toBeInTheDocument();
});