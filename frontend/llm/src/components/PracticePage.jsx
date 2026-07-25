import React, { useEffect, useState } from 'react';
import {
  ArrowLeft,
  ArrowUpRight,
  BookMarked, Plus,
  ClipboardCheck,
  FileText,
  Files,
  FolderHeart,
  HeartPulse,
  Lightbulb,
  NotebookPen,
  Sparkles,
  Target,
  UploadCloud,
} from 'lucide-react';
import { createLearningFocusTracker } from '../learningFocusTracker.js';
import { fetchJsonWithAuthFallback } from '../utils/api';
import QuestionTrainingPanel from './QuestionTrainingPanel';
import QualificationPaperPanel from './QualificationPaperPanel';
import SimulatedPatientChat from './SimulatedPatientChat';
import MistakeVariationPanel from './MistakeVariationPanel';
import PaperGenerationPanel from './PaperGenerationPanel';
import SmartPaperPanel from './SmartPaperPanel';
import QuestionWorkspacePage from './QuestionWorkspacePage';
import KnowledgeCardLibrary from './KnowledgeCardLibrary';
import { isTrainingTaskResultApproved } from '../pageDataLoaders.js';
import { practiceContextFromIntent } from './exam-atlas/examAtlasPageContext';

const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

function displayValue(value, depth = 0) {
  if (value === null || value === undefined || value === '') return '暂无';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (depth >= 2) return '已省略嵌套内容';
  if (Array.isArray(value)) {
    if (value.length === 0) return '暂无';
    const visibleItems = value.slice(0, 5).map((item) => displayValue(item, depth + 1));
    return `${visibleItems.join('；')}${value.length > 5 ? '；等' : ''}`;
  }
  if (isRecord(value)) {
    const entries = Object.entries(value);
    if (entries.length === 0) return '暂无';
    const visibleEntries = entries.slice(0, 5)
      .map(([key, item]) => `${key}：${displayValue(item, depth + 1)}`);
    return `${visibleEntries.join('；')}${entries.length > 5 ? '；等' : ''}`;
  }
  return '暂无';
}

function contentSections(content) {
  if (!isRecord(content)) return [];
  const sections = Array.isArray(content.sections) ? content.sections : content.cards;
  if (!Array.isArray(sections)) return [];
  return sections.slice(0, 8).map((section, index) => ({
    key: section?.id || section?.key || `${index}-${section?.title || 'section'}`,
    title: typeof section?.title === 'string' ? section.title : `内容 ${index + 1}`,
    body: displayValue(section?.body ?? section?.content ?? section?.full ?? section),
  }));
}

function EmptyState({ children }) {
  return <p className="[overflow-wrap:anywhere] py-5 text-sm leading-6 text-slate-500">{children}</p>;
}

function KnowledgeCardContent({ content }) {
  const front = content.front;
  const back = content.back;
  const memoryAnchor = content.memory_anchor;
  const hasCardFields = front !== undefined || back !== undefined || memoryAnchor !== undefined;

  if (!hasCardFields) return null;
  return (
    <div className="space-y-4">
      {front !== undefined && <div><h4 className="text-sm font-semibold text-slate-900">正面</h4><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(front)}</p></div>}
      {back !== undefined && <div><h4 className="text-sm font-semibold text-slate-900">背面</h4><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(back)}</p></div>}
      {memoryAnchor !== undefined && <div className="border-l-2 border-emerald-300 pl-4"><h4 className="text-sm font-semibold text-slate-900">记忆锚点</h4><p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(memoryAnchor)}</p></div>}
    </div>
  );
}

