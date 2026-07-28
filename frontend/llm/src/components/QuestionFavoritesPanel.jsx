import React, { useCallback, useEffect, useState } from 'react';
import { BookMarked, ChevronDown, ChevronUp, FolderPlus, Loader2, Play, Plus, Search, Star, Trash2, X } from 'lucide-react';
import {
  createFavoriteFolder,
  deleteFavorite,
  deleteFavoriteFolder,
  loadFavoriteFolders,
  loadFavorites,
} from './workshopLibraryApi';

function FavoriteContent({ item }) {
  const content = item.content || {};
  const options = Array.isArray(content.options) ? content.options : [];
  const answer = Array.isArray(content.standard_answer)
    ? content.standard_answer.join('、')
    : String(content.standard_answer || '');
  const myAnswer = String(content.my_answer || '');
  return <div className="workshop-library__detail">
    {content.question_content && <p className="workshop-library__question">{content.question_content}</p>}
    {options.length > 0 && <div style={{marginTop:8}}>{options.map((option, index) => {
      const key = option.option_id || option.key || option.id || String.fromCharCode(65 + index);
      const value = option.content || option.value || option.text || String(option);
      const val = String(key || value);
      const isMy = myAnswer.includes(val);
      const isCorrect = answer.includes(val);
      let bg = 'transparent';
      if (isMy && isCorrect) bg = '#dcfce7';
      else if (isMy && !isCorrect) bg = '#fee2e2';
      else if (!isMy && isCorrect) bg = '#dcfce7';
      return <div key={`${key}-${index}`} style={{background:bg,borderRadius:4,padding:'3px 8px',margin:'3px 0',fontSize:'.85rem'}}><strong>{key}.</strong> {String(value).replace(/^[A-Z][.．、)\s]\s*/, '')}{isMy&&<span style={{color:'#ef4444',fontSize:'.75rem',marginLeft:8}}>我的作答</span>}{isCorrect&&!isMy&&<span style={{color:'#16a34a',fontSize:'.75rem',marginLeft:8}}>正确答案</span>}</div>;
    })}</div>}
    {content.my_answer && <p style={{marginTop:8}}><strong>我的答案：</strong>{myAnswer}</p>}
    {answer && <p><strong>参考答案：</strong>{answer}</p>}
    {content.explanation && <p><strong>解析：</strong>{content.explanation}</p>}
  </div>;
}

