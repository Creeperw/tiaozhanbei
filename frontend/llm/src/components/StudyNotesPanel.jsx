import React, { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ChevronDown, ChevronUp, Loader2, NotebookPen, Plus, Search, Trash2, X } from 'lucide-react';
import {
  createNote,
  createNoteFolder,
  deleteNote,
  loadNoteFolders,
  loadNotes,
  updateNote,
} from './workshopLibraryApi';

const emptyDraft = { title: '', content: '' };

function QuestionContext({ context }) {
  if (!context?.question_content) return null;
  const options = Array.isArray(context.options) ? context.options : [];
  const answer = Array.isArray(context.standard_answer)
    ? context.standard_answer.join('、')
    : String(context.standard_answer || '');
  return <div className="workshop-notes__context">
    <strong>关联题目</strong>
    <p>{context.question_content}</p>
    {options.map((option, index) => <p key={index}>{option.option_id || option.key || String.fromCharCode(65 + index)}. {option.content || option.value || String(option)}</p>)}
    {answer && <p><strong>参考答案：</strong>{answer}</p>}
    {context.explanation && <p><strong>解析：</strong>{context.explanation}</p>}
  </div>;
}

export default function StudyNotesPanel() {
  const [notes, setNotes] = useState([]);
  const [draft, setDraft] = useState(emptyDraft);
  const [draftNotebook, setDraftNotebook] = useState('');
  const [query, setQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [dateFilter, setDateFilter] = useState('');
  const [selectedNotebook, setSelectedNotebook] = useState('');
  const [noteFolders, setNoteFolders] = useState([]);
  const [newNotebookName, setNewNotebookName] = useState('');
  const [newNotebookOpen, setNewNotebookOpen] = useState(false);
  const [expandedId, setExpandedId] = useState('');
  const [editDraft, setEditDraft] = useState(emptyDraft);
  const [composerOpen, setComposerOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = async () => {
    setLoading(true);
    setError('');
    try {
      const [notesPayload, foldersPayload] = await Promise.all([
        loadNotes(),
        loadNoteFolders(),
      ]);
      setNotes(notesPayload.items || []);
      setNoteFolders(foldersPayload.items || []);
    } catch (reason) {
      setError(reason.message || '笔记加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  const notebooks = useMemo(() => {
    const grouped = new Map(noteFolders.map((folder) => [folder.name, []]));
    notes.forEach((note) => {
      const name = String(note.context?.notebook || '默认笔记本').trim() || '默认笔记本';
      if (!grouped.has(name)) grouped.set(name, []);
      grouped.get(name).push(note);
    });
    if (grouped.size === 0) grouped.set('默认笔记本', []);
    return [...grouped.entries()].map(([name, items]) => ({ name, items }));
  }, [noteFolders, notes]);
  const notebookNotes = useMemo(
    () => notebooks.find((notebook) => notebook.name === selectedNotebook)?.items || [],
    [notebooks, selectedNotebook],
  );
  const sources = useMemo(() => [...new Set(notebookNotes.map((note) => note.source).filter(Boolean))], [notebookNotes]);
  const types = useMemo(() => [...new Set(notebookNotes.map((note) => note.note_type).filter(Boolean))], [notebookNotes]);
  const dates = useMemo(() => [...new Set(notebookNotes.map((note) => String(note.updated_at || note.created_at || '').slice(0, 10)).filter(Boolean))].sort().reverse(), [notebookNotes]);
  const visibleNotes = notebookNotes.filter((note) => {
    const keyword = query.trim().toLocaleLowerCase();
    return (!sourceFilter || note.source === sourceFilter)
      && (!typeFilter || note.note_type === typeFilter)
      && (!dateFilter || String(note.updated_at || note.created_at || '').slice(0, 10) === dateFilter)
      && (!keyword || `${note.title} ${note.content}`.toLocaleLowerCase().includes(keyword));
  });

  const addNote = async (event) => {
    event.preventDefault();
    if (!draft.title.trim() || !draft.content.trim()) return;
    setError('');
    try {
      await createNote({
        title: draft.title.trim(),
        content: draft.content.trim(),
        note_type: '心得体会',
        source: '训练工坊',
        context: { notebook: draftNotebook || selectedNotebook || '默认笔记本' },
      });
      setSelectedNotebook(draftNotebook || selectedNotebook || '默认笔记本');
      setDraft(emptyDraft);
      setComposerOpen(false);
      await refresh();
    } catch (reason) {
      setError(reason.message || '笔记保存失败');
    }
  };

  const openNote = (note) => {
    const open = expandedId === note.note_id;
    setExpandedId(open ? '' : note.note_id);
    setEditDraft(open ? emptyDraft : { title: note.title, content: note.content });
  };

  const saveEdit = async (noteId) => {
    if (!editDraft.title.trim() || !editDraft.content.trim()) return;
    setError('');
    try {
      await updateNote(noteId, {
        title: editDraft.title.trim(),
        content: editDraft.content.trim(),
      });
      await refresh();
    } catch (reason) {
      setError(reason.message || '笔记修改失败');
    }
  };

  const removeNote = async (noteId) => {
    setError('');
    try {
      await deleteNote(noteId);
      setExpandedId('');
      await refresh();
    } catch (reason) {
      setError(reason.message || '笔记删除失败');
    }
  };

  const openComposer = () => {
    setDraftNotebook(selectedNotebook || notebooks[0]?.name || '默认笔记本');
    setComposerOpen(true);
  };

  const leaveNotebook = () => {
    setSelectedNotebook('');
    setExpandedId('');
    setQuery('');
    setSourceFilter('');
    setTypeFilter('');
    setDateFilter('');
  };

  const createNotebook = async (event) => {
    event.preventDefault();
    const name = newNotebookName.trim();
    if (!name) return;
    setError('');
    try {
      const payload = await createNoteFolder(name);
      const folder = payload.folder;
      setNoteFolders((current) => current.some((item) => item.folder_id === folder.folder_id)
        ? current
        : [folder, ...current]);
      setSelectedNotebook(folder.name);
      setNewNotebookName('');
      setNewNotebookOpen(false);
    } catch (reason) {
      setError(reason.message || '笔记本创建失败');
    }
  };

  return <section className="workshop-notes" aria-labelledby="notes-title">
    <header className="workshop-library__header">
      <div><span>个人知识沉淀</span><h2 id="notes-title">{selectedNotebook || '笔记本'}</h2><p>{selectedNotebook ? `${notebookNotes.length} 条笔记` : `${notebooks.length} 个笔记本 · ${notes.length} 条笔记`}</p></div>
      {selectedNotebook && <button type="button" className="workshop-notes__new" onClick={openComposer}><Plus size={16} />新建笔记</button>}
    </header>
    {error && <p role="alert" className="workshop-library__error">{error}</p>}
    {loading ? <p role="status" className="workshop-library__loading"><Loader2 className="animate-spin" size={18} />正在加载笔记…</p> : !selectedNotebook ? <div className="workshop-notes__books" role="region" aria-label="笔记本书架">
      {notebooks.map((notebook, index) => <button key={notebook.name} type="button" className="workshop-notes__book" onClick={() => setSelectedNotebook(notebook.name)}><NotebookPen size={26} aria-hidden="true" /><strong>{notebook.name}</strong><small>{notebook.items.length} 条笔记</small><i aria-hidden="true">{String(index + 1).padStart(2, '0')}</i></button>)}
      <button type="button" className="workshop-notes__book workshop-notes__book--new" onClick={() => setNewNotebookOpen(true)}><Plus size={26} aria-hidden="true" /><strong>新建笔记本</strong></button>
    </div> : <>
      <div className="workshop-library__detail-header">
        <button type="button" onClick={leaveNotebook}><ArrowLeft size={16} />返回笔记本列表</button>
      </div>
      <div className="workshop-notes__filters">
        <label className="workshop-notes__search"><Search size={16} /><input aria-label="搜索笔记" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题或正文" /></label>
        <label>来源<select aria-label="筛选笔记来源" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="">全部</option>{sources.map((source) => <option key={source}>{source}</option>)}</select></label>
        <label>日期<select aria-label="筛选笔记日期" value={dateFilter} onChange={(event) => setDateFilter(event.target.value)}><option value="">全部</option>{dates.map((date) => <option key={date}>{date}</option>)}</select></label>
        <label>类型<select aria-label="筛选笔记类型" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="">全部</option>{types.map((type) => <option key={type}>{type}</option>)}</select></label>
      </div>
      {visibleNotes.length === 0 ? <div className="workshop-library__empty"><NotebookPen size={28} /><h3>暂无符合条件的笔记</h3><p>可以新建笔记，也可以从已批改题目的解析处创建。</p></div> : <div className="workshop-notes__list">
        {visibleNotes.map((note) => {
          const open = expandedId === note.note_id;
          return <article key={note.note_id} className={open ? 'is-open' : ''}>
            <button type="button" className="workshop-library__item-toggle" aria-expanded={open} onClick={() => openNote(note)}><span><strong>{note.title}</strong><small>{note.source} · {note.note_type} · {String(note.updated_at || '').slice(0, 10)}</small></span>{open ? <ChevronUp size={17} /> : <ChevronDown size={17} />}</button>
            {open && <div className="workshop-notes__editor"><QuestionContext context={note.context} /><label>标题<input value={editDraft.title} onChange={(event) => setEditDraft({ ...editDraft, title: event.target.value })} /></label><label>内容<textarea rows={5} value={editDraft.content} onChange={(event) => setEditDraft({ ...editDraft, content: event.target.value })} /></label><div><button type="button" onClick={() => saveEdit(note.note_id)}>保存修改</button><button type="button" className="workshop-library__delete" onClick={() => removeNote(note.note_id)}><Trash2 size={14} />删除笔记</button></div></div>}
          </article>;
        })}
      </div>}
    </>}
    {composerOpen && <div className="workshop-save-dialog" role="dialog" aria-modal="true" aria-labelledby="new-note-dialog-title">
      <div>
        <form onSubmit={addNote}>
          <header>
            <h3 id="new-note-dialog-title">新建笔记</h3>
            <button type="button" aria-label="关闭新建笔记窗口" onClick={() => setComposerOpen(false)}><X size={18} /></button>
          </header>
          <label>选择笔记本<select value={draftNotebook} onChange={(event) => setDraftNotebook(event.target.value)}>{notebooks.map((notebook) => <option key={notebook.name} value={notebook.name}>{notebook.name}</option>)}</select></label>
          <label>标题<input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} maxLength={200} placeholder="今天学到了什么？" /></label>
          <label>内容<textarea value={draft.content} onChange={(event) => setDraft({ ...draft, content: event.target.value })} maxLength={20000} rows={6} placeholder="写下理解、辨析要点或复习提醒…" /></label>
          <footer>
            <button type="button" onClick={() => setComposerOpen(false)}>取消</button>
            <button type="submit" disabled={!draft.title.trim() || !draft.content.trim()}>保存笔记</button>
          </footer>
        </form>
      </div>
    </div>}
    {newNotebookOpen && <div className="workshop-save-dialog" role="dialog" aria-modal="true" aria-labelledby="new-notebook-dialog-title">
      <div>
        <form onSubmit={createNotebook}>
          <header><h3 id="new-notebook-dialog-title">新建笔记本</h3><button type="button" aria-label="关闭新建笔记本窗口" onClick={() => setNewNotebookOpen(false)}><X size={18} /></button></header>
          <label>笔记本名称<input value={newNotebookName} onChange={(event) => setNewNotebookName(event.target.value)} maxLength={80} placeholder="例如：伤寒论" /></label>
          <footer><button type="button" onClick={() => setNewNotebookOpen(false)}>取消</button><button type="submit" disabled={!newNotebookName.trim()}>创建并进入</button></footer>
        </form>
      </div>
    </div>}
  </section>;
}
