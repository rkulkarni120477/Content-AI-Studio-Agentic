import { Fragment, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast, { Toaster } from 'react-hot-toast';
import { platformService } from '@features/platform/services/platformService';
import { analyticsService } from '@features/analytics/services/analyticsService';
import { useAuth } from '@hooks/useAuth';
import { ROLE_LABELS, ROLES, ROUTES } from '@utils/constants';
import { extractErrorMessage, formatTimestamp } from '@utils/helpers';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import AppBrand from '@components/common/AppBrand/AppBrand';
import IdentityBar from '@components/common/HeaderUser/IdentityBar';
// Sidebar UserPill moved to IdentityBar in the main header — keep import for easy restore.
// import UserPill from '@components/common/UserPill/UserPill';
import SelectionPageHeader from '@components/streamlit/SelectionPageHeader/SelectionPageHeader';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import Select from '@components/common/Select/Select';
import Pagination from '@components/common/Pagination/Pagination';
import TenantLabelsPanel from '@features/platform/components/TenantLabelsPanel/TenantLabelsPanel';
import { describeOverrides } from '@config/tenantLabels';
import styles from './TenantsPage.module.scss';

const EMPTY_FORM = {
  slug: '', name: '', client_name: '', max_users: 50,
  admin_username: '', admin_password: '', admin_display_name: '',
};

// Which client's content the org works on. Drives Source Library access for the
// org's members, so it is required at creation. Same options as Edit Project.
const CLIENT_OPTIONS = [
  { value: 'Cengage', label: 'Cengage' },
  { value: 'AIM', label: 'AIM' },
  { value: 'Academian', label: 'Academian' },
  { value: 'Demo', label: 'Demo' },
];

const ROLE_COLORS = {
  [ROLES.ADMIN]: '#7c3aed',
  [ROLES.REVIEWER]: '#0f766e',
  [ROLES.AUTHOR]: '#4338ca',
};

// These can be multi-KB blobs — shown via the "View Content" modal instead of
// the inline key/value grid, so the grid stays scannable.
const CONTENT_KEYS = ['system_prompt', 'user_prompt', 'output'];

export default function TenantsPage() {
  const navigate = useNavigate();
  const { logout, user, role } = useAuth();

  const [tenants, setTenants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const [view, setView] = useState('tenants'); // 'tenants' | 'audit' | 'config'
  const [auditItems, setAuditItems] = useState([]);
  const [auditLoading, setAuditLoading] = useState(false);
  const [auditPage, setAuditPage] = useState(1);
  const [auditTotal, setAuditTotal] = useState(0);
  const AUDIT_PAGE_SIZE = 25;
  const [expandedAuditId, setExpandedAuditId] = useState(null);
  const [viewContentEvent, setViewContentEvent] = useState(null);
  // Which tenant's labels are being edited; null shows the organization list.
  const [configTenantId, setConfigTenantId] = useState(null);

  const roleColor = ROLE_COLORS[role] ?? '#7c3aed';
  const roleLabel = ROLE_LABELS[role] ?? role ?? 'Admin';
  // Read from the loaded list so a save + reload refreshes the editor in place.
  const configTenant = tenants.find((t) => t.id === configTenantId) || null;

  useEffect(() => { load(); }, []);

  async function load() {
    setLoading(true);
    try {
      setTenants(await platformService.listTenants());
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(e) {
    e.preventDefault();
    if (!form.client_name) {
      toast.error('Please select a Client — it decides which Source Library content the organization can access.');
      return;
    }
    setSaving(true);
    try {
      await platformService.createTenant({
        slug: form.slug.trim().toLowerCase(),
        name: form.name.trim(),
        client_name: form.client_name,
        max_users: Number(form.max_users) || 50,
        admin_username: form.admin_username.trim(),
        admin_password: form.admin_password,
        admin_display_name: form.admin_display_name.trim() || form.admin_username.trim(),
      });
      toast.success('Tenant created');
      setShowNew(false);
      setForm(EMPTY_FORM);
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function toggleStatus(t) {
    const next = t.status === 'active' ? 'suspended' : 'active';
    try {
      await platformService.updateTenant(t.id, { status: next });
      toast.success(`Tenant ${next}`);
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    }
  }

  function set(k, v) { setForm((f) => ({ ...f, [k]: v })); }

  async function openAuditLog(page = 1) {
    setView('audit');
    setAuditLoading(true);
    try {
      const res = await analyticsService.getAuditTrail({ page, page_size: AUDIT_PAGE_SIZE });
      setAuditItems(res?.items || []);
      setAuditPage(page);
      setAuditTotal(res?.total ?? 0);
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setAuditLoading(false);
    }
  }

  function openConfiguration() {
    setView('config');
    setConfigTenantId(null);
  }

  function toggleAuditDetail(id) {
    setExpandedAuditId((cur) => (cur === id ? null : id));
  }

  function tenantName(projectId) {
    if (!projectId) return '—';
    return tenants.find((t) => t.id === projectId)?.name || `#${projectId}`;
  }

  function handleSignOut() {
    logout();
    navigate(ROUTES.LOGIN, { replace: true });
  }

  return (
    <div className={styles.layout}>
      <aside className={styles.sidebar} aria-label="Platform navigation">
        <AppBrand />
        {/* User identity moved to the top IdentityBar — kept for easy restore.
        <UserPill username={user?.username} roleLabel={roleLabel} roleColor={roleColor} />
        */}
        <div className={styles.navLabel}>Platform</div>
        <button
          type="button"
          className={`${styles.navBtn} ${view === 'tenants' ? styles.navBtnActive : ''}`}
          onClick={() => setView('tenants')}
        >
          🏢 Tenants
        </button>
        <button
          type="button"
          className={`${styles.navBtn} ${view === 'audit' ? styles.navBtnActive : ''}`}
          onClick={() => openAuditLog()}
        >
          📜 Audit Log
        </button>
        <button
          type="button"
          className={`${styles.navBtn} ${view === 'config' ? styles.navBtnActive : ''}`}
          onClick={openConfiguration}
        >
          ⚙️ Configuration
        </button>
        <div className={styles.sidebarSpacer} />
        <button type="button" className={styles.signOut} onClick={handleSignOut}>
          🚪 Sign Out
        </button>
      </aside>

      <main className={styles.main}>
        <IdentityBar username={user?.username} roleLabel={roleLabel} roleColor={roleColor} />
        <div className={styles.mainBody}>
        {view === 'tenants' ? (
          <>
            <div className={styles.headerRow}>
              <SelectionPageHeader
                eyebrow="Platform Admin"
                title="Tenants"
                subtitle="Manage organizations, licenses, and status."
              />
              <Button variant="primary" onClick={() => setShowNew(true)}>+ New Tenant</Button>
            </div>

            <SectionBadge
              icon="🏢"
              title="Organization Directory"
              subtitle="Create tenants, manage user seats, and open Roles, Category, or Users for each organization."
            />

            {loading ? (
              <div className={styles.center}><Loader size="lg" /></div>
            ) : tenants.length === 0 ? (
              <EmptyState
                icon="🏢"
                title="No tenants yet"
                message="Create the first organization to get started."
                action={() => setShowNew(true)}
                actionLabel="+ New Tenant"
              />
            ) : (
              <div className={styles.card}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Name</th>
                      <th>Code</th>
                      <th>Client</th>
                      <th>Users</th>
                      <th>Status</th>
                      <th className={styles.actionsCol}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tenants.map((t) => (
                      <tr key={t.id}>
                        <td className={styles.name}>{t.name}</td>
                        <td><code className={styles.code}>{t.slug}</code></td>
                        <td>{t.client_name || <span title="No client set — members will fall back to the default client in Source Library">⚠ not set</span>}</td>
                        <td>{t.active_users} / {t.max_users}</td>
                        <td>
                          <span className={t.status === 'active' ? styles.statusActive : styles.statusSuspended}>
                            {t.status === 'active' ? '● Active' : '○ Suspended'}
                          </span>
                        </td>
                        <td className={styles.actions}>
                          <Button variant="ghost" size="xs" onClick={() => navigate(ROUTES.TENANT_USERS(t.id))}>
                            Users
                          </Button>
                          <Button variant="ghost" size="xs" onClick={() => navigate(ROUTES.TENANT_ROLES(t.id))}>
                            Roles
                          </Button>
                          <Button variant="ghost" size="xs" onClick={() => navigate(ROUTES.PROJECT_CLUSTERS(t.id))}>
                            Category
                          </Button>
                          <Button variant="secondary" size="xs" onClick={() => toggleStatus(t)}>
                            {t.status === 'active' ? 'Suspend' : 'Activate'}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : view === 'config' ? (
          configTenant ? (
            <TenantLabelsPanel
              tenant={configTenant}
              onBack={() => setConfigTenantId(null)}
              onSaved={load}
            />
          ) : (
            <>
              <div className={styles.headerRow}>
                <SelectionPageHeader
                  eyebrow="Platform Admin"
                  title="Configuration"
                  subtitle="Rename the pipeline stages each organization sees."
                />
              </div>

              <SectionBadge
                icon="⚙️"
                title="Label Overrides"
                subtitle="Pick an organization to rename what its members call Title, Style, CDD and Blueprint. Everyone in that organization sees the new wording."
              />

              {loading ? (
                <div className={styles.center}><Loader size="lg" /></div>
              ) : tenants.length === 0 ? (
                <EmptyState
                  icon="🏢"
                  title="No tenants yet"
                  message="Create an organization first, then you can rename its labels."
                />
              ) : (
                <div className={styles.card}>
                  <table className={styles.table}>
                    <thead>
                      <tr>
                        <th>Name</th>
                        <th>Code</th>
                        <th>Client</th>
                        <th>Renamed labels</th>
                        <th>Status</th>
                        <th className={styles.actionsCol}>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tenants.map((t) => {
                        const renamed = describeOverrides(t.ui_labels);
                        return (
                          <tr key={t.id}>
                            <td className={styles.name}>{t.name}</td>
                            <td><code className={styles.code}>{t.slug}</code></td>
                            <td>{t.client_name || '—'}</td>
                            <td>
                              {renamed.length === 0 ? (
                                <span className={styles.labelDefault}>Using defaults</span>
                              ) : (
                                renamed.map((r) => (
                                  <span key={r.key} className={styles.labelChip}>
                                    {r.from} → {r.to}
                                  </span>
                                ))
                              )}
                            </td>
                            <td>
                              <span className={t.status === 'active' ? styles.statusActive : styles.statusSuspended}>
                                {t.status === 'active' ? '● Active' : '○ Suspended'}
                              </span>
                            </td>
                            <td className={styles.actions}>
                              <Button variant="ghost" size="xs" onClick={() => setConfigTenantId(t.id)}>
                                Configure
                              </Button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </>
          )
        ) : (
          <>
            <div className={styles.headerRow}>
              <SelectionPageHeader
                eyebrow="Platform Admin"
                title="Audit Log"
                subtitle="Structured log of every significant action across the platform."
              />
            </div>

            <SectionBadge
              icon="📜"
              title="Audit Trail"
              subtitle="Who did what, when — including exactly what went into each generation."
            />

            {auditLoading ? (
              <div className={styles.center}><Loader size="lg" /></div>
            ) : auditItems.length === 0 ? (
              <EmptyState icon="📜" title="No audit events yet" message="Audit events appear here as users work in the platform." />
            ) : (
              <div className={styles.card}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>When</th>
                      <th>User</th>
                      <th>Tenant</th>
                      <th>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {auditItems.map((ev) => (
                      <Fragment key={ev.id}>
                        <tr className={styles.auditRow} onClick={() => toggleAuditDetail(ev.id)}>
                          <td>{formatTimestamp(ev.created_at)}</td>
                          <td>{ev.actor}</td>
                          <td>{tenantName(ev.project_id)}</td>
                          <td>{ev.icon || ''} {ev.label || ev.action}</td>
                        </tr>
                        {expandedAuditId === ev.id && (
                          <tr className={styles.auditDetailRow}>
                            <td colSpan={4}>
                              {ev.metadata && Object.keys(ev.metadata).length > 0 ? (
                                <>
                                  <dl className={styles.auditDetail}>
                                    {Object.entries(ev.metadata).map(([k, v]) => (
                                      CONTENT_KEYS.includes(k) || v === null || v === undefined || v === '' ? null : (
                                        <div key={k} className={styles.auditDetail__row}>
                                          <dt>{k}</dt>
                                          <dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd>
                                        </div>
                                      )
                                    ))}
                                  </dl>
                                  {CONTENT_KEYS.some((k) => ev.metadata[k]) && (
                                    <Button variant="secondary" size="xs" onClick={() => setViewContentEvent(ev)}>
                                      📄 View Content
                                    </Button>
                                  )}
                                </>
                              ) : (
                                <p className={styles.emptyHint}>No additional details logged for this event.</p>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {!auditLoading && auditTotal > AUDIT_PAGE_SIZE && (
              <div className={styles.auditPagination}>
                <span className={styles.auditPagination__count}>{auditTotal} result{auditTotal !== 1 ? 's' : ''}</span>
                <Pagination
                  current={auditPage}
                  total={Math.ceil(auditTotal / AUDIT_PAGE_SIZE)}
                  onChange={(p) => openAuditLog(p)}
                />
              </div>
            )}
          </>
        )}
        </div>
      </main>

      <Modal
        open={showNew}
        onClose={() => setShowNew(false)}
        title="New Tenant"
        size="md"
        footer={null}
      >
        <p className={styles.modalSub}>Creates the organization and an initial tenant admin account.</p>
        <form onSubmit={handleCreate} className={styles.form}>
          <Input
            label="Organization code *"
            value={form.slug}
            onChange={(e) => set('slug', e.target.value)}
            placeholder="e.g. aim003"
            required
          />
          <Input
            label="Display name *"
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            placeholder="e.g. AIM 16 Block Development"
            required
          />
          <Select
            label="Client *"
            options={[{ value: '', label: 'Select client…' }, ...CLIENT_OPTIONS]}
            value={form.client_name}
            onChange={(e) => set('client_name', e.target.value)}
            required
          />
          <Input
            label="Max users (license)"
            type="number"
            min={1}
            value={form.max_users}
            onChange={(e) => set('max_users', e.target.value)}
          />

          <div className={styles.sectionLabel}>Initial tenant admin</div>
          <Input
            label="Username *"
            value={form.admin_username}
            onChange={(e) => set('admin_username', e.target.value)}
            required
          />
          <Input
            label="Password *"
            type="password"
            value={form.admin_password}
            onChange={(e) => set('admin_password', e.target.value)}
            minLength={6}
            required
          />
          <Input
            label="Display name"
            value={form.admin_display_name}
            onChange={(e) => set('admin_display_name', e.target.value)}
          />

          <div className={styles.formActions}>
            <Button type="button" variant="ghost" onClick={() => setShowNew(false)}>Cancel</Button>
            <Button type="submit" variant="primary" loading={saving}>Create Tenant</Button>
          </div>
        </form>
      </Modal>

      <Modal
        open={Boolean(viewContentEvent)}
        onClose={() => setViewContentEvent(null)}
        title={`Content — ${viewContentEvent?.label || viewContentEvent?.action || ''}`}
        size="xl"
        footer={null}
      >
        {viewContentEvent && (
          <div className={styles.contentView}>
            {viewContentEvent.metadata?.input_mode && (
              <span className={
                viewContentEvent.metadata.input_mode === 'truncated'
                  ? styles.inputModeBadgeTruncated
                  : styles.inputModeBadgeFull
              }>
                {viewContentEvent.metadata.input_mode === 'truncated'
                  ? '✂️ Source content was truncated to fit the model'
                  : '✅ Full, untruncated content was sent'}
              </span>
            )}
            {viewContentEvent.metadata?.system_prompt && (
              <div className={styles.contentBlock}>
                <div className={styles.contentBlock__label}>System Prompt</div>
                <pre className={styles.contentBlock__body}>{viewContentEvent.metadata.system_prompt}</pre>
              </div>
            )}
            {viewContentEvent.metadata?.user_prompt && (
              <div className={styles.contentBlock}>
                <div className={styles.contentBlock__label}>Full Input (prompt + source content)</div>
                <pre className={styles.contentBlock__body}>{viewContentEvent.metadata.user_prompt}</pre>
              </div>
            )}
            {viewContentEvent.metadata?.output && (
              <div className={styles.contentBlock}>
                <div className={styles.contentBlock__label}>Output</div>
                <pre className={styles.contentBlock__body}>{viewContentEvent.metadata.output}</pre>
              </div>
            )}
          </div>
        )}
      </Modal>

      <Toaster position="top-right" toastOptions={{ duration: 4000 }} />
    </div>
  );
}
