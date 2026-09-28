import { useRef, useState } from 'react';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  uploadChecklistThunk,
  updateChecklistItemThunk,
  deleteChecklistItemsThunk,
  deleteChecklistThunk,
} from '@features/review/reviewThunks';
import {
  selectReviewChecklist,
  selectReviewUploading,
  selectReviewSavingItem,
  selectReviewError,
} from '@features/review/reviewSlice';
import { useAuth } from '@hooks/useAuth';
import Button from '@components/common/Button/Button';
import styles from './ChecklistManager.module.scss';

const ACCEPT = '.pdf,.docx,.txt,.xlsx';

/**
 * Upload and curate the client's CE checklist. Rules import non-mandatory; the
 * user ticks the ones that block approval, and can edit any rule's wording.
 */
export default function ChecklistManager({ projectId = null }) {
  const dispatch = useAppDispatch();
  const { hasPermission } = useAuth();
  const checklist = useAppSelector(selectReviewChecklist);
  const isUploading = useAppSelector(selectReviewUploading);
  const savingItemId = useAppSelector(selectReviewSavingItem);
  const error = useAppSelector(selectReviewError);

  const canEdit = hasPermission('review.configure');
  const fileRef = useRef(null);
  const [pendingFile, setPendingFile] = useState(null);
  const [name, setName] = useState('');
  const [editingId, setEditingId] = useState(null);
  const [draftText, setDraftText] = useState('');
  const [selectedIds, setSelectedIds] = useState(() => new Set());

  const items = checklist?.items || [];
  const mandatoryCount = items.filter((i) => i.is_mandatory).length;
  const selectedCount = selectedIds.size;

  function toggleSelect(id) {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }

  async function deleteRules(ids) {
    if (!ids.length) return;
    const msg = ids.length === 1
      ? 'Are you sure you want to delete this rule? This action cannot be undone.'
      : `Are you sure you want to delete these ${ids.length} rules? This action cannot be undone.`;
    if (!window.confirm(msg)) return;
    await dispatch(deleteChecklistItemsThunk({ checklistId: checklist.id, itemIds: ids }));
    setSelectedIds(new Set());
  }

  async function onDeleteChecklist() {
    if (!window.confirm('Are you sure you want to delete this checklist and all its rules? This action cannot be undone.')) return;
    await dispatch(deleteChecklistThunk(checklist.id));
    setSelectedIds(new Set());
  }

  function onPickFile(e) {
    const f = e.target.files?.[0] || null;
    setPendingFile(f);
    if (f && !name.trim()) setName(f.name.replace(/\.[^.]+$/, ''));
  }

  async function onUpload() {
    if (!pendingFile) return;
    await dispatch(uploadChecklistThunk({ file: pendingFile, name: name.trim() || undefined, projectId }));
    setPendingFile(null);
    setName('');
    if (fileRef.current) fileRef.current.value = '';
  }

  function toggleMandatory(item) {
    dispatch(updateChecklistItemThunk({
      checklistId: checklist.id,
      itemId: item.id,
      patch: { is_mandatory: !item.is_mandatory },
    }));
  }

  function startEdit(item) {
    setEditingId(item.id);
    setDraftText(item.rule_text);
  }

  async function saveEdit(item) {
    const text = draftText.trim();
    if (text && text !== item.rule_text) {
      await dispatch(updateChecklistItemThunk({
        checklistId: checklist.id,
        itemId: item.id,
        patch: { rule_text: text },
      }));
    }
    setEditingId(null);
    setDraftText('');
  }

  return (
    <div className={styles.manager}>
      {error && <div className={styles.error}>⚠️ {error}</div>}

      {/* ── Upload ─────────────────────────────────────────────────── */}
      {canEdit && (
        <div className={styles.uploadRow}>
          <div className={styles.uploadFields}>
            <input
              ref={fileRef}
              type="file"
              accept={ACCEPT}
              onChange={onPickFile}
              className={styles.fileInput}
              disabled={isUploading}
            />
            <input
              type="text"
              className={styles.nameInput}
              placeholder="Checklist name (optional)"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={isUploading}
            />
          </div>
          <Button
            variant="primary"
            size="sm"
            loading={isUploading}
            disabled={!pendingFile || isUploading}
            onClick={onUpload}
          >
            {checklist ? '⬆️ Upload new version' : '⬆️ Upload checklist'}
          </Button>
        </div>
      )}
      {canEdit && (
        <p className={styles.hint}>Accepted formats: Excel, Word, PDF, or text (.xlsx, .docx, .pdf, .txt).</p>
      )}
      {isUploading && (
        <p className={styles.hint}>⏳ Splitting the checklist into rules… this can take a few moments.</p>
      )}

      {/* ── Rules ──────────────────────────────────────────────────── */}
      {!checklist && !isUploading && (
        <p className={styles.empty}>
          No checklist yet.{canEdit ? ' Upload a CE checklist file above to get started.'
            : ' Ask an admin to upload the CE checklist.'}
        </p>
      )}

      {checklist && (
        <>
          <div className={styles.metaRow}>
            <strong>{checklist.name}</strong>
            <span className={styles.badge}>v{checklist.version}</span>
            <span className={styles.metaText}>
              {items.length} rule{items.length === 1 ? '' : 's'} · {mandatoryCount} mandatory
            </span>
            {canEdit && (
              <span className={styles.metaActions}>
                {selectedCount > 0 && (
                  <Button variant="ghost" size="xs" onClick={() => deleteRules([...selectedIds])}>
                    🗑 Delete selected ({selectedCount})
                  </Button>
                )}
                <Button variant="ghost" size="xs" onClick={onDeleteChecklist}>
                  🗑 Delete checklist
                </Button>
              </span>
            )}
          </div>

          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  {canEdit && <th className={styles.colSel} />}
                  <th className={styles.colNum}>#</th>
                  <th>Rule</th>
                  <th className={styles.colSection}>Section</th>
                  <th className={styles.colMand} title="Mandatory rules block Ready for Approval when they fail">
                    Mandatory
                  </th>
                  {canEdit && <th className={styles.colAct} />}
                </tr>
              </thead>
              <tbody>
                {items.map((item, idx) => (
                  <tr key={item.id} className={selectedIds.has(item.id) ? styles.rowSelected : undefined}>
                    {canEdit && (
                      <td className={styles.colSel}>
                        <input
                          type="checkbox"
                          checked={selectedIds.has(item.id)}
                          onChange={() => toggleSelect(item.id)}
                          aria-label={`Select rule ${idx + 1}`}
                        />
                      </td>
                    )}
                    <td className={styles.colNum}>{idx + 1}</td>
                    <td>
                      {editingId === item.id ? (
                        <div className={styles.editBox}>
                          <textarea
                            className={styles.editArea}
                            value={draftText}
                            onChange={(e) => setDraftText(e.target.value)}
                            rows={2}
                          />
                          <div className={styles.editActions}>
                            <Button variant="primary" size="xs" loading={savingItemId === item.id} onClick={() => saveEdit(item)}>
                              Save
                            </Button>
                            <Button variant="ghost" size="xs" onClick={() => setEditingId(null)}>
                              Cancel
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <span className={styles.ruleText}>{item.rule_text}</span>
                      )}
                    </td>
                    <td className={styles.colSection}>{item.section || '—'}</td>
                    <td className={styles.colMand}>
                      <input
                        type="checkbox"
                        checked={!!item.is_mandatory}
                        disabled={!canEdit || savingItemId === item.id}
                        onChange={() => toggleMandatory(item)}
                        aria-label={`Mark rule ${idx + 1} mandatory`}
                      />
                    </td>
                    {canEdit && (
                      <td className={styles.colAct}>
                        {editingId !== item.id && (
                          <>
                            <Button variant="ghost" size="xs" onClick={() => startEdit(item)}>✏️</Button>
                            <Button variant="ghost" size="xs" onClick={() => deleteRules([item.id])} title="Delete rule">🗑</Button>
                          </>
                        )}
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
