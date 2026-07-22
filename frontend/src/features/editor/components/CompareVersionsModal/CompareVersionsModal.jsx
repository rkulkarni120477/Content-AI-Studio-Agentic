import { useEffect, useMemo, useState } from 'react';
import { diffWordsWithSpace } from 'diff';
import Modal from '@components/common/Modal/Modal';
import Button from '@components/common/Button/Button';
import Select from '@components/common/Select/Select';
import { formatDateTime } from '@utils/helpers';
import { editorService } from '@features/editor/services/editorService';
import styles from './CompareVersionsModal.module.scss';

function versionLabel(v) {
  const when = v.created_at ? formatDateTime(v.created_at) : '?';
  return `v${v.version_number || v.version_id} — ${v.change_source || 'edit'} (${when})`;
}

function DiffPane({ parts, side }) {
  if (parts == null) {
    return <p className={styles.loading}>Loading…</p>;
  }
  return (
    <pre className={styles.pane__text}>
      {parts
        .filter((part) => (side === 'left' ? !part.added : !part.removed))
        .map((part, i) => {
          const highlight = side === 'left' ? part.removed : part.added;
          return (
            <span key={i} className={highlight ? styles[side === 'left' ? 'removed' : 'added'] : undefined}>
              {part.value}
            </span>
          );
        })}
    </pre>
  );
}

export default function CompareVersionsModal({
  open, onClose, blockId, versions, currentContent, onRestore,
}) {
  const [selectedVersionId, setSelectedVersionId] = useState('');
  const [leftContent, setLeftContent] = useState(null);
  const [restoring, setRestoring] = useState(false);

  useEffect(() => {
    if (!open) return;
    const defaultId = versions[0]?.version_id;
    setSelectedVersionId(defaultId != null ? String(defaultId) : '');
  }, [open, versions]);

  useEffect(() => {
    if (!open || !selectedVersionId) return;
    let cancelled = false;
    setLeftContent(null);
    editorService.getBlockVersion(blockId, Number(selectedVersionId)).then((v) => {
      if (!cancelled) setLeftContent(v?.content ?? '');
    });
    return () => { cancelled = true; };
  }, [open, selectedVersionId, blockId]);

  const parts = useMemo(
    () => (leftContent != null ? diffWordsWithSpace(leftContent, currentContent || '') : null),
    [leftContent, currentContent],
  );

  const selectedMeta = versions.find((v) => String(v.version_id) === selectedVersionId);

  async function handleRestore() {
    if (!selectedVersionId) return;
    setRestoring(true);
    try {
      await onRestore(Number(selectedVersionId));
      onClose();
    } finally {
      setRestoring(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Compare Versions"
      size="full"
      className={styles.modal}
      footer={(
        <>
          <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
          <Button
            variant="primary"
            size="sm"
            loading={restoring}
            disabled={!selectedVersionId}
            onClick={handleRestore}
          >
            ⏪ Restore this version
          </Button>
        </>
      )}
    >
      <div className={styles.toolbar}>
        <Select
          label="Comparing against"
          options={versions.map((v) => ({ value: String(v.version_id), label: versionLabel(v) }))}
          value={selectedVersionId}
          onChange={(e) => setSelectedVersionId(e.target.value)}
          wrapperClassName={styles.toolbar__select}
        />
        <div className={styles.legend}>
          <span><i className={styles.removed}>abc</i> removed</span>
          <span><i className={styles.added}>abc</i> added</span>
        </div>
      </div>
      <div className={styles.grid}>
        <div className={styles.pane}>
          <p className={styles.pane__header}>
            {selectedMeta ? `v${selectedMeta.version_number || selectedMeta.version_id}` : 'Selected version'}
            <span className={styles.pane__sub}>{selectedMeta?.change_source || ''}</span>
          </p>
          <DiffPane parts={parts} side="left" />
        </div>
        <div className={styles.pane}>
          <p className={styles.pane__header}>
            Current
            <span className={styles.pane__sub}>Live editor content</span>
          </p>
          <DiffPane parts={parts} side="right" />
        </div>
      </div>
    </Modal>
  );
}
