import React, { useEffect, useRef, useState } from 'react';
import { DEFAULT_LEARNING_STAGES } from './learningStageModel';

const LEGACY_STAGE_IDS = {
  'classic-study': 'classics',
};

function normalizeStageId(stageId) {
  const normalized = LEGACY_STAGE_IDS[stageId] || stageId;
  return DEFAULT_LEARNING_STAGES.some((stage) => stage.id === normalized)
    ? normalized
    : 'classics';
}

export default function LearningStageSwitcher({
  currentStageId,
  onCurrentStageChange,
  onNavigate,
}) {
  const [open, setOpen] = useState(false);
  const [internalStageId, setInternalStageId] = useState(() => normalizeStageId(currentStageId));
  const [mobileSheet, setMobileSheet] = useState(() => (
    typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(max-width: 767px)').matches
  ));
  const rootRef = useRef(null);
  const dialogRef = useRef(null);
  const closeTimerRef = useRef(null);
  const suppressFocusOpenRef = useRef(false);
  const normalizedStageId = normalizeStageId(currentStageId || internalStageId);
  const currentIndex = Math.max(
    0,
    DEFAULT_LEARNING_STAGES.findIndex((stage) => stage.id === normalizedStageId),
  );
  const currentStage = DEFAULT_LEARNING_STAGES[currentIndex];

  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return undefined;
    const mediaQuery = window.matchMedia('(max-width: 767px)');
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
      if (event.key === 'Escape') {
        setOpen(false);
        suppressFocusOpenRef.current = true;
        window.setTimeout(() => {
          rootRef.current?.querySelector('.learning-stage-switcher__trigger')?.focus();
          suppressFocusOpenRef.current = false;
        }, 0);
      }
    };
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('keydown', closeOnEscape);
      if (mobileSheet) document.body.style.overflow = previousBodyOverflow;
    };
  }, [mobileSheet, open]);

  const cancelScheduledClose = () => {
    if (closeTimerRef.current === null) return;
    window.clearTimeout(closeTimerRef.current);
    closeTimerRef.current = null;
  };

  const openPreview = () => {
    cancelScheduledClose();
    setOpen(true);
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

  const selectStage = (stageId) => {
    const nextStageId = normalizeStageId(stageId);
    setInternalStageId(nextStageId);
    onCurrentStageChange?.(nextStageId);
    closeAndRestoreFocus();
  };

  return (
    <div
      ref={rootRef}
      className="learning-stage-switcher"
      data-open={String(open)}
      onMouseEnter={openPreview}
      onMouseLeave={schedulePreviewClose}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        type="button"
        className="learning-stage-switcher__trigger"
        aria-label={`当前阶段 · ${String(currentIndex + 1).padStart(2, '0')} · ${currentStage.title}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onFocus={() => {
          if (!suppressFocusOpenRef.current) setOpen(true);
        }}
        onClick={() => setOpen(true)}
      >
        <span>当前阶段</span>
        <strong>{String(currentIndex + 1).padStart(2, '0')} · {currentStage.title}</strong>
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
              <div>
                <span>Learning stages</span>
                <h3>学习阶段</h3>
              </div>
              <button
                type="button"
                className="learning-stage-switcher__close"
                aria-label="关闭学习阶段选择"
                onClick={closeAndRestoreFocus}
              >
                ×
              </button>
            </header>
            <ol className="learning-stage-switcher__cards">
              {DEFAULT_LEARNING_STAGES.map((stage, index) => {
                const state = index < currentIndex ? 'completed' : index === currentIndex ? 'current' : 'upcoming';
                return (
                  <li key={stage.id}>
                    <button
                      type="button"
                      data-state={state}
                      aria-current={state === 'current' ? 'step' : undefined}
                      onClick={() => selectStage(stage.id)}
                    >
                      <span>{String(index + 1).padStart(2, '0')}</span>
                      <strong>{stage.title}</strong>
                      <small>{state === 'completed' ? '已完成' : state === 'current' ? '当前阶段' : '待解锁'}</small>
                    </button>
                  </li>
                );
              })}
            </ol>
            <button
              type="button"
              className="learning-stage-switcher__full-route"
              onClick={() => onNavigate?.({ page: 'practice', params: { view: 'stages' } })}
            >
              查看完整进阶路线
            </button>
          </div>
        </>
      )}
    </div>
  );
}
