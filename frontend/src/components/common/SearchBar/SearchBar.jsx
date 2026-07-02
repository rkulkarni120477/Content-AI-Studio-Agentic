import { useRef } from 'react';
import { cn } from '@utils/helpers';
import styles from './SearchBar.module.scss';

export default function SearchBar({ value, onChange, placeholder = 'Search…', className, onClear }) {
  const inputRef = useRef(null);

  function handleClear() {
    onChange('');
    onClear?.();
    inputRef.current?.focus();
  }

  return (
    <div className={cn(styles.search, className)} role="search">
      <span className={styles.search__icon} aria-hidden="true">🔍</span>
      <input
        ref={inputRef}
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={styles.search__input}
        aria-label={placeholder}
      />
      {value && (
        <button
          type="button"
          className={styles.search__clear}
          onClick={handleClear}
          aria-label="Clear search"
        >
          ✕
        </button>
      )}
    </div>
  );
}
