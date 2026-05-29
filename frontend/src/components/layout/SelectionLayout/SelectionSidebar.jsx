import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAppDispatch } from '@app/hooks';
import { setSelectedProject, setSelectedCluster, setSelectedCourse } from '@features/dashboard/dashboardSlice';
import { useAuth } from '@hooks/useAuth';
import { ROLE_LABELS, ROLES, ROUTES } from '@utils/constants';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import styles from './SelectionSidebar.module.scss';

const ROLE_COLORS = {
  [ROLES.ADMIN]:    '#7c3aed',
  [ROLES.REVIEWER]: '#0f766e',
  [ROLES.AUTHOR]:   '#4338ca',
};

export default function SelectionSidebar({
  variant = 'project',
  projectName,
  clusterName,
  projectId,
  onBackClusters,
  onCreateProject,
  onCreateCluster,
  onCreateCourse,
  createLoading,
}) {
  const dispatch = useAppDispatch();
  const { user, role, logout, isAdmin, hasPermission } = useAuth();
  const navigate = useNavigate();

  function goDashboard() {
    dispatch(setSelectedProject(null));
    dispatch(setSelectedCluster(null));
    dispatch(setSelectedCourse(null));
    navigate(ROUTES.DASHBOARD);
  }

  function goClusters() {
    dispatch(setSelectedCluster(null));
    dispatch(setSelectedCourse(null));
    if (onBackClusters) onBackClusters();
    else navigate(-1);
  }
  const [showNewProject, setShowNewProject] = useState(false);
  const [showNewCluster, setShowNewCluster] = useState(false);
  const [showNewCourse, setShowNewCourse] = useState(false);
  const [form, setForm] = useState({ name: '', client: '', description: '' });

  function handleSignOut() {
    logout();
    navigate(ROUTES.LOGIN, { replace: true });
  }

  function resetForm() {
    setForm({ name: '', client: '', description: '' });
  }

  async function submitCreate(e) {
    e.preventDefault();
    if (!form.name.trim()) return;
    if (variant === 'project' && onCreateProject) {
      await onCreateProject({
        name: form.name.trim(),
        client_name: form.client.trim() || null,
        description: form.description.trim() || null,
      });
      setShowNewProject(false);
    } else if (variant === 'cluster' && onCreateCluster) {
      await onCreateCluster({
        name: form.name.trim(),
        description: form.description.trim() || null,
      });
      setShowNewCluster(false);
    } else if (variant === 'course' && onCreateCourse) {
      await onCreateCourse({
        name: form.name.trim(),
        description: form.description.trim() || null,
      });
      setShowNewCourse(false);
    }
    resetForm();
  }

  const roleColor = ROLE_COLORS[role] ?? '#4338ca';
  const roleLabel = ROLE_LABELS[role] ?? role;

  return (
    <aside className={styles.sidebar} aria-label="Selection navigation">
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
        <span className={styles.userPill__role} style={{ background: roleColor }}>{roleLabel}</span>
      </div>

      {(variant === 'cluster' || variant === 'course') && projectName && (
        <div className={styles.contextPill}>
          <div className={styles.contextPill__label}>Workspace</div>
          <div>📁 <strong>{projectName}</strong></div>
          {variant === 'course' && clusterName && (
            <div>🗂️ <strong>{clusterName}</strong></div>
          )}
        </div>
      )}

      {variant === 'cluster' && (
        <div className={styles.navRow}>
          <Button variant="ghost" size="sm" fullWidth onClick={goDashboard}>
            ← Projects
          </Button>
        </div>
      )}

      {variant === 'course' && (
        <div className={styles.navRow3}>
          <Button variant="ghost" size="sm" onClick={goDashboard}>← Projects</Button>
          <Button variant="ghost" size="sm" onClick={goClusters}>← Clusters</Button>
        </div>
      )}

      <div className={styles.divider} />

      {isAdmin && variant === 'project' && (
        <div className={styles.expander}>
          <button
            type="button"
            className={styles.expander__toggle}
            onClick={() => setShowNewProject((v) => !v)}
          >
            ➕ New Project {showNewProject ? '▾' : '▸'}
          </button>
          {showNewProject && (
            <form className={styles.form} onSubmit={submitCreate}>
              <Input label="Project Name *" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
              <Input label="Client Name" value={form.client} onChange={(e) => setForm((f) => ({ ...f, client: e.target.value }))} />
              <label className={styles.textareaLabel}>
                Description
                <textarea
                  rows={3}
                  value={form.description}
                  onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
                  className={styles.textarea}
                />
              </label>
              <Button type="submit" variant="primary" size="sm" fullWidth loading={createLoading}>
                Create Project
              </Button>
            </form>
          )}
        </div>
      )}

      {isAdmin && variant === 'project' && (
        <Button variant="ghost" size="sm" fullWidth className={styles.repoBtn} onClick={() => navigate(ROUTES.CENTRAL)}>
          🗄️ Repository
        </Button>
      )}

      {(hasPermission('course.create') || isAdmin) && variant === 'cluster' && (
        <div className={styles.expander}>
          <button type="button" className={styles.expander__toggle} onClick={() => setShowNewCluster((v) => !v)}>
            ➕ New Cluster {showNewCluster ? '▾' : '▸'}
          </button>
          {showNewCluster && (
            <form className={styles.form} onSubmit={submitCreate}>
              <Input label="Cluster Name *" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
              <label className={styles.textareaLabel}>
                Description
                <textarea rows={3} value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} className={styles.textarea} />
              </label>
              <Button type="submit" variant="primary" size="sm" fullWidth loading={createLoading}>Create Cluster</Button>
            </form>
          )}
        </div>
      )}

      {(hasPermission('course.create') || isAdmin) && variant === 'course' && (
        <div className={styles.expander}>
          <button type="button" className={styles.expander__toggle} onClick={() => setShowNewCourse((v) => !v)}>
            ➕ New Course {showNewCourse ? '▾' : '▸'}
          </button>
          {showNewCourse && (
            <form className={styles.form} onSubmit={submitCreate}>
              <Input label="Course Name *" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
              <label className={styles.textareaLabel}>
                Description
                <textarea rows={3} value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} className={styles.textarea} />
              </label>
              <Button type="submit" variant="primary" size="sm" fullWidth loading={createLoading}>Create Course</Button>
            </form>
          )}
        </div>
      )}

      <div className={styles.footer}>
        <Button variant="ghost" size="sm" fullWidth onClick={handleSignOut}>
          🚪 Sign Out
        </Button>
      </div>
    </aside>
  );
}
