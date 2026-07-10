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
    <article className={styles.card}>
      <div className={styles.card__body}>
        <h3 className={styles.card__title}>{title}</h3>
        {clientLine && <p className={styles.card__client}>Client: {clientLine}</p>}
        {desc && <p className={styles.card__desc}>{desc}</p>}
        {footerLine && <p className={styles.card__meta}>{footerLine}</p>}
      </div>

      <div className={styles.card__actions}>
        <Button variant="primary" size="sm" onClick={onOpen}>
          {openLabel}
        </Button>
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
        {canDelete && (
          <Button variant="danger" size="sm" onClick={onDelete}>
            🗑️ Delete
          </Button>
        )}
      </div>
    </article>
  );
}
