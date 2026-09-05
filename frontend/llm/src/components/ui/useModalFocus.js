import { useEffect, useRef } from 'react';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

export function useModalFocus(open) {
  const dialogRef = useRef(null);
  const previousFocusRef = useRef(null);

  useEffect(() => {
    if (!open || !dialogRef.current) return undefined;

    previousFocusRef.current = document.activeElement;
    const dialog = dialogRef.current;
    const focusable = () => Array.from(dialog.querySelectorAll(FOCUSABLE_SELECTOR));
    const initialTarget = dialog.querySelector('[data-autofocus]') || focusable()[0] || dialog;
    initialTarget.focus();

    const trapFocus = (event) => {
      if (event.key !== 'Tab') return;
      const targets = focusable();
      if (targets.length === 0) {
        event.preventDefault();
        dialog.focus();
        return;
      }
      const first = targets[0];
      const last = targets[targets.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    dialog.addEventListener('keydown', trapFocus);
    return () => {
      dialog.removeEventListener('keydown', trapFocus);
      if (previousFocusRef.current?.isConnected) previousFocusRef.current.focus();
    };
  }, [open]);

  return dialogRef;
}
