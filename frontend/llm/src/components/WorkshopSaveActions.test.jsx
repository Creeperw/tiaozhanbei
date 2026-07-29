import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { FavoriteQuestionButton, FavoriteQuestionIconButton, NoteQuestionButton } from './WorkshopSaveActions';
import * as api from './workshopLibraryApi';

vi.mock('./workshopLibraryApi', () => ({
  loadFavoriteFolders: vi.fn(),
  loadNoteFolders: vi.fn(),
  createFavoriteFolder: vi.fn(),
  createNoteFolder: vi.fn(),
  saveFavorite: vi.fn(),
  deleteFavorite: vi.fn(),
  createNote: vi.fn(),
}));

const question = {
  resource_id: 'Q1',
  title: '四君子汤的君药',
  defaultTitle: '智能组卷 · 第 1 题',
  content: {
    question_content: '四君子汤的君药是？',
    standard_answer: ['A'],
    explanation: '人参为君药。',
  },
};

describe('WorkshopSaveActions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.loadFavoriteFolders.mockResolvedValue({ items: [{ folder_id: 'F1', name: '方剂重点' }] });
    api.loadNoteFolders.mockResolvedValue({ items: [{ folder_id: 'N1', name: '默认笔记本' }] });
  });

  it('saves a graded question into a selected folder', async () => {
    api.saveFavorite.mockResolvedValue({ favorite: { favorite_id: 'V1' } });
    render(<FavoriteQuestionButton question={question} source="智能组卷" />);

    fireEvent.click(screen.getByRole('button', { name: '加入收藏' }));
    expect(await screen.findByRole('option', { name: '方剂重点' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '保存收藏' }));

    await waitFor(() => expect(api.saveFavorite).toHaveBeenCalledWith(expect.objectContaining({
      folder_id: 'F1', resource_id: 'Q1', content: question.content,
    })));
    expect(screen.getByText('已加入收藏')).toBeInTheDocument();
  });

  it('creates a note with the full graded question context', async () => {
    api.createNote.mockResolvedValue({ note: { note_id: 'N1' } });
    render(<NoteQuestionButton question={question} source="智能组卷" />);

    fireEvent.click(screen.getByRole('button', { name: '记笔记' }));
    await screen.findByRole('option', { name: '默认笔记本' });
    fireEvent.change(screen.getByPlaceholderText(/写下解题思路/), { target: { value: '记住人参为君药。' } });
    fireEvent.click(screen.getByRole('button', { name: '保存笔记' }));

    await waitFor(() => expect(api.createNote).toHaveBeenCalledWith(expect.objectContaining({
      title: '智能组卷 · 第 1 题', context: { ...question.content, notebook: '默认笔记本' }, resource_id: 'Q1',
    })));
    expect(screen.getByText('笔记已保存')).toBeInTheDocument();
  });

  it('favorites a question immediately from the solving screen', async () => {
    api.saveFavorite.mockResolvedValue({ favorite: { favorite_id: 'V1' } });
    render(<FavoriteQuestionIconButton question={question} source="题目训练" />);

    fireEvent.click(screen.getByRole('button', { name: '收藏本题' }));
    await waitFor(() => expect(api.saveFavorite).toHaveBeenCalledWith(expect.objectContaining({
      folder_id: 'F1',
      resource_id: 'Q1',
      source: '题目训练',
    })));
    const removeButton = screen.getByRole('button', { name: '取消收藏本题' });
    expect(removeButton).toBeEnabled();
    fireEvent.click(removeButton);
    await waitFor(() => expect(api.deleteFavorite).toHaveBeenCalledWith('V1'));
    expect(screen.getByRole('button', { name: '收藏本题' })).toBeEnabled();
  });
});
