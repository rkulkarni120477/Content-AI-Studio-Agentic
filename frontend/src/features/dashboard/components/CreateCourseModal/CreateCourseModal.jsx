import { useEffect, useState } from 'react';
import Modal from '@components/common/Modal/Modal';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Select from '@components/common/Select/Select';
import { useLabels } from '@hooks/useLabels';
import styles from './CreateCourseModal.module.scss';

const WORKFLOW_OPTIONS = [
  { value: 'print', label: 'Print' },
  { value: 'digital', label: 'Digital' },
];

/**
 * The create-course fork (reverse pipeline entry point).
 *
 * "New Title" reproduces today's exact scratch flow — it calls the same
 * `onCreateCourse(payload)` the sidebar form always called. "Import Title" is
 * additive and gated by `importEnabled` (off in Session 0, wired to the backend
 * flag in Session 4). See reverse_cas.md.
 */
export default function CreateCourseModal({
  open,
  onClose,
  onCreateCourse,
  createLoading = false,
  importEnabled = false,
  onImport,
}) {
  const L = useLabels();
  const [mode, setMode] = useState('choice');   // 'choice' | 'new'
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [workflow, setWorkflow] = useState('');
  const [error, setError] = useState(null);

  // Reset to the fork every time the modal opens.
  useEffect(() => {
    if (open) {
      setMode('choice');
      setName('');
      setDescription('');
      setWorkflow('');
      setError(null);
    }
  }, [open]);

  async function handleCreate(e) {
    e.preventDefault();
    if (!name.trim()) {
      setError('Name is required.');
      return;
    }
    setError(null);
    // Same payload shape the sidebar form produced — scratch path is unchanged.
    const payload = { name: name.trim(), description: description.trim() || null };
    if (workflow) payload.workflow = workflow;
    await onCreateCourse?.(payload);
    onClose?.();
  }

  function handleImport() {
    if (!importEnabled) return;
    onImport?.();
    onClose?.();
  }

  const footer =
    mode === 'new' ? (
      <>
        <Button variant="ghost" onClick={() => setMode('choice')} disabled={createLoading}>
          ← Back
        </Button>
        <Button variant="primary" onClick={handleCreate} loading={createLoading}>
          Create {L.title}
        </Button>
      </>
    ) : (
      <Button variant="ghost" onClick={onClose}>Cancel</Button>
    );

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={mode === 'new' ? `New ${L.title}` : `Create ${L.title}`}
      size="sm"
      footer={footer}
    >
      {mode === 'choice' ? (
        <div className={styles.choices}>
          <button
            type="button"
            className={styles.choice}
            onClick={() => setMode('new')}
          >
            <span className={styles.choice__icon} aria-hidden="true">✨</span>
            <span className={styles.choice__title}>New {L.title}</span>
            <span className={styles.choice__desc}>
              Build from scratch — {L.style} → {L.cdd} → {L.blueprint} → Generate → Editor.
            </span>
          </button>

          <button
            type="button"
            className={styles.choice}
            onClick={handleImport}
            disabled={!importEnabled}
            title={importEnabled ? undefined : 'Coming soon'}
            aria-disabled={!importEnabled}
          >
            <span className={styles.choice__icon} aria-hidden="true">📥</span>
            <span className={styles.choice__title}>
              Import {L.title}{!importEnabled && <span className={styles.badge}>Coming soon</span>}
            </span>
            <span className={styles.choice__desc}>
              Import an existing Canvas IMSCC package and reconstruct it in CAS.
            </span>
          </button>
        </div>
      ) : (
        <form onSubmit={handleCreate}>
          <Input
            label={`${L.title} Name *`}
            value={name}
            onChange={(e) => setName(e.target.value)}
            required
          />
          <label className={styles.textareaLabel}>
            Description
            <textarea
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              className={styles.textarea}
            />
          </label>
          <Select
            label="Choose workflow"
            placeholder="Choose options"
            options={WORKFLOW_OPTIONS}
            value={workflow}
            onChange={(e) => setWorkflow(e.target.value)}
          />
          {error && <p role="alert" className={styles.error}>{error}</p>}
        </form>
      )}
    </Modal>
  );
}
