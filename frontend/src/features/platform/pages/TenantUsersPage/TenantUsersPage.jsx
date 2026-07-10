import { useEffect, useState, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { platformService } from '@features/platform/services/platformService';
import { ROUTES } from '@utils/constants';
import { extractErrorMessage } from '@utils/helpers';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import Modal from '@components/common/Modal/Modal';
import Loader from '@components/common/Loader/Loader';
import ConfirmDialog from '@components/common/ConfirmDialog/ConfirmDialog';
import styles from './TenantUsersPage.module.scss';

const ROLES = [
  { value: 'admin',    label: 'Tenant Admin' },
  { value: 'reviewer', label: 'Prompt Manager' },
  { value: 'author',   label: 'User' },
];
const roleLabel = (r) => ROLES.find((o) => o.value === r)?.label || r;

export default function TenantUsersPage() {
  const { tenantId } = useParams();
  const navigate = useNavigate();

  const [tenant, setTenant]   = useState(null);
  const [members, setMembers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [editing, setEditing] = useState(null);
  const [deleting, setDeleting] = useState(null);
  const [saving, setSaving]   = useState(false);

  const [addForm, setAddForm]   = useState({ username: '', password: '', display_name: '', role: 'author' });
  const [editForm, setEditForm] = useState({ display_name: '', role: 'author', password: '', active: true });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [tenants, memberList] = await Promise.all([
        platformService.listTenants(),
        platformService.listMembers(tenantId),
      ]);
      setTenant(tenants.find((t) => String(t.id) === String(tenantId)) || null);
      setMembers(memberList);
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
        role: addForm.role,
      });
      toast.success('Member added');
      setShowAdd(false);
      setAddForm({ username: '', password: '', display_name: '', role: 'author' });
      load();
    } catch (e) {
      toast.error(extractErrorMessage(e));
    } finally {
      setSaving(false);
    }
  }

  function openEdit(m) {
    setEditing(m);
    setEditForm({ display_name: m.display_name || '', role: m.role, password: '', active: m.active });
  }

  async function handleEdit(e) {
    e.preventDefault();
    setSaving(true);
    try {
      await platformService.updateMember(tenantId, editing.user_id, {
        display_name: editForm.display_name.trim() || undefined,
        role: editForm.role,
        password: editForm.password || undefined,
        active: editForm.active,
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

  if (loading) return <div className={styles.page}><Loader size="lg" /></div>;

  return (
    <div className={styles.page}>
      <div className={styles.headerRow}>
        <div>
          <h1 className={styles.title}>{tenant?.name || 'Tenant'}</h1>
          <p className={styles.subtitle}>
            Organization code: <code className={styles.code}>{tenant?.slug}</code>
            {tenant && <> · {tenant.active_users} / {tenant.max_users} users</>}
          </p>
        </div>
        <div className={styles.headerActions}>
          <button className={styles.linkBtn} onClick={() => navigate(ROUTES.PROJECT_CLUSTERS(tenantId))}>Clusters</button>
          <button className={styles.linkBtn} onClick={() => navigate(ROUTES.TENANTS)}>← Tenants</button>
          <Button variant="primary" onClick={() => setShowAdd(true)}>+ Add user</Button>
        </div>
      </div>

      <div className={styles.card}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>USERNAME</th><th>DISPLAY NAME</th><th>ROLE</th><th>STATUS</th><th className={styles.actionsCol}>ACTIONS</th>
            </tr>
          </thead>
          <tbody>
            {members.length === 0 ? (
              <tr><td colSpan={5} className={styles.emptyCell}>No members yet.</td></tr>
            ) : members.map((m) => (
              <tr key={m.user_id}>
                <td><code className={styles.username}>{m.username}</code></td>
                <td>{m.display_name || '—'}</td>
                <td><span className={styles[`role_${m.role}`] || styles.roleDefault}>{roleLabel(m.role)}</span></td>
                <td>
                  <span className={m.active ? styles.statusActive : styles.statusInactive}>
                    {m.active ? '● Active' : '○ Inactive'}
                  </span>
                </td>
                <td className={styles.actions}>
                  <button className={styles.iconBtn} title="Edit" onClick={() => openEdit(m)}>✎</button>
                  <button className={styles.iconBtn} title={m.active ? 'Deactivate' : 'Activate'} onClick={() => toggleActive(m)}>
                    {m.active ? '⏸' : '▶'}
                  </button>
                  <button className={`${styles.iconBtn} ${styles.danger}`} title="Delete" onClick={() => setDeleting(m)}>🗑</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Add member */}
      <Modal open={showAdd} onClose={() => setShowAdd(false)} title="Add user" size="sm" footer={null}>
        <form onSubmit={handleAdd} className={styles.form}>
          <Input label="Username *" value={addForm.username}
            onChange={(e) => setAddForm((f) => ({ ...f, username: e.target.value }))} required />
          <Input label="Password *" type="password" minLength={6} value={addForm.password}
            onChange={(e) => setAddForm((f) => ({ ...f, password: e.target.value }))} required />
          <Input label="Display name" value={addForm.display_name}
            onChange={(e) => setAddForm((f) => ({ ...f, display_name: e.target.value }))} />
          <label className={styles.selectLabel}>
            Role
            <select className={styles.select} value={addForm.role}
              onChange={(e) => setAddForm((f) => ({ ...f, role: e.target.value }))}>
              {ROLES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <div className={styles.formActions}>
            <Button type="button" variant="ghost" onClick={() => setShowAdd(false)}>Cancel</Button>
            <Button type="submit" variant="primary" loading={saving}>Add user</Button>
          </div>
        </form>
      </Modal>

      {/* Edit member */}
      <Modal open={Boolean(editing)} onClose={() => setEditing(null)} title="Edit user" size="sm" footer={null}>
        {editing && (
          <form onSubmit={handleEdit} className={styles.form}>
            <Input label="Username" value={editing.username} disabled />
            <Input label="Display name" value={editForm.display_name}
              onChange={(e) => setEditForm((f) => ({ ...f, display_name: e.target.value }))} />
            <Input label="New password (leave blank to keep)" type="password"
              value={editForm.password} onChange={(e) => setEditForm((f) => ({ ...f, password: e.target.value }))} />
            <label className={styles.selectLabel}>
              Role
              <select className={styles.select} value={editForm.role}
                onChange={(e) => setEditForm((f) => ({ ...f, role: e.target.value }))}>
                {ROLES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
            </label>
            <label className={styles.checkboxRow}>
              <input type="checkbox" checked={editForm.active}
                onChange={(e) => setEditForm((f) => ({ ...f, active: e.target.checked }))} />
              Active
            </label>
            <div className={styles.formActions}>
              <Button type="button" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
              <Button type="submit" variant="primary" loading={saving}>Save changes</Button>
            </div>
          </form>
        )}
      </Modal>

      <ConfirmDialog
        open={Boolean(deleting)}
        onClose={() => setDeleting(null)}
        onConfirm={confirmDelete}
        title="Remove member"
        message={`Remove "${deleting?.username}" from this tenant?`}
      />
    </div>
  );
}
