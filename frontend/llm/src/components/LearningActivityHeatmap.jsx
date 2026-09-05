import React from 'react';

const MAX_WEEKS = 12;
const WEEKDAY_LABELS = ['一', '二', '三', '四', '五', '六', '日'];

const toDateKey = (value) => String(value || '').slice(0, 10);

const parseLocalDate = (value) => {
  const key = toDateKey(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return null;
  const [year, month, day] = key.split('-').map(Number);
  const parsed = new Date(year, month - 1, day);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const formatMonth = (value) => {
  const date = parseLocalDate(value);
  return date ? `${date.getMonth() + 1}月` : '';
};

const activityScore = (item) => {
  const minutes = Math.max(0, Number(item?.focus_minutes) || 0);
  const completion = Math.max(0, Math.min(1, Number(item?.task_completion_rate) || 0));
  const login = Math.max(0, Number(item?.login_days) || 0);
  return Math.min(1, (Math.min(minutes / 60, 1) * 0.56) + (completion * 0.34) + (login ? 0.1 : 0));
};

const activityTone = (score) => {
  if (score >= 0.76) return 'bg-[#A5D6A7]';
  if (score >= 0.52) return 'bg-[#C8E6C9]';
  if (score >= 0.28) return 'bg-[#D0F0E0]';
  if (score > 0) return 'bg-[#E8F5E9]';
  return 'bg-[#F0F9F4]';
};

const weekStart = (date) => {
  const result = new Date(date);
  const weekday = result.getDay() || 7;
  result.setDate(result.getDate() - weekday + 1);
  result.setHours(0, 0, 0, 0);
  return result;
};

const addDays = (date, count) => {
  const result = new Date(date);
  result.setDate(result.getDate() + count);
  return result;
};

const formatKey = (date) => [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');

export default function LearningActivityHeatmap({ series = [] }) {
  const itemByDate = new Map((Array.isArray(series) ? series : [])
    .filter((item) => parseLocalDate(item?.date))
    .map((item) => [toDateKey(item.date), item]));
  const latestDate = Array.from(itemByDate.keys()).sort().at(-1);
  const endDate = parseLocalDate(latestDate) || new Date();
  const endWeek = weekStart(endDate);
  const startWeek = addDays(endWeek, -7 * (MAX_WEEKS - 1));
  const weeks = Array.from({ length: MAX_WEEKS }, (_, index) => addDays(startWeek, index * 7));
  const totalActiveDays = Array.from(itemByDate.values()).filter((item) => activityScore(item) > 0).length;

  return (
    <section className="rounded-[24px] border border-emerald-100 bg-white p-4 shadow-sm shadow-emerald-950/5" aria-label="学习活跃度">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-base font-bold text-slate-950">学习活跃度</div>
        </div>
        <span className="rounded-full bg-emerald-50 px-2.5 py-1 text-xs font-medium text-emerald-800">{totalActiveDays} 个活跃日</span>
      </div>
      <div className="mt-3 min-w-0">
        <div className="grid w-full min-w-0 grid-cols-[20px_repeat(12,minmax(0,1fr))] gap-0.5 sm:grid-cols-[24px_repeat(12,minmax(0,1fr))] sm:gap-1">
          <div />
          {weeks.map((week, index) => (
            <div key={formatKey(week)} className="h-5 min-w-0 overflow-hidden whitespace-nowrap text-center text-[10px] leading-5 text-slate-400 sm:text-xs">{index === 0 || week.getDate() <= 7 ? formatMonth(formatKey(week)) : ''}</div>
          ))}
          {WEEKDAY_LABELS.map((weekday, dayIndex) => (
            <React.Fragment key={weekday}>
              <div className="flex h-5 min-w-0 items-center text-[10px] text-slate-400 sm:text-xs">{dayIndex % 2 ? '' : weekday}</div>
              {weeks.map((week) => {
                const date = addDays(week, dayIndex);
                const key = formatKey(date);
                const item = itemByDate.get(key);
                const score = activityScore(item);
                const title = item
                  ? `${key}：有效学习 ${Math.max(0, Number(item.focus_minutes) || 0)} 分钟，任务完成率 ${Math.round(Math.max(0, Math.min(1, Number(item.task_completion_rate) || 0)) * 100)}%`
                  : `${key}：暂无学习记录`;
                return <span key={key} title={title} aria-label={title} className={`h-5 min-w-0 rounded-[4px] ${activityTone(score)}`} />;
              })}
            </React.Fragment>
          ))}
        </div>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2 text-xs text-slate-400">
        <span>低</span>
        {['bg-[#F0F9F4]', 'bg-[#E8F5E9]', 'bg-[#D0F0E0]', 'bg-[#C8E6C9]', 'bg-[#A5D6A7]'].map((tone) => <i key={tone} className={`h-3.5 w-3.5 rounded-[3px] ${tone}`} />)}
        <span>高</span>
      </div>
    </section>
  );
}
