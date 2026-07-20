import React from 'react';
import { Home } from 'lucide-react';

export default function HomeButton({ onClick, className = '', label = '返回主页' }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label={label}
      className={[
        'inline-flex items-center gap-2 rounded-full border border-emerald-200 bg-white px-3 py-2 text-sm font-medium text-emerald-900 shadow-sm shadow-emerald-100/50 transition hover:bg-emerald-50',
        'sm:px-4',
        className,
      ].join(' ')}
    >
      <Home size={16} />
      <span className="hidden sm:inline">{label}</span>
      <span className="sm:hidden">主页</span>
    </button>
  );
}
