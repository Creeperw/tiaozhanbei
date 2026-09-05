import React, { useEffect, useId } from 'react';
import { AlertCircle, LoaderCircle, X } from 'lucide-react';
import { useModalFocus } from './useModalFocus';

export function Button({ children, loading = false, disabled = false, variant = 'primary', className = '', ...props }) {
  return (
    <button
      type="button"
      className={`button button--${variant} ${className}`.trim()}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading && <LoaderCircle aria-hidden="true" className="button__spinner" size={16} />}
      <span>{children}</span>
    </button>
  );
}

export function IconButton({ label, children, className = '', ...props }) {
  return (
    <button type="button" className={`icon-button ${className}`.trim()} aria-label={label} {...props}>
      {children}
    </button>
  );
}

export function SegmentedControl({ label, value, options, onChange }) {
  return (
    <fieldset className="segmented-control">
      <legend className="sr-only">{label}</legend>
      {options.map((option) => (
        <label key={option.value} className="segmented-control__option">
          <input
            type="radio"
            name={label}
            value={option.value}
            checked={value === option.value}
            onChange={() => onChange(option.value)}
          />
          <span>{option.label}</span>
        </label>
      ))}
    </fieldset>
  );
}

export function StatusBadge({ status = 'neutral', children }) {
  return <span className="status-badge" data-status={status}>{children}</span>;
}

export function Skeleton({ label = '加载中', lines = 3 }) {
  return (
    <div className="skeleton" aria-label={label} aria-busy="true" role="status">
      {Array.from({ length: lines }, (_, index) => <span key={index} />)}
    </div>
  );
}

export function EmptyState({ title, description, action }) {
  return (
    <section className="empty-state">
      <h2>{title}</h2>
      {description && <p>{description}</p>}
      {action}
    </section>
  );
}

export function InlineError({ message, onRetry }) {
  return (
    <div className="inline-error" role="alert">
      <AlertCircle aria-hidden="true" size={18} />
      <span>{message}</span>
      {onRetry && <Button variant="secondary" onClick={onRetry}>重试</Button>}
    </div>
  );
}

export function Drawer({ open, title, onClose, children }) {
  const titleId = useId();
  const dialogRef = useModalFocus(open);
  useEffect(() => {
    if (!open || !onClose) return undefined;
    const closeOnEscape = (event) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div className="drawer-backdrop" onMouseDown={onClose}>
      <aside
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="drawer"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="drawer__header">
          <h2 id={titleId}>{title}</h2>
          {onClose && <IconButton label="关闭" data-autofocus onClick={onClose}><X aria-hidden="true" size={20} /></IconButton>}
        </header>
        <div className="drawer__body">{children}</div>
      </aside>
    </div>
  );
}
