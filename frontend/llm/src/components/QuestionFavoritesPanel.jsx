import React, { useEffect, useState } from 'react';
import { BookMarked, ChevronDown, ChevronUp, FolderPlus, Loader2, Trash2 } from 'lucide-react';
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = async (preferredFolderId = selectedFolderId) => {
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
  };

  useEffect(() => { refresh(); }, []);

  const addFolder = async (event) => {
    event.preventDefault();
    if (!folderName.trim()) return;
    setError('');
    try {
      const payload = await createFavoriteFolder(folderName.trim());
      setFolderName('');
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

  const visibleFavorites = favorites.filter((item) => item.folder_id === selectedFolderId);

  return <section className="workshop-library" aria-labelledby="favorites-title">
    <header className="workshop-library__header">
      <div><span>个人知识沉淀</span><h2 id="favorites-title">知识收藏</h2><p>按收藏簿整理训练题目，复盘答案与解析。</p></div>
      <form onSubmit={addFolder} className="workshop-library__create">
        <label htmlFor="favorite-folder-name">新建收藏簿</label>
        <div><input id="favorite-folder-name" value={folderName} onChange={(event) => setFolderName(event.target.value)} maxLength={80} placeholder="例如：方剂重点" /><button type="submit" disabled={!folderName.trim()}><FolderPlus size={16} />新建</button></div>
      </form>
    </header>
    {error && <p role="alert" className="workshop-library__error">{error}</p>}
    {loading ? <p role="status" className="workshop-library__loading"><Loader2 className="animate-spin" size={18} />正在加载收藏…</p> : folders.length === 0 ? <div className="workshop-library__empty"><BookMarked size={28} /><h3>还没有收藏簿</h3><p>先新建收藏簿，再从已批改的题目中加入收藏。</p></div> : <div className="workshop-library__layout">
      <aside className="workshop-library__folder-grid" role="region" aria-label="收藏簿卡片">
        {folders.map((folder) => <button key={folder.folder_id} type="button" className="workshop-library__folder-card" aria-pressed={selectedFolderId === folder.folder_id} onClick={() => { setSelectedFolderId(folder.folder_id); setExpandedId(''); }}><span>{folder.name}</span><small>{folder.favorite_count} 项</small></button>)}
        <button type="button" className="workshop-library__danger" onClick={removeFolder}><Trash2 size={14} />删除当前收藏簿</button>
      </aside>
      <div className="workshop-library__items">
        {visibleFavorites.length === 0 ? <div className="workshop-library__empty workshop-library__empty--compact"><p>这个收藏簿还没有内容。</p><small>完成题目批改后，可在解析下方加入收藏。</small></div> : visibleFavorites.map((item) => {
          const open = expandedId === item.favorite_id;
          return <article key={item.favorite_id} className={open ? 'is-open' : ''}>
            <button type="button" className="workshop-library__item-toggle" aria-expanded={open} onClick={() => setExpandedId(open ? '' : item.favorite_id)}><span><strong>{item.title}</strong><small>{item.source} · {String(item.updated_at || '').slice(0, 10)}</small></span>{open ? <ChevronUp size={17} /> : <ChevronDown size={17} />}</button>
            {open && <div className="workshop-library__item-body"><FavoriteContent item={item} /><button type="button" className="workshop-library__delete" onClick={() => removeFavorite(item.favorite_id)}><Trash2 size={14} />取消收藏</button></div>}
          </article>;
        })}
      </div>
    </div>}
  </section>;
}
