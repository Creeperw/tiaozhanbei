import React, { useEffect, useState } from 'react';
import AtlasPracticePanel from './exam-atlas/AtlasPracticePanel';
import CaseTrainingPanel from './CaseTrainingPanel';
import MistakeVariationPanel from './MistakeVariationPanel';

const modes = [
  ['objective', '客观题'],
  ['case', '案例简答'],
  ['patient', 'AI 病患模拟'],
  ['variation', '错题变式'],
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
  practiceScope,
  onPracticeScopeChange,
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
          <div className="question-training-toolbar">
            <span>题目范围</span>
            <div className="question-training-scope" role="group" aria-label="题目范围">
              {[
                ['public', '正式题库'],
                ['user', '我的题目'],
                ['all', '全部题目'],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={practiceScope === value}
                  className={practiceScope === value ? 'is-active' : ''}
                  onClick={() => onPracticeScopeChange(value)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
          <AtlasPracticePanel
            key={`${mode}:${practiceScope}:${selectedKnowledgePoint?.kpId || selectedKnowledgePoint?.kp_id || 'all'}`}
            knowledgePoint={selectedKnowledgePoint}
            scope={practiceScope}
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
