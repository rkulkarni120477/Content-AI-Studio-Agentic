import Button from '../Button/Button';
import styles from './EmptyState.module.scss';

export default function EmptyState({
  icon,
  title     = 'Nothing here yet',
  message,
  action,
  actionLabel,
  actionVariant = 'primary',
}) {
  return (
    <div className={styles.empty} role="status">
      {icon && <span className={styles.empty__icon} aria-hidden="true">{icon}</span>}
      <h3 className={styles.empty__title}>{title}</h3>
      {message && <p className={styles.empty__message}>{message}</p>}
      {action && actionLabel && (
        <Button variant={actionVariant} onClick={action} className={styles.empty__action}>
          {actionLabel}
        </Button>
      )}
    </div>
  );
}
