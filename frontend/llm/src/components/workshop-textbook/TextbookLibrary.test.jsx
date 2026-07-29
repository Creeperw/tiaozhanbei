import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import TextbookLibrary from './TextbookLibrary';

it('renders textbook cards with introductions and opens the selected book', () => {
  const onOpen = vi.fn();
  const book = {
    node_id: 'BOOK_1',
    node_type: 'book',
    title: '《中医学基础》',
    stage_title: '中医基础阶段',
    navigation: { route_id: 'textbook_14_5', book: '中医学基础' },
  };

  render(<TextbookLibrary books={[book]} onOpen={onOpen} />);

  expect(screen.getByRole('region', { name: '教材学习列表' })).toBeInTheDocument();
  expect(screen.getByText(/系统讲解阴阳五行/)).toBeInTheDocument();
  expect(document.querySelector('.textbook-library-card__cover img')).toHaveAttribute('src', '/textbook-covers/%E4%B8%AD%E5%8C%BB%E5%AD%A6%E5%9F%BA%E7%A1%80.jpg');
  fireEvent.click(screen.getByRole('button', { name: '学习《中医学基础》' }));
  expect(onOpen).toHaveBeenCalledWith(book);
});
it('overrides the planning label for configured thirteen-five textbooks', () => {
  const book = {
    node_id: 'BOOK_13_5',
    title: '\u300a\u523a\u6cd5\u7078\u6cd5\u5b66\u300b',
    stage_title: '\u5341\u56db\u4e94\u89c4\u5212\u6559\u6750',
    navigation: { route_id: 'textbook_14_5', book: '\u523a\u6cd5\u7078\u6cd5\u5b66' },
  };

  render(<TextbookLibrary books={[book]} />);

  expect(screen.getByText('\u5341\u4e09\u4e94\u89c4\u5212\u6559\u6750')).toBeInTheDocument();
  expect(screen.queryByText('\u5341\u56db\u4e94\u89c4\u5212\u6559\u6750')).not.toBeInTheDocument();
});
