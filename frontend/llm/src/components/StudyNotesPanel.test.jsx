import React from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import StudyNotesPanel from './StudyNotesPanel';
import * as api from './workshopLibraryApi';

vi.mock('./workshopLibraryApi', () => ({
  loadNotes: vi.fn(),
  loadNoteFolders: vi.fn(),
  createNoteFolder: vi.fn(),
  createNote: vi.fn(),
  updateNote: vi.fn(),
  deleteNote: vi.fn(),
}));

describe('StudyNotesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.loadNoteFolders.mockResolvedValue({ items: [{ folder_id: 'NF1', name: '经方笔记', note_count: 1 }] });
    api.loadNotes.mockResolvedValue({ items: [{
      note_id: 'N1', title: '四君子汤记忆', content: '人参为君。', source: '智能组卷', note_type: '题目笔记', updated_at: '2026-07-25T08:00:00Z',
      context: { notebook: '经方笔记', question_content: '四君子汤的君药是？', standard_answer: ['A'] },
    }] });
  });

  it('creates, edits and deletes notes through the server API', async () => {
    api.createNote.mockResolvedValue({ note: { note_id: 'N2' } });
    api.updateNote.mockResolvedValue({ note: { note_id: 'N1' } });
    api.deleteNote.mockResolvedValue(null);
    render(<StudyNotesPanel />);

    fireEvent.click(await screen.findByRole('button', { name: /经方笔记/ }));
    expect(screen.getByRole('button', { name: '返回笔记本列表' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /四君子汤记忆/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '新建笔记' }));
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
    fireEvent.click(await screen.findByRole('button', { name: /经方笔记/ }));
    fireEvent.change(screen.getByLabelText('搜索笔记'), { target: { value: '不存在' } });
    expect(screen.getByText('暂无符合条件的笔记')).toBeInTheDocument();
  });

  it('creates a server-backed note from the merged note dialog', async () => {
    api.createNote.mockResolvedValue({ note: { note_id: 'N2' } });
    render(<StudyNotesPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /经方笔记/ }));

    expect(screen.queryByRole('dialog', { name: '新建笔记' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '新建笔记' }));

    const dialog = screen.getByRole('dialog', { name: '新建笔记' });
    fireEvent.change(within(dialog).getByLabelText('标题'), { target: { value: '补气方辨析' } });
    fireEvent.change(within(dialog).getByLabelText('内容'), { target: { value: '四君子汤是补气基础方。' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '保存笔记' }));

    await waitFor(() => expect(api.createNote).toHaveBeenCalledWith(expect.objectContaining({
      title: '补气方辨析',
      content: '四君子汤是补气基础方。',
      context: expect.objectContaining({ notebook: '经方笔记' }),
    })));
  });

  it('shows notebook books before entering the server-backed note list', async () => {
    render(<StudyNotesPanel />);

    const shelf = await screen.findByRole('region', { name: '笔记本书架' });
    expect(shelf).toHaveClass('workshop-notes__books');
    expect(screen.getByRole('button', { name: /经方笔记.*1 条笔记/ })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /四君子汤记忆/ })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '新建笔记本' })).toBeInTheDocument();
  });

  it('opens a new notebook dialog and enters the created notebook', async () => {
    api.createNoteFolder.mockResolvedValue({ folder: { folder_id: 'NF2', name: '伤寒论', note_count: 0 } });
    render(<StudyNotesPanel />);
    await screen.findByRole('region', { name: '笔记本书架' });

    fireEvent.click(screen.getByRole('button', { name: '新建笔记本' }));
    const dialog = screen.getByRole('dialog', { name: '新建笔记本' });
    fireEvent.change(within(dialog).getByLabelText('笔记本名称'), { target: { value: '伤寒论' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '创建并进入' }));

    await waitFor(() => expect(api.createNoteFolder).toHaveBeenCalledWith('伤寒论'));
    expect(await screen.findByRole('heading', { name: '伤寒论' })).toBeInTheDocument();
    expect(screen.getByText('暂无符合条件的笔记')).toBeInTheDocument();
  });

  it('loads an empty notebook from the server after remount', async () => {
    api.loadNoteFolders.mockResolvedValue({ items: [{ folder_id: 'NF2', name: '伤寒论', note_count: 0 }] });
    api.loadNotes.mockResolvedValue({ items: [] });

    const first = render(<StudyNotesPanel />);
    expect(await screen.findByRole('button', { name: /伤寒论.*0 条笔记/ })).toBeInTheDocument();
    first.unmount();

    render(<StudyNotesPanel />);
    expect(await screen.findByRole('button', { name: /伤寒论.*0 条笔记/ })).toBeInTheDocument();
    expect(api.loadNoteFolders).toHaveBeenCalledTimes(2);
  });
});
