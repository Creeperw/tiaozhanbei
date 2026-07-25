import React, { useEffect, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
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
  onBack,
}) {
  const titles = { objective: '专项训练', case: '专题训练' };
  const [mode, setMode] = useState(() => normalizeInitialMode(initialMode));

  useEffect(() => {
    setMode(normalizeInitialMode(initialMode));
  }, [initialMode]);

  if (!enabled) return <p className="mt-5 text-sm text-slate-600">题目训练暂未开放。</p>;

  return (
    <div className="flex flex-col h-full">
      <header className="flex items-center gap-4 border-b border-slate-200 px-5 py-4">
        {onBack && <button type="button" onClick={onBack} className="inline-flex items-center gap-2 rounded-lg border-2 border-emerald-600 bg-white px-3 py-2 text-sm font-semibold text-emerald-700 shadow-sm transition hover:bg-emerald-50"><ArrowLeft size={16} />返回训练工坊</button>}
        <h2 className="text-lg font-semibold text-slate-950">{titles[mode] || '题目训练'}</h2>
      </header>
      <div className="flex-1 overflow-y-auto">
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
      {mode === 'patient' && <SimulatedPatientChat showBack={false} />}
      {mode === 'variation' && <MistakeVariationPanel enabled />}
    </div>
      </div>
    </div>
  );
}
