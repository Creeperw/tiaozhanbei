import React, { useState } from 'react';
import AtlasPracticePanel from './exam-atlas/AtlasPracticePanel';
import CaseTrainingPanel from './CaseTrainingPanel';
import MistakeVariationPanel from './MistakeVariationPanel';

const modes = [
  ['practice', '题目作答与批改'],
  ['case', '案例训练'],
  ['variation', '错题变式'],
];

export default function QuestionTrainingPanel({
  enabled,
  selectedKnowledgePoint,
  practiceScope,
  onPracticeScopeChange,
  question,
  answer,
  onAnswerChange,
  onSubmit,
  loading,
}) {
  const [mode, setMode] = useState('practice');
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

      {mode === 'case' && <CaseTrainingPanel enabled />}
      {mode === 'variation' && <MistakeVariationPanel enabled />}
      {mode === 'practice' && selectedKnowledgePoint && (
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
          <AtlasPracticePanel knowledgePoint={selectedKnowledgePoint} scope={practiceScope} />
        </div>
      )}
      {mode === 'practice' && !selectedKnowledgePoint && (
        <div>
          <p className="text-sm font-medium leading-6 text-slate-900">{question.stem}</p>
          <p className="mt-2 text-sm text-slate-500">知识点：{question.knowledge_points.join('、')}</p>
          <label htmlFor="practice-answer" className="mt-5 block text-sm font-medium text-slate-700">你的答案</label>
          <textarea
            id="practice-answer"
            value={answer}
            onChange={(event) => onAnswerChange(event.target.value)}
            className="mt-2 min-h-32 w-full rounded-2xl border border-slate-200 bg-slate-50 p-4 text-sm leading-6"
            disabled={loading}
          />
          <button type="button" onClick={onSubmit} disabled={loading} className="mt-4 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-medium text-white disabled:opacity-55">
            {loading ? '正在批改…' : '提交并批改'}
          </button>
        </div>
      )}
    </div>
  );
}
