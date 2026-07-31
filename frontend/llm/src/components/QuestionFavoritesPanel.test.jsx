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
  saveFavorite: vi.fn(),
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

  it('moves a favorite only after saving it in the target collection', async () => {
    api.loadFavoriteFolders.mockResolvedValue({ items: [
      { folder_id: 'F1', name: '方剂重点', favorite_count: 1 },
      { folder_id: 'F2', name: '错题复盘', favorite_count: 0 },
    ] });
    api.saveFavorite.mockResolvedValue({});
    api.deleteFavorite.mockResolvedValue(null);
    render(<QuestionFavoritesPanel />);

    await screen.findByRole('heading', { name: '方剂重点' });
    fireEvent.click(screen.getByRole('button', { name: '移动收藏：四君子汤的君药' }));
    fireEvent.click(screen.getByRole('button', { name: '错题复盘' }));

    await waitFor(() => expect(api.saveFavorite).toHaveBeenCalledWith(expect.objectContaining({
      folder_id: 'F2', resource_id: 'V1', title: '四君子汤的君药',
    })));
    expect(api.deleteFavorite).toHaveBeenCalledWith('V1');
  });

  it('opens a page-level confirmation before removing a favorite', async () => {
    api.deleteFavorite.mockResolvedValue(null);
    render(<QuestionFavoritesPanel />);

    expect(await screen.findByRole('heading', { name: '方剂重点' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '删除收藏：四君子汤的君药' }));
    expect(screen.getByRole('dialog', { name: '删除这条收藏？' })).toBeInTheDocument();
    expect(api.deleteFavorite).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }));

    await waitFor(() => expect(api.deleteFavorite).toHaveBeenCalledWith('V1'));
  });

  it('opens a textbook page favorite in teaching resources', async () => {
    const onNavigate = vi.fn();
    api.loadFavorites.mockResolvedValue({ items: [{
      favorite_id: 'PDF1', folder_id: 'F1', resource_type: 'textbook_pdf_page',
      title: '《方剂学》第 10 页', source: '教学资源', updated_at: '2026-07-29T08:00:00Z',
      content: { book_title: '方剂学', edition: '十四五', route: 'textbook_14_5', pdf_page: 10 },
    }] });
    render(<QuestionFavoritesPanel onNavigate={onNavigate} />);

    fireEvent.click((await screen.findByText('《方剂学》第 10 页')).closest('button'));
    expect(onNavigate).toHaveBeenCalledWith({
      page: 'practice',
      params: {
        view: 'textbook-chapters', route: 'textbook_14_5', lv1: '方剂学',
        openPdf: true, pdfPage: 10, source: 'favorite',
      },
    });
  });

  it('creates a named collection folder', async () => {
    api.createFavoriteFolder.mockResolvedValue({ folder: { folder_id: 'F2', name: '经方辨析', favorite_count: 0 } });
    render(<QuestionFavoritesPanel />);
    await screen.findByRole('button', { name: /方剂重点/ });

    fireEvent.click(screen.getByRole('button', { name: '新建收藏题单' }));
    fireEvent.change(screen.getByLabelText('收藏簿名称'), { target: { value: '经方辨析' } });
    fireEvent.click(screen.getByRole('button', { name: '新建' }));

    await waitFor(() => expect(api.createFavoriteFolder).toHaveBeenCalledWith('经方辨析'));
  });

  it('shows server-backed collection folders in a persistent LeetCode-style sidebar', async () => {
    render(<QuestionFavoritesPanel />);

    const sidebar = await screen.findByRole('complementary', { name: '收藏题单' });
    expect(sidebar).toHaveClass('question-collection__sidebar');
    expect(screen.getByRole('button', { name: /方剂重点/ })).toBeInTheDocument();
    expect(screen.getByText('四君子汤的君药').closest('button')).toHaveClass('question-collection__item-open');
    expect(screen.getByRole('button', { name: '新建收藏题单' })).toBeInTheDocument();
  });

  it('filters favorites inside a selected collection book', async () => {
    render(<QuestionFavoritesPanel />);
    await screen.findByRole('button', { name: /方剂重点/ });

    expect(screen.getByLabelText('筛选收藏日期')).toBeInTheDocument();
    expect(screen.getByLabelText('筛选收藏来源')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索收藏'), { target: { value: '不存在' } });
    expect(screen.getByText('暂无符合条件的收藏')).toBeInTheDocument();
  });
});
