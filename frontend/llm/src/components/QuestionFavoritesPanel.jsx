import React, { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, BookMarked, ChevronDown, ChevronUp, FolderPlus, Loader2, Plus, Search, Trash2, X } from 'lucide-react';
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
  return <div className="workshop-library__detail">
    {content.question_content && <p className="workshop-library__question">{content.question_content}</p>}
    {options.length > 0 && <ol>{options.map((option, index) => {
      const key = option.option_id || option.key || option.id || String.fromCharCode(65 + index);
      const value = option.content || option.value || option.text || String(option);
      return <li key={`${key}-${index}`}><strong>{key}.</strong> {value}</li>;
    })}</ol>}
    {content.my_answer && <p><strong>我的答案：</strong>{String(content.my_answer)}</p>}
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
          : '',
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

  const leaveFolder = () => {
    setSelectedFolderId('');
    setExpandedId('');
    setQuery('');
    setSourceFilter('');
    setDateFilter('');
  };

  return <section className="workshop-library" aria-labelledby="favorites-title">
    <header className="workshop-library__header">
      <div><span>个人知识沉淀</span><h2 id="favorites-title">{selectedFolder?.name || '知识收藏'}</h2><p>{selectedFolder ? `${folderFavorites.length} 道收藏题目` : '按收藏簿整理训练题目，复盘答案与解析。'}</p></div>
    </header>
    {error && <p role="alert" className="workshop-library__error">{error}</p>}
    {loading ? <p role="status" className="workshop-library__loading"><Loader2 className="animate-spin" size={18} />正在加载收藏…</p> : !selectedFolder ? <div className="workshop-library__folder-grid" role="region" aria-label="收藏簿卡片">
      {folders.map((folder, index) => <button key={folder.folder_id} type="button" className="workshop-library__folder-card" onClick={() => setSelectedFolderId(folder.folder_id)}><BookMarked size={25} aria-hidden="true" /><span>{folder.name}</span><small>{folder.favorite_count} 项</small><i aria-hidden="true">{String(index + 1).padStart(2, '0')}</i></button>)}
      <button type="button" className="workshop-library__folder-card workshop-library__folder-card--new" onClick={() => setFolderComposerOpen(true)}><Plus size={25} aria-hidden="true" /><span>新建收藏簿</span></button>
    </div> : <div className="workshop-library__layout">
      <div className="workshop-library__detail-header">
        <button type="button" onClick={leaveFolder}><ArrowLeft size={16} />返回收藏簿列表</button>
        <button type="button" className="workshop-library__danger" onClick={removeFolder}><Trash2 size={14} />删除当前收藏簿</button>
      </div>
      <div className="workshop-notes__filters">
        <label className="workshop-notes__search"><Search size={16} /><input aria-label="搜索收藏" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索题目" /></label>
        <label>来源<select aria-label="筛选收藏来源" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="">全部</option>{sources.map((source) => <option key={source}>{source}</option>)}</select></label>
        <label>日期<select aria-label="筛选收藏日期" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)}><option value="">全部</option>{dates.map((date) => <option key={date}>{date}</option>)}</select></label>
      </div>
      <div className="workshop-library__items">
        {visibleFavorites.length === 0 ? <div className="workshop-library__empty workshop-library__empty--compact"><BookMarked size={24} /><p>{query || sourceFilter || dateFilter ? '暂无符合条件的收藏' : '这个收藏簿还没有内容。'}</p><small>完成题目批改后，可在解析下方加入收藏。</small></div> : visibleFavorites.map((item) => {
          const open = expandedId === item.favorite_id;
          return <article key={item.favorite_id} className={open ? 'is-open' : ''}>
            <button type="button" className="workshop-library__item-toggle" aria-expanded={open} onClick={() => setExpandedId(open ? '' : item.favorite_id)}><span><strong>{item.title}</strong><small>{item.source} · {String(item.updated_at || '').slice(0, 10)}</small></span>{open ? <ChevronUp size={17} /> : <ChevronDown size={17} />}</button>
            {open && <div className="workshop-library__item-body"><FavoriteContent item={item} /><button type="button" className="workshop-library__delete" onClick={() => removeFavorite(item.favorite_id)}><Trash2 size={14} />取消收藏</button></div>}
          </article>;
        })}
      </div>
    </div>}
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
