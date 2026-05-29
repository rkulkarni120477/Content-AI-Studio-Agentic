import Button from '../Button/Button';
import styles from './ErrorState.module.scss';

export default function ErrorState({
  title   = 'Something went wrong',
  message,
  onRetry,
  retryLabel = 'Try Again',
}) {
  return (
    <div className={styles.error} role="alert">
      <span className={styles.error__icon} aria-hidden="true">⚠️</span>
      <h3 className={styles.error__title}>{title}</h3>
      {message && <p className={styles.error__message}>{message}</p>}
      {onRetry && (
        <Button variant="secondary" onClick={onRetry} className={styles.error__action}>
          {retryLabel}
        </Button>
      )}
    </div>
  );
}
