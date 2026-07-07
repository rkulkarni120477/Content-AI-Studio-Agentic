// Prompts section shell — integrated Studio chrome.
//
// Replaces the standalone app's top-bar shell with the host's sidebar pattern
// (same brand block, user pill and dark indigo styling as the dashboard's
// SelectionSidebar), so Prompts reads as a section of Content AI Studio rather
// than a separate app. The intra-feature nav (Library / Courses / Requests /
// Reviews / Audit) and the "New Prompt" action move into the sidebar; page
// content keeps the feature's scoped `.pl-root` styles.
import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import AppBrand from '@components/common/AppBrand/AppBrand';
import { useAuth as useHostAuth } from '@hooks/useAuth';
import { ROLE_LABELS, ROLES, ROUTES } from '@utils/constants';
import { cn } from '@utils/helpers';
import { useAuth } from '../../context/AuthContext';
import { ToastProvider } from '../../context/ToastContext';
import { MAIN_NAV, visibleHeaderActions } from '../../utils/nav';
import PageView from './PageView';
import styles from './PromptLibraryLayout.module.scss';
import '../../styles/promptLibrary.scss';

const ROLE_COLORS = {
  [ROLES.ADMIN]:    '#7c3aed',
  [ROLES.REVIEWER]: '#0f766e',
  [ROLES.AUTHOR]:   '#4338ca',
};

export default function PromptLibraryLayout() {
  const { user } = useAuth();
  const { logout } = useHostAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const navItems = MAIN_NAV.filter((item) => item.visible(user));
  const headerActions = visibleHeaderActions(user, location.pathname);
  const role = user?.role;
  const roleColor = ROLE_COLORS[role] ?? '#4338ca';
  const roleLabel = ROLE_LABELS[role] ?? role;

  function handleSignOut() {
    logout();
    navigate(ROUTES.LOGIN, { replace: true });
  }

  return (
    <div className={styles.layout}>
      <aside className={styles.sidebar} aria-label="Prompts navigation">
        <AppBrand />

        <div className={styles.userPill}>
          <div className={styles.userPill__label}>Signed in as</div>
          <div className={styles.userPill__name}>{user?.username}</div>
          <span className={styles.userPill__role} style={{ background: roleColor }}>
            {roleLabel}
          </span>
        </div>

        <button type="button" className={styles.navBtn} onClick={() => navigate(ROUTES.DASHBOARD)}>
          ← Projects
        </button>

        <div className={styles.divider} />

        <div className={styles.sectionLabel}>📚 Prompts</div>
        <nav className={styles.nav}>
          {navItems.map((item) => (
            <NavLink
              key={`${item.to}-${item.label}`}
              to={item.to}
              end={item.end}
              className={({ isActive }) => cn(styles.navLink, isActive && styles.navLinkActive)}
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        {headerActions.length > 0 && (
          <>
            <div className={styles.divider} />
            {headerActions.map((action) => (
              <Link key={action.to} to={action.to} className={styles.actionBtn}>
                {action.label}
              </Link>
            ))}
          </>
        )}

        <div className={styles.footer}>
          <button type="button" className={styles.navBtn} onClick={handleSignOut}>
            🚪 Sign Out
          </button>
        </div>
      </aside>

      {/* `.pl-root` scopes the ported feature CSS (incl. its element reset) to
          the content pane only — the sidebar is host chrome and must stay out. */}
      <main className={cn(styles.main, 'pl-root')}>
        <ToastProvider>
          <div className={styles.content}>
            <PageView>
              <Outlet />
            </PageView>
          </div>
        </ToastProvider>
      </main>
    </div>
  );
}
