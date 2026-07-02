import { cn } from '@utils/helpers';
import styles from './Pagination.module.scss';

export default function Pagination({ current, total, onChange, maxVisible = 7 }) {
  if (total <= 1) return null;

  function pages() {
    if (total <= maxVisible) return range(1, total);
    const half  = Math.floor(maxVisible / 2);
    let start   = Math.max(1, current - half);
    let end     = Math.min(total, start + maxVisible - 1);
    if (end - start < maxVisible - 1) start = Math.max(1, end - maxVisible + 1);
    const ps = range(start, end);
    if (start > 2)     ps.unshift('…', 1);
    else if (start === 2) ps.unshift(1);
    if (end < total - 1)  ps.push('…', total);
    else if (end === total - 1) ps.push(total);
    return ps;
  }

  return (
    <nav className={styles.pagination} aria-label="Pagination">
      <button
        className={cn(styles.btn, styles['btn--nav'])}
        onClick={() => onChange(current - 1)}
        disabled={current <= 1}
        aria-label="Previous page"
      >
        ‹
      </button>

      {pages().map((p, i) =>
        p === '…' ? (
          <span key={`ellipsis-${i}`} className={styles.ellipsis} aria-hidden="true">…</span>
        ) : (
          <button
            key={p}
            className={cn(styles.btn, p === current && styles['btn--active'])}
            onClick={() => onChange(p)}
            aria-label={`Page ${p}`}
            aria-current={p === current ? 'page' : undefined}
          >
            {p}
          </button>
        ),
      )}

      <button
        className={cn(styles.btn, styles['btn--nav'])}
        onClick={() => onChange(current + 1)}
        disabled={current >= total}
        aria-label="Next page"
      >
        ›
      </button>
    </nav>
  );
}

function range(start, end) {
  return Array.from({ length: end - start + 1 }, (_, i) => start + i);
}
