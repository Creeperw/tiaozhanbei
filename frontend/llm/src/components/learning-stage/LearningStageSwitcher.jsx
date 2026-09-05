import React, { useEffect, useMemo, useRef, useState } from 'react';

function stageId(stage) {
  return String(stage?.node_id || stage?.membership_id || stage?.id || '');
}

function stageTitle(stage, index) {
  return String(stage?.title || stage?.name || `第 ${index + 1} 阶段`);
}

function initialStageIndex(stages, currentStageId) {
  const requested = stages.findIndex((stage) => stageId(stage) === currentStageId);
  if (requested >= 0) return requested;
  const active = stages.findIndex((stage) => ['in_progress', 'current'].includes(stage?.status));
  if (active >= 0) return active;
  const firstIncomplete = stages.findIndex((stage) => stage?.status !== 'completed');
  return firstIncomplete >= 0 ? firstIncomplete : Math.max(0, stages.length - 1);
}

export default function LearningStageSwitcher({
  stages = [],
  currentStageId = '',
  onCurrentStageChange,
  onNavigate,
}) {
  const normalizedStages = useMemo(
    () => (Array.isArray(stages) ? stages.filter((stage) => stageId(stage)) : []),
    [stages],
  );
  const [open, setOpen] = useState(false);
  const [mobileSheet, setMobileSheet] = useState(() => (
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(max-width: 740px)').matches
  ));
  const rootRef = useRef(null);
  const dialogRef = useRef(null);
  const closeTimerRef = useRef(null);
  const suppressFocusOpenRef = useRef(false);
  const currentIndex = initialStageIndex(normalizedStages, currentStageId);
  const currentStage = normalizedStages[currentIndex];

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const mediaQuery = window.matchMedia('(max-width: 740px)');
    const updateMobileSheet = (event) => setMobileSheet(event.matches);
    mediaQuery.addEventListener?.('change', updateMobileSheet);
    return () => mediaQuery.removeEventListener?.('change', updateMobileSheet);
  }, []);

  useEffect(() => () => {
    if (closeTimerRef.current !== null) window.clearTimeout(closeTimerRef.current);
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const previousBodyOverflow = document.body.style.overflow;
    if (mobileSheet) {
      dialogRef.current?.querySelector('button')?.focus();
      document.body.style.overflow = 'hidden';
    }
    const closeOnEscape = (event) => {
      if (event.key !== 'Escape') return;
      setOpen(false);
      suppressFocusOpenRef.current = true;
      window.setTimeout(() => {
        rootRef.current?.querySelector('.learning-stage-switcher__trigger')?.focus();
        suppressFocusOpenRef.current = false;
      }, 0);
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('keydown', closeOnEscape);
      if (mobileSheet) document.body.style.overflow = previousBodyOverflow;
    };
  }, [mobileSheet, open]);

  if (!currentStage) return null;

  const cancelScheduledClose = () => {
    if (closeTimerRef.current === null) return;
    window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = null;
  };

  const schedulePreviewClose = () => {
    cancelScheduledClose();
    closeTimerRef.current = window.setTimeout(() => {
      closeTimerRef.current = null;
      setOpen(false);
    }, 120);
  };

  const closeAndRestoreFocus = () => {
    setOpen(false);
    suppressFocusOpenRef.current = true;
    window.setTimeout(() => {
      rootRef.current?.querySelector('.learning-stage-switcher__trigger')?.focus();
      suppressFocusOpenRef.current = false;
    }, 0);
  };

  const keepFocusInMobileSheet = (event) => {
    if (!mobileSheet || event.key !== 'Tab') return;
    const focusable = Array.from(dialogRef.current?.querySelectorAll('button:not([disabled])') || []);
    const first = focusable[0];
    const last = focusable.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  };

  const selectStage = (stage) => {
    onCurrentStageChange?.(stageId(stage), stage);
    closeAndRestoreFocus();
  };

  return (
    <div
      ref={rootRef}
      className="learning-stage-switcher"
      data-open={String(open)}
      onMouseEnter={() => {
        cancelScheduledClose();
        setOpen(true);
      }}
      onMouseLeave={schedulePreviewClose}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        type="button"
        className="learning-stage-switcher__trigger"
        aria-label={`当前阶段 · ${String(currentIndex + 1).padStart(2, '0')} · ${stageTitle(currentStage, currentIndex)}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onFocus={() => {
          if (!suppressFocusOpenRef.current) setOpen(true);
        }}
        onClick={() => setOpen(true)}
      >
        <span>当前阶段</span>
        <strong>{String(currentIndex + 1).padStart(2, '0')} · {stageTitle(currentStage, currentIndex)}</strong>
        <i aria-hidden="true" />
      </button>

      {open && (
        <>
          <button
            type="button"
            className="learning-stage-switcher__backdrop"
            aria-label="关闭学习阶段选择"
            onClick={closeAndRestoreFocus}
          />
          <div
            ref={dialogRef}
            className="learning-stage-switcher__dialog"
            role="dialog"
            aria-label="学习阶段选择"
            aria-modal={mobileSheet ? true : undefined}
            onMouseEnter={cancelScheduledClose}
            onKeyDown={keepFocusInMobileSheet}
          >
            <header>
              <div><span>Learning stages</span><h3>学习阶段</h3></div>
              <button
                type="button"
                className="learning-stage-switcher__close"
                aria-label="关闭学习阶段选择"
                onClick={closeAndRestoreFocus}
              >×</button>
            </header>
            <ol className="learning-stage-switcher__cards">
              {normalizedStages.map((stage, index) => {
                const state = stage?.status === 'completed'
                  ? 'completed'
                  : index === currentIndex ? 'current' : 'upcoming';
                return (
                  <li key={stageId(stage)}>
                    <button
                      type="button"
                      data-state={state}
                      aria-current={state === 'current' ? 'step' : undefined}
                      onClick={() => selectStage(stage)}
                    >
                      <span>{String(index + 1).padStart(2, '0')}</span>
                      <strong>{stageTitle(stage, index)}</strong>
                      <small>{state === 'completed' ? '已完成' : state === 'current' ? '当前阶段' : '待学习'}</small>
                    </button>
                  </li>
                );
              })}
            </ol>
            <button
              type="button"
              className="learning-stage-switcher__full-route"
              onClick={() => onNavigate?.({ page: 'practice', params: { view: 'stages' } })}
            >查看完整进阶路线</button>
          </div>
        </>
      )}
    </div>
  );
}
