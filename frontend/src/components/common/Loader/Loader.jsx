import { cn } from '@utils/helpers';
import styles from './Loader.module.scss';

export default function Loader({ size = 'md', color = 'primary', label = 'Loading…', overlay = false }) {
  const spinner = (
    <span
      role="status"
      aria-label={label}
      className={cn(styles.loader, styles[`loader--${size}`], styles[`loader--${color}`])}
    >
      <span className={styles.loader__ring} />
    </span>
  );

  if (overlay) {
    return (
      <div className={styles.loader__overlay} aria-busy="true" aria-label={label}>
        {spinner}
      </div>
    );
  }

  return spinner;
}
