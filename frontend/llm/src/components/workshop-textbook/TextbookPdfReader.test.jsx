import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TextbookPdfReader from './TextbookPdfReader';
import { loadPdfReadingState, resolveTextbookPdf, savePdfReadingState } from './textbookPdfApi';

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  getDocument: vi.fn(),
}));
vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: '/pdf-worker.mjs' }));
vi.mock('./TextbookPageNotePopover', () => ({ default: () => null }));
vi.mock('../workshopLibraryApi', () => ({
  createFavoriteFolder: vi.fn(),
  deleteFavorite: vi.fn(),
  loadFavoriteFolders: vi.fn(),
  loadFavorites: vi.fn(),
  saveFavorite: vi.fn(),
}));
vi.mock('./textbookPdfApi', () => ({
  loadPdfAnnotations: vi.fn(),
  loadPdfReadingState: vi.fn(),
  resolveTextbookPdf: vi.fn(),
  savePdfAnnotations: vi.fn(),
  savePdfReadingState: vi.fn(),
}));

describe('TextbookPdfReader', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect() {}
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('identifies a catalogued textbook whose PDF file is not deployed', async () => {
    resolveTextbookPdf.mockResolvedValue({
      available: false,
      book: { book_id: 'TB_1', title: '中医学基础', edition: '十四五' },
    });

    render(<TextbookPdfReader bookTitle="中医学基础" onClose={vi.fn()} />);

    expect(await screen.findByRole('heading', { name: '电子教材文件未部署' })).toBeInTheDocument();
    expect(screen.getByText(/已在教材索引中，但服务端尚未提供对应 PDF 文件/)).toBeInTheDocument();
    await waitFor(() => expect(loadPdfReadingState).not.toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 500));
    expect(savePdfReadingState).not.toHaveBeenCalled();
  });
});
