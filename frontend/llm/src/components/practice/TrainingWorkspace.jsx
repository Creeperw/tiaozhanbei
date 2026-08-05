import React from 'react';
import { ArrowLeft } from 'lucide-react';
import KnowledgeCardLibrary from '../KnowledgeCardLibrary';
import KnowledgePointTrainingHub from '../KnowledgePointTrainingHub';
import MistakeRedoPanel from '../MistakeRedoPanel';
import QualificationPaperPanel from '../QualificationPaperPanel';
import QuestionFavoritesPanel from '../QuestionFavoritesPanel';
import QuestionTrainingPanel from '../QuestionTrainingPanel';
import SimulatedPatientChat from '../SimulatedPatientChat';
import SmartPaperPanel from '../SmartPaperPanel';
import TrainingHistoryPanel from '../TrainingHistoryPanel';
import { practiceContextFromIntent } from '../exam-atlas/examAtlasPageContext';
import { WORKSPACE_TITLES } from './taskRegistry';

function returnLabelFor(navigationContext) {
  const returnIntent = navigationContext.returnTo;
  if (returnIntent?.page === 'assistant') return '返回智能助手';
  if (returnIntent?.page === 'learning-path') return '返回学习路径';
  if (returnIntent?.page === 'qualification-route') return '返回今日学习';
  if (returnIntent?.page === 'personalization' && returnIntent?.params?.view === 'reports') return '返回学情报告';
  if (returnIntent?.page === 'practice' && returnIntent?.params?.view === 'textbook-chapters') return '返回教材学习';
  return '返回练习工坊';
}

export default function TrainingWorkspace({ navigationContext = {}, onNavigate, onBack }) {
  const activeTaskType = navigationContext.taskType || 'question_training';
  const activeInitialMode = navigationContext.initialMode || activeTaskType;
  const selectedKnowledgePoint = practiceContextFromIntent(navigationContext);
  const taskItemId = navigationContext.taskItemId || navigationContext.task_item_id || '';
  const returnLabel = returnLabelFor(navigationContext);
  const leaveWorkspace = () => {
    if (navigationContext.returnTo && onNavigate) onNavigate(navigationContext.returnTo);
    else onBack?.();
  };
  const handlePracticeResult = () => {};

  if (activeTaskType === 'ai_patient_simulation') {
    return (
      <div className="practice-workspace practice-workspace--ai_patient_simulation">
        <SimulatedPatientChat onBack={leaveWorkspace} />
      </div>
    );
  }

  return (
    <div className={`practice-workspace practice-workspace--${activeTaskType} space-y-5 text-slate-800`}>
      <div className={`practice-workspace__heading flex items-center gap-4 border-b border-slate-200 pb-4${activeTaskType === 'training_history' ? ' practice-workspace__heading--history' : ''}`}>
        <button type="button" className="practice-workspace__back" aria-label={returnLabel} onClick={leaveWorkspace}>
          <ArrowLeft aria-hidden="true" size={16} />返回
        </button>
        <h1 className="text-2xl font-bold text-slate-950">{WORKSPACE_TITLES[activeTaskType] || '练习任务'}</h1>
      </div>

      {selectedKnowledgePoint && (
        <section className="border border-emerald-200 bg-emerald-50 px-4 py-3 text-[15px] text-emerald-950" aria-label="当前考纲知识点">
          <div className="font-semibold">当前练习上下文：{selectedKnowledgePoint.kpName}</div>
          <p className="mt-2 leading-6 text-emerald-900">已按该知识点筛选练习内容，作答结果会写回掌握度与复习记录。</p>
        </section>
      )}

      <div className="min-w-0 space-y-5">
        <section className={`practice-task-panel rounded-[24px] border border-slate-200 bg-white p-5 shadow-sm shadow-slate-200/50${activeTaskType === 'paper_workspace' ? ' practice-task-panel--paper' : ''}`}>
          {activeTaskType === 'question_training' && !taskItemId ? (
            <QualificationPaperPanel enabled />
          ) : activeTaskType === 'mistake_redo' ? (
            <MistakeRedoPanel />
          ) : activeTaskType === 'training_history' ? (
            <TrainingHistoryPanel enabled />
          ) : activeTaskType === 'paper_workspace' ? (
            <SmartPaperPanel
              enabled
              paperId={navigationContext.paperId || navigationContext.paper_id || ''}
              taskItemId={taskItemId}
            />
          ) : activeTaskType === 'knowledge_cards' ? (
            <KnowledgeCardLibrary
              cardId={navigationContext.cardId || navigationContext.card_id || ''}
              kpId={navigationContext.kpId || navigationContext.kp_id || ''}
              taskItemId={taskItemId}
              initialResource={navigationContext.resourceView || navigationContext.resource_view || ''}
              directVideo={navigationContext.directVideo || navigationContext.video || null}
              directTitle={navigationContext.directTitle || navigationContext.kpName || navigationContext.kp_name || ''}
            />
          ) : activeTaskType === 'question_favorites' ? (
            <QuestionFavoritesPanel onNavigate={onNavigate} />
          ) : activeTaskType === 'topic_training' ? (
            <KnowledgePointTrainingHub
              initialKnowledgePoint={selectedKnowledgePoint}
              taskItemId={taskItemId}
              onResult={handlePracticeResult}
            />
          ) : ['question_training', 'special_training'].includes(activeTaskType) ? (
            <QuestionTrainingPanel
              enabled
              selectedKnowledgePoint={selectedKnowledgePoint}
              initialMode={activeInitialMode}
              onResult={handlePracticeResult}
              taskItemId={taskItemId}
            />
          ) : (
            <div className="mt-5 rounded-2xl border border-dashed border-slate-200 bg-slate-50 px-4 py-5 text-[15px] leading-6 text-slate-600">
              此模块正在准备中，暂不支持提交任务。
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
