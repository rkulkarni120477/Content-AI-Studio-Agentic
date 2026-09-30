import Button from '@components/common/Button/Button';
import { cn } from '@utils/helpers';
import styles from './StreamlitCard.module.scss';

export default function StreamlitCard({
  title,
  badge,
  clientLine,
  description,
  descriptionPlaceholder,
  footerLine,
  onOpen,
  openLabel = 'Open →',
  hideOpen = false,
  onEdit,
  onDelete,
  deleteLabel = '🗑️ Delete',
  onRestore,
  onManageUsers,
  canEdit = false,
  canDelete = false,
  canManageUsers = false,
}) {
  const trimmed = description?.trim();
  const isTruncated = Boolean(trimmed && trimmed.length > 120);
  const desc = trimmed
    ? (isTruncated ? `${trimmed.slice(0, 120)}…` : trimmed)
    : (descriptionPlaceholder || null);
  const descIsPlaceholder = !trimmed && Boolean(descriptionPlaceholder);

  return (
    <article className={styles.card}>
      <div className={styles.card__body}>
        <h3 className={styles.card__title}>
          {title}
          {badge && <span className={styles.card__badge}>{badge}</span>}
        </h3>
        {clientLine && <p className={styles.card__client}>Client: {clientLine}</p>}
        {desc && (
          <p
            className={cn(
              styles.card__desc,
              descIsPlaceholder && styles.card__descPlaceholder,
              isTruncated && styles.card__descTruncated,
            )}
            title={isTruncated ? trimmed : undefined}
          >
            {desc}
          </p>
        )}
        {footerLine && <p className={styles.card__meta}>{footerLine}</p>}
      </div>

      <div className={styles.card__actions}>
        {!hideOpen && onOpen && (
          <Button variant="primary" size="sm" onClick={onOpen}>
            {openLabel}
          </Button>
        )}
        {canEdit && (
          <Button variant="secondary" size="sm" onClick={onEdit}>
            ✏️ Edit
          </Button>
        )}
        {canManageUsers && (
          <Button variant="secondary" size="sm" onClick={onManageUsers}>
            👥 Users
          </Button>
        )}
        {onRestore && (
          <Button variant="secondary" size="sm" onClick={onRestore}>
            ↩ Restore
          </Button>
        )}
        {canDelete && (
          <Button variant="danger" size="sm" onClick={onDelete}>
            {deleteLabel}
          </Button>
        )}
      </div>
    </article>
  );
}
