import { useEffect, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAppDispatch } from '@app/hooks';
import { setSelectedProject, setSelectedCluster, setSelectedCourse } from '@features/dashboard/dashboardSlice';
import { useAuth } from '@hooks/useAuth';
import { useLabels } from '@hooks/useLabels';
// ROLE_LABELS/ROLES were only used by the sidebar identity chip, now moved to the top header.
import { ROUTES } from '@utils/constants';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import MultiSelect from '@components/common/MultiSelect/MultiSelect';
import Select from '@components/common/Select/Select';
import AppBrand from '@components/common/AppBrand/AppBrand';
import SidebarToggle from '@components/layout/SidebarToggle/SidebarToggle';
import { useSidebarCollapsed } from '@hooks/useSidebarCollapsed';
import { cn } from '@utils/helpers';
import {
  CLUSTER_PROMPT_API_MESSAGE,
  isClusterPromptApiAvailable,
} from '@features/clusterPrompt/clusterPromptApiDeps';
import { clusterPromptService } from '@features/clusterPrompt/clusterPromptService';
import { promptLabel } from '@features/clusterPrompt/clusterPromptUtils';
import { HEADER_ACTIONS, MAIN_NAV } from '@features/promptLibrary/utils/nav';
import styles from './SelectionSidebar.module.scss';

const LMS_PLATFORM_OPTIONS = ['Canvas', 'Moodle', 'TalentLMS', 'Docebo'];
const WORKFLOW_OPTIONS = [
  { value: 'print', label: 'Print' },
  { value: 'digital', label: 'Digital' },
];
const CLIENT_OPTIONS = [
  { value: 'cengage', label: 'Cengage' },
  { value: 'aim', label: 'AIM' },
  { value: 'academian', label: 'Academian' },
  { value: 'demo', label: 'Demo' },
];

// ROLE_COLORS was used by the sidebar identity chip, now moved to the top header.
// const ROLE_COLORS = {
//   [ROLES.ADMIN]:    '#7c3aed',
//   [ROLES.REVIEWER]: '#0f766e',
//   [ROLES.AUTHOR]:   '#4338ca',
// };

