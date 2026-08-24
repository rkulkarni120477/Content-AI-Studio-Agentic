// Impact-aware delete confirmation for the Prompt Library.
//
// Deleting here is an ARCHIVE (soft delete): the row keeps its version history
// and audit trail and can be restored from the Archived filter. That is worth
// saying out loud, but the part that actually needs a decision is the other
// half — a CAS pipeline prompt can be the default for a component or be locked
// to specific titles, and archiving it silently changes what other people's
// courses generate. So the dialog never asks "are you sure?" in the abstract:
// it names every binding the server found and states what each affected scope
// falls back to. Hence a bespoke dialog rather than the shared ConfirmDialog,
// whose single string `message` cannot carry that list.
import Modal from '@components/common/Modal/Modal';
import Button from '@components/common/Button/Button';
import { componentCategoryLabel } from '../../utils/prompt';
import styles from './DeletePromptDialog.module.scss';

function fixingLabel(f) {
  const where = f.scope_level === 'global'
    ? 'every title'
    : `the ${f.scope_level} “${f.scope_name || `#${f.id}`}”`;
  return `${componentCategoryLabel(f.component, null)} is locked to this prompt for ${where}`;
}

export default function DeletePromptDialog({
  open,
  prompt,
  usage,
  checking = false,
  deleting = false,
  error = '',
  onCancel,
  onConfirm,
}) {
  if (!open || !prompt) return null;

  const title = prompt.title || prompt.pipeline?.name || `Prompt #${prompt.id}`;
  const blocking = Boolean(usage?.blocking);
  // Until the usage check lands we cannot promise the delete is harmless, so
  // the confirm button stays disabled rather than defaulting to "no impact".
  const busy = checking || deleting;

  return (
    <Modal
      open={open}
      onClose={busy ? () => {} : onCancel}
      closable={!busy}
      title="Delete prompt"
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} disabled={deleting}>
            Cancel
          </Button>
          <Button
            variant="danger"
            onClick={onConfirm}
            loading={deleting}
            disabled={checking}
          >
            {blocking ? 'Delete and unbind' : 'Delete prompt'}
          </Button>
        </>
      }
    >
      <p className={styles.lead}>
        Delete <strong>{title}</strong>?
      </p>
      <p className={styles.note}>
        It is archived rather than erased — it leaves the library, every picker and
        all generation, and stays restorable from the <strong>🗄 Archived</strong> filter.
      </p>

      {checking && <p className={styles.note}>Checking where this prompt is used…</p>}

      {!checking && blocking && (
        <div className={styles.impact} role="alert">
          <div className={styles.impactTitle}>This prompt is still driving generation</div>
          <ul className={styles.impactList}>
            {usage.is_default && (
              <li>
                It is the default prompt for{' '}
                <strong>{componentCategoryLabel(usage.component_type, usage.variant)}</strong>.
              </li>
            )}
            {(usage.fixings || []).map((f) => (
              <li key={f.id}>{fixingLabel(f)}</li>
            ))}
          </ul>
          <div className={styles.impactFoot}>
            Deleting releases those bindings. Each affected scope falls back to the
            component default, or to the shipped file template if none is set.
            Restoring the prompt later does not restore the bindings.
          </div>
        </div>
      )}

      {!checking && !blocking && usage && (
        <p className={styles.note}>
          No component default or scope lock points at this prompt, so no title&apos;s
          generation changes.
        </p>
      )}

      {error && <p className={styles.error} role="alert">{error}</p>}
    </Modal>
  );
}
