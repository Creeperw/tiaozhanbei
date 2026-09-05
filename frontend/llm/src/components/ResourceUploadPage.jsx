import React, { useState } from 'react';
import { ArrowLeft, FileText, UploadCloud } from 'lucide-react';
import QuestionWorkspacePage from './QuestionWorkspacePage';
import UserSyllabusPage from './UserSyllabusPage';

export default function ResourceUploadPage({ onBack = null }) {
  const [view, setView] = useState('chooser');

  if (view === 'question') {
    return (
      <div className="question-workspace-shell space-y-5 text-slate-800">
        <div className="practice-workspace__heading question-workspace-shell__toolbar flex items-center gap-4 border-b border-slate-200 pb-4">
          <button type="button" className="practice-workspace__back" onClick={() => setView('chooser')}><ArrowLeft aria-hidden="true" size={16} />返回</button>
          <h1 className="text-2xl font-bold text-slate-950">上传题库</h1>
        </div>
        <QuestionWorkspacePage />
      </div>
    );
  }

  if (view === 'syllabus') {
    return (
      <div className="question-workspace-shell space-y-5 text-slate-800">
        <div className="practice-workspace__heading question-workspace-shell__toolbar flex items-center gap-4 border-b border-slate-200 pb-4">
          <button type="button" className="practice-workspace__back" onClick={() => setView('chooser')}><ArrowLeft aria-hidden="true" size={16} />返回</button>
          <h1 className="text-2xl font-bold text-slate-950">上传考纲</h1>
        </div>
        <UserSyllabusPage />
      </div>
    );
  }

  return (
    <div className="question-workspace-shell resource-upload-page space-y-5 text-slate-800">
      <div className="practice-workspace__heading question-workspace-shell__toolbar flex items-center gap-4 border-b border-slate-200 pb-4">
        {onBack && <button type="button" className="practice-workspace__back" onClick={onBack}><ArrowLeft aria-hidden="true" size={16} />返回</button>}
        <div>
          <span className="resource-upload-page__eyebrow">个人学习资源</span>
          <h1 className="text-2xl font-bold text-slate-950">上传资源</h1>
        </div>
      </div>
      <section className="question-workspace__section" aria-label="选择上传资源类型">
        <header><div><span>资源入口</span><h3>选择上传类型</h3></div></header>
        <div className="grid gap-3 md:grid-cols-2">
          <button type="button" className="practice-overview__utility-card" onClick={() => setView('question')}><UploadCloud size={22} /><span><strong>上传题库</strong><small>解析个人题目并进入题库审核流程。</small></span></button>
          <button type="button" className="practice-overview__utility-card" onClick={() => setView('syllabus')}><FileText size={22} /><span><strong>上传考纲</strong><small>用多模态模型结构化考纲并绑定当前激活考纲。</small></span></button>
        </div>
      </section>
    </div>
  );
}
