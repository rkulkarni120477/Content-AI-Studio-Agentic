import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@utils/helpers';
import styles from './MultiSelect.module.scss';

/**
 * Streamlit-style multiselect dropdown (Choose options + Select all + scrollable list).
 */
export default function MultiSelect({
  label,
  hint,
  placeholder = 'Choose options',
  options = [],
  value = [],
  onChange,
  disabled = false,
  className,
}) {
  const [open, setOpen] = useState(false);
  const [menuRect, setMenuRect] = useState(null);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const menuRef = useRef(null);
  const listId = useId();
  const selectedSet = new Set(value);

  useLayoutEffect(() => {
    if (!open || !triggerRef.current) {
      setMenuRect(null);
      return;
    }
    const MARGIN = 8;
    const PREFERRED_MAX_HEIGHT = 280;
    const MIN_USABLE_HEIGHT = 120;
    const update = () => {
      const rect = triggerRef.current.getBoundingClientRect();
      const spaceBelow = window.innerHeight - rect.bottom - MARGIN;
      const spaceAbove = rect.top - MARGIN;
      // Open upward only if downward space is too cramped to be usable AND
      // there's more room above — otherwise keep the default downward open
      // and simply clamp height so it never runs past the viewport edge.
      const openUpward = spaceBelow < MIN_USABLE_HEIGHT && spaceAbove > spaceBelow;
      const available = openUpward ? spaceAbove : spaceBelow;
      const maxHeight = Math.max(MIN_USABLE_HEIGHT, Math.min(PREFERRED_MAX_HEIGHT, available));
      setMenuRect({
        left: rect.left,
        width: rect.width,
        maxHeight,
        ...(openUpward
          ? { bottom: window.innerHeight - rect.top + 4 }
          : { top: rect.bottom + 4 }),
      });
    };
    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      const inTrigger = rootRef.current?.contains(e.target);
      const inMenu = menuRef.current?.contains(e.target);
      if (!inTrigger && !inMenu) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocClick);
    return () => document.removeEventListener('mousedown', onDocClick);
  }, [open]);

  const allSelected = options.length > 0 && options.every((o) => selectedSet.has(o.value));

  function toggleOption(optValue) {
    if (selectedSet.has(optValue)) {
      onChange(value.filter((v) => v !== optValue));
    } else {
      onChange([...value, optValue]);
    }
  }

  function toggleSelectAll() {
    if (allSelected) {
      onChange([]);
    } else {
      onChange(options.map((o) => o.value));
    }
  }

  const triggerLabel = value.length === 0
    ? placeholder
    : `${value.length} selected`;

  return (
    <div className={cn(styles.field, className)} ref={rootRef}>
      {label && (
        <div className={styles.field__labelRow}>
          <label className={styles.field__label} id={`${listId}-label`}>
            {label}
          </label>
          {hint && (
            <span className={styles.field__hintIcon} title={hint} aria-label={hint}>
              ?
            </span>
          )}
        </div>
      )}
      <button
        ref={triggerRef}
        type="button"
        className={cn(styles.trigger, open && styles['trigger--open'], disabled && styles['trigger--disabled'])}
        onClick={() => !disabled && setOpen((v) => !v)}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-labelledby={label ? `${listId}-label` : undefined}
        disabled={disabled}
      >
        <span className={cn(styles.trigger__text, value.length === 0 && styles['trigger__text--placeholder'])}>
          {triggerLabel}
        </span>
        <span className={styles.trigger__chevron} aria-hidden="true">▾</span>
      </button>

      {open && menuRect && createPortal(
        <div
          ref={menuRef}
          className={styles.menu}
          role="listbox"
          aria-multiselectable="true"
          style={{
            position: 'fixed',
            top: menuRect.top,
            bottom: menuRect.bottom,
            left: menuRect.left,
            width: menuRect.width,
            maxHeight: menuRect.maxHeight,
            zIndex: 10050,
          }}
        >
          {options.length === 0 ? (
            <p className={styles.menu__empty}>No options available</p>
          ) : (
            <>
              <label className={styles.menu__selectAll}>
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                />
                Select all
              </label>
              <div className={styles.menu__divider} />
              <ul className={styles.menu__list}>
                {options.map((opt) => (
                  <li key={opt.value}>
                    <label className={styles.menu__option}>
                      <input
                        type="checkbox"
                        checked={selectedSet.has(opt.value)}
                        onChange={() => toggleOption(opt.value)}
                      />
                      <span className={styles.menu__optionLabel} title={opt.label}>
                        {opt.label}
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>,
        document.body,
      )}
    </div>
  );
}
