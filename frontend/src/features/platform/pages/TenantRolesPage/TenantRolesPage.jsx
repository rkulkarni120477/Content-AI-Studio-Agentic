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
import IdentityBar from '@components/common/HeaderUser/IdentityBar';
// Sidebar UserPill moved to IdentityBar in the main header — keep import for easy restore.
// import UserPill from '@components/common/UserPill/UserPill';
import SelectionPageHeader from '@components/streamlit/SelectionPageHeader/SelectionPageHeader';
import SectionBadge from '@components/streamlit/SectionBadge/SectionBadge';
import styles from './TenantRolesPage.module.scss';

const EMPTY_FORM = { name: '', description: '', permissions: [] };

const ROLE_COLORS = {
  [ROLES.ADMIN]: '#7c3aed',
  [ROLES.REVIEWER]: '#0f766e',
  [ROLES.AUTHOR]: '#4338ca',
};

function categoryChecked(category, selected) {
  return category.permissions.length > 0 && category.permissions.every((p) => selected.includes(p.key));
}

export default function TenantRolesPage() {
  const { tenantId } = useParams();
  const navigate = useNavigate();
  const { logout, user, role } = useAuth();

  const [tenant, setTenant]   = useState(null);
  const [roles, setRoles]     = useState([]);
  const [catalog, setCatalog] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [saving, setSaving]   = useState(false);

  const [addForm, setAddForm]   = useState(EMPTY_FORM);
  const [editForm, setEditForm] = useState(EMPTY_FORM);

  const roleColor = ROLE_COLORS[role] ?? '#7c3aed';
  const signedInRoleLabel = ROLE_LABELS[role] ?? role ?? 'Admin';

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [tenants, roleList, permCatalog] = await Promise.all([
        platformService.listTenants(),
        platformService.listRoles(tenantId),
        platformService.getPermissionCatalog(),
      ]);
      setTenant(tenants.find((t) => String(t.id) === String(tenantId)) || null);
      setRoles(roleList);
      setCatalog(permCatalog);
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  function togglePermission(setForm, key) {
    setForm((f) => ({
      ...f,
      permissions: f.permissions.includes(key)
        ? f.permissions.filter((k) => k !== key)
        : [...f.permissions, key],
    }));
  }

  function toggleCategory(setForm, category) {
    setForm((f) => {
      const allChecked = categoryChecked(category, f.permissions);
      const keys = category.permissions.map((p) => p.key);
      return {
        ...f,
        permissions: allChecked
          ? f.permissions.filter((k) => !keys.includes(k))
          : [...new Set([...f.permissions, ...keys])],
      };
    });
  }

  async function handleAdd(e) {
    e.preventDefault();
    setSaving(true);
    try {
      await platformService.createRole(tenantId, {
        name: addForm.name.trim(),
        description: addForm.description.trim() || undefined,
        permissions: addForm.permissions,
      });
      toast.success('Role created');
      setShowAdd(false);
      setAddForm(EMPTY_FORM);
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  function openEdit(role) {
    setEditing(role);
    setEditForm({ name: role.name, description: role.description || '', permissions: [...role.permissions] });
  }

  async function handleEdit(e) {
    e.preventDefault();
    setSaving(true);
    try {
      await platformService.updateRole(tenantId, editing.id, {
        name: editForm.name.trim(),
        description: editForm.description.trim(),
        permissions: editForm.permissions,
      });
      toast.success('Role updated');
      setEditing(null);
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  async function confirmDelete() {
    try {
      await platformService.deleteRole(tenantId, deleting.id);
      toast.success('Role deleted');
      setDeleting(null);
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
      setDeleting(null);
    }
  }

  function handleSignOut() {
    logout();
    navigate(ROUTES.LOGIN, { replace: true });
  }

  function renderPermissionPicker(form, setForm) {
    return (
      <div className={styles.permGroups}>
        {catalog.map((category) => (
          <div key={category.category} className={styles.permCategory}>
            <label className={styles.categoryLabel}>
              <input
                type="checkbox"
                checked={categoryChecked(category, form.permissions)}
                onChange={() => toggleCategory(setForm, category)}
              />
              {category.label}
            </label>
            <div className={styles.permList}>
              {category.permissions.map((p) => (
                <label key={p.key} className={styles.permRow}>
                  <input
                    type="checkbox"
                    checked={form.permissions.includes(p.key)}
                    onChange={() => togglePermission(setForm, p.key)}
                  />
                  {p.label}
                </label>
              ))}
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className={styles.layout}>
      <aside className={styles.sidebar} aria-label="Platform navigation">
        <AppBrand />
        {/* User identity moved to the top IdentityBar — kept for easy restore.
        <UserPill username={user?.username} roleLabel={signedInRoleLabel} roleColor={roleColor} />
        */}
        <div className={styles.navLabel}>Platform</div>
        <button type="button" className={styles.navBtn} onClick={() => navigate(ROUTES.TENANTS)}>
          ← Tenants
        </button>
        <button type="button" className={styles.navBtn} onClick={() => navigate(ROUTES.TENANT_USERS(tenantId))}>
          👥 Users
        </button>
        <button type="button" className={`${styles.navBtn} ${styles.navBtnActive}`}>
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
        <IdentityBar username={user?.username} roleLabel={signedInRoleLabel} roleColor={roleColor} />
        <div className={styles.mainBody}>
        {loading ? (
          <div className={styles.center}><Loader size="lg" /></div>
        ) : (
          <>
            <div className={styles.headerRow}>
              <SelectionPageHeader
                eyebrow="Tenant Roles"
                title={tenant?.name || 'Tenant'}
                subtitle={
                  tenant
                    ? `Organization code: ${tenant.slug} · ${roles.length} role${roles.length !== 1 ? 's' : ''}`
                    : 'Define custom roles and permissions for this organization.'
                }
              />
              <div className={styles.headerActions}>
                <Button variant="secondary" onClick={() => navigate(ROUTES.TENANT_USERS(tenantId))}>
                  Users
                </Button>
                <Button variant="secondary" onClick={() => navigate(ROUTES.PROJECT_CLUSTERS(tenantId))}>
                  Category
                </Button>
                <Button variant="primary" onClick={() => setShowAdd(true)}>+ Add Role</Button>
              </div>
            </div>

            <SectionBadge
              icon="🛡️"
              title="Role Directory"
              subtitle="Create custom roles with tailored permission sets. System roles are read-only."
            />

            {roles.length === 0 ? (
              <EmptyState
                icon="🛡️"
                title="No roles yet"
                message="Create the first custom role for this organization."
                action={() => setShowAdd(true)}
                actionLabel="+ Add Role"
              />
            ) : (
              <div className={styles.card}>
                <table className={styles.table}>
                  <thead>
                    <tr>
                      <th>Role</th>
                      <th>Description</th>
                      <th>Type</th>
                      <th>Permissions</th>
                      <th className={styles.actionsCol}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {roles.map((r) => (
                      <tr key={r.id ?? r.key}>
                        <td className={styles.roleName}>{r.name}</td>
                        <td>{r.description || '—'}</td>
                        <td>
                          <span className={r.type === 'system' ? styles.typeSystem : styles.typeCustom}>
                            {r.type.toUpperCase()}
                          </span>
                        </td>
                        <td>{r.permissions.length}</td>
                        <td className={styles.actions}>
                          {r.type === 'custom' ? (
                            <>
                              <Button variant="ghost" size="xs" onClick={() => openEdit(r)}>Edit</Button>
                              <Button variant="danger" size="xs" onClick={() => setDeleting(r)}>Delete</Button>
                            </>
                          ) : (
                            <span className={styles.lockedHint}>Read-only</span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
        </div>
      </main>

      {/* Add role */}
      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="Add Role" size="md" footer={null}>
        <form onSubmit={handleAdd} className={styles.form}>
          <Input label="Role name *" value={addForm.name} placeholder="e.g. content_reviewer"
            onChange={(e) => setAddForm((f) => ({ ...f, name: e.target.value }))} required />
          <Input label="Description" value={addForm.description} placeholder="What this role is for"
            onChange={(e) => setAddForm((f) => ({ ...f, description: e.target.value }))} />
          <div className={styles.permHeading}>Permissions</div>
          {renderPermissionPicker(addForm, setAddForm)}
          <div className={styles.formActions}>
            <Button type="button" variant="ghost" onClick={() => setShowAdd(false)}>Cancel</Button>
            <Button type="submit" variant="primary" loading={saving}>Create Role</Button>
          </div>
        </form>
      </Modal>

      {/* Edit role */}
      <Modal open={Boolean(editing)} onClose={() => setEditing(null)} title="Edit Role" size="md" footer={null}>
        {editing && (
          <form onSubmit={handleEdit} className={styles.form}>
            <Input label="Role name *" value={editForm.name}
              onChange={(e) => setEditForm((f) => ({ ...f, name: e.target.value }))} required />
            <Input label="Description" value={editForm.description}
              onChange={(e) => setEditForm((f) => ({ ...f, description: e.target.value }))} />
            <div className={styles.permHeading}>Permissions</div>
            {renderPermissionPicker(editForm, setEditForm)}
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
        title="Delete role"
        message={`Delete the "${deleting?.name}" role? Members must be reassigned first if it's still in use.`}
      />
      <Toaster position="top-right" toastOptions={{ duration: 4000 }} />
    </div>
  );
}
