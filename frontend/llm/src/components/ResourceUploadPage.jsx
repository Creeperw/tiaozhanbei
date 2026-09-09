import React, { useCallback, useEffect, useState } from 'react';
import { ArrowLeft, BookOpen, Database, FileText, UploadCloud } from 'lucide-react';
import QuestionWorkspacePage from './QuestionWorkspacePage';
import UserSyllabusPage from './UserSyllabusPage';
import { TextbookUploadDialog } from './workshop-textbook/TextbookLibrary';
import { invalidateTextbookPdfCatalogCache } from './workshop-textbook/textbookPdfApi';
import { clearTeachingResourcesPageCache } from './teachingResourcesPageCache';
import KnowledgeUploadForm from './resource-upload/KnowledgeUploadForm';
import TextbookUploadHistory from './resource-upload/TextbookUploadHistory';
import { UPLOAD_TYPES } from './resource-upload/uploadNavigation';
import './resource-upload/resourceUpload.css';

const TYPES = [
  { id: 'textbook', title: '教材阅读', icon: BookOpen, description: '上传教材 PDF，生成目录并进入章节阅读。', note: '适合按整本教材学习。可选匹配现有知识库；不等同于个人知识资料入库。' },
  { id: 'knowledge', title: '个人知识资料', icon: Database, description: '将个人资料加入知识库，用于检索与讲解。', note: '仅当前用户可见。不会直接生成可练习的个人题目，也不会修改公共知识库。' },
  { id: 'question', title: '个人题库', icon: UploadCloud, description: '解析个人题目，预览、修订后手动确认。', note: '解析完成不等于已激活；只有你确认的题目才进入个人练习范围。' },
  { id: 'syllabus', title: '考纲', icon: FileText, description: '解析考试范围，作为学习与练题约束。', note: '注意：上传解析成功后会自动设为当前考纲，替换当前激活考纲，并影响后续练题与讲解。' },
];

export default function ResourceUploadPage({ onBack = null, initialType, onNavigate }) {
  const [view, setView] = useState(UPLOAD_TYPES.includes(initialType) ? initialType : '');
  const [busy, setBusy] = useState(false);
  const [historyRevision, setHistoryRevision] = useState(0);
  const [book, setBook] = useState(null);
  useEffect(() => {
    if (!busy) setView(UPLOAD_TYPES.includes(initialType) ? initialType : '');
    // Explicit navigation selects the type; completing work must not reset the form.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialType]);
  useEffect(() => {
    if (!busy) return undefined;
    const warn = event => { event.preventDefault(); event.returnValue = ''; };
    window.addEventListener('beforeunload', warn);
    return () => window.removeEventListener('beforeunload', warn);
  }, [busy]);
  const uploaded = useCallback(item => {
    invalidateTextbookPdfCatalogCache();
    clearTeachingResourcesPageCache();
    setBook(item);
    setHistoryRevision(value => value + 1);
  }, []);
  const openBook = item => onNavigate?.({ page: 'practice', params: {
    view: 'textbook-chapters', route: 'user_textbooks', lv1: item.title,
    bookId: item.book_id, uploaded: true, source: 'textbook-library',
  } });
  const selection = TYPES.find(type => type.id === view);
  return (
    <div className="resource-upload-page question-workspace-shell space-y-5 text-slate-800">
      <header className="resource-upload-heading">
        {onBack && <button type="button" disabled={busy} onClick={onBack}><ArrowLeft size={16} />返回</button>}
        <div><span>个人学习资源</span><h1>上传资源</h1><p>先选用途，再选文件。同一份 PDF 可以有不同用途，由你明确选择。</p></div>
      </header>
      <section className="resource-upload-types" aria-label="选择上传资源类型">
        {TYPES.map(({ id, title, icon, description }) => (
          <button type="button" key={id} aria-pressed={view === id} disabled={busy}
            onClick={() => setView(id)} className={view === id ? 'is-selected' : ''}>
            {React.createElement(icon, { size: 23, 'aria-hidden': true })}<strong>{title}</strong><small>{description}</small>
          </button>
        ))}
      </section>
      {busy && <p role="status" className="resource-upload-notice">正在处理，请勿切换页面或刷新。教材任务可稍后从上传记录查询；其他上传离开页面后结果需到对应资料页核实。</p>}
      {selection ? <>
        <aside className={`resource-upload-notice${view === 'syllabus' ? ' is-warning' : ''}`}>{selection.note}</aside>
        {view === 'textbook' && <>
          <TextbookUploadDialog embedded onUploaded={uploaded} onBusyChange={setBusy} />
          {book && <div className="resource-upload-result" role="status"><strong>教材处理完成：{book.title}</strong><button type="button" disabled={busy} onClick={() => openBook(book)}>阅读教材</button></div>}
          <TextbookUploadHistory revision={historyRevision} busy={busy} onOpen={openBook} />
        </>}
        {view === 'knowledge' && <KnowledgeUploadForm onBusyChange={setBusy} onNavigate={onNavigate} />}
        {view === 'question' && <><QuestionWorkspacePage onBusyChange={setBusy} /><button type="button" disabled={busy} onClick={() => onNavigate?.({ page: 'knowledge', params: { view: 'questions' } })}>查看个人题库</button></>}
        {view === 'syllabus' && <UserSyllabusPage onBusyChange={setBusy} />}
      </> : <p className="resource-upload-notice">请选择上方一种资源用途。公共资料与管理员题库审核仍从原管理入口操作。</p>}
    </div>
  );
}
