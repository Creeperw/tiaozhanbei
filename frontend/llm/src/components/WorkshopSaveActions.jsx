import React, { useEffect, useState } from 'react';
import { Bookmark, BookMarked, Loader2, NotebookPen, Plus, Star, X } from 'lucide-react';
import {
  createFavoriteFolder,
  createNote,
  createNoteFolder,
  deleteFavorite,
  loadFavoriteFolders,
  loadNoteFolders,
  saveFavorite,
} from './workshopLibraryApi';

export function FavoriteQuestionButton({ question, source = '练习工坊' }) {
  const [open, setOpen] = useState(false);
  const [folders, setFolders] = useState([]);
  const [selectedFolderId, setSelectedFolderId] = useState('');
  const [newFolderName, setNewFolderName] = useState('');
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!question?.resource_id) return;
    loadFavoriteFolders().then((payload) => {
      // Check if already favorited in any folder
      for (const f of (payload.items || [])) {
        if (f.resource_id === question.resource_id) { setSaved(true); return; }
      }
    }).catch(() => {});
  }, [question?.resource_id]);

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
      setSaved(true);
      setStatus('已加入收藏');
    } catch (reason) {
      setStatus(reason.message || '收藏失败');
    } finally {
      setLoading(false);
    }
  };

  return <>
    <button type="button" className="workshop-save-action" onClick={() => {
      if (saved) { setSaved(false); setStatus('已取消收藏'); }
      else { setOpen(true); }
    }} style={{borderColor:'#f0d78c',color:saved?'#a16207':'#c4940a',background:saved?'#fef9c3':'#fff',borderWidth:1,borderStyle:'solid'}}>
      <Star size={15} fill={saved ? 'currentColor' : 'none'} />{saved ? '已收藏' : '加入收藏'}
    </button>
    {open && <div className="workshop-save-dialog" role="dialog" aria-modal="true" aria-labelledby="favorite-dialog-title">
      <div>
        <header><h3 id="favorite-dialog-title">加入收藏夹</h3><button type="button" aria-label="关闭收藏窗口" onClick={() => setOpen(false)}><X size={18} /></button></header>
        <label>选择收藏簿<select value={selectedFolderId} onChange={(event) => setSelectedFolderId(event.target.value)}><option value="">请选择</option>{folders.map((folder) => <option key={folder.folder_id} value={folder.folder_id}>{folder.name}</option>)}</select></label>
        <label>或新建收藏簿<div className="workshop-save-dialog__inline"><input value={newFolderName} onChange={(event) => setNewFolderName(event.target.value)} placeholder="收藏簿名称" /><button type="button" onClick={addFolder} disabled={loading || !newFolderName.trim()}><Plus size={14} />新建</button></div></label>
        {status && <p role="status">{status}</p>}
        <footer><button type="button" onClick={() => setOpen(false)}>取消</button><button type="button" onClick={submit} disabled={loading || !selectedFolderId}>{loading && <Loader2 size={14} className="animate-spin" />}保存收藏</button></footer>
      </div>
    </div>}
  </>;
}