function ArtifactResult({ taskResult }) {
  const artifact = isRecord(taskResult?.artifact) ? taskResult.artifact : {};
  const content = artifact.content;
  const artifactType = artifact.artifact_type;

  if (!artifactType) {
    return <EmptyState>提交任务后，训练产物会在这里显示。</EmptyState>;
  }

  if (!isRecord(content)) {
    return (
      <div className="space-y-3">
        <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
        <p className="mt-2 break-words whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(content)}</p>
      </div>
    );
  }

  if (artifactType === 'grading_result') {
    const grading = isRecord(content.grading) ? content.grading : {};
    const remediation = isRecord(content.remediation) ? content.remediation : {};
    const reviewCard = isRecord(remediation.review_card) ? remediation.review_card : {};
    const variants = Array.isArray(remediation.variant_questions) ? remediation.variant_questions.slice(0, 5) : [];
    const hasContent = Object.keys(grading).length > 0 || Object.keys(reviewCard).length > 0 || variants.length > 0;

    if (!hasContent) return <EmptyState>批改已返回，但暂未提供可展示的详细产物。</EmptyState>;

    return (
      <div className="space-y-5">
        <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-slate-200 pb-4 text-sm text-slate-700">
          <span className="font-semibold text-slate-950">得分：{displayValue(grading.score)} / 100</span>
          <span>{grading.is_correct ? '判定：回答正确' : '判定：需要复盘'}</span>
          {grading.error_type && <span>错因：{displayValue(grading.error_type)}</span>}
        </div>
        <div>
          <h4 className="text-sm font-semibold text-slate-900">分析</h4>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(grading.analysis)}</p>
        </div>
        {grading.question_explanation && (
          <div className="border-l-2 border-sky-300 pl-4">
            <h4 className="text-sm font-semibold text-slate-900">题目解析</h4>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(grading.question_explanation)}</p>
            <p className="mt-2 text-xs text-slate-500">
              解析来源：{grading.explanation_source === 'generated_on_first_attempt' ? '首次作答自动生成并保存' : '题目解析库'}
            </p>
          </div>
        )}
        {(reviewCard.title || reviewCard.content) && (
          <div className="border-l-2 border-emerald-300 pl-4">
            <h4 className="text-sm font-semibold text-slate-900">{displayValue(reviewCard.title)}</h4>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(reviewCard.content)}</p>
          </div>
        )}
        {variants.length > 0 && (
          <div>
            <h4 className="text-sm font-semibold text-slate-900">变式练习</h4>
            <ol className="mt-2 space-y-2 text-sm leading-6 text-slate-700">
              {variants.map((item, index) => <li key={item?.key || index}>{index + 1}. {displayValue(item?.stem ?? item)}</li>)}
            </ol>
          </div>
        )}
      </div>
    );
  }

  if (artifactType === 'handout' || artifactType === 'knowledge_card') {
    const sections = contentSections(content);
    const fallback = content.body ?? content.content ?? content.full ?? content.summary;
    const knowledgeCard = artifactType === 'knowledge_card' ? <KnowledgeCardContent content={content} /> : null;
    return (
      <div className="space-y-5">
        <div>
          <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
          {taskResult?.summary && <p className="mt-2 text-sm leading-6 text-slate-600">{displayValue(taskResult.summary)}</p>}
        </div>
        {knowledgeCard || (sections.length > 0 ? sections.map((section) => (
          <div key={section.key} className="border-l-2 border-emerald-200 pl-4">
            <h4 className="text-sm font-semibold text-slate-900">{section.title}</h4>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-700">{section.body}</p>
          </div>
        )) : fallback !== undefined ? (
          <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(fallback)}</p>
        ) : <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(content)}</p>)}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h3 className="text-lg font-semibold text-slate-950">{displayValue(artifact.title || taskResult?.title)}</h3>
      <p className="whitespace-pre-wrap text-sm leading-6 text-slate-700">{displayValue(content)}</p>
    </div>
  );
}

const trainingCards = [
  {
    key: 'question_training',
    initialMode: 'objective',
    title: '综合套题',
    description: '覆盖核心知识点，系统巩固基础能力。',
    icon: ClipboardCheck,
    tone: 'emerald',
  },
  {
    key: 'paper_workspace',
    title: '智能组卷',
    description: '按学习目标组卷，灵活安排练习节奏。',
    icon: Files,
    tone: 'green',
  },
  {
    key: 'special_training',
    initialMode: 'case_training',
    title: '专项训练',
    description: '聚焦案例简答，针对题型强化训练。',
    icon: Target,
    tone: 'teal',
  },
  {
    key: 'topic_training',
    initialMode: 'objective',
    title: '专题训练',
    description: '围绕专题集中练习，突破理解难点。',
    icon: Lightbulb,
    tone: 'cyan',
  },
  {
    key: 'ai_patient_simulation',
    title: '模拟病患',
    description: '置身临床情境，训练辨证与问诊思路。',
    icon: HeartPulse,
    tone: 'rose',
  },
  {
    key: 'question_workspace',
    title: '上传题库',
    description: '上传学习资料，沉淀个人专属题库。',
    icon: UploadCloud,
    tone: 'amber',
  },
];

const utilityCards = [
  {
    key: 'mistake_variation',
    title: '错题库',
    description: '整理错题记录，生成变式并针对性复盘。',
    icon: FolderHeart,
    available: true,
  },
  {
    key: 'question_favorites',
    title: '收藏夹',
    description: '汇总重点内容，随时回顾复习。',
    icon: BookMarked,
    available: true,
  },
  {
    key: 'study_notes',
    title: '学习笔记',
    description: '沉淀学习心得，形成个人知识脉络。',
    icon: NotebookPen,
    available: true,
  },
];