export default function QuestionFavoritesPanel() {
  const [folders, setFolders] = useState([]);
  const [favorites, setFavorites] = useState([]);
  const [selectedFolderId, setSelectedFolderId] = useState('');
  const [expandedId, setExpandedId] = useState('');
  const [folderName, setFolderName] = useState('');
  const [folderComposerOpen, setFolderComposerOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [dateFilter, setDateFilter] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async (preferredFolderId = '') => {
    setLoading(true);
    setError('');
    try {
      const [folderPayload, favoritePayload] = await Promise.all([
        loadFavoriteFolders(),
        loadFavorites(),
      ]);
      const nextFolders = folderPayload.items || [];
      setFolders(nextFolders);
      setFavorites(favoritePayload.items || []);
      setSelectedFolderId(
        nextFolders.some((item) => item.folder_id === preferredFolderId)
          ? preferredFolderId
          : nextFolders[0]?.folder_id || '',
      );
    } catch (reason) {
      setError(reason.message || '收藏加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const addFolder = async (event) => {
    event.preventDefault();
    if (!folderName.trim()) return;
    setError('');
    try {
      const payload = await createFavoriteFolder(folderName.trim());
      setFolderName('');
      setFolderComposerOpen(false);
      await refresh(payload.folder.folder_id);
    } catch (reason) {
      setError(reason.message || '收藏簿创建失败');
    }
  };

  const removeFolder = async () => {
    if (!selectedFolderId) return;
    setError('');
    try {
      await deleteFavoriteFolder(selectedFolderId);
      await refresh('');
    } catch (reason) {
      setError(reason.message || '收藏簿删除失败');
    }
  };

  const removeFavorite = async (favoriteId) => {
    setError('');
    try {
      await deleteFavorite(favoriteId);
      setExpandedId((current) => current === favoriteId ? '' : current);
      await refresh(selectedFolderId);
    } catch (reason) {
      setError(reason.message || '取消收藏失败');
    }
  };

  const selectedFolder = folders.find((folder) => folder.folder_id === selectedFolderId);
  const folderFavorites = favorites.filter((item) => item.folder_id === selectedFolderId);
  const sources = [...new Set(folderFavorites.map((item) => item.source).filter(Boolean))];
  const dates = [...new Set(folderFavorites.map((item) => String(item.updated_at || '').slice(0, 10)).filter(Boolean))].sort().reverse();
  const visibleFavorites = folderFavorites.filter((item) => {
    const keyword = query.trim().toLocaleLowerCase();
    return (!sourceFilter || item.source === sourceFilter)
      && (!dateFilter || String(item.updated_at || '').slice(0, 10) === dateFilter)
      && (!keyword || `${item.title} ${item.content?.question_content || ''}`.toLocaleLowerCase().includes(keyword));
  });

  return <section className="question-collection" aria-labelledby="favorites-title">
    {error && <p role="alert" className="workshop-library__error">{error}</p>}
    {loading ? <p role="status" className="workshop-library__loading"><Loader2 className="animate-spin" size={18} />正在加载收藏夹…</p> : (
      <div className="question-collection__shell">
        <aside className="question-collection__sidebar" aria-label="收藏题单">
          <header><BookMarked size={20} /><strong>我的题单</strong><button type="button" aria-label="新建收藏题单" onClick={() => setFolderComposerOpen(true)}><Plus size={16} /></button></header>
          <nav>
            {folders.map((folder) => (
              <button
                key={folder.folder_id}
                type="button"
                className={selectedFolderId === folder.folder_id ? 'is-active' : ''}
                onClick={() => { setSelectedFolderId(folder.folder_id); setExpandedId(''); }}
              >
                <Star size={15} aria-hidden="true" />
                <span>{folder.name}</span>
                <small>{folder.favorite_count}</small>
              </button>
            ))}
          </nav>
          {!folders.length && <p>点击上方“+”新建题单；首次收藏题目时也会自动创建默认收藏。</p>}
        </aside>

        <main className="question-collection__main">
          <section className="question-collection__summary">
            <span className="question-collection__star"><Star size={34} fill="currentColor" aria-hidden="true" /></span>
            <div><small>收藏夹</small><h2 id="favorites-title">{selectedFolder?.name || '我的收藏'}</h2><p>{folderFavorites.length} 道题 · 集中复盘题干、答案与解析</p></div>
            <button type="button" disabled={!visibleFavorites.length} onClick={() => setExpandedId(visibleFavorites[0]?.favorite_id || '')}><Play size={16} fill="currentColor" />开始复习</button>
            {selectedFolder && <button type="button" className="question-collection__delete-folder" onClick={removeFolder} aria-label="删除当前题单"><Trash2 size={15} /></button>}
          </section>

          <div className="question-collection__filters">
            <label><Search size={16} /><input aria-label="搜索收藏" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索题目" /></label>
            <select aria-label="筛选收藏来源" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="">全部来源</option>{sources.map((source) => <option key={source}>{source}</option>)}</select>
            <select aria-label="筛选收藏日期" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)}><option value="">全部日期</option>{dates.map((date) => <option key={date}>{date}</option>)}</select>
          </div>

          <div className="question-collection__items">
            {visibleFavorites.length === 0 ? <div className="workshop-library__empty workshop-library__empty--compact"><BookMarked size={24} /><p>{query || sourceFilter || dateFilter ? '暂无符合条件的收藏' : '这个题单还没有题目。'}</p><small>在任何解题界面点击书签图标即可收藏。</small></div> : visibleFavorites.map((item, index) => {
              const open = expandedId === item.favorite_id;
              return <article key={item.favorite_id} className={open ? 'is-open' : ''}>
                <button type="button" className="question-collection__item-toggle" aria-expanded={open} onClick={() => setExpandedId(open ? '' : item.favorite_id)}>
                  <span className="question-collection__position">{index + 1}</span>
                  <span><strong>{item.title}</strong><small>{item.source} · {String(item.updated_at || '').slice(0, 10)}</small></span>
                  {open ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
                </button>
                {open && <div className="workshop-library__item-body"><FavoriteContent item={item} /><button type="button" className="workshop-library__delete" onClick={() => removeFavorite(item.favorite_id)}><Trash2 size={14} />取消收藏</button></div>}
              </article>;
            })}
          </div>
        </main>
      </div>
    )}
    {folderComposerOpen && <div className="workshop-save-dialog" role="dialog" aria-modal="true" aria-labelledby="favorite-folder-dialog-title">
      <div>
        <form onSubmit={addFolder}>
          <header><h3 id="favorite-folder-dialog-title">新建收藏簿</h3><button type="button" aria-label="关闭新建收藏簿窗口" onClick={() => setFolderComposerOpen(false)}><X size={18} /></button></header>
          <label htmlFor="favorite-folder-name">收藏簿名称</label>
          <input id="favorite-folder-name" value={folderName} onChange={(event) => setFolderName(event.target.value)} maxLength={80} placeholder="例如：方剂重点" />
          <footer><button type="button" onClick={() => setFolderComposerOpen(false)}>取消</button><button type="submit" disabled={!folderName.trim()}><FolderPlus size={16} />新建</button></footer>
        </form>
      </div>
    </div>}
  </section>;
}
