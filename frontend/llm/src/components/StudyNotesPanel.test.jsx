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
  uploadNoteImage: vi.fn(),
}));

describe('StudyNotesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.loadNoteFolders.mockResolvedValue({ items: [{ folder_id: 'NF1', name: '经方笔记', note_count: 1 }] });
    api.loadNotes.mockResolvedValue({ items: [{
      note_id: 'N1',
      title: '四君子汤记忆',
      content: '## 配伍\n\n**人参**为君。',
      source: '智能组卷',
      note_type: '题目笔记',
      updated_at: '2026-07-25T08:00:00Z',
      context: { notebook: '经方笔记', question_content: '四君子汤的君药是？', standard_answer: ['A'] },
    }] });
  });

  it('edits, previews and deletes a Markdown note', async () => {
    api.updateNote.mockResolvedValue({ note: { note_id: 'N1' } });
    api.deleteNote.mockResolvedValue(null);
    render(<StudyNotesPanel />);

    fireEvent.click(await screen.findByRole('button', { name: /四君子汤记忆/ }));
    expect(screen.getByText('四君子汤的君药是？')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('笔记标题'), { target: { value: '四君子汤配伍' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(api.updateNote).toHaveBeenCalledWith('N1', expect.objectContaining({ title: '四君子汤配伍' })));

    fireEvent.click(screen.getByRole('button', { name: '预览' }));
    expect(screen.getByRole('heading', { name: '配伍' })).toBeInTheDocument();
    expect(screen.getByText('人参')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '删除' }));
    await waitFor(() => expect(api.deleteNote).toHaveBeenCalledWith('N1'));
  });

  it('creates a note as a full-page editor document', async () => {
    api.createNote.mockResolvedValue({ note: { note_id: 'N2' } });
    render(<StudyNotesPanel />);

    fireEvent.click(await screen.findByRole('button', { name: '新建笔记' }));
    fireEvent.change(screen.getByLabelText('笔记标题'), { target: { value: '补气方辨析' } });
    fireEvent.change(screen.getByLabelText('笔记内容'), { target: { value: '# 补气方\\n四君子汤是基础方。' } });
    fireEvent.click(screen.getByRole('button', { name: '保存' }));

    await waitFor(() => expect(api.createNote).toHaveBeenCalledWith(expect.objectContaining({
      title: '补气方辨析',
      content: '# 补气方\\n四君子汤是基础方。',
      context: { notebook: '经方笔记' },
    })));
  });

  it('uploads an image and inserts its user-scoped URL into Markdown', async () => {
    api.uploadNoteImage.mockResolvedValue({ image_id: 'abc', url: '/api/v1/workshop/note-images/abc' });
    render(<StudyNotesPanel />);
    fireEvent.click(await screen.findByRole('button', { name: /四君子汤记忆/ }));

    const input = document.querySelector('input[type="file"]');
    const file = new File(['image'], '方剂图.png', { type: 'image/png' });
    fireEvent.change(input, { target: { files: [file] } });

    await waitFor(() => expect(api.uploadNoteImage).toHaveBeenCalledWith(file));
    expect(screen.getByLabelText('笔记内容').value).toContain('/api/v1/workshop/note-images/abc');
  });

  it('creates a server-backed notebook from the sidebar', async () => {
    api.createNoteFolder.mockResolvedValue({ folder: { folder_id: 'NF2', name: '伤寒论', note_count: 0 } });
    render(<StudyNotesPanel />);
    await screen.findByRole('button', { name: /经方笔记/ });

    fireEvent.click(screen.getByRole('button', { name: '新建笔记本' }));
    const dialog = screen.getByRole('dialog', { name: '新建笔记本' });
    fireEvent.change(within(dialog).getByLabelText('笔记本名称'), { target: { value: '伤寒论' } });
    fireEvent.click(within(dialog).getByRole('button', { name: '创建并进入' }));

    await waitFor(() => expect(api.createNoteFolder).toHaveBeenCalledWith('伤寒论'));
    expect(await screen.findByText('这个笔记本还没有内容。')).toBeInTheDocument();
  });
});
