import React, { useEffect, useState } from 'react';
import { BookMarked, Loader2, NotebookPen, Plus, X } from 'lucide-react';
import {
  createFavoriteFolder,
  createNote,
  loadFavoriteFolders,
  saveFavorite,
} from './workshopLibraryApi';

export function FavoriteQuestionButton({ question, source = '训练工坊' }) {
  const [open, setOpen] = useState(false);
  const [folders, setFolders] = useState([]);
  const [selectedFolderId, setSelectedFolderId] = useState('');
  const [newFolderName, setNewFolderName] = useState('');
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    loadFavoriteFolders()
      .then((payload) => {
        const items = payload.items || [];
        setFolders(items);
        setSelectedFolderId((current) => current || items[0]?.folder_id || '');
      })
      .catch((reason) => setStatus(reason.message || '收藏簿加载失败'))
      .finally(() => setLoading(false));
  }, [open]);

  const addFolder = async () => {
    if (!newFolderName.trim()) return;
    setLoading(true);
    setStatus('');
    try {
      const payload = await createFavoriteFolder(newFolderName.trim());
      const folder = payload.folder;
      setFolders((current) => current.some((item) => item.folder_id === folder.folder_id)
        ? current
        : [folder, ...current]);
      setSelectedFolderId(folder.folder_id);
      setNewFolderName('');
    } catch (reason) {
      setStatus(reason.message || '收藏簿创建失败');
    } finally {
      setLoading(false);
    }
  };

  const submit = async () => {
    if (!selectedFolderId) return;
    setLoading(true);
    setStatus('');
    try {
      await saveFavorite({
        folder_id: selectedFolderId,
        resource_type: 'question',
        resource_id: question.resource_id,
        title: question.title,
        source,
        content: question.content,
      });
      setStatus('已加入收藏');
    } catch (reason) {
      setStatus(reason.message || '收藏失败');
    } finally {
      setLoading(false);
    }
  };

  return <>
    <button type="button" className="workshop-save-action" onClick={() => setOpen(true)}><BookMarked size={15} />加入收藏</button>
    {open && <div className="workshop-save-dialog" role="dialog" aria-modal="true" aria-labelledby="favorite-dialog-title">
      <div>
        <header><h3 id="favorite-dialog-title">加入知识收藏</h3><button type="button" aria-label="关闭收藏窗口" onClick={() => setOpen(false)}><X size={18} /></button></header>
        <label>选择收藏簿<select value={selectedFolderId} onChange={(event) => setSelectedFolderId(event.target.value)}><option value="">请选择</option>{folders.map((folder) => <option key={folder.folder_id} value={folder.folder_id}>{folder.name}</option>)}</select></label>
        <label>或新建收藏簿<div className="workshop-save-dialog__inline"><input value={newFolderName} onChange={(event) => setNewFolderName(event.target.value)} placeholder="收藏簿名称" /><button type="button" onClick={addFolder} disabled={loading || !newFolderName.trim()}><Plus size={14} />新建</button></div></label>
        {status && <p role="status">{status}</p>}
        <footer><button type="button" onClick={() => setOpen(false)}>取消</button><button type="button" onClick={submit} disabled={loading || !selectedFolderId}>{loading && <Loader2 size={14} className="animate-spin" />}保存收藏</button></footer>
      </div>
    </div>}
  </>;
}

export function NoteQuestionButton({ question, source = '训练工坊' }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState(question.defaultTitle || '');
  const [content, setContent] = useState('');
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);

  const submit = async () => {
    if (!title.trim() || !content.trim()) return;
    setLoading(true);
    setStatus('');
    try {
      await createNote({
        title: title.trim(),
        content: content.trim(),
        note_type: '题目笔记',
        source,
        resource_type: 'question',
        resource_id: question.resource_id,
        context: question.content,
      });
      setStatus('笔记已保存');
    } catch (reason) {
      setStatus(reason.message || '笔记保存失败');
    } finally {
      setLoading(false);
    }
  };

  return <>
    <button type="button" className="workshop-save-action" onClick={() => setOpen(true)}><NotebookPen size={15} />记笔记</button>
    {open && <div className="workshop-save-dialog" role="dialog" aria-modal="true" aria-labelledby="note-dialog-title">
      <div>
        <header><h3 id="note-dialog-title">记录题目笔记</h3><button type="button" aria-label="关闭笔记窗口" onClick={() => setOpen(false)}><X size={18} /></button></header>
        <label>标题<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={200} /></label>
        <label>内容<textarea value={content} onChange={(event) => setContent(event.target.value)} rows={5} maxLength={20000} placeholder="写下解题思路、易错点或复习提醒…" /></label>
        {status && <p role="status">{status}</p>}
        <footer><button type="button" onClick={() => setOpen(false)}>取消</button><button type="button" onClick={submit} disabled={loading || !title.trim() || !content.trim()}>{loading && <Loader2 size={14} className="animate-spin" />}保存笔记</button></footer>
      </div>
    </div>}
  </>;
}
