import React from 'react';

export default function PageLoadingSpinner({ className = '', label = '正在加载' }) {
  return (
    <div
      className={`page-loading-spinner${className ? ` ${className}` : ''}`}
      role="status"
      aria-label={label}
    >
      <span className="page-loading-spinner__ring" aria-hidden="true" />
    </div>
  );
}
