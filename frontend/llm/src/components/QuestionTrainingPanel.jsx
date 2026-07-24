import React, { useEffect, useState } from 'react';
import AtlasPracticePanel from './exam-atlas/AtlasPracticePanel';
import CaseTrainingPanel from './CaseTrainingPanel';
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
}) {
  const [mode, setMode] = useState(() => normalizeInitialMode(initialMode));

  useEffect(() => {
    setMode(normalizeInitialMode(initialMode));
  }, [initialMode]);

  if (!enabled) return <p className="mt-5 text-sm text-slate-600">题目训练暂未开放。</p>;

  return (
    <div className="question-training-panel">
      <div className="question-training-mode-tabs" role="tablist" aria-label="题目训练模式">
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

      {(mode === 'objective' || mode === 'case') && (
        <div className="question-training-content">
          <AtlasPracticePanel
            key={`${mode}:${selectedKnowledgePoint?.kpId || selectedKnowledgePoint?.kp_id || 'all'}`}
            knowledgePoint={selectedKnowledgePoint}
            scope="public"
            mode={mode}
            onResult={onResult}
          />
        </div>
      )}
      {mode === 'patient' && <CaseTrainingPanel enabled />}
      {mode === 'variation' && <MistakeVariationPanel enabled />}
    </div>
  );
}
