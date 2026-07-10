import { useEffect, useLayoutEffect, useRef, useState } from 'react';

/**
 * Scrollable single-select. Caps open-list height (native <select> cannot).
 * When fitContent is true, min-width matches the widest option so the
 * closed control and open menu share the same width.
 */
export default function CompactSelect({
  id,
  value,
  onChange,
  options,
  disabled = false,
  title,
  className = '',
  fitContent = false,
  'aria-label': ariaLabel,
}) {
  const [open, setOpen] = useState(false);
  const [minWidth, setMinWidth] = useState(undefined);
  const rootRef = useRef(null);
  const sizerRef = useRef(null);
  const selected = options.find((o) => o.value === value) || options[0];

  useLayoutEffect(() => {
    if (!fitContent || !sizerRef.current) {
      setMinWidth(undefined);
      return;
    }
    const next = Math.ceil(sizerRef.current.scrollWidth);
    setMinWidth(next > 0 ? next : undefined);
  }, [fitContent, options]);

  useEffect(() => {
    if (!open) return undefined;
    function onDoc(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) setOpen(false);
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  useEffect(() => {
    if (disabled) setOpen(false);
  }, [disabled]);

  const rootClass = [
    'compact-select',
    fitContent ? 'compact-select--fit' : '',
    open ? 'is-open' : '',
    disabled ? 'is-disabled' : '',
    className,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div
      className={rootClass}
      ref={rootRef}
      title={title}
      style={fitContent && minWidth ? { minWidth: `${minWidth}px` } : undefined}
    >
      {fitContent && (
        <div className="compact-select__sizer" ref={sizerRef} aria-hidden="true">
          {options.map((o) => (
            <span key={o.value === '' ? '__all__' : String(o.value)}>{o.label}</span>
          ))}
        </div>
      )}
      <button
        id={id}
        type="button"
        className="compact-select__trigger"
        aria-label={ariaLabel}
        aria-expanded={open}
        aria-haspopup="listbox"
        disabled={disabled}
        onClick={() => {
          if (!disabled) setOpen((v) => !v);
        }}
      >
        <span className="compact-select__label">{selected?.label ?? ''}</span>
        <span className="compact-select__chevron" aria-hidden="true">▾</span>
      </button>
      {open && !disabled && (
        <ul className="compact-select__menu" role="listbox">
          {options.map((o) => (
            <li key={o.value === '' ? '__all__' : String(o.value)}>
              <button
                type="button"
                role="option"
                className="compact-select__option"
                aria-selected={o.value === value}
                onClick={() => {
                  onChange(o.value);
                  setOpen(false);
                }}
              >
                {o.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
