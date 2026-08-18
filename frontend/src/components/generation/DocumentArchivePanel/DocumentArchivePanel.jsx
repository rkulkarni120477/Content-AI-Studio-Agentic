import { useMemo, useState } from 'react';
import Button from '@components/common/Button/Button';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import { formatDate } from '@utils/helpers';
import styles from './DocumentArchivePanel.module.scss';

/**
 * The document list for CDDs and Blueprints, with archive management.
 *
 * Shared by both pages because the two lists have the same failure mode: a
 * course accumulates dozens of documents whose titles are character-for-
 * character identical, and no way to remove any of them. One course reached 55
 * CDDs, nine of them from a single afternoon of retries.
 *
 * Two problems, deliberately solved together — deleting alone would not help
 * while every remaining row still looked the same:
 *   1. rows carry who made them, when, and how many versions they hold, so they
 *      can be told apart;
 *   2. rows can be archived (reversible) and, once archived and unreferenced,
 *      permanently deleted (not).
 */

/** "12 Aug 2026 · platformadmin · 3 versions" — what makes one row distinct from the next. */
function describe(doc) {
  const parts = [formatDate(doc.created_at)];
  if (doc.created_by) parts.push(doc.created_by);
  const versions = doc.references?.version_count ?? 0;
  if (versions) parts.push(`${versions} version${versions === 1 ? '' : 's'}`);
  return parts.filter(Boolean).join(' · ');
}

/** Why this row is worth keeping — shown so "safe to delete" is never a guess. */
function describeReferences(doc) {
  const refs = doc.references;
  if (!refs) return '';
  const parts = [];
  if (refs.blueprint_count) parts.push(`${refs.blueprint_count} blueprint(s)`);
  if (refs.generation_count) parts.push(`${refs.generation_count} generation(s)`);
  if (refs.feedback_count) parts.push(`${refs.feedback_count} feedback record(s)`);
  return parts.length ? `Used by ${parts.join(', ')}` : '';
}

