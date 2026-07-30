import React, { useMemo, useState } from 'react';
import { ArrowRight, BookOpen, Search } from 'lucide-react';
import {
  filterTextbookViewModels,
  TEXTBOOK_FILTERS,
  textbookFilterCounts,
} from './textbookLibraryModel';
import './textbookLibrary.css';

const EMPTY_TEXT = {
  learning: '当前没有学习中的教材',
  'not-started': '暂无未开始教材',
  completed: '还没有完成整本教材',
  planned: '尚未加入长期学习计划',
};

function progressSummary(book) {
  if (!book.statusKnown) return '进度待统计';
  if (book.isCompleted) return '已完成';
  if (!book.hasStarted) return '尚未开始';
  if (book.totalSectionCount > 0) {
    return `已完成 ${book.completedSectionCount}/${book.totalSectionCount} 小节`;
  }
  return '学习中';
}

export default function TextbookLibrary({
  books = [],
  emptyText = '教材正在准备中',
  onOpen,
  remainingCount = 0,
  onExpandAll,
  progressLoading = false,
  catalogBooks = books,
}) {
  const [query, setQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState('all');
  const normalizedQuery = query.trim();
  const filterSourceBooks = activeFilter === 'all' && !normalizedQuery ? books : catalogBooks;
  const filteredBooks = useMemo(() => filterTextbookViewModels(filterSourceBooks, {
    filter: activeFilter,
    query,
  }), [activeFilter, filterSourceBooks, query]);
  const counts = useMemo(
    () => textbookFilterCounts(catalogBooks, { progressLoading }),
    [catalogBooks, progressLoading],
  );
  const learningCount = counts.learning === null ? '--' : counts.learning;
  const noResultsText = query.trim()
    ? '没有找到匹配的教材'
    : EMPTY_TEXT[activeFilter] || emptyText;

  return (
    <section className="textbook-library" aria-label="教材学习列表">
      <div className="textbook-library__title-row">
        <div><span>Textbook library</span><h2>教材学习</h2></div>
        <label className="textbook-library__search">
          <Search aria-hidden="true" size={18} />
          <input aria-label="搜索教材" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索教材" />
        </label>
        <div className="textbook-library__filters" aria-label="教材状态筛选">
          {TEXTBOOK_FILTERS.map((filter) => {
            const count = counts[filter.id];
            return (
              <button
                key={filter.id}
                type="button"
                className={activeFilter === filter.id ? 'is-active' : ''}
                aria-pressed={activeFilter === filter.id}
                onClick={() => setActiveFilter(filter.id)}
              >
                {filter.label}<span>{count === null ? '--' : count}</span>
              </button>
            );
          })}
        </div>
        <p>共 {counts.all} 本教材，其中 {learningCount} 本学习中</p>
      </div>

      {filteredBooks.length ? (
        <div className="textbook-library__list">
          {filteredBooks.map((book) => {
            const percentage = book.progressAvailable ? Math.round(book.progress * 100) : null;
            const descriptionId = `textbook-description-${String(book.id).replace(/[^\w-]/g, '-')}`;
            return (
              <article key={book.id} className={`textbook-library-card${book.isCurrent ? ' is-current' : ''}`}>
                <span className="textbook-library-card__cover">
                  <img src={book.coverUrl} alt="" loading="lazy" />
                </span>
                <div className="textbook-library-card__body">
                  <div className="textbook-library-card__badges">
                    <span>{book.categoryLabel}</span>
                    {book.isCurrent && <strong>当前在学</strong>}
                  </div>
                  <h3>《{book.name}》</h3>
                  <p className="textbook-library-card__facts">
                    <span>{book.chapterCount} 章</span><i aria-hidden="true">·</i><span>{book.knowledgePointCount} 个知识点</span>
                  </p>
                  <div className="textbook-library-card__progress-row">
                    <span>{progressSummary(book)}</span>
                    <div
                      className="textbook-library-card__progress"
                      role={percentage === null ? undefined : 'progressbar'}
                      aria-label={`${book.name}学习进度`}
                      aria-valuemin={percentage === null ? undefined : 0}
                      aria-valuemax={percentage === null ? undefined : 100}
                      aria-valuenow={percentage ?? undefined}
                    ><i style={{ width: `${percentage || 0}%` }} /></div>
                    <strong>{percentage === null ? '进度待统计' : `${percentage}%`}</strong>
                  </div>
                  <button type="button" onClick={() => onOpen?.(book)} aria-label={`继续学习《${book.name}》`} aria-describedby={descriptionId}>
                    <BookOpen aria-hidden="true" size={15} />继续学习<ArrowRight aria-hidden="true" size={15} />
                  </button>
                  <div id={descriptionId} className="textbook-library-card__description">
                    <p>{book.description}</p>
                  </div>
                </div>
              </article>
            );
          })}
        </div>
      ) : <div className="textbook-library__empty">{noResultsText}</div>}
      {remainingCount > 0 && activeFilter === 'all' && !normalizedQuery && (
        <div className="textbook-library__expand">
          <button type="button" onClick={onExpandAll}>展开全部教材</button>
          <span>还可查看 {remainingCount} 本教材</span>
        </div>
      )}
    </section>
  );
}
