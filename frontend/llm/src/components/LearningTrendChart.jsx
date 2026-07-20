import React from 'react';

const chartWidth = 320;
const chartHeight = 136;
const padding = { top: 12, right: 12, bottom: 28, left: 28 };

const chartPoints = (values) => {
  if (!values.length) return '';
  const max = Math.max(1, ...values);
  const innerWidth = chartWidth - padding.left - padding.right;
  const innerHeight = chartHeight - padding.top - padding.bottom;
  return values.map((value, index) => {
    const x = padding.left + (values.length === 1 ? innerWidth / 2 : (index / (values.length - 1)) * innerWidth);
    const y = padding.top + innerHeight - (value / max) * innerHeight;
    return `${x},${y}`;
  }).join(' ');
};

export default function LearningTrendChart({ chart }) {
  const values = chart.values || [];
  const latestValue = values.at(-1) ?? 0;
  const points = chartPoints(values);

  return (
    <section className="rounded-2xl border border-emerald-100 bg-white p-4 shadow-sm shadow-emerald-100/50">
      <div className="flex items-baseline justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-900">{chart.label}</h3>
        <span className="text-sm font-semibold text-emerald-800">{latestValue}{chart.suffix}</span>
      </div>
      {values.length > 0 ? (
        <svg viewBox={`0 0 ${chartWidth} ${chartHeight}`} className="mt-4 h-36 w-full" role="img" aria-label={`${chart.label}趋势图`}>
          <line x1={padding.left} x2={chartWidth - padding.right} y1={chartHeight - padding.bottom} y2={chartHeight - padding.bottom} stroke="#bbf7d0" />
          <polyline fill="none" points={points} stroke="#059669" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3" />
          {values.map((value, index) => {
            const [x, y] = points.split(' ')[index].split(',');
            return <circle key={`${chart.dates[index]}-${value}`} cx={x} cy={y} fill="#047857" r="3.5" />;
          })}
          {chart.dates.filter((_, index) => index === 0 || index === chart.dates.length - 1).map((date, index) => (
            <text key={date} x={index === 0 ? padding.left : chartWidth - padding.right} y={chartHeight - 8} fill="#64748b" fontSize="10" textAnchor={index === 0 ? 'start' : 'end'}>{date}</text>
          ))}
        </svg>
      ) : (
        <div className="mt-4 flex h-36 items-center justify-center rounded-xl bg-emerald-50/60 text-sm text-emerald-900">暂无该时段的学习记录</div>
      )}
    </section>
  );
}
