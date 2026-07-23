import { useEffect, useMemo, useRef, useState } from 'react';
import Button from '@components/common/Button/Button';
import { toYouTubeEmbedUrl } from './youtube';
import styles from './ImageDialog.module.scss';

/**
 * Insert a YouTube video embed. Accepts a watch / youtu.be / shorts / embed URL,
 * validates it as YouTube, previews it before insertion, and returns the
 * canonical embed URL.
 */
export default function EmbedDialog({ open, onSubmit, onClose }) {
  const [input, setInput] = useState('');
  const [touched, setTouched] = useState(false);
  const dialogRef = useRef(null);

  const embedUrl = useMemo(() => toYouTubeEmbedUrl(input), [input]);
  const invalid = touched && input.trim() && !embedUrl;

  useEffect(() => {
    if (!open) return;
    setInput('');
    setTouched(false);
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    dialogRef.current?.focus();
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  function submit() {
    setTouched(true);
    if (!embedUrl) return;
    onSubmit({ src: embedUrl });
  }

  return (
    <div className={styles.overlay} role="presentation" onMouseDown={onClose}>
      <div
        className={styles.dialog}
        role="dialog"
        aria-modal="true"
        aria-label="Insert video"
        tabIndex={-1}
        ref={dialogRef}
        onMouseDown={(e) => e.stopPropagation()}
      >
        <h3 className={styles.title}>Insert video</h3>

        <label className={styles.label} htmlFor="embed-url">YouTube URL</label>
        <input
          id="embed-url"
          className={styles.input}
          type="url"
          placeholder="https://www.youtube.com/watch?v=…"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onBlur={() => setTouched(true)}
        />
        <p className={styles.info}>Only YouTube videos are supported.</p>
        {invalid && <p className={styles.error}>⚠️ That doesn’t look like a YouTube link.</p>}

        {embedUrl && (
          <div className={styles.embedPreview}>
            <iframe
              src={embedUrl}
              title="Video preview"
              frameBorder="0"
              allow="accelerometer; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
            />
          </div>
        )}

        <div className={styles.actions}>
          <span className={styles.actions__spacer} />
          <Button variant="ghost" size="sm" onClick={onClose}>Cancel</Button>
          <Button variant="primary" size="sm" onClick={submit} disabled={!embedUrl}>Insert</Button>
        </div>
      </div>
    </div>
  );
}
