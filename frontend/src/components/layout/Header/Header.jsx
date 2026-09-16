import { useAuth } from '@hooks/useAuth';
import { ROLE_LABELS, ROLES } from '@utils/constants';
import HeaderUser from '@components/common/HeaderUser/HeaderUser';
import JobBell from '@features/jobs/JobBell';
import styles from './Header.module.scss';

const ROLE_COLORS = {
  [ROLES.ADMIN]:    '#7c3aed',
  [ROLES.REVIEWER]: '#0f766e',
  [ROLES.AUTHOR]:   '#4338ca',
};

export default function Header({ title, breadcrumbs, actions, hideUser = false }) {
  const { user, role } = useAuth();
  const roleLabel = ROLE_LABELS[role] ?? role ?? '';
  const roleColor = ROLE_COLORS[role] ?? '#7c3aed';

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
        <JobBell />
        {!hideUser && user && (
          <HeaderUser username={user.username} roleLabel={roleLabel} roleColor={roleColor} />
        )}
      </div>
    </header>
  );
}