const workspaceTitles = {
  question_training: '综合套题',
  special_training: '专项训练',
  topic_training: '专题训练',
  ai_patient_simulation: '模拟病患',
  mistake_variation: '错题库',
  paper_workspace: '智能组卷',
  knowledge_cards: '知识卡片',
  question_favorites: '收藏夹',
  study_notes: '学习笔记',
};

const legacyTaskTypes = {
  practice_grading: { taskType: 'question_training', initialMode: 'objective' },
  case_training: { taskType: 'ai_patient_simulation', initialMode: 'ai_patient_simulation' },
  knowledge_cards: { taskType: 'knowledge_cards', initialMode: 'knowledge_cards' },
  knowledge_card_generation: { taskType: 'knowledge_cards', initialMode: 'knowledge_cards' },
  paper_generation: { taskType: 'paper_workspace', initialMode: 'paper_workspace' },
};

const normalizeTaskIntent = (taskType = '') => legacyTaskTypes[taskType] || {
  taskType: taskType || 'question_training',
  initialMode: taskType,
};

function QuestionFavoritesPanel({ onBack }) {
  var _useState = useState(function() { try { return JSON.parse(localStorage.getItem('qp-favorite-questions') || '[]'); } catch(e) { return []; } });
  var favs = _useState[0], setFavs = _useState[1];
  var _useState2 = useState(null), expanded = _useState2[0], setExpanded = _useState2[1];
  var _useState3 = useState(null), selectedBook = _useState3[0], setSelectedBook = _useState3[1];
  var stdAnswer = function(item) { var ans = item.standard_answer; if (Array.isArray(ans)) return ans.join('、'); if (typeof ans === 'string') return ans; return ''; };
  var hasCorrect = function(item) { return stdAnswer(item).length > 0; };
  var removeFav = function(e, questionId) { e.stopPropagation(); var next = favs.filter(function(f) { return f.question_id !== questionId; }); setFavs(next); localStorage.setItem('qp-favorite-questions', JSON.stringify(next)); if (expanded === questionId) setExpanded(null); };
  var books = {}; favs.forEach(function(f) { var b = f.book || '默认'; if (!books[b]) books[b] = []; books[b].push(f); });
  if (selectedBook) {
    var items = books[selectedBook] || [];
    return (
      <div className="flex flex-col h-full">
        <header className="flex items-center gap-4 border-b border-slate-200 px-5 py-4">
          <button type="button" onClick={function() { setSelectedBook(null); }} className="inline-flex items-center gap-2 rounded-lg border-2 border-emerald-600 bg-white px-3 py-2 text-sm font-semibold text-emerald-700 shadow-sm transition hover:bg-emerald-50"><ArrowLeft size={16} />返回收藏簿列表</button>
          <div><h2 className="text-lg font-semibold text-slate-950">{selectedBook}</h2><p className="mt-1 text-sm text-slate-600">{items.length} 道题目</p></div>
        </header>
        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-3">
          {items.map(function(item, i) {
            var isOpen = expanded === item.question_id;
            var correct = stdAnswer(item), correctVals = correct.split(',');
            return React.createElement('div', { key: i, className: 'rounded-xl border bg-white shadow-sm ' + (isOpen ? 'border-emerald-400 ring-1 ring-emerald-200' : 'border-slate-200') },
              React.createElement('button', { type: 'button', onClick: function() { setExpanded(isOpen ? null : item.question_id); }, className: 'w-full text-left p-4 flex items-start gap-3' },
                React.createElement('p', { className: 'text-sm leading-6 text-slate-800 flex-1' }, item.question_content || '（题目内容缺失）'),
                React.createElement('span', { className: 'shrink-0 flex items-center gap-2' },
                  React.createElement('button', { type: 'button', onClick: function(e) { removeFav(e, item.question_id); }, className: 'text-xs text-rose-500 hover:text-rose-700' }, '移除'),
                  React.createElement('span', { className: 'text-xs text-slate-400' }, isOpen ? '收起' : '展开')
                )
              ),
              isOpen && React.createElement('div', { className: 'border-t border-slate-100 px-4 pb-4 space-y-3' },
                item.options && item.options.length > 0 && React.createElement('div', null,
                  React.createElement('p', { className: 'text-xs font-semibold text-slate-500 mb-2' }, '选项'),
                  React.createElement('div', { className: 'space-y-1' },
                    item.options.map(function(opt, j) {
                      var v = opt.option_id || opt.id || '';
                      var my = item.my_answer && String(item.my_answer).split(',').indexOf(v) >= 0;
                      var ok = correctVals.indexOf(v) >= 0;
                      var c = 'text-slate-600';
                      if (my && ok) c = 'bg-emerald-50 border border-emerald-300 text-emerald-900 font-medium';
                      else if (my) c = 'bg-rose-50 border border-rose-300 text-rose-900 font-medium';
                      else if (ok && hasCorrect(item)) c = 'bg-emerald-50 border border-emerald-200 text-emerald-800';
                      return React.createElement('div', { key: j, className: 'text-sm px-3 py-1.5 rounded-lg ' + c },
                        React.createElement('strong', null, v + '. '), opt.content,
                        my && ok && React.createElement('span', { className: 'ml-2 text-xs text-emerald-600' }, '✓ 正确'),
                        my && !ok && React.createElement('span', { className: 'ml-2 text-xs text-rose-600' }, '✗ 你的作答'),
                        !my && ok && hasCorrect(item) && React.createElement('span', { className: 'ml-2 text-xs text-emerald-600' }, '✓ 正确答案')
                      );
                    })
                  )
                ),
                item.explanation && React.createElement('div', null,
                  React.createElement('p', { className: 'text-xs font-semibold text-slate-500 mb-1' }, '解析'),
                  React.createElement('p', { className: 'text-sm leading-6 text-slate-700 bg-slate-50 rounded-lg p-3' }, item.explanation)
                ),
                React.createElement('p', { className: 'text-xs text-slate-400' }, '收藏于' + (item.source || '综合套题') + ' · ' + (item.saved_at || '').slice(0, 10))
              )
            );
          })}
        </div>
      </div>
    );
  }
  var bookNames = Object.keys(books);
  return (
    <div className="flex flex-col h-full">
      <header className="flex items-center gap-4 border-b border-slate-200 px-5 py-4">
        {onBack && <button type="button" onClick={onBack} className="inline-flex items-center gap-2 rounded-lg border-2 border-emerald-600 bg-white px-3 py-2 text-sm font-semibold text-emerald-700 shadow-sm transition hover:bg-emerald-50"><ArrowLeft size={16} />返回训练工坊</button>}
        <div className="flex items-center gap-5" style={{flex:1,minWidth:0}}>
          <div><h2 className="text-lg font-semibold text-slate-950">收藏夹</h2><p className="mt-1 text-sm text-slate-600">{bookNames.length} 个收藏簿 · {favs.length} 道题目</p></div>
          <button type="button" onClick={function() { var name = prompt('请输入新收藏簿名称：'); if (name && name.trim()) setFavs(function(prev) { return prev.slice(); }); }} className="ml-auto inline-flex items-center gap-1 rounded-lg border-2 border-amber-400 bg-amber-50 px-3 py-2.5 text-base font-semibold text-amber-700 shadow-sm transition hover:bg-amber-100"><Plus size={18} />新建收藏簿</button>
        </div>
      </header>
      <div className="flex-1 overflow-y-auto px-5 py-4">
        {bookNames.length === 0 ? (
          <p className="text-center text-slate-400 py-12">暂无收藏，在套题中点击「加入收藏」即可</p>
        ) : (
          <div className="space-y-3">
            {bookNames.map(function(name, i) {
              var gradients = ['linear-gradient(135deg, #fff 0%, #f9fdfa 100%)','linear-gradient(135deg, #f9fdfa 0%, #f0faf4 100%)','linear-gradient(135deg, #f0faf4 0%, #e8f7ef 100%)'];
              return <button key={name} type="button" onClick={function() { setSelectedBook(name); }} className="w-full rounded-xl border border-slate-200 bg-white p-4 shadow-sm text-left hover:border-emerald-300 hover:shadow-md transition" style={{ background: gradients[i % 3] }}>
                <strong className="block text-sm text-slate-900">{name}</strong>
                <span className="text-xs text-slate-500">{books[name].length} 道题目</span>
              </button>;
            })}
          </div>
        )}
      </div>
    </div>
  );
}
const STORAGE_NOTES = 'study-notes';
const getNotes = () => { try { return JSON.parse(localStorage.getItem(STORAGE_NOTES) || '[]'); } catch { return []; } };

