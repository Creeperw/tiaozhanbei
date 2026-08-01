import React from 'react';

const clampRatio = (value) => Math.max(0, Math.min(1, Number(value) || 0));
const integer = (value) => Math.max(0, Math.round(Number(value) || 0));
const displayKnowledgePointName = (item) => {
  const name = String(item?.kp_name || item?.name || '').trim();
  return name && !/^[A-Z_]*\d+[A-Z0-9_-]*$/i.test(name)
    ? name
    : '未命名知识点';
};

const masteryTone = (value) => {
  if (value === null || value === undefined) return 'bg-slate-100';
  const score = clampRatio(value);
  if (score >= 0.8) return 'bg-[#A5D6A7]';
  if (score >= 0.6) return 'bg-[#C8E6C9]';
  if (score >= 0.4) return 'bg-[#D0F0E0]';
  if (score >= 0.2) return 'bg-[#E8F5E9]';
  return 'bg-[#F0F9F4]';
};

export default function MasteryHeatmap({ items = [] }) {
  const safeItems = Array.isArray(items) ? items : [];
  const columns = [
    ['score', '当前掌握', (item) => item.score ?? item.mastery_score],
    ['confidence', '可信度', (item) => item.confidence],
    ['attempt_count', '练习次数', (item) => {
      const maximum = Math.max(1, ...safeItems.map((candidate) => Number(candidate.attempt_count) || 0));
      return (Number(item.attempt_count) || 0) / maximum;
    }],
    ['retention', '复习保持', (item) => item.retention],
  ];

  return (
    <section className="rounded-[24px] border border-slate-200/90 bg-white p-5 shadow-sm shadow-slate-200/45 sm:p-6" aria-label="知识点掌握情况">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <h2 className="flex items-center gap-2 text-xl font-bold text-slate-950">知识点掌握情况 <span aria-hidden="true" className="text-base font-normal text-slate-400">ⓘ</span></h2>
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span>弱</span>
          {['bg-[#F0F9F4]', 'bg-[#E8F5E9]', 'bg-[#D0F0E0]', 'bg-[#C8E6C9]', 'bg-[#A5D6A7]'].map((tone) => <i key={tone} aria-hidden="true" className={`h-4 w-4 rounded-sm ${tone}`} />)}
          <span>强</span>
        </div>
      </div>
      {safeItems.length > 0 ? (
        <div className="mt-6 overflow-x-auto">
          <div className="min-w-[560px]">
            <div className="grid grid-cols-[minmax(10rem,1.5fr)_repeat(4,minmax(5.5rem,1fr))] items-center gap-2 px-2 text-center text-sm font-semibold text-slate-600">
              <span className="text-left">知识点</span>
              {columns.map(([, label]) => <span key={label}>{label}</span>)}
            </div>
            <div className="mt-3 max-h-[632px] space-y-2 overflow-y-auto pr-1">
              {safeItems.map((item, index) => (
                <div key={item.kp_id || item.kp_name || index} className="grid grid-cols-[minmax(10rem,1.5fr)_repeat(4,minmax(5.5rem,1fr))] items-center gap-2 rounded-xl px-2 py-2 hover:bg-emerald-50/40">
                  <span className="truncate text-sm font-normal text-slate-700" title={displayKnowledgePointName(item)}>{displayKnowledgePointName(item)}</span>
                  {columns.map(([key, label, valueFor]) => {
                    const value = valueFor(item);
                    const unavailable = value === null || value === undefined;
                    const knowledgePointName = displayKnowledgePointName(item);
                    return (
                      <span key={key} title={unavailable ? `${knowledgePointName}：${label}数据不足` : `${knowledgePointName}：${label} ${key === 'attempt_count' ? integer(item.attempt_count) : `${Math.round(clampRatio(value) * 100)}%`}`} className={`grid h-12 place-items-center rounded-lg text-base font-semibold ${masteryTone(value)}`}>
                        {unavailable ? '—' : key === 'attempt_count' ? integer(item.attempt_count) : `${Math.round(clampRatio(value) * 100)}%`}
                      </span>
                    );
                  })}
                </div>
              ))}
            </div>
          </div>
        </div>
      ) : (
        <div className="mt-5 rounded-2xl bg-slate-50 px-5 py-8 text-center text-sm font-normal text-slate-500">完成练习后生成知识点掌握情况。</div>
      )}
    </section>
  );
}
