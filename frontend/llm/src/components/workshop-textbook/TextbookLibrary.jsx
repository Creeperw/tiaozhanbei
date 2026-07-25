import React from 'react';
import { ArrowRight, Layers3 } from 'lucide-react';
import { textbookCoverUrl, textbookIntroduction } from './textbookMetadata';
import './textbookLibrary.css';

export default function TextbookLibrary({
  books = [],
  emptyText = '教材正在准备中',
  onOpen,
  remainingCount = 0,
  onExpandAll,
}) {
  return (
    <section className="textbook-library" aria-label="教材学习列表">
      <div className="textbook-library__heading">
        <div><span>Textbook library</span><h2>教材学习</h2></div>
        <p>选择一本教材，按章节、小节和知识点循序学习。</p>
      </div>
      {books.length ? (
        <div className="textbook-library__list">
          {books.map((item) => {
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
      ) : <div className="textbook-library__empty">{emptyText}</div>}
      {remainingCount > 0 && (
        <div className="textbook-library__expand">
          <button type="button" onClick={onExpandAll}>展开所有教材</button>
          <span>还可查看 {remainingCount} 本教材</span>
        </div>
      )}
    </section>
  );
}
