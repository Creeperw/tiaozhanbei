import React, { useEffect, useState } from 'react';
import { Loader2, NotebookPen, Save, X } from 'lucide-react';
import { createNote, loadNotes, updateNote } from '../workshopLibraryApi';

const noteTitle = (book, page) => `《${book.title}》第 ${page} 页笔记`;
const noteStarter = (book, page) => `> 在《${book.title}》第 ${page} 页做笔记。\n\n`;

export default function TextbookPageNotePopover({ book, page, route, onClose }) {
  const [note, setNote] = useState(null);
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let active = true;
    const resourceId = `${book.book_id}:page:${page}`;
    setLoading(true);
    setSaved(false);
    setError('');
    loadNotes().then((payload) => {
      if (!active) return;
      const existing = (payload.items || []).find((item) => (
        item.resource_type === 'textbook_pdf_page' && item.resource_id === resourceId
      ));
      setNote(existing || null);
      setContent(existing?.content || noteStarter(book, page));
    }).catch((reason) => {
      if (active) setError(reason.message || '笔记加载失败');
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [book, page]);

  const save = async () => {
    const value = content.trim();
    if (!value || saving) return;
    setSaving(true);
    setSaved(false);
    setError('');
    try {
      if (note) {
        await updateNote(note.note_id, { title: noteTitle(book, page), content: value });
      } else {
        const payload = await createNote({
          title: noteTitle(book, page),
          content: value,
          note_type: '教材页笔记',
          source: '教学资源',
          resource_type: 'textbook_pdf_page',
          resource_id: `${book.book_id}:page:${page}`,
          context: {
            notebook: `${book.title}阅读笔记`,
            book_id: book.book_id,
            book_title: book.title,
            edition: book.edition,
            route,
            pdf_page: page,
          },
        });
        setNote(payload.note);
      }
      setSaved(true);
    } catch (reason) {
      setError(reason.message || '笔记保存失败');
    } finally {
      setSaving(false);
    }
  };

  return (
    <aside className="textbook-page-note" role="dialog" aria-modal="false" aria-labelledby="textbook-page-note-title">
      <header>
        <span><NotebookPen aria-hidden="true" size={18} /></span>
        <div><strong id="textbook-page-note-title">第 {page} 页笔记</strong><small>《{book.title}》</small></div>
        <button type="button" aria-label="关闭页笔记" onClick={onClose}><X size={17} /></button>
      </header>
      {loading ? (
        <div className="textbook-page-note__loading" role="status"><Loader2 className="is-spinning" size={18} />正在读取笔记</div>
      ) : (
        <>
          <textarea
            aria-label={`《${book.title}》第 ${page} 页笔记内容`}
            value={content}
            onChange={(event) => { setContent(event.target.value); setSaved(false); }}
            placeholder="记录这一页的重点，支持 Markdown…"
            autoFocus
          />
          {error && <p role="alert">{error}</p>}
          <footer>
            <small>{saved ? '已保存' : '仅点击保存后才会写入笔记本'}</small>
            <button type="button" onClick={save} disabled={saving || !content.trim()}>
              {saving ? <Loader2 className="is-spinning" size={15} /> : <Save size={15} />}
              保存
            </button>
          </footer>
        </>
      )}
    </aside>
  );
}
