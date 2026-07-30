import React, { useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ArrowUpRight,
  Bold,
  Code2,
  Eye,
  FilePlus2,
  Heading2,
  ImagePlus,
  Italic,
  Link2,
  List,
  ListOrdered,
  Loader2,
  NotebookPen,
  PanelLeftClose,
  PencilLine,
  Plus,
  Quote,
  Save,
  Search,
  Trash2,
  X,
} from 'lucide-react';
import {
  createNote,
  createNoteFolder,
  deleteNote,
  loadNoteFolders,
  loadNotes,
  updateNote,
  uploadNoteImage,
} from './workshopLibraryApi';

const emptyDraft = { title: '', content: '' };

function NoteSourceContext({ context, onOpenSource }) {
  if (context?.book_id && context?.pdf_page) {
    return (
      <aside className="notion-note__question-context notion-note__source-context">
        <strong>教材页来源</strong>
        <p>《{context.book_title || '教材'}》第 {context.pdf_page} 页</p>
        {onOpenSource && <button type="button" onClick={() => onOpenSource(context)}>返回原页 <ArrowUpRight size={14} /></button>}
      </aside>
    );
  }
  if (!context?.question_content) return null;
  const answer = Array.isArray(context.standard_answer)
    ? context.standard_answer.join('、')
    : String(context.standard_answer || '');
  const myAnswer = String(context.my_answer || '');
  const options = Array.isArray(context.options) ? context.options : [];
  return (
    <aside className="notion-note__question-context">
      <strong>关联题目</strong>
      <p>{context.question_content}</p>
      {options.length > 0 && (
        <div style={{marginTop:8}}>
          {options.map((opt, i) => {
            const label = String.fromCharCode(65 + i);
            const text = typeof opt === 'string' ? opt : (opt.label || opt.content || opt.value || '');
            const val = String(opt.value || opt.label || text);
            const isMy = myAnswer.includes(val);
            const isCorrect = answer.includes(val);
            let bg = 'transparent';
            if (isMy && isCorrect) bg = '#dcfce7';
            else if (isMy && !isCorrect) bg = '#fee2e2';
            else if (!isMy && isCorrect) bg = '#dcfce7';
            return <div key={i} style={{background:bg,borderRadius:4,padding:'3px 8px',margin:'3px 0',fontSize:'.85rem'}}>{label}. {String(text).replace(/^[A-Z][.．、)\s]\s*/, '')}{isMy&&<span style={{color:'#ef4444',fontSize:'.75rem',marginLeft:8}}>我的作答</span>}{isCorrect&&!isMy&&<span style={{color:'#16a34a',fontSize:'.75rem',marginLeft:8}}>正确答案</span>}</div>;
          })}
        </div>
      )}
      {answer && <p style={{marginTop:8}}><b>参考答案：</b>{answer}</p>}
      {context.explanation && <p style={{marginTop:4}}><b>解析：</b>{context.explanation}</p>}
    </aside>
  );
}

function MarkdownDocument({ children }) {
  return (
    <div className="notion-note__markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children || '*还没有正文。*'}</ReactMarkdown>
    </div>
  );
}

const tools = [
  { label: '二级标题', icon: Heading2, before: '## ', after: '' },
  { label: '粗体', icon: Bold, before: '**', after: '**' },
  { label: '斜体', icon: Italic, before: '*', after: '*' },
  { label: '无序列表', icon: List, before: '- ', after: '' },
  { label: '有序列表', icon: ListOrdered, before: '1. ', after: '' },
  { label: '引用', icon: Quote, before: '> ', after: '' },
  { label: '行内代码', icon: Code2, before: '`', after: '`' },
  { label: '链接', icon: Link2, before: '[', after: '](https://)' },
];