export default function SelectionSidebar({
  variant = 'project',
  projectName,
  clusterName,
  projectId,
  onBackClusters,
  onCreateProject,
  onCreateCluster,
  onCreateCourse,
  onRequestCreateCourse,
  createLoading,
}) {
  const dispatch = useAppDispatch();
  const { role, logout, isAdmin, hasPermission } = useAuth();
  const L = useLabels();
  const navigate = useNavigate();
  const { pathname } = useLocation();

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
  const [collapsed, toggleCollapsed] = useSidebarCollapsed();
  const [showNewProject, setShowNewProject] = useState(false);
  const [showNewCluster, setShowNewCluster] = useState(false);
  const [showNewCourse, setShowNewCourse] = useState(false);
  // Prompt options group (Category screen) — open by default so every prompt
  // destination is visible without leaving the view.
  const [showPrompts, setShowPrompts] = useState(true);
  // The PL visibility rules read user.role; useAuth exposes role separately.
  const promptNavItems = [...MAIN_NAV, ...HEADER_ACTIONS].filter((i) => i.visible({ role }));
  // Prompt Library pages host this same sidebar, so the group doubles as the
  // section's nav — highlight the destination we're currently on.
  const isPromptItemActive = (item) => (item.end ? pathname === item.to : pathname.startsWith(item.to));

  // Collapsed-rail ➕: expand the sidebar and open the create form in one click.
  function expandWith(openForm) {
    toggleCollapsed();
    openForm(true);
  }
  const [form, setForm] = useState({
    name: '',
    client: 'cengage',
    description: '',
    lms_platform: '',
    workflow: '',
  });
  const [copyPromptIds, setCopyPromptIds] = useState([]);
  const [promptOptions, setPromptOptions] = useState([]);
  const clusterPromptApiReady = isClusterPromptApiAvailable();

  useEffect(() => {
    if (!clusterPromptApiReady || variant !== 'cluster' || !showNewCluster) return;
    clusterPromptService.list()
      .then((res) => {
        setPromptOptions(
          (res.items || []).map((p) => ({ value: String(p.id), label: promptLabel(p) })),
        );
      })
      .catch(() => setPromptOptions([]));
  }, [clusterPromptApiReady, variant, showNewCluster]);

  function handleSignOut() {
    logout();
    navigate(ROUTES.LOGIN, { replace: true });
  }

  function resetForm() {
    setForm({
      name: '',
      client: 'cengage',
      description: '',
      lms_platform: '',
      workflow: '',
    });
    setCopyPromptIds([]);
  }

  async function submitCreate(e) {
    e.preventDefault();
    if (!form.name.trim()) return;
    if (variant === 'project' && onCreateProject) {
      await onCreateProject({
        name: form.name.trim(),
        client_name: form.client || 'demo',
        description: form.description.trim() || null,
      });
      setShowNewProject(false);
    } else if (variant === 'cluster' && onCreateCluster) {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim() || null,
      };
      if (clusterPromptApiReady && copyPromptIds.length) {
        payload.copy_prompt_ids = copyPromptIds.map(Number);
      }
      if (form.lms_platform) {
        payload.lms_platform = form.lms_platform;
      }
      if (form.workflow) {
        payload.workflow = form.workflow;
      }
      await onCreateCluster(payload);
      setShowNewCluster(false);
    } else if (variant === 'course' && onCreateCourse) {
      const payload = {
        name: form.name.trim(),
        description: form.description.trim() || null,
      };
      if (form.workflow) {
        payload.workflow = form.workflow;
      }
      await onCreateCourse(payload);
      setShowNewCourse(false);
    }
    resetForm();
  }

  // Identity (roleColor/roleLabel) moved to the top header (HeaderUser).
  // const roleColor = ROLE_COLORS[role] ?? '#4338ca';
  // const roleLabel = ROLE_LABELS[role] ?? role;

  return (
    <aside
      className={cn(styles.sidebar, collapsed && styles['sidebar--collapsed'])}
      aria-label="Selection navigation"
    >
      <div className={cn(styles.header, collapsed && styles['header--collapsed'])}>
        <AppBrand compact={collapsed} />
        <SidebarToggle collapsed={collapsed} onToggle={toggleCollapsed} />
      </div>

      {/*
      {collapsed ? (
        <div
          className={styles.userDot}
          style={{ background: roleColor }}
          title={`${user?.username} — ${roleLabel}`}
        >
          {(user?.username || '?').charAt(0).toUpperCase()}
        </div>
      ) : (
        <div className={styles.userPill}>
          <div className={styles.userPill__label}>Signed in as</div>
          <div className={styles.userPill__name}>{user?.username}</div>
          <span className={styles.userPill__role} style={{ background: roleColor }}>{roleLabel}</span>
      </div>
      )}
      */}

      {!collapsed && (variant === 'cluster' || variant === 'course') && projectName && (
        <div className={styles.contextPill}>
          <div className={styles.contextPill__label}>Workspace</div>
          <div>📁 <strong>{projectName}</strong></div>
          {variant === 'course' && clusterName && (
            <div>🗂️ <strong>{clusterName}</strong></div>
          )}
        </div>
      )}

      {variant === 'cluster' && (
        <div className={collapsed ? styles.navCol : (onBackClusters ? styles.navRow3 : styles.navRow)}>
          <button
            type="button"
            className={cn(styles.navBtn, collapsed && styles.iconOnly)}
            onClick={goDashboard}
            title={collapsed ? 'Back to Projects' : undefined}
          >
            {collapsed ? '🏠' : '← Projects'}
          </button>
          {/* Prompt Library hosts this sidebar too; give it a way back into the
              project's Category/Course drill-down (ClustersPage never passes
              onBackClusters, so it stays hidden there). */}
          {onBackClusters && (
            <button
              type="button"
              className={cn(styles.navBtn, collapsed && styles.iconOnly)}
              onClick={goClusters}
              title={collapsed ? 'Back to Categories' : undefined}
            >
              {collapsed ? '🗂️' : '← Categories'}
            </button>
          )}
        </div>
      )}

      {variant === 'course' && (
        <div className={collapsed ? styles.navCol : styles.navRow3}>
          <button
            type="button"
            className={cn(styles.navBtn, collapsed && styles.iconOnly)}
            onClick={goDashboard}
            title={collapsed ? 'Back to Projects' : undefined}
          >
            {collapsed ? '🏠' : '← Projects'}
          </button>
          <button
            type="button"
            className={cn(styles.navBtn, collapsed && styles.iconOnly)}
            onClick={goClusters}
            title={collapsed ? 'Back to Categories' : undefined}
          >
            {collapsed ? '🗂️' : '← Categories'}
          </button>
        </div>
      )}

      <div className={styles.divider} />

      {isAdmin && variant === 'project' && collapsed && (
        <button
          type="button"
          className={cn(styles.navBtn, styles.iconOnly)}
          title="New Project"
          onClick={() => expandWith(setShowNewProject)}
        >
          ➕
        </button>
      )}

      {isAdmin && variant === 'project' && !collapsed && (
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
              <Select
                label="Client *"
                options={CLIENT_OPTIONS}
                value={form.client}
                onChange={(e) => setForm((f) => ({ ...f, client: e.target.value }))}
              />
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

      {(hasPermission('course.create') || isAdmin) && variant === 'cluster' && onCreateCluster && collapsed && (
        <button
          type="button"
          className={cn(styles.navBtn, styles.iconOnly)}
          title="New Category"
          onClick={() => expandWith(setShowNewCluster)}
        >
          ➕
        </button>
      )}

      {(hasPermission('course.create') || isAdmin) && variant === 'cluster' && onCreateCluster && !collapsed && (
        <div className={styles.expander}>
          <button type="button" className={styles.expander__toggle} onClick={() => setShowNewCluster((v) => !v)}>
            ➕ New Category {showNewCluster ? '▾' : '▸'}
          </button>
          {showNewCluster && (
            <form className={styles.form} onSubmit={submitCreate}>
              <Input label="Category Name *" value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
              <label className={styles.textareaLabel}>
                Description
                <textarea rows={3} value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} className={styles.textarea} />
              </label>
              <MultiSelect
                label="Choose Category Prompts"
                hint={
                  clusterPromptApiReady
                    ? `Select category prompts to auto-inject into the ${L.style} context for every ${L.titleLower} in this category. Optional.`
                    : `${CLUSTER_PROMPT_API_MESSAGE} Selection is preserved in UI only until APIs are available.`
                }
                options={promptOptions}
                value={copyPromptIds}
                onChange={setCopyPromptIds}
                disabled={!clusterPromptApiReady}
              />
              <Select
                label="Choose LMS Platform"
                placeholder="Choose options"
                options={LMS_PLATFORM_OPTIONS}
                value={form.lms_platform}
                onChange={(e) => setForm((f) => ({ ...f, lms_platform: e.target.value }))}
              />
              <Select
                label="Choose workflow"
                placeholder="Choose options"
                options={WORKFLOW_OPTIONS}
                value={form.workflow}
                onChange={(e) => setForm((f) => ({ ...f, workflow: e.target.value }))}
              />
              <Button type="submit" variant="primary" size="sm" fullWidth loading={createLoading}>Create Category</Button>
            </form>
          )}
        </div>
      )}

      {variant === 'cluster' && collapsed && (
        <button
          type="button"
          className={cn(styles.navBtn, styles.iconOnly)}
          title="Prompts"
          onClick={() => expandWith(setShowPrompts)}
        >
          📚
        </button>
      )}

      {variant === 'cluster' && !collapsed && (
        <div className={styles.expander}>
          <button
            type="button"
            className={styles.expander__toggle}
            onClick={() => setShowPrompts((v) => !v)}
          >
            📚 Prompts {showPrompts ? '▾' : '▸'}
          </button>
          {showPrompts && (
            <div className={styles.navCol}>
              {promptNavItems.map((item) => (
                <button
                  key={item.to}
                  type="button"
                  className={cn(styles.navBtn, isPromptItemActive(item) && styles.navBtnActive)}
                  onClick={() => navigate(item.to)}
                >
                  {item.icon} {item.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {(hasPermission('course.create') || isAdmin) && variant === 'course' && collapsed && (
        <button
          type="button"
          className={cn(styles.navBtn, styles.iconOnly)}
          title={`Create ${L.title}`}
          onClick={onRequestCreateCourse ? onRequestCreateCourse : () => expandWith(setShowNewCourse)}
        >
          ➕
        </button>
      )}

      {/* Reverse pipeline: when a modal handler is supplied, the course create
          action opens the New-vs-Import fork instead of the inline form. The
          "New Course" choice in that modal calls the identical onCreateCourse
          path, so the scratch flow is unchanged. See reverse_cas.md. */}
      {(hasPermission('course.create') || isAdmin) && variant === 'course' && !collapsed && onRequestCreateCourse && (
        <div className={styles.expander}>
          <button type="button" className={styles.expander__toggle} onClick={onRequestCreateCourse}>
            ➕ Create {L.title}
          </button>
        </div>
      )}

      {(hasPermission('course.create') || isAdmin) && variant === 'course' && !collapsed && !onRequestCreateCourse && (
        <div className={styles.expander}>
          <button type="button" className={styles.expander__toggle} onClick={() => setShowNewCourse((v) => !v)}>
            ➕ New {L.title} {showNewCourse ? '▾' : '▸'}
          </button>
          {showNewCourse && (
            <form className={styles.form} onSubmit={submitCreate}>
              <Input label={`${L.title} Name *`} value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} required />
              <label className={styles.textareaLabel}>
                Description
                <textarea rows={3} value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} className={styles.textarea} />
              </label>
              <Select
                label="Choose workflow"
                placeholder="Choose options"
                options={WORKFLOW_OPTIONS}
                value={form.workflow}
                onChange={(e) => setForm((f) => ({ ...f, workflow: e.target.value }))}
              />
              <Button type="submit" variant="primary" size="sm" fullWidth loading={createLoading}>Create {L.title}</Button>
            </form>
          )}
        </div>
      )}

      <div className={styles.footer}>
        <button
          type="button"
          className={cn(styles.navBtn, collapsed && styles.iconOnly)}
          onClick={handleSignOut}
          title={collapsed ? 'Sign Out' : undefined}
        >
          {collapsed ? '🚪' : '🚪 Sign Out'}
        </button>
      </div>
    </aside>
  );
}
