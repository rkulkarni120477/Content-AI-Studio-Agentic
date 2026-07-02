import Button from '@components/common/Button/Button';
import styles from './StreamlitCard.module.scss';

export default function StreamlitCard({
  title,
  clientLine,
  description,
  footerLine,
  onOpen,
  openLabel = 'Open →',
  onEdit,
  onDelete,
  onManageUsers,
  canEdit = false,
  canDelete = false,
  canManageUsers = false,
}) {
  const desc = description?.length > 90 ? `${description.slice(0, 90)}…` : description;

  return (
    <div className={styles.wrap}>
      <div className={styles.card}>
        <div className={styles.card__title}>{title}</div>
        {clientLine && <div className={styles.card__client}>Client: {clientLine}</div>}
        {desc && <div className={styles.card__desc}>{desc}</div>}
        {footerLine && <div className={styles.card__footer}>{footerLine}</div>}
      </div>

      <Button variant="primary" size="sm" fullWidth onClick={onOpen}>
        {openLabel}
      </Button>

      {(canEdit || canDelete || canManageUsers) && (
        <div className={styles.actions}>
          {canEdit && (
            <Button variant="secondary" size="sm" fullWidth onClick={onEdit}>
              ✏️ Edit
            </Button>
          )}
          {canManageUsers && (
            <Button variant="secondary" size="sm" fullWidth onClick={onManageUsers}>
              👥 Users
            </Button>
          )}
          {canDelete && (
            <Button variant="danger" size="sm" fullWidth onClick={onDelete}>
              🗑️ Delete
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
