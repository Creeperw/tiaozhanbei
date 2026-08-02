import React from 'react';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import TextbookLibrary from './TextbookLibrary';

function textbook(overrides = {}) {
  return {
    id: 'BOOK_1',
    name: '中医学基础',
    routeId: 'textbook_14_5',
    categoryLabel: '十四五规划教材',
    coverUrl: '/textbook-covers/%E4%B8%AD%E5%8C%BB%E5%AD%A6%E5%9F%BA%E7%A1%80.jpg',
    description: '系统讲解阴阳五行、藏象经络等中医基础理论。',
    chapterCount: 16,
    knowledgePointCount: 120,
    completedSectionCount: 24,
    totalSectionCount: 86,
    progress: 0.28,
    progressAvailable: true,
    statusKnown: true,
    hasStarted: true,
    isCompleted: false,
    isCurrent: true,
    isPlanned: true,
    ...overrides,
  };
}

it('renders the complete textbook card and opens the selected book', () => {
  const onOpen = vi.fn();
  const book = textbook();

  render(<TextbookLibrary books={[book]} onOpen={onOpen} />);

  const library = screen.getByRole('region', { name: '教材学习列表' });
  expect(within(library).getByText('《中医学基础》')).toBeInTheDocument();
  expect(within(library).getByText('16 章')).toBeInTheDocument();
  expect(within(library).getByText('120 个知识点')).toBeInTheDocument();
  expect(within(library).getByText('28%')).toBeInTheDocument();
  expect(within(library).getByText('当前在学')).toBeInTheDocument();
  expect(within(library).getByText(/系统讲解阴阳五行/)).toBeInTheDocument();
  fireEvent.click(within(library).getByRole('button', { name: '继续学习《中医学基础》' }));
  expect(onOpen).toHaveBeenCalledWith(book);
});

it('combines status filters with search and keeps multiple learning textbooks', () => {
  render(<TextbookLibrary books={[
    textbook(),
    textbook({ id: 'BOOK_2', name: '中医诊断学', isCurrent: false, isPlanned: false, progress: 0.4 }),
    textbook({ id: 'BOOK_3', name: '中医文化学', isCurrent: false, hasStarted: false, progress: 0 }),
    textbook({ id: 'BOOK_4', name: '中医养生学', isCurrent: false, isCompleted: true, progress: 1 }),
  ]} />);

  fireEvent.click(screen.getByRole('button', { name: /全部状态/ }));
  fireEvent.click(screen.getByRole('menuitemradio', { name: /学习中/ }));
  expect(screen.getByText('《中医学基础》')).toBeInTheDocument();
  expect(screen.getByText('《中医诊断学》')).toBeInTheDocument();
  expect(screen.queryByText('《中医文化学》')).not.toBeInTheDocument();

  fireEvent.change(screen.getByRole('textbox', { name: '搜索教材' }), { target: { value: '诊断' } });
  expect(screen.queryByText('《中医学基础》')).not.toBeInTheDocument();
  expect(screen.getByText('《中医诊断学》')).toBeInTheDocument();
});

it('keeps unknown progress out of status filters and preserves the expand action', () => {
  const catalogue = [
    textbook({ id: 'BOOK_1' }),
    textbook({ id: 'BOOK_2', name: '中医诊断学', isCurrent: false, isPlanned: false, statusKnown: false, progressAvailable: false, progress: null, hasStarted: false }),
    textbook({ id: 'BOOK_3', name: '中医文化学', isCurrent: false, isPlanned: false, statusKnown: false, progressAvailable: false, progress: null, hasStarted: false }),
  ];
  render(<TextbookLibrary
    books={[textbook({ statusKnown: false, progressAvailable: false, progress: null })]}
    catalogBooks={catalogue}
    progressLoading
    remainingCount={3}
    onExpandAll={vi.fn()}
  />);

  expect(screen.getAllByText('进度待统计').length).toBeGreaterThan(0);
  expect(screen.getByRole('button', { name: '全部教材3' })).toBeInTheDocument();
  expect(screen.getByText(/其中 1 本学习中/)).toBeInTheDocument();
  expect(screen.queryByText('--')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '展开全部教材' })).toBeInTheDocument();
});

it('filters textbooks by uploaded and platform sources from the source menu', () => {
  render(<TextbookLibrary books={[
    textbook({ id: 'BOOK_1', origin: 'user_upload' }),
    textbook({ id: 'BOOK_2', name: '中医诊断学', origin: 'platform', isCurrent: false, isPlanned: false }),
  ]} />);

  expect(screen.getByText('《中医学基础》')).toBeInTheDocument();
  expect(screen.getByText('《中医诊断学》')).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /全部教材/ }));
  fireEvent.click(screen.getByRole('menuitemradio', { name: /用户上传/ }));

  expect(screen.getByText('《中医学基础》')).toBeInTheDocument();
  expect(screen.queryByText('《中医诊断学》')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: /用户上传/ }));
  fireEvent.click(screen.getByRole('menuitemradio', { name: /平台自带/ }));

  expect(screen.queryByText('《中医学基础》')).not.toBeInTheDocument();
  expect(screen.getByText('《中医诊断学》')).toBeInTheDocument();
});