function StudyNotesPanel({ onBack }) {
  const [notes, setNotes] = useState(getNotes);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [expanded, setExpanded] = useState(null);
  const [editTitle, setEditTitle] = useState('');
  const [editContent, setEditContent] = useState('');
  const [sourceFilter, setSourceFilter] = useState('全部');
  const [dateFilter, setDateFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('全部');
  const [search, setSearch] = useState('');
  const sources = ['全部', '综合套题', '智能组卷', '专题训练', '专项训练', '学习笔记'];
  const types = ['全部', '心得体会', '题目笔记'];
  const dates = [...new Set(notes.map(function(n) { return (n.created_at || n.date || '').slice(0, 10); }))].filter(Boolean).sort().reverse();
  const filtered = notes.filter(function(n) {
    if (sourceFilter !== '全部' && (n.source || '学习笔记') !== sourceFilter) return false;
    if (dateFilter && (n.created_at || n.date || '').slice(0, 10) !== dateFilter) return false;
    if (typeFilter !== '全部' && (n.type || '心得体会') !== typeFilter) return false;
    if (search && (n.title || '').indexOf(search) === -1 && (n.content || '').indexOf(search) === -1) return false;
    return true;
  });
  var saveNote = function() {
    if (!title.trim() || !content.trim()) return;
    var next = [{ id: 'note-' + Date.now(), title: title.trim(), content: content.trim(), type: '心得体会', source: '学习笔记', created_at: new Date().toISOString() }].concat(notes);
    setNotes(next); localStorage.setItem(STORAGE_NOTES, JSON.stringify(next)); setTitle(''); setContent('');
  };
  var deleteNote = function(e, note) {
    e.stopPropagation();
    var next = notes.filter(function(n) { return n.id !== note.id; });
    setNotes(next); localStorage.setItem(STORAGE_NOTES, JSON.stringify(next));
    if (expanded === note.id) setExpanded(null);
    var attId = note.attemptId || (note.qpKey ? note.qpKey.split('-').slice(0, -1).join('-') : '');
    if (note.qpKey && attId) { try { var qpNotes = JSON.parse(localStorage.getItem('qp-notes-' + attId) || '{}'); var posKey = note.qpKey.replace(attId + '-', ''); delete qpNotes[posKey]; localStorage.setItem('qp-notes-' + attId, JSON.stringify(qpNotes)); } catch(e) {} }
  };
  return (
    <div className="flex flex-col h-full">
      <header className="flex items-center gap-4 border-b border-slate-200 px-5 py-4" style={{flexWrap:'wrap'}}>
        {onBack && <button type="button" onClick={onBack} className="inline-flex items-center gap-2 rounded-lg border-2 border-emerald-600 bg-white px-3 py-2 text-sm font-semibold text-emerald-700 shadow-sm transition hover:bg-emerald-50"><ArrowLeft size={16} />返回训练工坊</button>}
        <div className="flex items-center gap-5" style={{flex:1,minWidth:0,flexWrap:'wrap'}}>
          <div><h2 className="text-lg font-semibold text-slate-950">学习笔记</h2><p className="mt-1 text-sm text-slate-600">共 {filtered.length} 条笔记</p></div>
          <label className="text-base font-semibold text-slate-900">来源<select value={sourceFilter} onChange={function(e) { setSourceFilter(e.target.value); }} className="ml-2 rounded-lg border border-slate-400 bg-white px-3 py-2.5 text-base text-slate-800">{sources.map(function(s) { return <option key={s} value={s}>{s}</option>; })}</select></label>
          <label className="text-base font-semibold text-slate-900">日期<select value={dateFilter} onChange={function(e) { setDateFilter(e.target.value); }} className="ml-2 rounded-lg border border-slate-400 bg-white px-3 py-2.5 text-base text-slate-800"><option value="">全部</option>{dates.map(function(d) { return <option key={d} value={d}>{d}</option>; })}</select></label>
          <label className="text-base font-semibold text-slate-900">类型<select value={typeFilter} onChange={function(e) { setTypeFilter(e.target.value); }} className="ml-2 rounded-lg border border-slate-400 bg-white px-3 py-2.5 text-base text-slate-800">{types.map(function(t) { return <option key={t} value={t}>{t}</option>; })}</select></label>
          <input type="text" value={search} onChange={function(e) { setSearch(e.target.value); }} placeholder="搜索笔记…" className="rounded-lg border border-slate-400 bg-white px-3 py-2.5 text-base text-slate-800 outline-none focus:border-emerald-500" style={{width:160}} />
        </div>
      </header>
      <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
        <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm space-y-3">
          <input type="text" value={title} onChange={function(e) { setTitle(e.target.value); }} placeholder="笔记标题" className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-500" />
          <textarea value={content} onChange={function(e) { setContent(e.target.value); }} placeholder="写下你的学习心得…" rows={3} className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm outline-none focus:border-emerald-500" style={{resize:'none'}} />
          <button type="button" onClick={saveNote} disabled={!title.trim() || !content.trim()} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700 disabled:opacity-50">保存笔记</button>
        </div>
        {filtered.length === 0 ? (
          <p className="text-center text-slate-400 py-8">暂无笔记，开始记录你的学习心得吧</p>
        ) : (
          <div className="space-y-3">
            {filtered.map(function(note, i) {
              var isOpen = expanded === note.id;
              var gradients = ['linear-gradient(135deg, #fff 0%, #f9fdfa 100%)','linear-gradient(135deg, #f9fdfa 0%, #f0faf4 100%)','linear-gradient(135deg, #f0faf4 0%, #e8f7ef 100%)'];
              return (
                <div key={note.id} className={'rounded-xl border bg-white shadow-sm transition ' + (isOpen ? 'border-emerald-400 ring-1 ring-emerald-200' : 'border-slate-200')} style={{ background: gradients[i % 3] }}>
                  <button type="button" onClick={function() { if (isOpen) { setExpanded(null); } else { setExpanded(note.id); setEditTitle(note.title); setEditContent(note.content); } }} className="w-full text-left p-4 flex items-start gap-3">
                    <span className="flex-1 min-w-0"><strong className="block text-sm text-slate-900">{note.title}</strong><span className="text-xs text-slate-400">{(note.source || '学习笔记') + ' · ' + (note.type || '心得体会') + ' · ' + (note.created_at || note.date || '').slice(0, 10)}</span></span>
                    <button type="button" onClick={function(e) { deleteNote(e, note); }} className="shrink-0 text-xs text-rose-500 hover:text-rose-700">删除</button>
                    <span className="text-xs text-slate-400">{isOpen ? '收起' : '展开'}</span>
                  </button>
                  {isOpen && <div className="border-t border-slate-100 px-4 pb-4 space-y-3">
                    {note.question_content && <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-sm leading-6 text-amber-900"><p className="text-xs font-semibold text-amber-700 mb-1">题目内容</p>{note.question_content}{note.options && note.options.length > 0 && <div className="space-y-1 mt-2"><p className="text-xs font-semibold text-amber-700">选项</p>{note.options.map(function(opt, j) { var v = opt.option_id || opt.id || ''; var ans = Array.isArray(note.standard_answer) ? note.standard_answer : []; var isCorrect = ans.indexOf(v) >= 0; return <div key={j} className={isCorrect ? 'text-emerald-800 font-medium' : ''}><strong>{v}.</strong> {opt.content}{isCorrect ? ' ✓' : ''}</div>; })}</div>}{Array.isArray(note.standard_answer) && note.standard_answer.length > 0 && <p className="text-xs mt-2"><span className="font-semibold text-amber-700">正确答案：</span>{note.standard_answer.join('、')}</p>}</div>}
                    <input type="text" value={editTitle} onChange={function(e) { setEditTitle(e.target.value); }} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-emerald-400" />
                    <textarea value={editContent} onChange={function(e) { setEditContent(e.target.value); }} rows={4} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm outline-none focus:border-emerald-400" style={{resize:'none'}} />
                    <button type="button" onClick={function() { var next = notes.map(function(n) { return n.id === note.id ? Object.assign({}, n, { title: editTitle.trim() || n.title, content: editContent }) : n; }); setNotes(next); localStorage.setItem(STORAGE_NOTES, JSON.stringify(next)); }} className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-semibold text-white hover:bg-emerald-700">保存修改</button>
                  </div>}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function TrainingBannerIllustration() {
  return (
    <div className="practice-overview__illustration" aria-hidden="true">
      <div className="practice-overview__speech-bubble">快来跟我一起练习吧</div>
      <img
        className="practice-overview__character"
        src="/assistant-character/lizhizhen-center-cutout.png"
        alt=""
      />
    </div>
  );
}

function TrainingOverview({ onOpenModule }) {
  return (
    <section className="practice-overview" aria-labelledby="practice-overview-title">
      <header className="practice-overview__banner">
        <div>
          <span className="practice-overview__eyebrow"><Sparkles size={16} aria-hidden="true" />训练中心</span>
          <h1 id="practice-overview-title">训练工坊，实战精进</h1>
          <p>聚焦实战训练，强化能力，在每一次复盘中稳步精进。</p>
        </div>
        <TrainingBannerIllustration />
      </header>

      <div className="practice-overview__layout">
        <section className="practice-overview__main" aria-label="训练路径">
          <div className="practice-overview__section-heading">
            <div>
              <span>训练路径</span>
              <h2>选择今天的练习方式</h2>
            </div>
          </div>
          <div className="practice-overview__training-grid">
            {trainingCards.map((card) => {
              const Icon = card.icon;
              return (
                <button
                  key={`${card.title}-${card.key}`}
                  type="button"
                  className={`practice-overview__training-card practice-overview__training-card--${card.tone}`}
                  onClick={() => onOpenModule(card)}
                >
                  <span className="practice-overview__card-icon"><Icon aria-hidden="true" size={26} /></span>
                  <span className="practice-overview__card-copy">
                    <strong>{card.title}</strong>
                    <small>{card.description}</small>
                  </span>
                  <ArrowUpRight className="practice-overview__card-arrow" aria-hidden="true" size={20} />
                </button>
              );
            })}
          </div>
        </section>

        <aside className="practice-overview__utilities" aria-label="常用学习工具">
          <div className="practice-overview__section-heading">
            <div>
              <span>常用工具</span>
              <h2>复盘与沉淀</h2>
            </div>
          </div>
          <div className="practice-overview__utility-list">
            {utilityCards.map((card) => {
              const Icon = card.icon;
              const content = <>
                <span className="practice-overview__utility-icon"><Icon aria-hidden="true" size={22} /></span>
                <span><strong>{card.title}</strong><small>{card.description}</small></span>
              </>;
              return card.available ? (
                <button
                  key={card.title}
                  type="button"
                  className="practice-overview__utility-card"
                  onClick={() => onOpenModule(card)}
                >
                  {content}
                </button>
              ) : (
                <div key={card.title} className="practice-overview__utility-card" aria-disabled="true">
                  {content}
                </div>
              );
            })}
          </div>
        </aside>
      </div>

      <footer className="practice-overview__summary" aria-label="学习摘要">
        <span><ClipboardCheck aria-hidden="true" size={18} /><b>今日练习</b><em>暂无记录</em></span>
        <span><Target aria-hidden="true" size={18} /><b>正确率</b><em>暂无记录</em></span>
        <span><BookMarked aria-hidden="true" size={18} /><b>累计学习</b><em>暂无记录</em></span>
        <p>保持练习，积累每一次进步。</p>
      </footer>
    </section>
  );
}

export default function PracticePage({ navigationContext = {} }) {
  const selectedKnowledgePoint = practiceContextFromIntent(navigationContext);
  const initialTaskIntent = normalizeTaskIntent(navigationContext.taskType);
  const [activeTaskType, setActiveTaskType] = useState(() => initialTaskIntent.taskType);
  const [activeInitialMode, setActiveInitialMode] = useState(() => initialTaskIntent.initialMode);
  const [taskResult, setTaskResult] = useState(null);
  const [mobilePage, setMobilePage] = useState('task');
  const [view, setView] = useState(() => (navigationContext.taskType || navigationContext.view === 'workspace' ? 'workspace' : 'overview'));

  useEffect(() => {
    const request = async (path, body) => {
      const result = await fetchJsonWithAuthFallback({
        paths: [path],
        options: {
          method: 'POST',
          keepalive: true,
          ...(body === undefined ? {} : { body: JSON.stringify(body) }),
        },
      });
      return result.data;
    };
    const tracker = createLearningFocusTracker({
      request,
      resourceType: 'training_workspace',
      resourceId: 'practice',
    });
    tracker.start().catch(() => {});
    return () => {
      tracker.stop().catch(() => {});
    };
  }, []);

  const handlePracticeResult = (result, question) => {
    const grading = result?.grading || {};
    setTaskResult({
      task_id: result?.attempt_id || `practice-${question.question_id}`,
      task_type: 'practice_grading',
      status: 'completed',
      title: `${question.question_type}批改结果`,
      summary: grading.is_correct ? '回答正确，学习记录已更新。' : '回答错误，已进入错题记录。',
      artifact: {
        artifact_type: 'grading_result',
        title: '练习批改结果',
        content: { grading, remediation: {} },
      },
      evidence_pack: {},
      audit: { decision: 'pass', reason: '正式题库受控批改已完成' },
      trace: [],
      learning_updates: { writeback: result?.writeback || {} },
      next_actions: [],
    });
    setMobilePage('result');
  };

  const openWorkshopModule = ({ key, initialMode }) => {
    setActiveTaskType(key);
    setActiveInitialMode(initialMode || key);
    setTaskResult(null);
    setMobilePage('task');
    setView('workspace');
  };

  const taskResultApproved = isTrainingTaskResultApproved(taskResult);

  if (view === 'overview') {
    return <TrainingOverview onOpenModule={openWorkshopModule} />;
  }

  if (activeTaskType === 'question_workspace') {
    return (
      <div className="space-y-5 text-slate-800">
        <div className="practice-workspace__toolbar">
          <button type="button" className="practice-workspace__back" onClick={() => setView('overview')}>
            <ArrowLeft aria-hidden="true" size={18} />返回训练工坊
          </button>
        </div>
        <QuestionWorkspacePage />
      </div>
    );
  }

  const isSP = activeTaskType === 'ai_patient_simulation';
  const isFullPanel = ['question_training', 'special_training', 'topic_training', 'paper_workspace', 'mistake_variation', 'question_favorites', 'study_notes'].includes(activeTaskType);

  if (isSP) {
    return <SimulatedPatientChat onBack={() => setView('overview')} />;
  }

  if (isFullPanel) {
    return (
      <div className="flex flex-col h-full text-slate-800">
        <div className="flex-1 min-h-0 min-w-0">
          {activeTaskType === 'question_training' ? (
            <QualificationPaperPanel enabled onBack={() => setView('overview')} />
          ) : activeTaskType === 'mistake_variation' ? (
            <MistakeVariationPanel enabled onBack={() => setView('overview')} />
          ) : activeTaskType === 'paper_workspace' ? (
            <SmartPaperPanel enabled paperId={navigationContext.paperId || navigationContext.paper_id || ''} onBack={() => setView('overview')} />
          ) : activeTaskType === 'question_favorites' ? (
            <QuestionFavoritesPanel onBack={() => setView('overview')} />
          ) : activeTaskType === 'study_notes' ? (
            <StudyNotesPanel onBack={() => setView('overview')} />
          ) : ['special_training', 'topic_training'].includes(activeTaskType) ? (
            <QuestionTrainingPanel enabled selectedKnowledgePoint={selectedKnowledgePoint} initialMode={activeInitialMode} onResult={handlePracticeResult} onBack={() => setView('overview')} />
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-5 text-slate-800">
      <div className="practice-workspace__toolbar">
        <button type="button" className="practice-workspace__back" onClick={() => setView('overview')}>
          <ArrowLeft aria-hidden="true" size={18} />返回训练工坊
        </button>
      </div>
      <header>
        <span className="app-shell__section-label">训练工坊</span>
        <h1 className="mt-1 text-2xl font-semibold text-slate-950">{workspaceTitles[activeTaskType] || '训练任务'}</h1>
      </header>

      <div className="practice-mobile-tabs" role="tablist" aria-label="移动端训练视图">
        {[
          ['task', '任务'],
          ['result', '结果'],
        ].map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={mobilePage === key}
            onClick={() => setMobilePage(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {selectedKnowledgePoint && (
        <section className="border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-950" aria-label="当前考纲知识点">
          <div className="font-semibold">当前训练上下文：{selectedKnowledgePoint.kpName}</div>
          <div className="mt-1 font-mono text-xs text-emerald-800">{selectedKnowledgePoint.kpId}</div>
          <p className="mt-2 leading-6 text-emerald-900">
            该知识点已带入训练工坊；当前兼容示例题不代表该知识点的正式题目，正式题源筛选将在题库接入后启用。
          </p>
        </section>
      )}

      <div className="min-w-0 space-y-5">
          <section data-mobile-active={String(mobilePage === 'task')} className="practice-task-panel rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50">
            {activeTaskType === 'question_training' ? (
              <QualificationPaperPanel enabled />
            ) : activeTaskType === 'mistake_variation' ? (
              <MistakeVariationPanel enabled />
            ) : activeTaskType === 'paper_workspace' ? (
              <SmartPaperPanel enabled paperId={navigationContext.paperId || navigationContext.paper_id || ''} />
            ) : activeTaskType === 'knowledge_cards' ? (
              <KnowledgeCardLibrary
                cardId={navigationContext.cardId || navigationContext.card_id || ''}
                kpId={navigationContext.kpId || navigationContext.kp_id || ''}
              />
            ) : ['question_training', 'special_training', 'topic_training'].includes(activeTaskType) ? (
              <QuestionTrainingPanel
                enabled
                selectedKnowledgePoint={selectedKnowledgePoint}
                initialMode={activeInitialMode}
                onResult={handlePracticeResult}
              />
            ) : (
              <div className="mt-5 rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-sm leading-6 text-slate-600">
                此模块正在准备中，暂不支持提交任务。
              </div>
            )}

          </section>

          <section
            data-testid="practice-result-panel"
            data-mobile-active={String(mobilePage === 'result')}
            aria-busy="false"
            aria-labelledby="training-artifact-title"
            className="practice-result-panel rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50"
          >
            <div className="flex items-center gap-2 text-sm font-medium text-slate-600">
              <FileText size={16} aria-hidden="true" />
              <h2 id="training-artifact-title" className="text-sm font-semibold text-slate-900">训练产物</h2>
            </div>
            {taskResult && !taskResultApproved ? (
              <div role="alert" className="mt-4 border border-rose-300 bg-rose-50 px-4 py-4 text-rose-950">
                <h3 className="text-base font-semibold">{displayValue(taskResult.title || taskResult.artifact?.title)}</h3>
                <p className="mt-2 text-sm font-semibold leading-6">审核未通过/任务未完成，该候选内容不可作为学习依据。</p>
                <p className="mt-2 text-sm leading-6">状态：{displayValue(taskResult.status)}</p>
                <p className="mt-1 text-sm leading-6">审核原因：{displayValue(taskResult.audit?.reason)}</p>
                <p className="mt-3 text-sm font-semibold">请调整输入后重试。</p>
              </div>
            ) : (
              <div className="mt-4 [overflow-wrap:anywhere]"><ArtifactResult taskResult={taskResult} /></div>
            )}
          </section>
      </div>
    </div>
  );
}
