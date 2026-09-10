import { useEffect, useState } from 'react';
import Button from '@components/common/Button/Button';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import { truncate } from '@utils/helpers';
import styles from './BulkActionSection.module.scss';

/**
 * Bulk Workflow Status Update — a reusable "select many, transition together"
 * section. Reused for Draft → In Review, Approved → Published, and (upgraded
 * in place) the pre-existing In Review → Approved bulk-approve section, so
 * all three share the same select-all/highlight/confirm/per-item-result UX
 * instead of three near-duplicate implementations.
 *
 * `items` is expected to already be pre-filtered to the source state (the
 * Draft column, the Approved column, …) — eligibility for these transitions
 * is determined entirely by workflow_state, so every item shown here is
 * eligible by construction. A late-arriving ineligibility (the block moved
 * under the user between page load and click) is not a client-side check;
 * it surfaces as a per-item failure with a reason in the result the server
 * returns, same as any other partial failure.
 */
export default function BulkActionSection({
  title,
  emptyMessage,
  items,
  actionLabel,
  confirmTitle = 'Confirm bulk action',
  confirmMessage,
  extraFields = null,
  canRun = true,
  onRun,
  renderResult,
}) {
  const [selected, setSelected] = useState(() => new Set());
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  // Selection must never point at an item that has left this list (moved
  // state elsewhere, or the list refreshed) — stale ids would silently no-op
  // on the next bulk call instead of reflecting what's actually selectable.
  useEffect(() => {
    const validIds = new Set(items.map((i) => i.id));
    setSelected((prev) => {
      const next = new Set([...prev].filter((id) => validIds.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [items]);

  const allSelected = items.length > 0 && selected.size === items.length;

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(items.map((i) => i.id)));
  }

  function toggleOne(id) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function handleConfirm() {
    setLoading(true);
    try {
      const res = await onRun([...selected]);
      setResult(res);
      setConfirmOpen(false);
      setSelected(new Set());
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className={styles.section}>
      <h2 className={styles.title}>{title}</h2>
      {items.length === 0 ? (
        <p className={styles.emptyHint}>{emptyMessage}</p>
      ) : (
        <>
          <label className={styles.selectAllRow}>
            <input
              type="checkbox"
              checked={allSelected}
              onChange={toggleAll}
              aria-label={`Select all ${items.length} item(s)`}
            />
            Select All ({items.length})
          </label>

          <ul className={styles.list}>
            {items.map((item) => {
              const isSelected = selected.has(item.id);
              return (
                <li
                  key={item.id}
                  className={[styles.item, isSelected ? styles['item--selected'] : ''].filter(Boolean).join(' ')}
                >
                  <label className={styles.itemLabel}>
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleOne(item.id)}
                    />
                    <span className={styles.itemText}>
                      #{item.id} — {truncate(item.block_label || `Block ${item.id}`, 50)}
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>

          {extraFields}

          <Button
            variant="primary"
            disabled={selected.size === 0 || !canRun}
            onClick={() => setConfirmOpen(true)}
          >
            {actionLabel} {selected.size > 0 ? `(${selected.size})` : ''}
          </Button>

          {result && (
            <div className={styles.result}>
              {renderResult(result)}
            </div>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={handleConfirm}
        title={confirmTitle}
        message={confirmMessage(selected.size)}
        confirmLabel={actionLabel}
        variant="primary"
        loading={loading}
      />
    </section>
  );
}
