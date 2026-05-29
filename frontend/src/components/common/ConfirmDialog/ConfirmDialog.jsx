import Modal from '../Modal/Modal';
import Button from '../Button/Button';
import styles from './ConfirmDialog.module.scss';

export default function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title      = 'Confirm Action',
  message    = 'Are you sure you want to proceed? This action cannot be undone.',
  confirmLabel = 'Confirm',
  cancelLabel  = 'Cancel',
  variant      = 'danger',
  loading      = false,
}) {
  return (
    <Modal
      open={open}
      onClose={onClose}
      title={title}
      size="xs"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={loading}>{cancelLabel}</Button>
          <Button variant={variant} onClick={onConfirm} loading={loading}>{confirmLabel}</Button>
        </>
      }
    >
      <p className={styles.message}>{message}</p>
    </Modal>
  );
}