export default function DocumentArchivePanel({
  label,                  // 'CDD' | 'Blueprint'
  docs = [],              // live documents
  archivedDocs = [],
  activeId = null,        // the pinned one, if any
  selectedId = null,
  busy = false,
  canPurge = false,       // admin — permanent delete is hidden without it
  onSelect,
  onSetActive,
  onArchive,              // (id, { unpin }) => void
  onRestore,              // (id) => void
  onPurge,                // (id) => void
  onBulkArchive,          // (ids) => void
  refusal = null,         // { needsUnpin, blockers, message } from a refused call
  onDismissRefusal,
}) {
  const [showArchived, setShowArchived] = useState(false);
  const [confirm, setConfirm] = useState(null);   // { kind, id, title, count }

  // Rows nothing points at: not pinned, no derived blueprints, no generations,
  // no feedback. Exactly the set that can be cleared without consequences.
  const unreferencedIds = useMemo(
    () => docs.filter((d) => d.references?.can_purge && d.id !== activeId).map((d) => d.id),
    [docs, activeId],
  );

  const closeConfirm = () => setConfirm(null);

  const runConfirm = () => {
    if (!confirm) return;
    const { kind, id, ids } = confirm;
    if (kind === 'archive') onArchive?.(id, { unpin: false });
    if (kind === 'unpinArchive') onArchive?.(id, { unpin: true });
    if (kind === 'purge') onPurge?.(id);
    if (kind === 'bulk') onBulkArchive?.(ids);
    closeConfirm();
  };

  const confirmCopy = () => {
    if (!confirm) return { title: '', message: '', confirmLabel: 'Confirm', variant: 'danger' };
    switch (confirm.kind) {
      case 'archive':
        return {
          title: `Archive this ${label}?`,
          message: `"${confirm.title}" will be removed from the list. Nothing is deleted — `
                 + 'you can restore it at any time from the archived list.',
          confirmLabel: 'Archive',
          variant: 'primary',
        };
      case 'unpinArchive':
        return {
          title: `Archive the active ${label}?`,
          message: `"${confirm.title}" is currently pinned as active for this course. `
                 + 'Archiving it will unpin it, and generation will have no '
                 + `${label} until you pin another one. It can still be restored.`,
          confirmLabel: 'Unpin and archive',
          variant: 'danger',
        };
      case 'purge':
        return {
          title: `Permanently delete this ${label}?`,
          message: `"${confirm.title}" and its saved versions will be deleted for good. `
                 + 'This cannot be undone.',
          confirmLabel: 'Delete permanently',
          variant: 'danger',
        };
      case 'bulk':
        return {
          title: `Archive ${confirm.ids.length} unused ${label}s?`,
          message: `${confirm.ids.length} ${label}s are not pinned and nothing was built `
                 + 'from them. They will be archived, not deleted, and can be restored '
                 + 'individually afterwards.',
          confirmLabel: `Archive ${confirm.ids.length}`,
          variant: 'primary',
        };
      default:
        return { title: '', message: '', confirmLabel: 'Confirm', variant: 'danger' };
    }
  };

  const copy = confirmCopy();

  return (
    <div className={styles.panel}>
      {/* A refusal the user can act on, kept next to the list rather than
          replacing it — the reason is only useful beside the thing it is about. */}
      {refusal && (
        <div className={styles.refusal} role="alert">
          <div className={styles.refusal__body}>
            <strong>{refusal.needsUnpin ? 'Pinned as active' : `Cannot delete this ${label}`}</strong>
            <ul className={styles.refusal__list}>
              {(refusal.blockers?.length ? refusal.blockers : [refusal.message])
                .filter(Boolean)
                .map((b) => <li key={b}>{b}</li>)}
            </ul>
          </div>
          <Button variant="ghost" size="sm" onClick={onDismissRefusal}>Dismiss</Button>
        </div>
      )}

      <div className={styles.toolbar}>
        <span className={styles.toolbar__count}>
          {docs.length} {label}{docs.length === 1 ? '' : 's'}
        </span>
        <div className={styles.toolbar__actions}>
          {unreferencedIds.length > 1 && (
            <Button
              variant="ghost" size="sm" disabled={busy}
              onClick={() => setConfirm({ kind: 'bulk', ids: unreferencedIds })}
            >
              🧹 Archive {unreferencedIds.length} unused
            </Button>
          )}
          {archivedDocs.length > 0 && (
            <Button
              variant="ghost" size="sm"
              onClick={() => setShowArchived((v) => !v)}
              aria-expanded={showArchived}
            >
              {showArchived ? 'Hide' : 'Show'} archived ({archivedDocs.length})
            </Button>
          )}
        </div>
      </div>

      <ul className={styles.list}>
        {docs.map((doc) => {
          const isActive = activeId === doc.id;
          const used = describeReferences(doc);
          return (
            <li
              key={doc.id}
              className={`${styles.item} ${selectedId === doc.id ? styles['item--selected'] : ''}`}
            >
              <button
                type="button"
                className={styles.item__info}
                onClick={() => onSelect?.(doc.id)}
                title={`Open ${doc.title || doc.course_title || label}`}
              >
                <span className={styles.item__title}>
                  {doc.title || doc.course_title || `${label} ${doc.id}`}
                </span>
                <span className={styles.item__meta}>
                  #{doc.id} · {describe(doc)}{used ? ` · ${used}` : ''}
                </span>
              </button>
              <div className={styles.item__actions}>
                {isActive
                  ? <span className={styles.badgeActive}>Active</span>
                  : (
                    <Button variant="ghost" size="sm" disabled={busy}
                            onClick={() => onSetActive?.(doc.id)}>
                      Set Active
                    </Button>
                  )}
                <Button
                  variant="ghost" size="sm" disabled={busy}
                  title={isActive
                    ? `Archiving the active ${label} will unpin it`
                    : `Archive this ${label} (reversible)`}
                  onClick={() => setConfirm({
                    kind: isActive ? 'unpinArchive' : 'archive',
                    id: doc.id,
                    title: doc.title || doc.course_title || `${label} ${doc.id}`,
                  })}
                >
                  🗄️
                </Button>
              </div>
            </li>
          );
        })}
      </ul>

      {showArchived && (
        <div className={styles.archived}>
          <h4 className={styles.archived__title}>Archived</h4>
          <ul className={styles.list}>
            {archivedDocs.map((doc) => {
              const blockers = doc.references?.blockers ?? [];
              return (
                <li key={doc.id} className={`${styles.item} ${styles['item--archived']}`}>
                  <div className={styles.item__info}>
                    <span className={styles.item__title}>
                      {doc.title || doc.course_title || `${label} ${doc.id}`}
                    </span>
                    <span className={styles.item__meta}>
                      #{doc.id} · {describe(doc)}
                      {doc.deleted_by ? ` · archived by ${doc.deleted_by}` : ''}
                    </span>
                  </div>
                  <div className={styles.item__actions}>
                    <Button variant="ghost" size="sm" disabled={busy}
                            onClick={() => onRestore?.(doc.id)}>
                      Restore
                    </Button>
                    {/* Permanent delete appears only for admins, and only when
                        the server says nothing references it — offering a button
                        that always refuses is worse than not offering one. */}
                    {canPurge && (
                      <Button
                        variant="ghost" size="sm"
                        disabled={busy || blockers.length > 0}
                        title={blockers.length ? blockers.join('; ') : 'Delete permanently'}
                        onClick={() => setConfirm({
                          kind: 'purge',
                          id: doc.id,
                          title: doc.title || doc.course_title || `${label} ${doc.id}`,
                        })}
                      >
                        🗑️
                      </Button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <ConfirmDialog
        open={Boolean(confirm)}
        onClose={closeConfirm}
        onConfirm={runConfirm}
        loading={busy}
        title={copy.title}
        message={copy.message}
        confirmLabel={copy.confirmLabel}
        variant={copy.variant}
      />
    </div>
  );
}
