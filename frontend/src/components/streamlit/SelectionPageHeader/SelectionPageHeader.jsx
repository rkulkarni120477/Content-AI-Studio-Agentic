import styles from './SelectionPageHeader.module.scss';

export default function SelectionPageHeader({ eyebrow, title, subtitle }) {
  return (
    <header className={styles.header}>
      {eyebrow && <div className={styles.header__eyebrow}>{eyebrow}</div>}
      <h1 className={styles.header__title}>{title}</h1>
      {subtitle && <p className={styles.header__subtitle}>{subtitle}</p>}
    </header>
  );
}
