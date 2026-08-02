import { describe, expect, it } from 'vitest';
import {
  buildTextbookViewModels,
  filterTextbookViewModels,
  textbookFilterCounts,
  textbookSourceCounts,
} from './textbookLibraryModel';
import { textbookIntroduction } from './textbookMetadata';

const atlasBooks = [
  { id: 'a', name: '中医学基础', children_count: 16, count: 120, navigation: { route_id: 'textbook_14_5', book: '中医学基础' } },
  { id: 'b', name: '中医诊断学', children_count: 12, count: 86, navigation: { route_id: 'textbook_14_5', book: '中医诊断学' } },
];

describe('textbook library unified model', () => {
  it('combines Atlas, progress, plan and current-task fields', () => {
    const books = buildTextbookViewModels({
      textbooks: atlasBooks,
      plannedBooks: [{ title: '《中医学基础》' }],
      currentBookName: '《中医学基础》',
      snapshotsByBook: {
        中医学基础: { progress: 0.5, completedSections: 5, totalSections: 10, lastSectionId: 's5' },
        中医诊断学: { progress: 1, completedSections: 8, totalSections: 8 },
      },
    });

    expect(books[0]).toMatchObject({
      name: '中医学基础', chapterCount: 16, knowledgePointCount: 120,
      progress: 0.5, hasStarted: true, isCompleted: false, isCurrent: true, isPlanned: true,
    });
    expect(books[0].description).toBe(textbookIntroduction('中医学基础'));
    expect(books[1]).toMatchObject({ isCompleted: true, isCurrent: false, isPlanned: false });
  });

  it('filters only known states while plan membership remains independently available', () => {
    const books = [
      { name: 'a', statusKnown: true, hasStarted: true, isCompleted: false, isPlanned: true, categoryLabel: '', description: '' },
      { name: 'b', statusKnown: false, hasStarted: false, isCompleted: false, isPlanned: true, categoryLabel: '', description: '' },
    ];
    expect(filterTextbookViewModels(books, { filter: 'learning' })).toEqual([books[0]]);
    expect(filterTextbookViewModels(books, { filter: 'planned' })).toEqual(books);
    expect(textbookFilterCounts(books, { progressLoading: true })).toMatchObject({ learning: 1, planned: 2 });
  });

  it('keeps origin on view models and filters by uploaded/platform source', () => {
    const books = buildTextbookViewModels({
      textbooks: [
        { id: 'u1', name: '自编讲义', origin: 'user_upload', navigation: { route_id: 'user_textbooks', book: '自编讲义' } },
        { id: 'p1', name: '中医学基础', navigation: { route_id: 'textbook_14_5', book: '中医学基础' } },
      ],
    });

    expect(books[0].origin).toBe('user_upload');
    expect(books[1].origin).toBe('platform');
    expect(filterTextbookViewModels(books, { source: 'uploaded' })).toEqual([books[0]]);
    expect(filterTextbookViewModels(books, { source: 'platform' })).toEqual([books[1]]);
    expect(filterTextbookViewModels(books, { source: 'all' })).toEqual(books);
    expect(textbookSourceCounts(books)).toEqual({ all: 2, uploaded: 1, platform: 1 });
  });
});
