import Button from '@components/common/Button/Button';
import styles from './HierarchyCard.module.scss';

export default function HierarchyCard({
  icon,
  name,
  metaLines = [],
  isActive,
  onOpen,
  onEdit,
  onDelete,
  onManageUsers,
  canEdit = false,
  canDelete = false,
  canManageUsers = false,
  openLabel = 'Open →',
}) {
  return (
    <article className={`${styles.card} ${isActive ? styles['card--active'] : ''}`}>
      <div className={styles.card__body}>
        <span className={styles.card__icon} aria-hidden="true">{icon}</span>
        <div className={styles.card__info}>
          <span className={styles.card__name}>{name}</span>
          {metaLines.filter(Boolean).map((line) => (
            <span key={line} className={styles.card__meta}>{line}</span>
          ))}
        </div>
        {isActive && <span className={styles.card__check} aria-hidden="true">✓</span>}
      </div>

      <div className={styles.card__actions}>
        <Button variant="primary" size="sm" onClick={onOpen}>
          {openLabel}
        </Button>
        {(canEdit || canManageUsers || canDelete) && (
          <div className={styles.card__secondary}>
            {canEdit && (
              <Button variant="ghost" size="sm" onClick={onEdit}>
                Edit
              </Button>
            )}
            {canManageUsers && (
              <Button variant="ghost" size="sm" onClick={onManageUsers}>
                Users
              </Button>
            )}
            {canDelete && (
              <Button variant="danger" size="sm" onClick={onDelete}>
                Delete
              </Button>
            )}
          </div>
        )}
      </div>
    </article>
  );
}
