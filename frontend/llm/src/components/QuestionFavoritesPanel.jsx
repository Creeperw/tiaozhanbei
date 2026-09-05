import React, { useCallback, useEffect, useState } from 'react';
import { BookMarked, FolderPlus, Loader2, MoveRight, Play, Plus, Search, Star, Trash2, X } from 'lucide-react';
import {
  createFavoriteFolder,
  deleteFavorite,
  deleteFavoriteFolder,
  loadFavoriteFolders,
  loadFavorites,
  saveFavorite,
} from './workshopLibraryApi';

const KNOWLEDGE_FAVORITE_TYPES = new Set(['textbook_pdf_page']);
const KNOWLEDGE_FOLDER_NAMES = new Set(['知识点收藏', '教材页收藏']);

const isKnowledgeFavorite = (item) => (
  KNOWLEDGE_FAVORITE_TYPES.has(String(item?.resource_type || ''))
  || Boolean(item?.content?.book_title && item?.content?.pdf_page)
);

const isQuestionFavorite = (item) => !isKnowledgeFavorite(item)
  && (!item?.resource_type || item.resource_type === 'question');

export default function QuestionFavoritesPanel({ onNavigate, collectionType = 'question' }) {
  const knowledgeCollection = collectionType === 'knowledge';
  const [folders, setFolders] = useState([]);
  const [favorites, setFavorites] = useState([]);
  const [selectedFolderId, setSelectedFolderId] = useState('');
  const [pendingDelete, setPendingDelete] = useState(null);
  const [pendingMove, setPendingMove] = useState(null);
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
      const allFavorites = favoritePayload.items || [];
      const nextFavorites = allFavorites.filter(
        knowledgeCollection ? isKnowledgeFavorite : isQuestionFavorite,
      );
      const favoriteCounts = nextFavorites.reduce((counts, item) => {
        counts.set(item.folder_id, (counts.get(item.folder_id) || 0) + 1);
        return counts;
      }, new Map());
      const nextFolders = (folderPayload.items || []).filter((folder) => (
        knowledgeCollection
          ? KNOWLEDGE_FOLDER_NAMES.has(folder.name) || favoriteCounts.has(folder.folder_id)
          : !KNOWLEDGE_FOLDER_NAMES.has(folder.name)
      )).map((folder) => ({
        ...folder,
        favorite_count: favoriteCounts.get(folder.folder_id) || 0,
      }));
      setFolders(nextFolders);
      setFavorites(nextFavorites);
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
  }, [knowledgeCollection]);

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
      await refresh(selectedFolderId);
      setPendingDelete(null);
    } catch (reason) {
      setError(reason.message || '取消收藏失败');
    }
  };

  const moveFavorite = async (item, targetFolderId) => {
    setError('');
    try {
      await saveFavorite({
        folder_id: targetFolderId,
        resource_type: item.resource_type || 'question',
        resource_id: item.resource_id || item.favorite_id,
        title: item.title,
        content: item.content || {},
        source: item.source || '练习工坊',
      });
      await deleteFavorite(item.favorite_id);
      setPendingMove(null);
      await refresh(selectedFolderId);
    } catch (reason) {
      setError(reason.message || '移动收藏失败');
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

  const openFavorite = (item) => {
    const content = item.content || {};
    const titleBook = String(item.title || '').match(/《([^》]+)》/)?.[1] || '';
    if (item.resource_type === 'textbook_pdf_page' || (content.book_title && content.pdf_page)) {
      onNavigate?.({
        page: 'practice',
        params: {
          view: 'textbook-chapters',
          route: content.route || (content.edition === '十三五' ? 'textbook_13_5' : 'textbook_14_5'),
          lv1: content.book_title || titleBook,
          openPdf: true,
          pdfPage: Number(content.pdf_page) || 1,
          source: 'favorite',
        },
      });
      return;
    }
    onNavigate?.({
      page: 'practice',
      params: {
        view: 'workspace',
        taskType: 'question_training',
        favoriteId: item.favorite_id,
        questionId: content.question_id || item.resource_id || '',
      },
    });
  };

  const collectionLabel = knowledgeCollection ? '知识点收藏' : '我的题单';
  const sidebarLabel = knowledgeCollection ? '知识点收藏夹' : '收藏题单';
  const emptyLabel = knowledgeCollection ? '知识点收藏还没有内容。' : '我的题单还没有内容。';
  const filteredEmptyLabel = knowledgeCollection ? '暂无符合条件的知识点收藏' : '暂无符合条件的收藏';
  const searchPlaceholder = knowledgeCollection ? '搜索教材或页码' : '搜索题目';

  return <section className={`question-collection${knowledgeCollection ? ' question-collection--knowledge' : ''}`} aria-labelledby="favorites-title">
    {error && <p role="alert" className="workshop-library__error">{error}</p>}
    {loading ? <p role="status" className="workshop-library__loading"><Loader2 className="animate-spin" size={18} />正在加载{collectionLabel}…</p> : (
      <div className="question-collection__shell">
        <aside className="question-collection__sidebar" aria-label={sidebarLabel}>
          <header><BookMarked size={20} /><strong>{collectionLabel}</strong>{!knowledgeCollection && <button type="button" aria-label="新建收藏题单" onClick={() => setFolderComposerOpen(true)}><Plus size={16} /></button>}</header>
          <nav>
            {folders.map((folder) => (
              <button
                key={folder.folder_id}
                type="button"
                className={selectedFolderId === folder.folder_id ? 'is-active' : ''}
                onClick={() => setSelectedFolderId(folder.folder_id)}
              >
                <Star size={15} aria-hidden="true" />
                <span>{folder.name}</span>
                <small>{folder.favorite_count}</small>
              </button>
            ))}
          </nav>
          {!folders.length && <p>{knowledgeCollection ? '在电子教材中点击“收藏本页”，内容会自动汇总到这里。' : '点击上方“+”新建题单；首次收藏题目时也会自动创建默认收藏。'}</p>}
        </aside>

        <main className="question-collection__main">
          <section className="question-collection__summary">
            <span className="question-collection__star"><Star size={34} fill="currentColor" aria-hidden="true" /></span>
            <div><small>{collectionLabel}</small><h2 id="favorites-title">{selectedFolder?.name || collectionLabel}</h2><p>{folderFavorites.length} 项收藏 · 点击直接返回对应内容</p></div>
            <button type="button" disabled={!visibleFavorites.length} onClick={() => openFavorite(visibleFavorites[0])}><Play size={16} fill="currentColor" />开始学习</button>
            {!knowledgeCollection && selectedFolder && <button type="button" className="question-collection__delete-folder" onClick={removeFolder} aria-label="删除当前题单"><Trash2 size={15} /></button>}
          </section>

          <div className="question-collection__filters">
            <label><Search size={16} /><input aria-label="搜索收藏" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={searchPlaceholder} /></label>
            <select aria-label="筛选收藏来源" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="">全部来源</option>{sources.map((source) => <option key={source}>{source}</option>)}</select>
            <select aria-label="筛选收藏日期" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)}><option value="">全部日期</option>{dates.map((date) => <option key={date}>{date}</option>)}</select>
          </div>

          <div className="question-collection__items">
            {visibleFavorites.length === 0 ? <div className="workshop-library__empty workshop-library__empty--compact"><BookMarked size={24} /><p>{query || sourceFilter || dateFilter ? filteredEmptyLabel : emptyLabel}</p><small>{knowledgeCollection ? '在电子教材中点击“收藏本页”即可加入知识点收藏。' : '在解题界面点击书签图标即可加入题单。'}</small></div> : visibleFavorites.map((item, index) => (
              <article key={item.favorite_id}>
                <button type="button" className="question-collection__item-open" onClick={() => openFavorite(item)}>
                  <span className="question-collection__position">{index + 1}</span>
                  <span><strong>{item.title}</strong><small>{item.source} · {String(item.updated_at || '').slice(0, 10)}</small></span>
                </button>
                {!knowledgeCollection && <button type="button" className="question-collection__item-delete" aria-label={`移动收藏：${item.title}`} onClick={() => setPendingMove(item)}><MoveRight size={15} /></button>}
                <button type="button" className="question-collection__item-delete" aria-label={`删除收藏：${item.title}`} onClick={() => setPendingDelete(item)}><Trash2 size={15} /></button>
              </article>
            ))}
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
    {pendingMove && <div className="workshop-save-dialog" role="dialog" aria-modal="true" aria-labelledby="favorite-move-title">
      <div>
        <header><h3 id="favorite-move-title">移动收藏</h3><button type="button" aria-label="关闭移动收藏窗口" onClick={() => setPendingMove(null)}><X size={18} /></button></header>
        <p>选择要移动到的收藏簿。</p>
        <div className="question-collection__move-targets">
          {folders.filter((folder) => folder.folder_id !== selectedFolderId).map((folder) => (
            <button key={folder.folder_id} type="button" onClick={() => moveFavorite(pendingMove, folder.folder_id)}>{folder.name}</button>
          ))}
          {folders.length < 2 && <span>请先新建另一个收藏簿。</span>}
        </div>
        <footer><button type="button" onClick={() => setPendingMove(null)}>取消</button></footer>
      </div>
    </div>}
    {pendingDelete && <div className="question-collection__confirm-backdrop" role="dialog" aria-modal="true" aria-labelledby="favorite-delete-title" onMouseDown={(event) => event.target === event.currentTarget && setPendingDelete(null)}>
      <div className="question-collection__confirm">
        <span><Trash2 size={19} /></span>
        <h3 id="favorite-delete-title">删除这条收藏？</h3>
        <p>“{pendingDelete.title}”将从收藏夹中移除。</p>
        <footer><button type="button" onClick={() => setPendingDelete(null)}>取消</button><button type="button" onClick={() => removeFavorite(pendingDelete.favorite_id)}>确认删除</button></footer>
      </div>
    </div>}
  </section>;
}
