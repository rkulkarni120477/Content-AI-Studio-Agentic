import styles from './SectionBadge.module.scss';

export default function SectionBadge({ icon, title, subtitle }) {
  return (
    <div className={styles.badge} role="region" aria-label={title}>
      <div className={styles.badge__title}>
        {icon && <span className={styles.badge__icon} aria-hidden="true">{icon}</span>}
        {title}
      </div>
      {subtitle && <p className={styles.badge__subtitle}>{subtitle}</p>}
    </div>
  );
}
