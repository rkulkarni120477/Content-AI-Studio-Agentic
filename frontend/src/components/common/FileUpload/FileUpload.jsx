import { useRef, useState } from 'react';
import { cn } from '@utils/helpers';
import styles from './FileUpload.module.scss';

export default function FileUpload({
  accept    = '.pdf,.docx,.txt',
  multiple  = false,
  onChange,
  label     = 'Drop files here or click to browse',
  hint,
  error,
  disabled  = false,
  className,
}) {
  const inputRef   = useRef(null);
  const [dragging, setDragging] = useState(false);

  function handleFiles(files) {
    if (!files?.length) return;
    onChange?.(Array.from(files));
  }

  function onDrop(e) {
    e.preventDefault();
    setDragging(false);
    if (!disabled) handleFiles(e.dataTransfer.files);
  }

  return (
    <div className={cn(styles.upload, error && styles['upload--error'], className)}>
      <div
        className={cn(styles.dropzone, dragging && styles['dropzone--active'], disabled && styles['dropzone--disabled'])}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => !disabled && inputRef.current?.click()}
        role="button"
        tabIndex={disabled ? -1 : 0}
        onKeyDown={(e) => e.key === 'Enter' && !disabled && inputRef.current?.click()}
        aria-label={label}
      >
        <span className={styles.dropzone__icon} aria-hidden="true">📎</span>
        <span className={styles.dropzone__label}>{label}</span>
        {hint && <span className={styles.dropzone__hint}>{hint}</span>}
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        multiple={multiple}
        className={styles.input}
        onChange={(e) => handleFiles(e.target.files)}
        disabled={disabled}
        aria-hidden="true"
        tabIndex={-1}
      />
      {error && <p className={styles.error} role="alert">{error}</p>}
    </div>
  );
}
