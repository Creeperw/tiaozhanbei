import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import QuestionFavoritesPanel from './QuestionFavoritesPanel';
import * as api from './workshopLibraryApi';

vi.mock('./workshopLibraryApi', () => ({
  loadFavoriteFolders: vi.fn(),
  loadFavorites: vi.fn(),
  createFavoriteFolder: vi.fn(),
  deleteFavoriteFolder: vi.fn(),
  deleteFavorite: vi.fn(),
}));

describe('QuestionFavoritesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.loadFavoriteFolders.mockResolvedValue({ items: [{ folder_id: 'F1', name: '方剂重点', favorite_count: 1 }] });
    api.loadFavorites.mockResolvedValue({ items: [{
      favorite_id: 'V1', folder_id: 'F1', title: '四君子汤的君药', source: '智能组卷', updated_at: '2026-07-25T08:00:00Z',
      content: { question_content: '四君子汤的君药是？', standard_answer: ['A'], explanation: '人参为君药。' },
    }] });
  });

  it('loads a collection folder and removes a favorite', async () => {
    api.deleteFavorite.mockResolvedValue(null);
    render(<QuestionFavoritesPanel />);

    expect(await screen.findByRole('button', { name: /方剂重点/ })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /四君子汤的君药/ }));
    expect(screen.getByText('人参为君药。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消收藏' }));

    await waitFor(() => expect(api.deleteFavorite).toHaveBeenCalledWith('V1'));
  });

  it('creates a named collection folder', async () => {
    api.createFavoriteFolder.mockResolvedValue({ folder: { folder_id: 'F2', name: '经方辨析', favorite_count: 0 } });
    render(<QuestionFavoritesPanel />);
    await screen.findByRole('button', { name: /方剂重点/ });

    fireEvent.change(screen.getByLabelText('新建收藏簿'), { target: { value: '经方辨析' } });
    fireEvent.click(screen.getByRole('button', { name: '新建' }));

    await waitFor(() => expect(api.createFavoriteFolder).toHaveBeenCalledWith('经方辨析'));
  });
});