export function FavoriteQuestionIconButton({ question, source = '练习工坊' }) {
  const [favoriteId, setFavoriteId] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [checked, setChecked] = useState(false);
  const saved = Boolean(favoriteId);

  useEffect(() => {
    if (checked || !question?.resource_id) return;
    setChecked(true);
    loadFavoriteFolders().then((payload) => {
      for (const folder of (payload.items || [])) {
        // Quick check: if this question is already in any folder
        if (folder.resource_id === question.resource_id || folder.question_id === question.resource_id) {
          setFavoriteId(folder.favorite_id || '1');
          return;
        }
      }
    }).catch(() => {});
  }, [question?.resource_id]);

  const toggleFavorite = async () => {
    if (loading || !question?.resource_id) return;
    setLoading(true);
    setError('');
    try {
      if (favoriteId) {
        await deleteFavorite(favoriteId);
        setFavoriteId('');
        return;
      }
      const payload = await loadFavoriteFolders();
      let folder = (payload.items || []).find((item) => item.name === '默认收藏')
        || (payload.items || [])[0];
      if (!folder) {
        const created = await createFavoriteFolder('默认收藏');
        folder = created.folder;
      }
      const savedPayload = await saveFavorite({
        folder_id: folder.folder_id,
        resource_type: 'question',
        resource_id: question.resource_id,
        title: question.title,
        source,
        content: question.content,
      });
      const savedId = savedPayload?.favorite?.favorite_id;
      if (!savedId) throw new Error('收藏记录缺少标识');
      setFavoriteId(savedId);
    } catch (reason) {
      setError(reason.message || '收藏失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <span className="question-favorite-control">
      <button
        type="button"
        className={saved ? 'text-amber-500' : 'text-amber-400 hover:text-amber-500'}
        aria-label={saved ? '取消收藏本题' : '收藏本题'}
        title={saved ? '取消收藏本题' : '收藏本题'}
        disabled={loading}
        onClick={toggleFavorite}
        style={{background:'transparent',border:'none',cursor:'pointer',padding:2}}
      >
        {saved ? <Star size={17} aria-hidden="true" fill="currentColor" /> : <Star size={17} aria-hidden="true" />}
      </button>
      {error && <small role="alert">{error}</small>}
    </span>
  );
}

export function NoteQuestionButton({ question, source = '练习工坊' }) {
  const [open, setOpen] = useState(false);
  const [notebooks, setNotebooks] = useState([]);
  const [selectedNotebook, setSelectedNotebook] = useState('');
  const [newNotebookName, setNewNotebookName] = useState('');
  const [noteTitle, setNoteTitle] = useState(question.defaultTitle || '');
  const [noteContent, setNoteContent] = useState('');
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    loadNoteFolders()
      .then((payload) => {
        const items = payload.items || [];
        setNotebooks(items);
        setSelectedNotebook((c) => c || items[0]?.name || '默认笔记本');
      })
      .catch((reason) => setStatus(reason.message || '笔记本加载失败'))
      .finally(() => setLoading(false));
  }, [open]);

  const addNotebook = async () => {
    if (!newNotebookName.trim()) return;
    setLoading(true); setStatus('');
    try {
      const payload = await createNoteFolder(newNotebookName.trim());
      const folder = payload.folder;
      setNotebooks((c) => c.some((i) => i.folder_id === folder.folder_id) ? c : [folder, ...c]);
      setSelectedNotebook(folder.name); setNewNotebookName('');
    } catch (reason) { setStatus(reason.message || '笔记本创建失败'); }
    finally { setLoading(false); }
  };

  const submit = async () => {
    if (!noteTitle.trim() || !noteContent.trim()) return;
    setLoading(true); setStatus('');
    try {
      await createNote({
        title: noteTitle.trim(), content: noteContent.trim(), note_type: '题目笔记',
        source, resource_type: 'question', resource_id: question.resource_id,
        context: { ...question.content, notebook: selectedNotebook || '默认笔记本' },
      });
      setStatus('笔记已保存');
    } catch (reason) { setStatus(reason.message || '笔记保存失败'); }
    finally { setLoading(false); }
  };

  const q = question.content || {};
  const myAnswer = q.my_answer || '';
  const stdAnswer = Array.isArray(q.standard_answer) ? q.standard_answer.join(', ') : (q.standard_answer || '');
  const options = Array.isArray(q.options) ? q.options : [];

  return <>
    <button type="button" className="workshop-save-action" onClick={() => setOpen(true)}><NotebookPen size={15} />记笔记</button>
    {open && <div className="workshop-save-dialog" role="dialog" aria-modal="true" aria-labelledby="note-dialog-title">
      <div>
        <header><h3 id="note-dialog-title">记录题目笔记</h3><button type="button" aria-label="关闭笔记窗口" onClick={() => setOpen(false)}><X size={18} /></button></header>
        <div style={{maxHeight:'180px',overflowY:'auto',marginBottom:'12px',padding:'10px',background:'#f8fafc',borderRadius:'8px',fontSize:'.82rem',lineHeight:1.6}}>
          <div style={{fontWeight:600,marginBottom:4}}>{String(q.question_content || '').slice(0, 200)}</div>
          {options.length > 0 && <div style={{marginTop:6}}>
            {options.map((opt, i) => {
              const label = String.fromCharCode(65 + i);
              const text = typeof opt === 'string' ? opt : (opt.label || opt.content || opt.value || '');
              const val = String(opt.value || opt.label || text);
              const isMy = String(myAnswer).includes(val);
              const isCorrect = String(stdAnswer).includes(val);
              let bg = 'transparent';
              if (isMy && isCorrect) bg = '#dcfce7';
              else if (isMy && !isCorrect) bg = '#fee2e2';
              else if (!isMy && isCorrect) bg = '#dcfce7';
              return <div key={i} style={{background:bg,borderRadius:4,padding:'2px 6px',margin:'2px 0'}}>{label}. {String(text).replace(/^[A-Z][.．、)\s]\s*/, '')}{isMy && <span style={{color:'#ef4444',fontSize:'.7rem',marginLeft:6}}>我的作答</span>}{isCorrect && !isMy && <span style={{color:'#16a34a',fontSize:'.7rem',marginLeft:6}}>正确答案</span>}</div>;
            })}
          </div>}
          {q.explanation && <div style={{marginTop:8,color:'#64748b',fontSize:'.78rem'}}>解析：{String(q.explanation).slice(0, 300)}</div>}
        </div>
        <label>选择笔记本<select value={selectedNotebook} onChange={(e) => setSelectedNotebook(e.target.value)}><option value="">请选择</option>{notebooks.map((nb) => <option key={nb.folder_id} value={nb.name}>{nb.name}</option>)}</select></label>
        <label>或新建笔记本<div className="workshop-save-dialog__inline"><input value={newNotebookName} onChange={(e) => setNewNotebookName(e.target.value)} placeholder="笔记本名称" /><button type="button" onClick={addNotebook} disabled={loading || !newNotebookName.trim()}><Plus size={14} />新建</button></div></label>
        <label>标题<input value={noteTitle} onChange={(e) => setNoteTitle(e.target.value)} maxLength={200} /></label>
        <label>内容<textarea value={noteContent} onChange={(e) => setNoteContent(e.target.value)} rows={5} maxLength={20000} placeholder="写下解题思路、易错点或复习提醒…" /></label>
        {status && <p role="status">{status}</p>}
        <footer><button type="button" onClick={() => setOpen(false)}>取消</button><button type="button" onClick={submit} disabled={loading || !noteTitle.trim() || !noteContent.trim()}>{loading && <Loader2 size={14} className="animate-spin" />}保存笔记</button></footer>
      </div>
    </div>}
  </>;
}
