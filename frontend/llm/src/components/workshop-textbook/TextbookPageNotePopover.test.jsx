import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import TextbookPageNotePopover from './TextbookPageNotePopover';
import * as api from '../workshopLibraryApi';

vi.mock('../workshopLibraryApi', () => ({
  createNote: vi.fn(),
  loadNotes: vi.fn(),
  updateNote: vi.fn(),
}));

const book = { book_id: 'TB1', title: '方剂学', edition: '十四五' };

describe('TextbookPageNotePopover', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.loadNotes.mockResolvedValue({ items: [] });
    api.createNote.mockResolvedValue({ note: { note_id: 'N1' } });
  });

  it('does not create notes while opening or turning pages and only saves explicitly', async () => {
    const { rerender } = render(<TextbookPageNotePopover book={book} page={6} route="textbook_14_5" onClose={() => {}} />);
    expect((await screen.findByLabelText('《方剂学》第 6 页笔记内容')).value).toContain('第 6 页');
    expect(api.createNote).not.toHaveBeenCalled();

    rerender(<TextbookPageNotePopover book={book} page={7} route="textbook_14_5" onClose={() => {}} />);
    expect((await screen.findByLabelText('《方剂学》第 7 页笔记内容')).value).toContain('第 7 页');
    expect(api.createNote).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '保存' }));
    await waitFor(() => expect(api.createNote).toHaveBeenCalledWith(expect.objectContaining({
      resource_id: 'TB1:page:7',
      context: expect.objectContaining({ book_title: '方剂学', pdf_page: 7 }),
    })));
  });
});
