import { useEffect } from 'react';
import { NavLink, useNavigate, useParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  selectSelectedProject, selectSelectedCluster, selectSelectedCourse,
  setSelectedProject, setSelectedCluster, setSelectedCourse,
} from '@features/dashboard/dashboardSlice';
import { selectActiveCdd } from '@features/cdd/cddSlice';
import { selectActiveStyle } from '@features/style/styleSlice';
import { fetchCddsThunk } from '@features/cdd/cddThunks';
import { fetchStylesThunk } from '@features/style/styleThunks';
import { useAuth } from '@hooks/useAuth';
import { useLabels } from '@hooks/useLabels';
import { applyTerminology } from '@config/tenantLabels';
// ROLE_LABELS/ROLES were only used by the sidebar identity chip, now moved to the top header.
import { ROUTES } from '@utils/constants';
import TargetModelPanel from './TargetModelPanel';
// GettingStartedGuide hidden per request — kept commented for easy restore.
// import GettingStartedGuide from '@components/layout/GettingStartedGuide/GettingStartedGuide';
import AppBrand from '@components/common/AppBrand/AppBrand';
import SidebarToggle from '@components/layout/SidebarToggle/SidebarToggle';
import { useSidebarCollapsed } from '@hooks/useSidebarCollapsed';
import { cn } from '@utils/helpers';
import { flushDeferredToasts } from '@utils/deferredToast';
import styles from './WorkspaceSidebar.module.scss';

// `segment` is the URL and never changes; `labelKey` picks the tenant's word for
// the three renameable stages (see @config/tenantLabels). Items without a
// labelKey keep their fixed label.
const NAV_ITEMS = [
  { label: 'Source Library', icon: '📚', segment: 'sources' },
  { labelKey: 'style',     icon: '🎨', segment: 'style' },
  { labelKey: 'cdd',       icon: '📘', segment: 'cdd' },
  { labelKey: 'blueprint', icon: '🧩', segment: 'blueprint' },
  { label: 'Generate',  icon: '⚙️', segment: 'generate' },
  { label: 'Editor',    icon: '✏️', segment: 'editor' },
  { label: 'Feedback',  icon: '💬', segment: 'feedback' },
  { label: 'Workflow',  icon: '🚦', segment: 'workflow' },
  { label: 'Export',    icon: '📦', segment: 'export' },
  { label: 'Analytics', icon: '📊', segment: 'analytics' },
];

// ROLE_COLORS was used by the sidebar identity chip, now moved to the top header.
// const ROLE_COLORS = {
//   [ROLES.ADMIN]:    '#7c3aed',
//   [ROLES.REVIEWER]: '#0f766e',
//   [ROLES.AUTHOR]:   '#4338ca',
// };

