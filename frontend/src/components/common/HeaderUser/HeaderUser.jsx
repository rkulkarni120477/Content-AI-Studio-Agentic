import { cn } from '@utils/helpers';
import styles from './HeaderUser.module.scss';

/**
 * Compact identity chip (username + role) shown in the top app header.
 * Avatar intentionally omitted — label and role only.
 */
export default function HeaderUser({ username, roleLabel, roleColor, className }) {
  return (
    <div className={cn(styles.headerUser, className)}>
      <span className={styles.headerUser__name} title={username}>{username || '—'}</span>
      {roleLabel && (
        <span className={styles.headerUser__role} style={{ background: roleColor }}>
          {roleLabel}
        </span>
      )}
    </div>
  );
}
