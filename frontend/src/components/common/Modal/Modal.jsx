import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@utils/helpers';
import Button from '../Button/Button';
import styles from './Modal.module.scss';

export default function Modal({
  open,
  onClose,
  title,
  size      = 'md',
  children,
  footer,
  closable  = true,
  className,
}) {
  const overlayRef = useRef(null);
  const firstFocusRef = useRef(null);

  // Lock scroll while open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
      setTimeout(() => firstFocusRef.current?.focus(), 50);
    } else {
      document.body.style.overflow = '';
    }
    return () => { document.body.style.overflow = ''; };
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open || !closable) return;
    const handler = (e) => { if (e.key === 'Escape') onClose?.(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, closable, onClose]);

  if (!open) return null;

  return createPortal(
    <div
      className={styles.overlay}
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onClick={(e) => { if (e.target === overlayRef.current && closable) onClose?.(); }}
      ref={overlayRef}
    >
      <div
        className={cn(styles.modal, styles[`modal--${size}`], className)}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className={styles.modal__header}>
          <h2 className={styles.modal__title} ref={firstFocusRef} tabIndex={-1}>{title}</h2>
          {closable && (
            <button
              className={styles.modal__close}
              onClick={onClose}
              aria-label="Close modal"
              type="button"
            >
              ✕
            </button>
          )}
        </div>

        {/* Body */}
        <div className={styles.modal__body}>{children}</div>

        {/* Footer */}
        {footer && <div className={styles.modal__footer}>{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
