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
    <div className="mt-5 space-y-5">
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="题目训练模式">
        {modes.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={mode === key}
            onClick={() => setMode(key)}
            className={`rounded-xl border px-3 py-2 text-sm font-medium ${mode === key ? 'border-emerald-300 bg-emerald-50 text-emerald-900' : 'border-slate-200 bg-white text-slate-600'}`}
          >
            {label}
          </button>
        ))}
      </div>

      {(mode === 'objective' || mode === 'case') && (
        <div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="text-xs font-semibold text-slate-600">题目范围</span>
            <div className="inline-flex rounded-lg border border-slate-200 bg-slate-50 p-1" role="group" aria-label="题目范围">
              {[
                ['public', '正式题库'],
                ['user', '我的题目'],
                ['all', '全部题目'],
              ].map(([value, label]) => (
                <button
                  key={value}
                  type="button"
                  aria-pressed={practiceScope === value}
                  className={`min-h-10 rounded-md px-3 text-xs font-semibold ${practiceScope === value ? 'bg-white text-emerald-800 shadow-sm' : 'text-slate-600'}`}
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
