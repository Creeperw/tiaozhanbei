import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import StudyNotesPanel from './StudyNotesPanel';
import * as api from './workshopLibraryApi';

vi.mock('./workshopLibraryApi', () => ({
  loadNotes: vi.fn(),
  createNote: vi.fn(),
  updateNote: vi.fn(),
  deleteNote: vi.fn(),
}));

describe('StudyNotesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.loadNotes.mockResolvedValue({ items: [{
      note_id: 'N1', title: '四君子汤记忆', content: '人参为君。', source: '智能组卷', note_type: '题目笔记', updated_at: '2026-07-25T08:00:00Z',
      context: { question_content: '四君子汤的君药是？', standard_answer: ['A'] },
    }] });
  });

  it('creates, edits and deletes notes through the server API', async () => {
    api.createNote.mockResolvedValue({ note: { note_id: 'N2' } });
    api.updateNote.mockResolvedValue({ note: { note_id: 'N1' } });
    api.deleteNote.mockResolvedValue(null);
    render(<StudyNotesPanel />);

    expect(await screen.findByRole('button', { name: /四君子汤记忆/ })).toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText('今天学到了什么？'), { target: { value: '补气方辨析' } });
    fireEvent.change(screen.getByPlaceholderText(/写下理解/), { target: { value: '四君子汤是补气基础方。' } });
    fireEvent.click(screen.getByRole('button', { name: '保存笔记' }));
    await waitFor(() => expect(api.createNote).toHaveBeenCalledWith(expect.objectContaining({ title: '补气方辨析' })));

    fireEvent.click(screen.getByRole('button', { name: /四君子汤记忆/ }));
    expect(screen.getByText('四君子汤的君药是？')).toBeInTheDocument();
    const titleInputs = screen.getAllByDisplayValue('四君子汤记忆');
    fireEvent.change(titleInputs.at(-1), { target: { value: '四君子汤配伍' } });
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }));
    await waitFor(() => expect(api.updateNote).toHaveBeenCalledWith('N1', expect.objectContaining({ title: '四君子汤配伍' })));

    fireEvent.click(screen.getByRole('button', { name: '删除笔记' }));
    await waitFor(() => expect(api.deleteNote).toHaveBeenCalledWith('N1'));
  });

  it('filters notes by search text', async () => {
    render(<StudyNotesPanel />);
    await screen.findByRole('button', { name: /四君子汤记忆/ });
    fireEvent.change(screen.getByLabelText('搜索笔记'), { target: { value: '不存在' } });
    expect(screen.getByText('暂无符合条件的笔记')).toBeInTheDocument();
  });
});
