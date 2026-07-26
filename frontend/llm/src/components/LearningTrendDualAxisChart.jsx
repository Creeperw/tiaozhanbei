import React from 'react';

const chartWidth = 640;
const chartHeight = 250;
const padding = { top: 22, right: 66, bottom: 44, left: 66 };
const ratioTicks = [0, 0.25, 0.5, 0.75, 1];

const toFiniteNumber = (value) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : 0;
};

const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));

const formatDate = (value) => {
  const text = String(value || '');
  return /^\d{4}-\d{2}-\d{2}$/.test(text) ? text.slice(5) : (text || '—');
};

const niceAxisMaximum = (value, fallback) => {
  const maximum = Math.max(0, value);
  if (maximum === 0) return fallback;
  const padded = maximum * 1.12;
  const magnitude = 10 ** Math.floor(Math.log10(padded));
  const step = magnitude / 2;
  return Math.ceil(padded / step) * step;
};

const linePoints = (items, valueFor, xFor, yFor) => items
  .map((item, index) => `${xFor(index)},${yFor(valueFor(item))}`)
  .join(' ');

export default function LearningTrendDualAxisChart({ series = [] }) {
  const data = Array.isArray(series)
    ? series.map((item) => ({
      date: String(item?.date || ''),
      minutes: Math.max(0, toFiniteNumber(item?.focus_minutes)),
      completionRate: clamp(toFiniteNumber(item?.task_completion_rate), 0, 1),
    }))
    : [];
  const latest = data.at(-1) || { minutes: 0, completionRate: 0 };
  const leftMaximum = niceAxisMaximum(Math.max(0, ...data.map((item) => item.minutes)), 10);
  const maximumCompletion = Math.max(0, ...data.map((item) => item.completionRate));
  const completionMaximum = maximumCompletion > 0
    ? Math.min(1, Math.ceil(maximumCompletion * 1.08 * 10) / 10)
    : 0.1;
  const innerWidth = chartWidth - padding.left - padding.right;
  const innerHeight = chartHeight - padding.top - padding.bottom;
  const xFor = (index) => padding.left + (data.length <= 1 ? innerWidth / 2 : (index / (data.length - 1)) * innerWidth);
  const yForMinutes = (value) => padding.top + innerHeight - (clamp(value, 0, leftMaximum) / leftMaximum) * innerHeight;
  const yForCompletion = (value) => padding.top + innerHeight - (clamp(value, 0, completionMaximum) / completionMaximum) * innerHeight;
  const minutePoints = linePoints(data, (item) => item.minutes, xFor, yForMinutes);
  const completionPoints = linePoints(data, (item) => item.completionRate, xFor, yForCompletion);
  const labelStep = Math.max(1, Math.ceil(data.length / 5));
  const dateLabelIndexes = data.map((_, index) => index).filter((index) => (
    index === 0 || index === data.length - 1 || index % labelStep === 0
  ));

  return (
    <section className="rounded-[24px] border border-emerald-100 bg-white p-4 shadow-sm shadow-emerald-950/5" aria-label="每日学习趋势">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-base font-bold text-slate-950">学习趋势</div>
        </div>
        <div className="flex flex-wrap gap-1.5 text-xs font-medium">
          <span className="inline-flex items-center gap-1.5 rounded-full bg-emerald-50 px-2.5 py-1 text-emerald-800"><i className="h-2 w-2 rounded-full bg-[#16865f]" />有效学习时长</span>
          <span className="inline-flex items-center gap-1.5 rounded-full bg-orange-50 px-2.5 py-1 text-orange-800"><i className="h-2 w-2 rounded-full bg-[#d9823f]" />任务完成率</span>
        </div>
      </div>

      {data.length > 0 ? (
        <figure className="mt-3">
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500">
            <span>最新有效学习 <strong className="font-mono text-sm text-emerald-800">{latest.minutes} 分钟</strong></span>
            <span>最新任务完成率 <strong className="font-mono text-sm text-orange-800">{Math.round(latest.completionRate * 100)}%</strong></span>
          </div>
          <svg
            viewBox={`0 0 ${chartWidth} ${chartHeight}`}
            className="mt-2 h-auto w-full"
            role="img"
            aria-label={`有效学习时长与任务完成率趋势图，最新有效学习时长 ${latest.minutes} 分钟，最新任务完成率 ${Math.round(latest.completionRate * 100)}%`}
          >
            <title>有效学习时长与任务完成率趋势</title>
            {ratioTicks.map((tick) => {
              const y = padding.top + innerHeight - tick * innerHeight;
              return (
                <g key={tick}>
                  <line x1={padding.left} x2={chartWidth - padding.right} y1={y} y2={y} stroke="#e2ebe7" strokeDasharray={tick === 0 ? '0' : '4 5'} />
                  <text x={padding.left - 12} y={y + 5} fill="#668078" fontSize="14" textAnchor="end">{Math.round(leftMaximum * tick)}</text>
                  <text x={chartWidth - padding.right + 12} y={y + 5} fill="#a86b32" fontSize="14">{Math.round(completionMaximum * tick * 100)}%</text>
                </g>
              );
            })}
            <line x1={padding.left} x2={padding.left} y1={padding.top} y2={chartHeight - padding.bottom} stroke="#cfe0d8" />
            <line x1={chartWidth - padding.right} x2={chartWidth - padding.right} y1={padding.top} y2={chartHeight - padding.bottom} stroke="#ead7c5" />
            <line x1={padding.left} x2={chartWidth - padding.right} y1={chartHeight - padding.bottom} y2={chartHeight - padding.bottom} stroke="#cfe0d8" />
            <text x={17} y={chartHeight / 2} fill="#34705c" fontSize="15" textAnchor="middle" transform={`rotate(-90 17 ${chartHeight / 2})`}>有效学习时长（分钟）</text>
            <text x={chartWidth - 17} y={chartHeight / 2} fill="#a86b32" fontSize="15" textAnchor="middle" transform={`rotate(90 ${chartWidth - 17} ${chartHeight / 2})`}>任务完成率（%）</text>
            <polyline fill="none" points={minutePoints} stroke="#16865f" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3.5" />
            <polyline fill="none" points={completionPoints} stroke="#d9823f" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3.5" />
            {data.map((item, index) => (
              <g key={`${item.date}-${index}`}>
                <circle cx={xFor(index)} cy={yForMinutes(item.minutes)} fill="#16865f" r="3.75" />
                <circle cx={xFor(index)} cy={yForCompletion(item.completionRate)} fill="#d9823f" r="3.75" />
                <title>{`${item.date || '未标注日期'}：有效学习 ${item.minutes} 分钟，任务完成率 ${Math.round(item.completionRate * 100)}%`}</title>
              </g>
            ))}
            {dateLabelIndexes.map((index) => (
              <text
                key={`${data[index].date}-${index}`}
                x={xFor(index)}
                y={chartHeight - 20}
                fill="#64748b"
                fontSize="14"
                textAnchor={index === 0 ? 'start' : (index === data.length - 1 ? 'end' : 'middle')}
              >
                {formatDate(data[index].date)}
              </text>
            ))}
            <text x={chartWidth / 2} y={chartHeight - 2} fill="#64748b" fontSize="14" textAnchor="middle">日期</text>
          </svg>
        </figure>
      ) : (
        <div className="mt-3 flex min-h-40 items-center justify-center rounded-2xl bg-slate-50 px-4 text-sm text-slate-500">暂无后端学习趋势数据</div>
      )}
    </section>
  );
}