export default function WorkspaceSidebar() {
  const { courseId } = useParams();
  const navigate = useNavigate();
  const dispatch = useAppDispatch();
  const { logout, isPlatformAdmin, projectId: authProjectId } = useAuth();
  const L = useLabels();
  const proj = useAppSelector(selectSelectedProject);
  const cluster = useAppSelector(selectSelectedCluster);
  const course = useAppSelector(selectSelectedCourse);
  const activeCdd = useAppSelector(selectActiveCdd);
  const activeStyle = useAppSelector(selectActiveStyle);
  const [collapsed, toggleCollapsed] = useSidebarCollapsed();
  const cid = courseId || course?.id;

  useEffect(() => {
    flushDeferredToasts();
  }, []);

  useEffect(() => {
    if (!cid) return;
    dispatch(fetchCddsThunk(cid));
    // Pass the URL courseId explicitly (mirrors fetchCddsThunk). Relying on the
    // dashboard slice here missed the active Style right after an import, when
    // the slice isn't hydrated yet — so the Global State pill showed "—".
    dispatch(fetchStylesThunk({ courseId: cid }));
  }, [cid, dispatch]);

  function handleSignOut() {
    logout();
    navigate(ROUTES.LOGIN, { replace: true });
  }

  function goProjects() {
    if (isPlatformAdmin) {
      dispatch(setSelectedProject(null));
      dispatch(setSelectedCluster(null));
      dispatch(setSelectedCourse(null));
      navigate(ROUTES.DASHBOARD);
      return;
    }
    const pid = proj?.id || authProjectId;
    if (!pid) return;
    dispatch(setSelectedCluster(null));
    dispatch(setSelectedCourse(null));
    navigate(ROUTES.PROJECT_CLUSTERS(pid));
  }

  function goClusters() {
    if (proj?.id) {
      dispatch(setSelectedCluster(null));
      dispatch(setSelectedCourse(null));
      navigate(ROUTES.PROJECT_CLUSTERS(proj.id));
    }
  }

  function goCourses() {
    if (proj?.id && cluster?.id) {
      dispatch(setSelectedCourse(null));
      navigate(ROUTES.CLUSTER_COURSES(proj.id, cluster.id));
    }
  }

  // const roleColor = ROLE_COLORS[role] ?? '#4338ca'; // identity moved to top header
  // Swap the tenant's word into the stored name at display time (names are stored
  // with the default word baked in at import; DB is left untouched).
  const styleLabel = applyTerminology(activeStyle?.name, L, ['style']) || '—';
  const cddLabel = activeCdd
    ? `${applyTerminology(activeCdd.title || activeCdd.course_title || L.cdd, L, ['cdd'])} (${activeCdd.active_version || 'v1'})`
    : '—';

  return (
    <aside
      className={cn(styles.sidebar, collapsed && styles['sidebar--collapsed'])}
      aria-label="Workspace navigation"
    >
      <div className={cn(styles.header, collapsed && styles['header--collapsed'])}>
        <AppBrand compact={collapsed} />
        <SidebarToggle collapsed={collapsed} onToggle={toggleCollapsed} />
      </div>

      {/* Identity (username + role) moved to the top header (HeaderUser) per request.
          Kept commented for easy restore — no longer shown in the workspace sidebar.
      {collapsed ? (
        <div
          className={styles.userDot}
          style={{ background: roleColor }}
          title={`${user?.username} — ${ROLE_LABELS[role] ?? role}`}
        >
          {(user?.username || '?').charAt(0).toUpperCase()}
        </div>
      ) : (
        <div className={styles.userPill}>
          <div className={styles.userPill__label}>Signed in as</div>
          <div className={styles.userPill__name}>{user?.username}</div>
          <span className={styles.userPill__role} style={{ background: roleColor }}>
            {ROLE_LABELS[role] ?? role}
          </span>
        </div>
      )}
      */}

      {!collapsed && (
        <div className={styles.contextPill}>
          <div className={styles.contextPill__label}>Workspace</div>
          <div>📁 <strong>{proj?.name ?? '—'}</strong></div>
          <div>🗂️ <strong>{cluster?.name ?? '—'}</strong></div>
          <div>📖 <strong>{course?.name ?? '—'}</strong></div>
        </div>
      )}

      <div className={collapsed ? styles.backCol : styles.backRow}>
        <button
          type="button"
          className={styles.backBtn}
          onClick={goProjects}
          title={collapsed ? 'Back to Projects' : undefined}
        >
          {collapsed ? '🏠' : <>←<br />Projects</>}
        </button>
        <button
          type="button"
          className={styles.backBtn}
          onClick={goClusters}
          title={collapsed ? 'Back to Categories' : undefined}
        >
          {collapsed ? '🗂️' : <>←<br />Categories</>}
        </button>
        <button
          type="button"
          className={styles.backBtn}
          onClick={goCourses}
          title={collapsed ? `Back to ${L.titles}` : undefined}
        >
          {collapsed ? '📖' : <>←<br />{L.titles}</>}
        </button>
      </div>

      <div className={styles.divider} />

      {!collapsed && (
        <div className={styles.statePill}>
          <div className={styles.statePill__label}>Global State</div>
          <div className={styles.statePill__row}>
            <span className={styles.statePill__dot} style={{ background: activeStyle ? '#10b981' : '#9ca3af' }} />
            🎨 {L.style}: <strong>{styleLabel}</strong>
          </div>
          <div className={styles.statePill__row}>
            <span className={styles.statePill__dot} style={{ background: activeCdd ? '#10b981' : '#9ca3af' }} />
            📘 {L.cdd}: <strong>{cddLabel}</strong>
          </div>
        </div>
      )}

      {/* Getting Started guide hidden per request.
      {!collapsed && <GettingStartedGuide />}
      */}

      {!collapsed && <TargetModelPanel />}

      {!collapsed && <div className={styles.divider} />}

      {!collapsed && <div className={styles.navLabel}>Navigation</div>}
      <nav className={styles.nav}>
        {NAV_ITEMS.map((item) => {
          const itemLabel = item.labelKey ? L[item.labelKey] : item.label;
          return (
            <NavLink
              key={item.segment}
              to={`/workspace/${cid}/${item.segment}`}
              className={({ isActive }) => cn(
                styles.nav__item,
                collapsed && styles['nav__item--collapsed'],
                isActive && styles['nav__item--active'],
              )}
              title={collapsed ? itemLabel : undefined}
            >
              <span>{item.icon}</span>
              {!collapsed && ` ${itemLabel}`}
            </NavLink>
          );
        })}
      </nav>

      <div className={styles.footer}>
        <button
          type="button"
          className={styles.signOut}
          onClick={handleSignOut}
          title={collapsed ? 'Sign Out' : undefined}
        >
          {collapsed ? '🚪' : '🚪 Sign Out'}
        </button>
      </div>
    </aside>
  );
}
