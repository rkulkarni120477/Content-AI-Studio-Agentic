import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { platformService } from '@features/platform/services/platformService';
import { useAuth } from '@hooks/useAuth';
import { ROUTES } from '@utils/constants';
import { extractErrorMessage } from '@utils/helpers';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import AppBrand from '@components/common/AppBrand/AppBrand';
import styles from './TenantsPage.module.scss';

const EMPTY_FORM = {
  slug: '', name: '', max_users: 50,
  admin_username: '', admin_password: '', admin_display_name: '',
};

export default function TenantsPage() {
  const navigate = useNavigate();
  const { logout } = useAuth();

  const [tenants, setTenants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showNew, setShowNew] = useState(false);
  const [form, setForm]       = useState(EMPTY_FORM);
  const [saving, setSaving]   = useState(false);

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

  return (
    <div className={styles.page}>
      <header className={styles.topbar}>
        <AppBrand />
        <button className={styles.signout} onClick={() => { logout(); navigate(ROUTES.LOGIN, { replace: true }); }}>
          Sign Out
        </button>
      </header>

      <div className={styles.headerRow}>
        <div>
          <h1 className={styles.title}>Tenants</h1>
          <p className={styles.subtitle}>Manage organizations, licenses, and status.</p>
        </div>
        <Button variant="primary" onClick={() => setShowNew(true)}>+ New tenant</Button>
      </div>

      {loading ? (
        <Loader size="lg" />
      ) : tenants.length === 0 ? (
        <div className={styles.empty}>No tenants yet — create the first one.</div>
      ) : (
        <div className={styles.card}>
          <table className={styles.table}>
            <thead>
              <tr>
                <th>NAME</th><th>CODE</th><th>USERS</th><th>STATUS</th><th className={styles.actionsCol}>ACTIONS</th>
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
                      {t.status}
                    </span>
                  </td>
                  <td className={styles.actions}>
                    <button className={styles.linkBtn} onClick={() => navigate(ROUTES.TENANT_USERS(t.id))}>Users</button>
                    <button className={styles.linkBtn} onClick={() => navigate(ROUTES.PROJECT_CLUSTERS(t.id))}>Clusters</button>
                    <button className={styles.linkBtn} onClick={() => toggleStatus(t)}>
                      {t.status === 'active' ? 'Suspend' : 'Activate'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Modal
        open={showNew}
        onClose={() => setShowNew(false)}
        title="New tenant"
        size="md"
        footer={null}
      >
        <p className={styles.modalSub}>Creates the organization and an initial tenant admin account.</p>
        <form onSubmit={handleCreate} className={styles.form}>
          <Input label="Organization code *" value={form.slug}
            onChange={(e) => set('slug', e.target.value)} placeholder="e.g. aim003" required />
          <Input label="Display name *" value={form.name}
            onChange={(e) => set('name', e.target.value)} placeholder="e.g. AIM 16 Block Development" required />
          <Input label="Max users (license)" type="number" min={1} value={form.max_users}
            onChange={(e) => set('max_users', e.target.value)} />

          <div className={styles.sectionLabel}>Initial tenant admin</div>
          <Input label="Username *" value={form.admin_username}
            onChange={(e) => set('admin_username', e.target.value)} required />
          <Input label="Password *" type="password" value={form.admin_password}
            onChange={(e) => set('admin_password', e.target.value)} minLength={6} required />
          <Input label="Display name" value={form.admin_display_name}
            onChange={(e) => set('admin_display_name', e.target.value)} />

          <div className={styles.formActions}>
            <Button type="button" variant="ghost" onClick={() => setShowNew(false)}>Cancel</Button>
            <Button type="submit" variant="primary" loading={saving}>Create tenant</Button>
          </div>
        </form>
      </Modal>
    </div>
  );
}
