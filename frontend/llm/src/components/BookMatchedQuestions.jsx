import React, { useEffect, useState } from 'react';
import { FileQuestion, LoaderCircle, RefreshCw, Search } from 'lucide-react';
import { loadBookMatchedQuestions } from './workshop-textbook/textbookPdfApi';
import './bookMatchedQuestions.css';

export default function BookMatchedQuestions({ bookId, bookTitle }) {
  const [payload, setPayload] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');

  const load = () => {
    if (!bookId) return;
    setLoading(true);
    setError('');
    loadBookMatchedQuestions(bookId)
      .then((data) => setPayload(data))
      .catch((reason) => setError(reason.message || '加载匹配题目失败'))
      .finally(() => setLoading(false));
  };

  useEffect(load, [bookId]);

  const items = (payload?.items || []).filter((item) => {
    const text = String(item.stem || '') + String(item.source || '') + String(item.type || '');
    return !query || text.includes(query.trim());
  });

  return (
    <section className="book-questions" aria-label="本书匹配题目">
      <header className="book-questions__header">
        <div><span>Question matching</span><h2>本书匹配题目</h2><p>按“知识可迁移性”要求，上传教材会尝试与题库匹配；未勾选匹配的教材也可在此按需触发。</p></div>
        <div className="book-questions__actions">
          <label className="book-questions__search"><Search aria-hidden="true" size={15} /><input aria-label="搜索题目" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索题目/来源" /></label>
          <button type="button" onClick={load} disabled={loading}><RefreshCw aria-hidden="true" size={15} />重新匹配</button>
        </div>
      </header>

      {loading ? (
        <div className="book-questions__state" role="status"><LoaderCircle className="is-spinning" />正在匹配题目…</div>
      ) : error ? (
        <div className="book-questions__state" role="alert"><h3>加载失败</h3><p>{error}</p></div>
      ) : payload?.matched === false ? (
        <div className="book-questions__state"><FileQuestion aria-hidden="true" size={24} /><h3>暂未匹配到题目</h3><p>{payload.reason || '未生成匹配结果'}</p></div>
      ) : items.length ? (
        <>
          <div className="book-questions__meta"><span>共 {payload.total} 道匹配题目</span>{items.length < payload.total ? <span>显示 {items.length} 道</span> : null}</div>
          <ul className="book-questions__list">
            {items.map((item, index) => (
              <li key={`${item.question_id}-${index}`} className="book-questions__item">
                <div className="book-questions__item-top">
                  <span className="book-questions__score">{Math.round((item.score || 0) * 100)}%</span>
                  <strong>{item.type || '题目'}</strong>
                  <small>{item.source || ''}</small>
                </div>
                <p>{item.stem || ''}</p>
                {item.question_id && <em>{item.question_id}</em>}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <div className="book-questions__state"><FileQuestion aria-hidden="true" size={24} /><h3>没有匹配到题目</h3><p>这本书可能不在题库覆盖范围内，接口已执行匹配但结果为空。</p></div>
      )}
    </section>
  );
}