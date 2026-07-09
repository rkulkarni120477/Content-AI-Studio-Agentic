import { useEffect, useRef, useState } from 'react';

export default function TeamMultiSelect({ options, value, onChange }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    function handleOutside(e) {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleOutside);
    return () => document.removeEventListener('mousedown', handleOutside);
  }, []);

  const selectedNames = options.filter((t) => value.includes(t.id)).map((t) => t.name);
  const display = selectedNames.length === 0 ? '— Select teams —' : selectedNames.join(', ');

  function toggle(id) {
    onChange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
  }

  return (
    <div className={`ms-dropdown${open ? ' is-open' : ''}`} ref={rootRef}>
      <button
        type="button"
        className="ms-dropdown-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className={selectedNames.length ? 'ms-value' : 'ms-placeholder'}>{display}</span>
      </button>
      {open && (
        <ul className="ms-dropdown-menu" role="listbox" aria-multiselectable="true">
          {options.map((t) => {
            const selected = value.includes(t.id);
            return (
              <li key={t.id} role="none">
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  className={`ms-dropdown-option${selected ? ' is-selected' : ''}`}
                  onClick={() => toggle(t.id)}
                >
                  <span className="ms-check" aria-hidden>
                    {selected ? '✓' : ''}
                  </span>
                  {t.name}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
