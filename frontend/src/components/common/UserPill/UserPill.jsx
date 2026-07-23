import { cn } from '@utils/helpers';
import styles from './UserPill.module.scss';

/**
 * Shared "Signed in as" identity pill used across platform/admin sidebars.
 * Keeps the markup in one place so every consumer stays visually consistent.
 */
export default function UserPill({ username, roleLabel, roleColor, className }) {
  return (
    <div className={cn(styles.userPill, className)}>
      <div className={styles.userPill__label}>Signed in as</div>
      <div className={styles.userPill__name}>{username || '—'}</div>
      <span className={styles.userPill__role} style={{ background: roleColor }}>
        {roleLabel}
      </span>
    </div>
  );
}
