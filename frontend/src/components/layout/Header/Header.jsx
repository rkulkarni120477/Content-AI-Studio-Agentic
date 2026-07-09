import { useAppSelector } from '@app/hooks';
import { selectUser, selectIsPlatformAdmin } from '@features/auth/authSlice';
import styles from './Header.module.scss';

export default function Header({ title, breadcrumbs, actions }) {
  const user            = useAppSelector(selectUser);
  const isPlatformAdmin = useAppSelector(selectIsPlatformAdmin);

  return (
    <header className={styles.header}>
      <div className={styles.header__left}>
        {breadcrumbs && (
          <nav aria-label="Breadcrumb">
            <ol className={styles.breadcrumb}>
              {breadcrumbs.map((crumb, i) => (
                <li key={i} className={styles.breadcrumb__item}>
                  {crumb.to ? (
                    <a href={crumb.to} className={styles.breadcrumb__link}>{crumb.label}</a>
                  ) : (
                    <span className={styles.breadcrumb__current} aria-current="page">{crumb.label}</span>
                  )}
                </li>
              ))}
            </ol>
          </nav>
        )}
        {title && <h1 className={styles.header__title}>{title}</h1>}
      </div>

      <div className={styles.header__right}>
        {actions}
        {user && (
          <div className={styles.header__user}>
            {isPlatformAdmin && (
              <span className={styles.header__badge} data-variant="platform">Platform Admin</span>
            )}
            <span className={styles.header__username}>{user.username}</span>
          </div>
        )}
      </div>
    </header>
  );
}
