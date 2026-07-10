import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
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
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import AppBrand from '@components/common/AppBrand/AppBrand';
import SelectionPageHeader from '@components/streamlit/SelectionPageHeader/SelectionPageHeader';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import styles from './TenantUsersPage.module.scss';

const ROLE_COLORS = {
  [ROLES.ADMIN]: '#7c3aed',
  [ROLES.REVIEWER]: '#0f766e',
  [ROLES.AUTHOR]: '#4338ca',
};

// Dropdown option values are encoded as "system:admin" or "custom:7" so a
// single <select> can pick from both system and tenant-defined roles.
const systemOptionValue = (key) => `system:${key}`;
const customOptionValue = (id) => `custom:${id}`;

function roleToOptionValue(member) {
  return member.custom_role_id != null ? customOptionValue(member.custom_role_id) : systemOptionValue(member.role);
}

function optionValueToPayload(value) {
  const [kind, id] = value.split(':');
  return kind === 'custom' ? { custom_role_id: Number(id) } : { role: id };
}

export default function TenantUsersPage() {
  const { tenantId } = useParams();
  const navigate = useNavigate();
  const { logout, user, role } = useAuth();

  const [tenant, setTenant] = useState(null);
  const [members, setMembers] = useState([]);
  const [roles, setRoles]     = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [saving, setSaving] = useState(false);

  const [addForm, setAddForm]   = useState({ username: '', password: '', display_name: '', roleOption: '' });
  const [editForm, setEditForm] = useState({ display_name: '', roleOption: '', password: '', active: true });

  const roleColor = ROLE_COLORS[role] ?? '#7c3aed';
  const signedInRoleLabel = ROLE_LABELS[role] ?? role ?? 'Admin';

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [tenants, memberList, roleList] = await Promise.all([
        platformService.listTenants(),
        platformService.listMembers(tenantId),
        platformService.listRoles(tenantId),
      ]);
      setTenant(tenants.find((t) => String(t.id) === String(tenantId)) || null);
      setMembers(memberList);
      setRoles(roleList);
      setAddForm((f) => ({ ...f, roleOption: f.roleOption || systemOptionValue('author') }));
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  async function handleAdd(e) {
    e.preventDefault();
    setSaving(true);
    try {
      await platformService.addMember(tenantId, {
        username: addForm.username.trim(),
        password: addForm.password,
        display_name: addForm.display_name.trim() || addForm.username.trim(),
        ...optionValueToPayload(addForm.roleOption),
      });
      toast.success('Member added');
      setShowAdd(false);
      setAddForm({ username: '', password: '', display_name: '', roleOption: systemOptionValue('author') });
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  function openEdit(m) {
    setEditing(m);
    setEditForm({ display_name: m.display_name || '', roleOption: roleToOptionValue(m), password: '', active: m.active });
  }

  async function handleEdit(e) {
    e.preventDefault();
    setSaving(true);
    try {
      await platformService.updateMember(tenantId, editing.user_id, {
        display_name: editForm.display_name.trim() || undefined,
        password: editForm.password || undefined,
        active: editForm.active,
        ...optionValueToPayload(editForm.roleOption),
      });
      toast.success('Member updated');
      setEditing(null);
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function toggleActive(m) {
    try {
      await platformService.updateMember(tenantId, m.user_id, { active: !m.active });
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    }
  }

  async function confirmDelete() {
    try {
      await platformService.removeMember(tenantId, deleting.user_id);
      toast.success('Member removed');
      setDeleting(null);
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    }
  }

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
          <span className={styles.userPill__role} style={{ background: roleColor }}>{signedInRoleLabel}</span>
        </div>
        <div className={styles.navLabel}>Platform</div>
        <button type="button" className={styles.navBtn} onClick={() => navigate(ROUTES.TENANTS)}>
          ← Tenants
        </button>
        <button type="button" className={`${styles.navBtn} ${styles.navBtnActive}`}>
          👥 Users
        </button>
        <button type="button" className={styles.navBtn} onClick={() => navigate(ROUTES.TENANT_ROLES(tenantId))}>
          🛡️ Roles
        </button>
        <button
          type="button"
          className={styles.navBtn}
          onClick={() => navigate(ROUTES.PROJECT_CLUSTERS(tenantId))}
        >
          🗂️ Category
        </button>
        <div className={styles.sidebarSpacer} />
        <button type="button" className={styles.signOut} onClick={handleSignOut}>
          🚪 Sign Out
        </button>
      </aside>

      <main className={styles.main}>
        {loading ? (
          <div className={styles.center}><Loader size="lg" /></div>
        ) : (
          <>
            <div className={styles.headerRow}>
              <SelectionPageHeader
                eyebrow="Tenant Users"
                title={tenant?.name || 'Tenant'}
                subtitle={
                  tenant
                    ? `Organization code: ${tenant.slug} · ${tenant.active_users} / ${tenant.max_users} users`
                    : 'Manage members for this organization.'
                }
              />
              <div className={styles.headerActions}>
                <Button variant="secondary" onClick={() => navigate(ROUTES.TENANT_ROLES(tenantId))}>
                  Roles
                </Button>
                <Button variant="secondary" onClick={() => navigate(ROUTES.PROJECT_CLUSTERS(tenantId))}>
                  Category
                </Button>
                <Button variant="primary" onClick={() => setShowAdd(true)}>+ Add User</Button>
              </div>
            </div>

            <SectionBadge
              icon="👥"
              title="Member Directory"
              subtitle="Add users, assign roles, and activate or deactivate accounts for this tenant."
            />

            {members.length === 0 ? (
              <EmptyState
                icon="👥"
                title="No members yet"
                message="Add the first user for this organization."
                action={() => setShowAdd(true)}
                actionLabel="+ Add User"
              />
            ) : (
              <div className={styles.card}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Username</th>
                      <th>Display Name</th>
                      <th>Role</th>
                      <th>Status</th>
                      <th className={styles.actionsCol}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {members.map((m) => (
                      <tr key={m.user_id}>
                        <td><code className={styles.username}>{m.username}</code></td>
                        <td>{m.display_name || '—'}</td>
                        <td>
                          <span className={m.custom_role_id ? styles.roleCustom : (styles[`role_${m.role}`] || styles.roleDefault)}>
                            {m.role_display}
                          </span>
                        </td>
                        <td>
                          <span className={m.active ? styles.statusActive : styles.statusInactive}>
                            {m.active ? '● Active' : '○ Inactive'}
                          </span>
                        </td>
                        <td className={styles.actions}>
                          <Button variant="ghost" size="xs" onClick={() => openEdit(m)}>Edit</Button>
                          <Button variant="secondary" size="xs" onClick={() => toggleActive(m)}>
                            {m.active ? 'Deactivate' : 'Activate'}
                          </Button>
                          <Button variant="danger" size="xs" onClick={() => setDeleting(m)}>Remove</Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </main>

      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="Add User" size="sm" footer={null}>
        <form onSubmit={handleAdd} className={styles.form}>
          <Input
            label="Username *"
            value={addForm.username}
            onChange={(e) => setAddForm((f) => ({ ...f, username: e.target.value }))}
            required
          />
          <Input
            label="Password *"
            type="password"
            minLength={6}
            value={addForm.password}
            onChange={(e) => setAddForm((f) => ({ ...f, password: e.target.value }))}
            required
          />
          <Input
            label="Display name"
            value={addForm.display_name}
            onChange={(e) => setAddForm((f) => ({ ...f, display_name: e.target.value }))}
          />
          <label className={styles.selectLabel}>
            Role (permissions) *
            <select className={styles.select} value={addForm.roleOption}
              onChange={(e) => setAddForm((f) => ({ ...f, roleOption: e.target.value }))}>
              {roles.map((r) => (
                <option
                  key={r.id ?? r.key}
                  value={r.type === 'custom' ? customOptionValue(r.id) : systemOptionValue(r.key)}
                >
                  {r.name}
                </option>
              ))}
            </select>
          </label>
          <div className={styles.formActions}>
            <Button type="button" variant="ghost" onClick={() => setShowAdd(false)}>Cancel</Button>
            <Button type="submit" variant="primary" loading={saving}>Add User</Button>
          </div>
        </form>
      </Modal>

      <Modal open={Boolean(editing)} onClose={() => setEditing(null)} title="Edit User" size="sm" footer={null}>
        {editing && (
          <form onSubmit={handleEdit} className={styles.form}>
            <Input label="Username" value={editing.username} disabled />
            <Input
              label="Display name"
              value={editForm.display_name}
              onChange={(e) => setEditForm((f) => ({ ...f, display_name: e.target.value }))}
            />
            <Input
              label="New password (leave blank to keep)"
              type="password"
              value={editForm.password}
              onChange={(e) => setEditForm((f) => ({ ...f, password: e.target.value }))}
            />
            <label className={styles.selectLabel}>
              Role (permissions) *
              <select className={styles.select} value={editForm.roleOption}
                onChange={(e) => setEditForm((f) => ({ ...f, roleOption: e.target.value }))}>
                {roles.map((r) => (
                  <option
                    key={r.id ?? r.key}
                    value={r.type === 'custom' ? customOptionValue(r.id) : systemOptionValue(r.key)}
                  >
                    {r.name}
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.checkboxRow}>
              <input
                type="checkbox"
                checked={editForm.active}
                onChange={(e) => setEditForm((f) => ({ ...f, active: e.target.checked }))}
              />
              Active
            </label>
            <div className={styles.formActions}>
              <Button type="button" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
              <Button type="submit" variant="primary" loading={saving}>Save Changes</Button>
            </div>
          </form>
        )}
      </Modal>

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(null)}
        onConfirm={confirmDelete}
        title="Remove Member"
        message={`Remove "${deleting?.username}" from this tenant?`}
      />
      <Toaster position="top-right" toastOptions={{ duration: 4000 }} />
    </div>
  );
}
