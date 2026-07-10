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
import { ROLE_LABELS, ROLES, ROUTES } from '@utils/constants';
import TargetModelPanel from './TargetModelPanel';
import GettingStartedGuide from '@components/layout/GettingStartedGuide/GettingStartedGuide';
import AppBrand from '@components/common/AppBrand/AppBrand';
import SidebarToggle from '@components/layout/SidebarToggle/SidebarToggle';
import { useSidebarCollapsed } from '@hooks/useSidebarCollapsed';
import { cn } from '@utils/helpers';
import { flushDeferredToasts } from '@utils/deferredToast';
import styles from './WorkspaceSidebar.module.scss';

const NAV_ITEMS = [
  { label: 'Source Library', icon: '📚', segment: 'sources' },
  { label: 'Style',     icon: '🎨', segment: 'style' },
  { label: 'CDD',       icon: '📘', segment: 'cdd' },
  { label: 'Blueprint', icon: '🧩', segment: 'blueprint' },
  { label: 'Generate',  icon: '⚙️', segment: 'generate' },
  { label: 'Editor',    icon: '✏️', segment: 'editor' },
  { label: 'Workflow',  icon: '🚦', segment: 'workflow' },
  { label: 'Export',    icon: '📦', segment: 'export' },
  { label: 'Analytics', icon: '📊', segment: 'analytics' },
];

const ROLE_COLORS = {
  [ROLES.ADMIN]:    '#7c3aed',
  [ROLES.REVIEWER]: '#0f766e',
  [ROLES.AUTHOR]:   '#4338ca',
};

export default function WorkspaceSidebar() {
  const { courseId } = useParams();
  const navigate = useNavigate();
  const dispatch = useAppDispatch();
  const { user, role, logout } = useAuth();
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
    dispatch(fetchStylesThunk());
  }, [cid, dispatch]);

  function handleSignOut() {
    logout();
    navigate(ROUTES.LOGIN, { replace: true });
  }

  function goProjects() {
    dispatch(setSelectedProject(null));
    dispatch(setSelectedCluster(null));
    dispatch(setSelectedCourse(null));
    navigate(ROUTES.DASHBOARD);
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

  const roleColor = ROLE_COLORS[role] ?? '#4338ca';
  const styleLabel = activeStyle?.name || '—';
  const cddLabel = activeCdd
    ? `${activeCdd.title || activeCdd.course_title || 'CDD'} (${activeCdd.active_version || 'v1'})`
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
          title={collapsed ? 'Back to Courses' : undefined}
        >
          {collapsed ? '📖' : <>←<br />Courses</>}
        </button>
      </div>

      <div className={styles.divider} />

      {!collapsed && (
        <div className={styles.statePill}>
          <div className={styles.statePill__label}>Global State</div>
          <div className={styles.statePill__row}>
            <span className={styles.statePill__dot} style={{ background: activeStyle ? '#10b981' : '#9ca3af' }} />
            🎨 Style: <strong>{styleLabel}</strong>
          </div>
          <div className={styles.statePill__row}>
            <span className={styles.statePill__dot} style={{ background: activeCdd ? '#10b981' : '#9ca3af' }} />
            📘 CDD: <strong>{cddLabel}</strong>
          </div>
        </div>
      )}

      {!collapsed && <GettingStartedGuide />}

      {!collapsed && <TargetModelPanel />}

      {!collapsed && <div className={styles.divider} />}

      {!collapsed && <div className={styles.navLabel}>Navigation</div>}
      <nav className={styles.nav}>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.segment}
            to={`/workspace/${cid}/${item.segment}`}
            className={({ isActive }) => cn(
              styles.nav__item,
              collapsed && styles['nav__item--collapsed'],
              isActive && styles['nav__item--active'],
            )}
            title={collapsed ? item.label : undefined}
          >
            <span>{item.icon}</span>
            {!collapsed && ` ${item.label}`}
          </NavLink>
        ))}
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
