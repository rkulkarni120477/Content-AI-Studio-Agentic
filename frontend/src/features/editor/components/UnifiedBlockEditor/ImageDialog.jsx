import { useEffect, useRef, useState } from 'react';
import Button from '@components/common/Button/Button';
import styles from './ImageDialog.module.scss';

const ACCEPT = 'image/png,image/jpeg,image/gif,image/webp';
const ALIGN_OPTIONS = [
  { value: '', label: 'Default' },
  { value: 'left', label: 'Left' },
  { value: 'center', label: 'Center' },
  { value: 'right', label: 'Right' },
];

/**
 * Insert / edit an editor image. Supports uploading a file (via `onUpload`,
 * which returns a URL) or entering an approved URL, plus alt text, width, and
 * alignment. `initial` (when editing a selected image) prefills the fields.
 */
export default function ImageDialog({ open, editing, initial, onUpload, onSubmit, onRemove, onClose }) {
  const [src, setSrc] = useState('');
  const [alt, setAlt] = useState('');
  const [width, setWidth] = useState('');
  const [align, setAlign] = useState('');
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState(null);
  const dialogRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    setSrc(initial?.src || '');
    setAlt(initial?.alt || '');
    setWidth(initial?.width || '');
    setAlign(initial?.align || '');
    setError(null);
    setProgress(0);
    setUploading(false);
  }, [open, initial]);

  // Focus the dialog for keyboard users, and close on Escape.
  useEffect(() => {
    if (!open) return undefined;
    dialogRef.current?.focus();
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  async function handleFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setError(null);
    setUploading(true);
    setProgress(0);
    try {
      const url = await onUpload(file, setProgress);
      setSrc(url);
      if (!alt) setAlt(file.name.replace(/\.[^.]+$/, ''));
    } catch (err) {
      setError(err?.message || 'Upload failed. Please try again.');
    } finally {
      setUploading(false);
    }
  }

  function submit() {
    if (!src.trim()) {
      setError('Provide an image URL or upload a file first.');
      return;
    }
    onSubmit({
      src: src.trim(),
      alt: alt.trim(),
      width: width.trim() || null,
      align: align || null,
    });
  }

  return (
    <div className={styles.overlay} role="presentation" onMouseDown={onClose}>
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label={editing ? 'Edit image' : 'Insert image'}
        tabIndex={-1}
        ref={dialogRef}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h3 className={styles.title}>{editing ? 'Edit image' : 'Insert image'}</h3>

        <label className={styles.label} htmlFor="img-upload">Upload an image</label>
        <input id="img-upload" type="file" accept={ACCEPT} onChange={handleFile} disabled={uploading} />
        {uploading && <p className={styles.info}>Uploading… {progress}%</p>}

        <label className={styles.label} htmlFor="img-url">…or image URL</label>
        <input
          id="img-url"
          className={styles.input}
          type="url"
          placeholder="https://…"
          value={src}
          onChange={(e) => setSrc(e.target.value)}
        />

        <label className={styles.label} htmlFor="img-alt">Alternative text</label>
        <input
          id="img-alt"
          className={styles.input}
          type="text"
          placeholder="Describe the image for screen readers"
          value={alt}
          onChange={(e) => setAlt(e.target.value)}
        />
        {!alt.trim() && <p className={styles.warn}>⚠️ No alt text — add a description for accessibility.</p>}

        <div className={styles.row}>
          <div className={styles.row__col}>
            <label className={styles.label} htmlFor="img-width">Width (px or %)</label>
            <input
              id="img-width"
              className={styles.input}
              type="text"
              placeholder="e.g. 400 or 60%"
              value={width}
              onChange={(e) => setWidth(e.target.value)}
            />
          </div>
          <div className={styles.row__col}>
            <label className={styles.label} htmlFor="img-align">Alignment</label>
            <select
              id="img-align"
              className={styles.input}
              value={align}
              onChange={(e) => setAlign(e.target.value)}
            >
              {ALIGN_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
        </div>

        {error && <p className={styles.error}>⚠️ {error}</p>}

        <div className={styles.actions}>
          {editing && (
            <Button variant="ghost" size="sm" onClick={onRemove}>🗑 Remove</Button>
          )}
          <span className={styles.actions__spacer} />
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button variant="primary" size="sm" onClick={submit} disabled={uploading}>
            {editing ? 'Update' : 'Insert'}
          </Button>
        </div>
      </div>
    </div>
  );
}
