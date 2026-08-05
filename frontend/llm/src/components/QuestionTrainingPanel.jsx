import React, { useEffect, useState } from 'react';
import AtlasPracticePanel from './exam-atlas/AtlasPracticePanel';
import SimulatedPatientChat from './SimulatedPatientChat';
import MistakeVariationPanel from './MistakeVariationPanel';

const modes = [
  ['objective', '客观题'],
  ['case', '案例简答'],
];

function normalizeInitialMode(value) {
  if (value === 'mistake_variation') return 'variation';
  if (value === 'case_training') return 'case';
  if (value === 'ai_patient_simulation') return 'patient';
  return 'objective';
}

export default function QuestionTrainingPanel({
  enabled,
  selectedKnowledgePoint,
  initialMode = '',
  onResult,
  taskItemId = '',
}) {
  const [mode, setMode] = useState(() => normalizeInitialMode(initialMode));

  useEffect(() => {
    setMode(normalizeInitialMode(initialMode));
  }, [initialMode]);

  if (!enabled) return <p className="mt-5 text-[15px] text-slate-600">题目练习暂未开放。</p>;

  return (
    <div className="question-training-panel">
      {taskItemId ? (
        <div className="question-training-mode-tabs" aria-label="今日任务练习模式">
          <strong>今日任务题目</strong>
          <span>题型以当前任务冻结的正式题目为准</span>
        </div>
      ) : (
        <div className="question-training-mode-tabs" role="tablist" aria-label="题目练习模式">
          {modes.map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={mode === key}
              onClick={() => setMode(key)}
              className={mode === key ? 'is-active' : ''}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {(mode === 'objective' || mode === 'case') && (
        <div className="question-training-content">
          <AtlasPracticePanel
            key={`${taskItemId || 'free'}:${mode}:${selectedKnowledgePoint?.kpId || selectedKnowledgePoint?.kp_id || 'all'}`}
            knowledgePoint={selectedKnowledgePoint}
            scope="public"
            mode={mode}
            onResult={onResult}
            taskItemId={taskItemId}
          />
        </div>
      )}
      {mode === 'patient' && <SimulatedPatientChat showBack={false} />}
      {mode === 'variation' && <MistakeVariationPanel enabled />}
    </div>
  );
}
