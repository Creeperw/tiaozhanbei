import React from 'react';

const chartWidth = 640;
const chartHeight = 340;
const padding = { top: 34, right: 76, bottom: 58, left: 76 };
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

const minuteAxisMaximum = (value) => {
  const maximum = Math.max(0, value);
  if (maximum <= 30) return 30;
  if (maximum <= 60) return 60;
  if (maximum <= 90) return 90;
  if (maximum <= 120) return 120;
  if (maximum <= 180) return 180;
  return Math.ceil(maximum / 60) * 60;
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
  const leftMaximum = minuteAxisMaximum(Math.max(0, ...data.map((item) => item.minutes)));
  const innerWidth = chartWidth - padding.left - padding.right;
  const innerHeight = chartHeight - padding.top - padding.bottom;
  const xFor = (index) => padding.left + (data.length <= 1 ? innerWidth / 2 : (index / (data.length - 1)) * innerWidth);
  const yForMinutes = (value) => padding.top + innerHeight - (clamp(value, 0, leftMaximum) / leftMaximum) * innerHeight;
  const yForCompletion = (value) => padding.top + innerHeight - clamp(value, 0, 1) * innerHeight;
  const completionPoints = linePoints(data, (item) => item.completionRate, xFor, yForCompletion);
  const barWidth = Math.min(22, Math.max(8, innerWidth / Math.max(data.length * 1.8, 1)));
  const labelStep = Math.max(1, Math.ceil(data.length / 5));
  const dateLabelIndexes = data.map((_, index) => index).filter((index) => (
    index === 0 || index === data.length - 1 || index % labelStep === 0
  ));

  return (
    <section className="rounded-[28px] border border-emerald-100 bg-white p-5 shadow-sm shadow-emerald-950/5" aria-label="每日学习趋势">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-lg font-bold text-slate-950">学习趋势</div>
        </div>
        <div className="flex flex-wrap gap-2 text-sm font-medium">
          <span className="inline-flex items-center gap-2 rounded-full bg-emerald-50 px-3 py-1.5 text-emerald-800"><i className="h-2.5 w-2.5 rounded-full bg-emerald-600" />有效学习时长</span>
          <span className="inline-flex items-center gap-2 rounded-full bg-sky-50 px-3 py-1.5 text-sky-800"><i className="h-2.5 w-2.5 rounded-full bg-sky-600" />任务完成率</span>
        </div>
      </div>

      {data.length > 0 ? (
        <figure className="mt-4">
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm text-slate-500">
            <span>最新有效学习 <strong className="font-mono text-base text-emerald-800">{latest.minutes} 分钟</strong></span>
            <span>最新任务完成率 <strong className="font-mono text-base text-sky-800">{Math.round(latest.completionRate * 100)}%</strong></span>
          </div>
          <svg
            viewBox={`0 0 ${chartWidth} ${chartHeight}`}
            className="mt-3 h-auto w-full"
            role="img"
            aria-label={`有效学习时长柱状图与任务完成率折线图，最新有效学习时长 ${latest.minutes} 分钟，最新任务完成率 ${Math.round(latest.completionRate * 100)}%`}
          >
            <title>有效学习时长柱状图与任务完成率折线图</title>
            {ratioTicks.map((tick) => {
              const y = padding.top + innerHeight - tick * innerHeight;
              return (
                <g key={tick}>
                  <line x1={padding.left} x2={chartWidth - padding.right} y1={y} y2={y} stroke="#e2ebe7" strokeDasharray={tick === 0 ? '0' : '4 5'} />
                  <text x={padding.left - 12} y={y + 5} fill="#668078" fontSize="14" textAnchor="end">{Math.round(leftMaximum * tick)}</text>
                  <text x={chartWidth - padding.right + 12} y={y + 5} fill="#5e84a2" fontSize="14">{Math.round(tick * 100)}%</text>
                </g>
              );
            })}
            <line x1={padding.left} x2={padding.left} y1={padding.top} y2={chartHeight - padding.bottom} stroke="#cfe0d8" />
            <line x1={chartWidth - padding.right} x2={chartWidth - padding.right} y1={padding.top} y2={chartHeight - padding.bottom} stroke="#d9e7f3" />
            <line x1={padding.left} x2={chartWidth - padding.right} y1={chartHeight - padding.bottom} y2={chartHeight - padding.bottom} stroke="#cfe0d8" />
            <text x={17} y={chartHeight / 2} fill="#34705c" fontSize="15" textAnchor="middle" transform={`rotate(-90 17 ${chartHeight / 2})`}>有效学习时长（分钟）</text>
            <text x={chartWidth - 17} y={chartHeight / 2} fill="#3677a8" fontSize="15" textAnchor="middle" transform={`rotate(90 ${chartWidth - 17} ${chartHeight / 2})`}>任务完成率（%）</text>
            {data.map((item, index) => {
              const x = xFor(index);
              const y = yForMinutes(item.minutes);
              const baseline = chartHeight - padding.bottom;
              return (
                <rect
                  key={`minutes-bar-${item.date}-${index}`}
                  x={x - barWidth / 2}
                  y={y}
                  width={barWidth}
                  height={Math.max(0, baseline - y)}
                  rx="3"
                  fill="#3f986c"
                  fillOpacity=".9"
                />
              );
            })}
            <polyline fill="none" points={completionPoints} stroke="#3b82b6" strokeLinecap="round" strokeLinejoin="round" strokeWidth="3.5" />
            {data.map((item, index) => (
              <g key={`${item.date}-${index}`}>
                <circle cx={xFor(index)} cy={yForCompletion(item.completionRate)} fill="#3b82b6" r="4.75" />
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
        <div className="mt-3 flex min-h-52 items-center justify-center rounded-2xl bg-slate-50 px-4 text-sm text-slate-500">暂无后端学习趋势数据</div>
      )}
    </section>
  );
}
