import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast, { Toaster } from 'react-hot-toast';
import { platformService } from '@features/platform/services/platformService';
import { useAuth } from '@hooks/useAuth';
import { ROLE_LABELS, ROLES, ROUTES } from '@utils/constants';
import { extractErrorMessage } from '@utils/helpers';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import EmptyState from '@components/common/EmptyState/EmptyState';
import AppBrand from '@components/common/AppBrand/AppBrand';
import SelectionPageHeader from '@components/streamlit/SelectionPageHeader/SelectionPageHeader';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import styles from './TenantsPage.module.scss';

const EMPTY_FORM = {
  slug: '', name: '', max_users: 50,
  admin_username: '', admin_password: '', admin_display_name: '',
};

const ROLE_COLORS = {
  [ROLES.ADMIN]: '#7c3aed',
  [ROLES.REVIEWER]: '#0f766e',
  [ROLES.AUTHOR]: '#4338ca',
};

export default function TenantsPage() {
  const navigate = useNavigate();
  const { logout, user, role } = useAuth();

  const [tenants, setTenants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [form, setForm] = useState(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  const roleColor = ROLE_COLORS[role] ?? '#7c3aed';
  const roleLabel = ROLE_LABELS[role] ?? role ?? 'Admin';

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
    setSaving(true);
    try {
      await platformService.createTenant({
        slug: form.slug.trim().toLowerCase(),
        name: form.name.trim(),
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

  function handleSignOut() {
    logout();
    navigate(ROUTES.LOGIN, { replace: true });
  }

  return (
    <div className={styles.layout}>
      <aside className={styles.sidebar} aria-label="Platform navigation">
        <AppBrand />
        <div className={styles.userPill}>
          <div className={styles.userPill__label}>Signed in as</div>
          <div className={styles.userPill__name}>{user?.username || '—'}</div>
          <span className={styles.userPill__role} style={{ background: roleColor }}>{roleLabel}</span>
        </div>
        <div className={styles.navLabel}>Platform</div>
        <button type="button" className={`${styles.navBtn} ${styles.navBtnActive}`}>
          🏢 Tenants
        </button>
        <div className={styles.sidebarSpacer} />
        <button type="button" className={styles.signOut} onClick={handleSignOut}>
          🚪 Sign Out
        </button>
      </aside>

      <main className={styles.main}>
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
      <Toaster position="top-right" toastOptions={{ duration: 4000 }} />
    </div>
  );
}