export default function StudyNotesPanel({
  bookContext = null,
  initialPage = null,
  autoCreatePageNote = false,
  onOpenSource = null,
  onNavigate = null,
  compact = false,
}) {
  const [notes, setNotes] = useState([]);
  const [folders, setFolders] = useState([]);
  const [selectedNotebook, setSelectedNotebook] = useState('');
  const [activeNoteId, setActiveNoteId] = useState('');
  const [draft, setDraft] = useState(emptyDraft);
  const [isNew, setIsNew] = useState(false);
  const [mode, setMode] = useState('edit');
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [imageUploading, setImageUploading] = useState(false);
  const [error, setError] = useState('');
  const [newNotebookOpen, setNewNotebookOpen] = useState(false);
  const [newNotebookName, setNewNotebookName] = useState('');
  const [confirmDeleteNote, setConfirmDeleteNote] = useState(false);
  const editorRef = useRef(null);
  const imageInputRef = useRef(null);
  const autoCreatedRef = useRef(new Set());

  const refresh = async ({ keepActive = true } = {}) => {
    setLoading(true);
    setError('');
    try {
      const [notesPayload, folderPayload] = await Promise.all([loadNotes(), loadNoteFolders()]);
      const allNotes = notesPayload.items || [];
      const nextNotes = bookContext?.title
        ? allNotes.filter((note) => (
          bookContext.book_id
            ? note.context?.book_id === bookContext.book_id
            : note.context?.book_title === bookContext.title
        ))
        : allNotes;
      const nextFolders = bookContext?.title
        ? (folderPayload.items || []).filter((folder) => folder.name === `${bookContext.title}阅读笔记`)
        : folderPayload.items || [];
      setNotes(nextNotes);
      setFolders(nextFolders);
      if (!selectedNotebook) setSelectedNotebook(
        bookContext?.title ? `${bookContext.title}阅读笔记` : nextFolders[0]?.name || '默认笔记本',
      );
      if (keepActive && activeNoteId) {
        const next = nextNotes.find((note) => note.note_id === activeNoteId);
        if (next) setDraft({ title: next.title, content: next.content });
      }
    } catch (reason) {
      setError(reason.message || '笔记加载失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh({ keepActive: false }); }, []);

  useEffect(() => {
    if (!autoCreatePageNote || loading || !bookContext?.book_id || !initialPage) return;
    const resourceId = `${bookContext.book_id}:page:${initialPage}`;
    if (autoCreatedRef.current.has(resourceId)) return;
    autoCreatedRef.current.add(resourceId);
    const existing = notes.find((note) => note.resource_type === 'textbook_pdf_page' && note.resource_id === resourceId);
    if (existing) {
      setSelectedNotebook(existing.context?.notebook || `${bookContext.title}阅读笔记`);
      openNote(existing);
      return;
    }
    const notebook = `${bookContext.title}阅读笔记`;
    createNote({
      title: `《${bookContext.title}》第 ${initialPage} 页笔记`,
      content: `> 在《${bookContext.title}》第 ${initialPage} 页做笔记。\n\n`,
      note_type: '教材页笔记',
      source: '教学资源',
      resource_type: 'textbook_pdf_page',
      resource_id: resourceId,
      context: {
        notebook,
        book_id: bookContext.book_id,
        book_title: bookContext.title,
        edition: bookContext.edition,
        route: bookContext.route,
        pdf_page: initialPage,
      },
    }).then((payload) => {
      setSelectedNotebook(notebook);
      setNotes((current) => [payload.note, ...current]);
      openNote(payload.note);
    }).catch((reason) => setError(reason.message || '教材页笔记创建失败'));
  }, [autoCreatePageNote, bookContext, initialPage, loading, notes]);

  const notebooks = useMemo(() => {
    const map = new Map(folders.map((folder) => [folder.name, { ...folder, items: [] }]));
    notes.forEach((note) => {
      const name = String(note.context?.notebook || '默认笔记本').trim() || '默认笔记本';
      if (!map.has(name)) map.set(name, { folder_id: name, name, items: [] });
      map.get(name).items.push(note);
    });
    if (bookContext?.title && map.size === 0) {
      const name = `${bookContext.title}阅读笔记`;
      map.set(name, { folder_id: name, name, items: [] });
    } else if (map.size === 0) {
      map.set('默认笔记本', { folder_id: 'default-notebook', name: '默认笔记本', items: [] });
    }
    return [...map.values()];
  }, [bookContext?.title, folders, notes]);

  const notebookNotes = useMemo(() => notes.filter(
    (note) => String(note.context?.notebook || '默认笔记本') === selectedNotebook,
  ), [notes, selectedNotebook]);
  const visibleNotes = notebookNotes.filter((note) => {
    const keyword = query.trim().toLocaleLowerCase();
    return !keyword || `${note.title} ${note.content}`.toLocaleLowerCase().includes(keyword);
  });
  const activeNote = notes.find((note) => note.note_id === activeNoteId);

  const selectNotebook = (name) => {
    setSelectedNotebook(name);
    setActiveNoteId('');
    setDraft(emptyDraft);
    setIsNew(false);
  };

  const openNote = (note) => {
    setActiveNoteId(note.note_id);
    setDraft({ title: note.title, content: note.content });
    setIsNew(false);
    setMode('edit');
  };

  const newNote = () => {
    setActiveNoteId('');
    setDraft({ title: '无标题', content: '' });
    setIsNew(true);
    setMode('edit');
    requestAnimationFrame(() => editorRef.current?.focus());
  };

  const save = async () => {
    if (!draft.title.trim() || !draft.content.trim() || !selectedNotebook) return;
    setSaving(true);
    setError('');
    try {
      if (isNew) {
        const payload = await createNote({
          title: draft.title.trim(),
          content: draft.content,
          note_type: bookContext ? '教材页笔记' : '笔记本',
          source: bookContext ? '教学资源' : '训练工坊',
          resource_type: bookContext && initialPage ? 'textbook_pdf_page' : null,
          resource_id: bookContext && initialPage ? `${bookContext.book_id}:page:${initialPage}` : null,
          context: bookContext ? {
            notebook: selectedNotebook,
            book_id: bookContext.book_id,
            book_title: bookContext.title,
            edition: bookContext.edition,
            route: bookContext.route,
            pdf_page: initialPage,
          } : { notebook: selectedNotebook },
        });
        setActiveNoteId(payload.note.note_id);
        setIsNew(false);
      } else if (activeNoteId) {
        await updateNote(activeNoteId, { title: draft.title.trim(), content: draft.content });
      }
      await refresh();
    } catch (reason) {
      setError(reason.message || '笔记保存失败');
    } finally {
      setSaving(false);
    }
  };

  const remove = async () => {
    if (!activeNoteId) return;
    setError('');
    setConfirmDeleteNote(false);
    try {
      await deleteNote(activeNoteId);
      setActiveNoteId('');
      setDraft(emptyDraft);
      await refresh({ keepActive: false });
    } catch (reason) {
      setError(reason.message || '笔记删除失败');
    }
  };

  const insertMarkdown = (before, after) => {
    const editor = editorRef.current;
    if (!editor) {
      setDraft((current) => ({ ...current, content: `${current.content}${before}${after}` }));
      return;
    }
    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const selected = draft.content.slice(start, end);
    const next = `${draft.content.slice(0, start)}${before}${selected}${after}${draft.content.slice(end)}`;
    setDraft({ ...draft, content: next });
    requestAnimationFrame(() => {
      editor.focus();
      editor.setSelectionRange(start + before.length, start + before.length + selected.length);
    });
  };

  const uploadImage = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    setImageUploading(true);
    setError('');
    try {
      const payload = await uploadNoteImage(file);
      const alt = file.name.replace(/\.[^.]+$/, '') || '笔记图片';
      insertMarkdown(`\n![${alt}](${payload.url})\n`, '');
    } catch (reason) {
      setError(reason.message || '图片上传失败');
    } finally {
      setImageUploading(false);
    }
  };

  const createNotebook = async (event) => {
    event.preventDefault();
    if (!newNotebookName.trim()) return;
    try {
      const payload = await createNoteFolder(newNotebookName.trim());
      setFolders((current) => [payload.folder, ...current.filter((item) => item.folder_id !== payload.folder.folder_id)]);
      setSelectedNotebook(payload.folder.name);
      setActiveNoteId('');
      setNewNotebookName('');
      setNewNotebookOpen(false);
    } catch (reason) {
      setError(reason.message || '笔记本创建失败');
    }
  };

  const openSource = (context) => {
    if (onOpenSource) { onOpenSource(context); return; }
    onNavigate?.({
      page: 'practice',
      params: {
        view: 'textbook-chapters',
        lv1: context.book_title,
        openPdf: true,
        pdfPage: context.pdf_page,
      },
    });
  };

  return (
    <section className={`notion-notes ${compact ? 'is-compact' : ''}`} aria-labelledby="notes-title">
      <aside className="notion-notes__sidebar">
        <header><NotebookPen size={19} /><strong id="notes-title">笔记本</strong><button type="button" aria-label="新建笔记本" onClick={() => setNewNotebookOpen(true)}><Plus size={16} /></button></header>
        <div className="notion-notes__notebooks">
          {notebooks.map((notebook) => (
            <button key={notebook.name} type="button" className={selectedNotebook === notebook.name ? 'is-active' : ''} onClick={() => selectNotebook(notebook.name)}>
              <PanelLeftClose size={14} /><span>{notebook.name}</span><small>{notebook.items.length}</small>
            </button>
          ))}
        </div>
        <div className="notion-notes__note-tools">
          <label><Search size={15} /><input aria-label="搜索笔记" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索笔记" /></label>
          <button type="button" onClick={newNote} disabled={!selectedNotebook}><FilePlus2 size={15} />新建笔记</button>
        </div>
        <nav className="notion-notes__note-list" aria-label="笔记列表">
          {visibleNotes.map((note) => (
            <button key={note.note_id} type="button" className={activeNoteId === note.note_id ? 'is-active' : ''} onClick={() => openNote(note)}>
              <strong>{note.title}</strong><small>{String(note.updated_at || note.created_at || '').slice(0, 10)}</small>
            </button>
          ))}
          {!loading && selectedNotebook && !visibleNotes.length && <p>这个笔记本还没有内容。</p>}
        </nav>
      </aside>

      <main className="notion-note">
        {loading ? <p role="status" className="workshop-library__loading"><Loader2 className="animate-spin" size={18} />正在加载笔记…</p> : (!activeNote && !isNew) ? (
          <div className="notion-note__empty"><NotebookPen size={34} /><h2>{selectedNotebook || '笔记本'}</h2><p>从左侧选择一篇笔记，或创建新页面。</p><button type="button" onClick={newNote} disabled={!selectedNotebook}><Plus size={16} />新建页面</button></div>
        ) : (
          <>
            <header className="notion-note__topbar">
              <div className="notion-note__mode">
                <button type="button" className={mode === 'edit' ? 'is-active' : ''} onClick={() => setMode('edit')}><PencilLine size={15} />编辑</button>
                <button type="button" className={mode === 'preview' ? 'is-active' : ''} onClick={() => setMode('preview')}><Eye size={15} />预览</button>
              </div>
              <div><button type="button" onClick={() => setConfirmDeleteNote(true)} disabled={isNew || !activeNoteId} className="is-danger"><Trash2 size={15} />删除</button><button type="button" onClick={save} disabled={saving || !draft.title.trim() || !draft.content.trim()}><Save size={15} />{saving ? '保存中…' : '保存'}</button></div>
            </header>
            <div className="notion-note__document">
              <input className="notion-note__title" aria-label="笔记标题" value={draft.title} onChange={(event) => setDraft({ ...draft, title: event.target.value })} maxLength={200} placeholder="无标题" />
              <NoteSourceContext context={activeNote?.context} onOpenSource={onOpenSource || onNavigate ? openSource : null} />
              {mode === 'edit' ? (
                <>
                  <div className="notion-note__toolbar" aria-label="Markdown 工具栏">
                    {tools.map((tool) => {
                      const Icon = tool.icon;
                      return <button key={tool.label} type="button" title={tool.label} aria-label={tool.label} onClick={() => insertMarkdown(tool.before, tool.after)}><Icon size={16} /></button>;
                    })}
                    <button type="button" title="上传图片" aria-label="上传图片" disabled={imageUploading} onClick={() => imageInputRef.current?.click()}>{imageUploading ? <Loader2 size={16} className="animate-spin" /> : <ImagePlus size={16} />}</button>
                    <input ref={imageInputRef} hidden type="file" accept="image/jpeg,image/png,image/webp,image/gif" onChange={uploadImage} />
                    <span>支持 Markdown 与图片</span>
                  </div>
                  <textarea
                    ref={editorRef}
                    aria-label="笔记内容"
                    value={draft.content}
                    onChange={(event) => setDraft({ ...draft, content: event.target.value })}
                    maxLength={20000}
                    placeholder="输入 / 开始记录，或使用上方 Markdown 工具……"
                  />
                </>
              ) : <MarkdownDocument>{draft.content}</MarkdownDocument>}
            </div>
          </>
        )}
        {error && <p role="alert" className="notion-note__error">{error}</p>}
      </main>

      {newNotebookOpen && <div className="workshop-save-dialog" role="dialog" aria-modal="true" aria-labelledby="new-notebook-dialog-title"><div><form onSubmit={createNotebook}><header><h3 id="new-notebook-dialog-title">新建笔记本</h3><button type="button" aria-label="关闭新建笔记本窗口" onClick={() => setNewNotebookOpen(false)}><X size={18} /></button></header><label>笔记本名称<input value={newNotebookName} onChange={(event) => setNewNotebookName(event.target.value)} maxLength={80} placeholder="例如：伤寒论" /></label><footer><button type="button" onClick={() => setNewNotebookOpen(false)}>取消</button><button type="submit" disabled={!newNotebookName.trim()}>创建并进入</button></footer></form></div></div>}
      {confirmDeleteNote && <div className="workshop-save-dialog" role="dialog" aria-modal="true"><div><header><h3>确认删除</h3><button type="button" onClick={() => setConfirmDeleteNote(false)}><X size={18} /></button></header><p className="py-3 text-base text-slate-900">确定要删除这篇笔记吗？删除后不可恢复。</p><footer><button type="button" onClick={() => setConfirmDeleteNote(false)} style={{background:'#d1fae5',color:'#059669',border:'none',borderRadius:8,padding:'8px 18px',fontWeight:600,cursor:'pointer'}}>取消</button><button type="button" onClick={remove} style={{background:'#fee2e2',color:'#dc2626',border:'none',borderRadius:8,padding:'8px 18px',fontWeight:600,cursor:'pointer'}}>确认删除</button></footer></div></div>}
    </section>
  );
}
