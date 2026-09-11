import React from 'react';
import { ArrowLeft } from 'lucide-react';
import QuestionTrainingPanel from '../QuestionTrainingPanel';
import QualificationPaperPanel from '../QualificationPaperPanel';
import SimulatedPatientChat from '../SimulatedPatientChat';
import MistakeRedoPanel from '../MistakeRedoPanel';
import MistakeVariationPanel from '../MistakeVariationPanel';
import TrainingHistoryPanel from '../TrainingHistoryPanel';
import SmartPaperPanel from '../SmartPaperPanel';
import QuestionWorkspacePage from '../QuestionWorkspacePage';
import KnowledgeCardLibrary from '../KnowledgeCardLibrary';
import KnowledgePointTrainingHub from '../KnowledgePointTrainingHub';
import QuestionFavoritesPanel from '../QuestionFavoritesPanel';
import KnowledgePointFavoritesPanel from '../KnowledgePointFavoritesPanel';
import StudyNotesPanel from '../StudyNotesPanel';
import { practiceContextFromIntent } from '../exam-atlas/examAtlasPageContext';
import { workspaceTitles, normalizeTaskIntent } from './taskRegistry';

const returnLabelFor = (returnIntent) => {
  if (returnIntent?.page === 'assistant') return '返回智能助教';
  if (returnIntent?.page === 'learning-path') return '返回学习路径';
  if (returnIntent?.page === 'qualification-route') return '返回今日学习';
  if (returnIntent?.page === 'personalization' && returnIntent?.params?.view === 'reports') return '返回学情报告';
  if (returnIntent?.page === 'practice' && returnIntent?.params?.view === 'textbook-chapters') return '返回教材学习';
  if (returnIntent?.page === 'practice' && !returnIntent?.params?.view) return '返回教学资源';
  return '返回练习工坊';
};

function WorkspaceHeader({ title, returnLabel, onBack, history = false }) {
  return (
    <div className={`practice-workspace__heading flex items-center gap-4 border-b border-slate-200 pb-4${history ? ' practice-workspace__heading--history' : ''}`}>
      <button type="button" className="practice-workspace__back" aria-label={returnLabel} onClick={onBack}>
        <ArrowLeft aria-hidden="true" size={16} />返回
      </button>
      <h1 className="text-2xl font-bold text-slate-950">{title}</h1>
    </div>
  );
}

function TaskPanel({ taskType, initialMode, navigationContext, selectedKnowledgePoint, taskItemId, onNavigate, onResult }) {
  if (taskType === 'question_training' && !taskItemId) return <QualificationPaperPanel enabled />;
  if (taskType === 'mistake_redo') return <MistakeRedoPanel />;
  if (taskType === 'mistake_variation') return <MistakeVariationPanel enabled />;
  if (taskType === 'training_history') return <TrainingHistoryPanel enabled />;
  if (taskType === 'paper_workspace') {
    return <SmartPaperPanel enabled paperId={navigationContext.paperId || navigationContext.paper_id || ''} taskItemId={taskItemId} />;
  }
  if (taskType === 'knowledge_cards') {
    return (
      <KnowledgeCardLibrary
        cardId={navigationContext.cardId || navigationContext.card_id || ''}
        kpId={navigationContext.kpId || navigationContext.kp_id || ''}
        taskItemId={taskItemId}
        initialResource={navigationContext.resourceView || navigationContext.resource_view || ''}
        directVideo={navigationContext.directVideo || navigationContext.video || null}
        directTitle={navigationContext.directTitle || navigationContext.kpName || navigationContext.kp_name || ''}
      />
    );
  }
  if (taskType === 'question_favorites') return <QuestionFavoritesPanel onNavigate={onNavigate} />;
  if (taskType === 'knowledge_favorites') return <KnowledgePointFavoritesPanel onNavigate={onNavigate} />;
  if (taskType === 'study_notes') return <StudyNotesPanel onNavigate={onNavigate} />;
  if (taskType === 'topic_training') {
    return <KnowledgePointTrainingHub initialKnowledgePoint={selectedKnowledgePoint} taskItemId={taskItemId} onResult={onResult} />;
  }
  if (['question_training', 'special_training'].includes(taskType)) {
    return <QuestionTrainingPanel enabled selectedKnowledgePoint={selectedKnowledgePoint} initialMode={initialMode} onResult={onResult} taskItemId={taskItemId} practiceOrigin={taskType} />;
  }
  return <div className="mt-5 rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-[15px] leading-6 text-slate-600">此模块正在准备中，暂不支持提交任务。</div>;
}

export default function TrainingWorkspace({ navigationContext = {}, onNavigate, onBack }) {
  const selectedKnowledgePoint = practiceContextFromIntent(navigationContext);
  const intent = normalizeTaskIntent(navigationContext.taskType);
  const taskType = intent.taskType;
  const taskItemId = navigationContext.taskItemId || navigationContext.task_item_id || '';
  const returnIntent = navigationContext.returnTo;
  const leaveWorkspace = () => (returnIntent && onNavigate ? onNavigate(returnIntent) : onBack?.());

  if (taskType === 'question_workspace') {
    return (
      <div className="question-workspace-shell space-y-5 text-slate-800">
        <div className="practice-workspace__heading question-workspace-shell__toolbar flex items-center gap-4 border-b border-slate-200 pb-4">
          <button type="button" className="practice-workspace__back" onClick={leaveWorkspace}><ArrowLeft aria-hidden="true" size={16} />返回</button>
          <h1 className="text-2xl font-bold text-slate-950">上传题库</h1>
        </div>
        <QuestionWorkspacePage />
      </div>
    );
  }

  if (taskType === 'ai_patient_simulation') return <div className="practice-workspace practice-workspace--ai_patient_simulation"><SimulatedPatientChat onBack={leaveWorkspace} /></div>;

  return (
    <div className={`practice-workspace practice-workspace--${taskType} space-y-5 text-slate-800`}>
      <WorkspaceHeader title={workspaceTitles[taskType] || '练习任务'} returnLabel={returnLabelFor(returnIntent)} onBack={leaveWorkspace} history={taskType === 'training_history'} />
      {selectedKnowledgePoint && (
        <section className="border border-emerald-200 bg-emerald-50 px-4 py-3 text-[15px] text-emerald-950" aria-label="当前考纲知识点">
          <div className="font-semibold">当前练习上下文：{selectedKnowledgePoint.kpName}</div>
          <p className="mt-2 leading-6 text-emerald-900">已按该知识点筛选练习内容，作答结果会写回掌握度与复习记录。</p>
        </section>
      )}
      <div className="min-w-0 space-y-5">
        <section className={`practice-task-panel rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50${taskType === 'paper_workspace' ? ' practice-task-panel--paper' : ''}`}>
          <TaskPanel
            taskType={taskType}
            initialMode={intent.initialMode}
            navigationContext={navigationContext}
            selectedKnowledgePoint={selectedKnowledgePoint}
            taskItemId={taskItemId}
            onNavigate={onNavigate}
          />
        </section>
      </div>
    </div>
  );
}
