import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import TextbookPdfReader from './TextbookPdfReader';
import { getDocument } from 'pdfjs-dist';
import { loadPdfAnnotations, loadPdfReadingState, resolveTextbookPdf, savePdfReadingState } from './textbookPdfApi';

const fakePage = {
  getViewport: ({ scale }) => ({ width: 800 * scale, height: 1100 * scale, scale }),
  render: () => ({ promise: Promise.resolve() }),
  getTextContent: () => Promise.resolve({ items: [{ str: '阴阳五行' }] }),
};

const fakeDocument = {
  numPages: 3,
  getPage: vi.fn().mockResolvedValue(fakePage),
  getOutline: () => Promise.resolve([]),
};

vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  TextLayer: vi.fn().mockImplementation(function TextLayerMock({ textContentSource, container }) {
    this.textContentSource = textContentSource;
    this.container = container;
    this.render = vi.fn(() => {
      if (container) {
        container.dataset.textLayer = 'rendered';
      }
      return undefined;
    });
    this.cancel = vi.fn();
  }),
  getDocument: vi.fn(),
}));
vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: '/pdf-worker.mjs' }));
vi.mock('../../lib/pdfWorkerEntry.js?worker', () => ({ default: class { constructor() {} postMessage() {} terminate() {} } }));
vi.mock('./TextbookPageNotePopover', () => ({ default: () => null }));
vi.mock('./PdfAiPanel', () => ({
  default: ({ onClose }) => (
    <div><span>AI 助手面板</span><button type="button" onClick={onClose}>关闭AI</button></div>
  ),
}));
vi.mock('../workshopLibraryApi', () => ({
  createFavoriteFolder: vi.fn().mockResolvedValue({ folder: { folder_id: 'F_1' } }),
  deleteFavorite: vi.fn(),
  loadFavoriteFolders: vi.fn().mockResolvedValue({ items: [] }),
  loadFavorites: vi.fn().mockResolvedValue({ items: [] }),
  saveFavorite: vi.fn(),
}));
vi.mock('./textbookPdfApi', () => ({
  loadPdfAnnotations: vi.fn().mockResolvedValue({ annotations: [] }),
  loadPdfReadingState: vi.fn().mockResolvedValue({ page_number: 1, zoom: 1 }),
  resolveTextbookPdf: vi.fn(),
  savePdfAnnotations: vi.fn().mockResolvedValue({ ok: true }),
  savePdfReadingState: vi.fn().mockResolvedValue({ ok: true }),
}));

function setupResolvedBook({ annotations = [] } = {}) {
  resolveTextbookPdf.mockResolvedValue({
    available: true,
    book: {
      book_id: 'TB_1',
      title: '中医学基础',
      edition: '十四五',
      available: true,
      file_url: '/api/v1/textbooks/pdfs/TB_1/file',
    },
  });
  getDocument.mockReturnValue({ promise: Promise.resolve(fakeDocument) });
  loadPdfAnnotations.mockResolvedValue({ annotations });
}

describe('TextbookPdfReader', () => {
  beforeEach(() => {
    vi.stubGlobal('ResizeObserver', class {
      observe() {}
      disconnect() {}
    });
    vi.clearAllMocks();
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

  it('defaults to vertical scroll layout with the select tool active', async () => {
    setupResolvedBook();
    render(<TextbookPdfReader bookTitle="中医学基础" onClose={vi.fn()} />);

    expect(await screen.findByRole('button', { name: '选择' })).toHaveClass('is-active');
    await waitFor(() => expect(document.querySelector('.textbook-pdf__viewport')).toHaveClass('is-vertical'));
    expect(document.querySelector('.textbook-pdf__layout-menu')).not.toBeInTheDocument();
  });

  it('renders a scroll annotation layer over the center page in scroll mode', async () => {
    setupResolvedBook();
    render(<TextbookPdfReader bookTitle="中医学基础" onClose={vi.fn()} />);
    await screen.findByRole('button', { name: '选择' });

    fireEvent.click(screen.getByRole('button', { name: '翻页方式' }));
    fireEvent.click(screen.getByRole('menuitemradio', { name: '纵向滚动' }));

    await waitFor(() => expect(document.querySelector('.textbook-pdf__scroll-annotations')).toBeInTheDocument());
    expect(document.querySelector('.textbook-pdf__scroll-annotations .textbook-pdf__annotations')).toHaveClass('tool-select');
  });

  it('switches pen color from the palette', async () => {
    setupResolvedBook();
    render(<TextbookPdfReader bookTitle="中医学基础" onClose={vi.fn()} />);
    await screen.findByRole('button', { name: '选择' });

    fireEvent.click(screen.getByRole('button', { name: '画笔' }));
    expect(screen.getByRole('button', { name: '画笔' })).toHaveClass('is-active');
    expect(document.querySelector('.textbook-pdf__color-dot')).toHaveStyle({ background: '#19845f' });

    fireEvent.click(screen.getByRole('button', { name: '批注颜色' }));
    fireEvent.click(screen.getByRole('button', { name: '颜色 #1d6fb8' }));
    expect(document.querySelector('.textbook-pdf__color-dot')).toHaveStyle({ background: '#1d6fb8' });
    expect(screen.queryByRole('group', { name: '批注颜色选择' })).not.toBeInTheDocument();
  });

  it('renders shape annotation buttons in the toolbar', async () => {
    setupResolvedBook();
    render(<TextbookPdfReader bookTitle="中医学基础" onClose={vi.fn()} />);
    await screen.findByRole('button', { name: '选择' });

    for (const label of ['直线', '箭头', '矩形', '圆形', '清空本页批注']) {
      expect(screen.getByRole('button', { name: label })).toBeInTheDocument();
    }
    expect(screen.getByRole('button', { name: '清空本页批注' })).toBeDisabled();
  });

  it('clears all page annotations when the toolbar button is used', async () => {
    setupResolvedBook({
      annotations: [
        { id: 'a1', type: 'pen', color: '#19845f', points: [{ x: 0.1, y: 0.2 }, { x: 0.2, y: 0.3 }] },
      ],
    });
    render(<TextbookPdfReader bookTitle="中医学基础" onClose={vi.fn()} />);
    await screen.findByRole('button', { name: '选择' });

    expect(await screen.findByRole('button', { name: '清空本页批注' })).not.toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: '清空本页批注' }));

    await waitFor(() => expect(document.querySelectorAll('.textbook-pdf__annotations polyline').length).toBe(0));
    expect(screen.getByRole('button', { name: '清空本页批注' })).toBeDisabled();
  });

  it('exports the current page image and links the original PDF download', async () => {
    setupResolvedBook();
    render(<TextbookPdfReader bookTitle="中医学基础" onClose={vi.fn()} />);
    await screen.findByRole('button', { name: '选择' });

    const pdfLink = screen.getByRole('link', { name: /下载PDF/ });
    expect(pdfLink).toHaveAttribute('href', '/api/v1/textbooks/pdfs/TB_1/file');
    expect(pdfLink).toHaveAttribute('download');
  });

  it('opens and closes the AI assistant panel', async () => {
    setupResolvedBook();
    render(<TextbookPdfReader bookTitle="中医学基础" onClose={vi.fn()} />);
    await screen.findByRole('button', { name: '选择' });

    fireEvent.click(screen.getByRole('button', { name: 'AI 助手' }));
    expect(await screen.findByText('AI 助手面板')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '关闭AI' }));
    expect(screen.queryByText('AI 助手面板')).not.toBeInTheDocument();
  });
});
