import React, { useEffect, useMemo, useState } from 'react';
import { ChevronDown, ChevronUp, Loader2, NotebookPen, Plus, Search, Trash2 } from 'lucide-react';
import { createNote, deleteNote, loadNotes, updateNote } from './workshopLibraryApi';

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
  const [query, setQuery] = useState('');
  const [sourceFilter, setSourceFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [expandedId, setExpandedId] = useState('');
  const [editDraft, setEditDraft] = useState(emptyDraft);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = async () => {
    setLoading(true);
    setError('');
    try {
      const payload = await loadNotes();
      setNotes(payload.items || []);
    } catch (reason) {
      setError(reason.message || '笔记加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  const sources = useMemo(() => [...new Set(notes.map((note) => note.source).filter(Boolean))], [notes]);
  const types = useMemo(() => [...new Set(notes.map((note) => note.note_type).filter(Boolean))], [notes]);
  const visibleNotes = notes.filter((note) => {
    const keyword = query.trim().toLocaleLowerCase();
    return (!sourceFilter || note.source === sourceFilter)
      && (!typeFilter || note.note_type === typeFilter)
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
        context: {},
      });
      setDraft(emptyDraft);
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

  return <section className="workshop-notes" aria-labelledby="notes-title">
    <header className="workshop-library__header">
      <div><span>个人知识沉淀</span><h2 id="notes-title">学习笔记</h2><p>记录学习心得，也统一查看从试卷解析生成的题目笔记。</p></div>
    </header>
    <form className="workshop-notes__composer" onSubmit={addNote}>
      <label>标题<input value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} maxLength={200} placeholder="今天学到了什么？" /></label>
      <label>内容<textarea value={draft.content} onChange={(event) => setDraft({ ...draft, content: event.target.value })} maxLength={20000} rows={4} placeholder="写下理解、辨析要点或复习提醒…" /></label>
      <button type="submit" disabled={!draft.title.trim() || !draft.content.trim()}><Plus size={16} />保存笔记</button>
    </form>
    <div className="workshop-notes__filters">
      <label className="workshop-notes__search"><Search size={16} /><input aria-label="搜索笔记" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索标题或正文" /></label>
      <label>来源<select aria-label="筛选笔记来源" value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}><option value="">全部</option>{sources.map((source) => <option key={source}>{source}</option>)}</select></label>
      <label>类型<select aria-label="筛选笔记类型" value={typeFilter} onChange={(event) => setTypeFilter(event.target.value)}><option value="">全部</option>{types.map((type) => <option key={type}>{type}</option>)}</select></label>
    </div>
    {error && <p role="alert" className="workshop-library__error">{error}</p>}
    {loading ? <p role="status" className="workshop-library__loading"><Loader2 className="animate-spin" size={18} />正在加载笔记…</p> : visibleNotes.length === 0 ? <div className="workshop-library__empty"><NotebookPen size={28} /><h3>暂无符合条件的笔记</h3><p>可以在上方直接记录，也可以从已批改题目的解析处创建。</p></div> : <div className="workshop-notes__list">
      {visibleNotes.map((note) => {
        const open = expandedId === note.note_id;
        return <article key={note.note_id}>
          <button type="button" className="workshop-library__item-toggle" aria-expanded={open} onClick={() => openNote(note)}><span><strong>{note.title}</strong><small>{note.source} · {note.note_type} · {String(note.updated_at || '').slice(0, 10)}</small></span>{open ? <ChevronUp size={17} /> : <ChevronDown size={17} />}</button>
          {open && <div className="workshop-notes__editor"><QuestionContext context={note.context} /><label>标题<input value={editDraft.title} onChange={(event) => setEditDraft({ ...editDraft, title: event.target.value })} /></label><label>内容<textarea rows={5} value={editDraft.content} onChange={(event) => setEditDraft({ ...editDraft, content: event.target.value })} /></label><div><button type="button" onClick={() => saveEdit(note.note_id)}>保存修改</button><button type="button" className="workshop-library__delete" onClick={() => removeNote(note.note_id)}><Trash2 size={14} />删除笔记</button></div></div>}
        </article>;
      })}
    </div>}
  </section>;
}
