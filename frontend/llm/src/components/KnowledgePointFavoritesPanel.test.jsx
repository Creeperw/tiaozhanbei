import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import KnowledgePointFavoritesPanel from './KnowledgePointFavoritesPanel';
import * as api from './workshopLibraryApi';

vi.mock('./workshopLibraryApi', () => ({
  loadFavoriteFolders: vi.fn(),
  loadFavorites: vi.fn(),
  createFavoriteFolder: vi.fn(),
  deleteFavoriteFolder: vi.fn(),
  deleteFavorite: vi.fn(),
  saveFavorite: vi.fn(),
}));

describe('KnowledgePointFavoritesPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.loadFavoriteFolders.mockResolvedValue({ items: [{ folder_id: 'F1', name: '教材页收藏', favorite_count: 1 }] });
    api.loadFavorites.mockResolvedValue({ items: [{
      favorite_id: 'PDF1', folder_id: 'F1', resource_type: 'textbook_pdf_page',
      title: '《方剂学》第 10 页', source: '教学资源', updated_at: '2026-07-29T08:00:00Z',
      content: { book_title: '方剂学', edition: '十四五', route: 'textbook_14_5', pdf_page: 10 },
    }] });
  });

  it('opens the saved textbook page', async () => {
    const onNavigate = vi.fn();
    render(<KnowledgePointFavoritesPanel onNavigate={onNavigate} />);

    fireEvent.click((await screen.findByText('《方剂学》第 10 页')).closest('button'));
    expect(onNavigate).toHaveBeenCalledWith({
      page: 'practice',
      params: {
        view: 'textbook-chapters', route: 'textbook_14_5', lv1: '方剂学',
        openPdf: true, pdfPage: 10, source: 'favorite',
      },
    });
  });

  it('only renders textbook page favorites', async () => {
    api.loadFavorites.mockResolvedValue({ items: [
      { favorite_id: 'Q1', folder_id: 'F1', resource_type: 'question', title: '题目收藏', source: '专项特训', content: {} },
      { favorite_id: 'PDF1', folder_id: 'F1', resource_type: 'textbook_pdf_page', title: '《方剂学》第 10 页', source: '教学资源', content: { book_title: '方剂学', pdf_page: 10 } },
    ] });
    render(<KnowledgePointFavoritesPanel />);

    expect(await screen.findByText('《方剂学》第 10 页')).toBeInTheDocument();
    expect(screen.queryByText('题目收藏')).not.toBeInTheDocument();
  });
});