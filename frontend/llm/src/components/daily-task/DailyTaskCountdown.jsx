import React, { useEffect, useRef, useState } from 'react';
import { Clock3 } from 'lucide-react';

function initialSeconds(timer) {
  if (Number.isInteger(timer?.remaining_seconds)) {
    return Math.max(0, timer.remaining_seconds);
  }
  const due = Date.parse(timer?.refresh_due_at || '');
  const server = Date.parse(timer?.server_time || '');
  if (!Number.isFinite(due) || !Number.isFinite(server)) return null;
  return Math.max(0, Math.ceil((due - server) / 1000));
}

function formatSeconds(total) {
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':');
}

export default function DailyTaskCountdown({ timer, onExpire, className = '' }) {
  const dueAt = timer?.refresh_due_at || '';
  const [clock, setClock] = useState(() => ({
    dueAt,
    remaining: initialSeconds(timer),
  }));
  const firedFor = useRef('');
  const remaining = clock.dueAt === dueAt ? clock.remaining : initialSeconds(timer);

  useEffect(() => {
    const initial = initialSeconds(timer);
    const startedAt = Date.now();
    const update = () => setClock({
      dueAt,
      remaining: initial === null
        ? null
        : Math.max(0, initial - Math.floor((Date.now() - startedAt) / 1000)),
    });
    const interval = window.setInterval(update, 1000);
    window.addEventListener('focus', update);
    document.addEventListener('visibilitychange', update);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener('focus', update);
      document.removeEventListener('visibilitychange', update);
    };
  }, [dueAt, timer]);

  useEffect(() => {
    if (remaining !== 0 || !dueAt || firedFor.current === dueAt) return;
    firedFor.current = dueAt;
    onExpire?.();
  }, [dueAt, onExpire, remaining]);

  if (!timer?.available || remaining === null) return null;
  return (
    <div className={`daily-task-countdown ${className}`.trim()} aria-label="今日任务自动更新时间">
      <span><Clock3 aria-hidden="true" size={13} />距自动更新</span>
      <strong>{formatSeconds(remaining)}</strong>
    </div>
  );
}
