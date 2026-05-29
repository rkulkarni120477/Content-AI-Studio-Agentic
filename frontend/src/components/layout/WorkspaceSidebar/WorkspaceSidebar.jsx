import { NavLink, useNavigate, useParams } from 'react-router-dom';
import { useAppDispatch, useAppSelector } from '@app/hooks';
import {
  selectSelectedProject, selectSelectedCluster, selectSelectedCourse,
  setSelectedProject, setSelectedCluster, setSelectedCourse,
} from '@features/dashboard/dashboardSlice';
import { useAuth } from '@hooks/useAuth';
import { ROLE_LABELS, ROLES, ROUTES } from '@utils/constants';
import TargetModelPanel from './TargetModelPanel';
import styles from './WorkspaceSidebar.module.scss';

const NAV_ITEMS = [
  { label: 'Style',     icon: '🎨', segment: 'style' },
  { label: 'CDD',       icon: '📘', segment: 'cdd' },
  { label: 'Blueprint', icon: '🧩', segment: 'blueprint' },
  { label: 'Generate',  icon: '⚙️', segment: 'generate' },
  { label: 'Editor',    icon: '✏️', segment: 'editor' },
  { label: 'Workflow',  icon: '🚦', segment: 'workflow' },
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
  const cid = courseId || course?.id;

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

  return (
    <aside className={styles.sidebar} aria-label="Workspace navigation">
      <div className={styles.brand}>
        <span className={styles.brand__logo} aria-hidden="true">🎓</span>
        <div>
          <div className={styles.brand__title}>Content AI Studio</div>
          <div className={styles.brand__tag}>Enterprise AI Platform</div>
        </div>
      </div>

      <div className={styles.userPill}>
        <div className={styles.userPill__label}>Signed in as</div>
        <div className={styles.userPill__name}>{user?.username}</div>
        <span className={styles.userPill__role} style={{ background: roleColor }}>
          {ROLE_LABELS[role] ?? role}
        </span>
      </div>

      <div className={styles.contextPill}>
        <div className={styles.contextPill__label}>Workspace</div>
        <div>📁 <strong>{proj?.name ?? '—'}</strong></div>
        <div>🗂️ <strong>{cluster?.name ?? '—'}</strong></div>
        <div>📖 <strong>{course?.name ?? '—'}</strong></div>
      </div>

      <div className={styles.backRow}>
        <button type="button" className={styles.backBtn} onClick={goProjects}>← Projects</button>
        <button type="button" className={styles.backBtn} onClick={goClusters}>← Clusters</button>
        <button type="button" className={styles.backBtn} onClick={goCourses}>← Courses</button>
      </div>

      <div className={styles.divider} />

      <div className={styles.statePill}>
        <div className={styles.statePill__label}>Global State</div>
        <div className={styles.statePill__row}>
          <span className={styles.statePill__dot} style={{ background: '#9ca3af' }} />
          🎨 Style: <strong>—</strong>
        </div>
        <div className={styles.statePill__row}>
          <span className={styles.statePill__dot} style={{ background: '#9ca3af' }} />
          📘 CDD: <strong>—</strong>
        </div>
      </div>

      <TargetModelPanel />

      <div className={styles.divider} />

      <div className={styles.navLabel}>Navigation</div>
      <nav className={styles.nav}>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.segment}
            to={`/workspace/${cid}/${item.segment}`}
            className={({ isActive }) => `${styles.nav__item} ${isActive ? styles['nav__item--active'] : ''}`}
          >
            <span>{item.icon}</span> {item.label}
          </NavLink>
        ))}
      </nav>

      <div className={styles.footer}>
        <button type="button" className={styles.signOut} onClick={handleSignOut}>🚪 Sign Out</button>
      </div>
    </aside>
  );
}
