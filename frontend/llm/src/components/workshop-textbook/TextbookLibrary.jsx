import React, { useMemo, useState } from 'react';
import { ArrowRight, Layers3, Search } from 'lucide-react';
import { textbookCoverUrl, textbookIntroduction } from './textbookMetadata';
import './textbookLibrary.css';

export default function TextbookLibrary({
  books = [],
  emptyText = '教材正在准备中',
  onOpen,
  remainingCount = 0,
  onExpandAll,
}) {
  const [query, setQuery] = useState('');
  const normalizedQuery = query.trim().toLowerCase();
  const filteredBooks = useMemo(() => {
    if (!normalizedQuery) return books;
    return books.filter((item) => {
      const name = String(item.book || item.navigation?.book || item.title || '').toLowerCase();
      const description = String(item.description || item.stage_title || '').toLowerCase();
      return name.includes(normalizedQuery) || description.includes(normalizedQuery);
    });
  }, [books, normalizedQuery]);
  return (
    <section className="textbook-library" aria-label="教材学习列表">
      <div className="textbook-library__heading">
        <div><span>Textbook library</span><h2>教材学习</h2></div>
        <label className="textbook-library__search">
          <Search aria-hidden="true" size={16} />
          <input aria-label="搜索教材" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索教材" />
        </label>
        <p>选择一本教材，按章节、小节和知识点循序学习。</p>
      </div>
      {filteredBooks.length ? (
        <div className="textbook-library__list">
          {filteredBooks.map((item) => {
            const name = String(item.book || item.navigation?.book || item.title || '').replace(/[《》]/g, '').trim();
            return (
              <button key={item.id || item.node_id || `${item.route_id}:${name}`} type="button" className="textbook-library-card" onClick={() => onOpen?.(item)} aria-label={`学习《${name}》`}>
                <span className="textbook-library-card__cover">
                  <img src={textbookCoverUrl(name)} alt="" loading="lazy" />
                </span>
                <span className="textbook-library-card__body">
                  <small>{item.stage_title || '专业教材'}</small>
                  <strong>《{name}》</strong>
                  <span>{textbookIntroduction(name)}</span>
                  <em><Layers3 aria-hidden="true" size={14} />进入章节学习<ArrowRight aria-hidden="true" size={15} /></em>
                </span>
              </button>
            );
          })}
        </div>
      ) : <div className="textbook-library__empty">{query ? '没有找到匹配的教材' : emptyText}</div>}
      {remainingCount > 0 && (
        <div className="textbook-library__expand">
          <button type="button" onClick={onExpandAll}>展开所有教材</button>
          <span>还可查看 {remainingCount} 本教材</span>
        </div>
      )}
    </section>
  );
}
