import { useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { platformService } from '@features/platform/services/platformService';
import { ROUTES } from '@utils/constants';
import styles from './PlatformTenantUsersPage.module.scss';

const ROLE_OPTIONS = [
  { value: 'author',   label: 'Author (ID)' },
  { value: 'reviewer', label: 'Reviewer (Lead)' },
  { value: 'admin',    label: 'Admin (Director)' },
  { value: '__custom__', label: 'Custom…' },
];

function initials(username = '') {
  return username.slice(0, 2).toUpperCase();
}

export default function PlatformTenantUsersPage() {
  const { tenantId } = useParams();
  const navigate     = useNavigate();

  const [users, setUsers]             = useState([]);
  const [usage, setUsage]             = useState(null);
  const [loading, setLoading]         = useState(true);
  const [error, setError]             = useState(null);
  const [showModal, setShowModal]     = useState(false);
  const [submitting, setSubmitting]   = useState(false);
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [form, setForm]         = useState({ username: '', password: '', role: 'author' });
  const [customRole, setCustomRole] = useState('');

  useEffect(() => { load(); }, [tenantId]);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const [userList, usageData] = await Promise.all([
        platformService.listTenantUsers(tenantId),
        platformService.getTenantUsage(tenantId),
      ]);
      setUsers(userList);
      setUsage(usageData);
    } catch (e) {
      setError(e.message || 'Failed to load users');
    } finally {
      setLoading(false);
    }
  }

  function handleField(k, v) {
    setForm(f => ({ ...f, [k]: v }));
  }

  async function handleCreate(e) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    const resolvedRole = form.role === '__custom__' ? customRole.trim() : form.role;
    if (!resolvedRole) { setError('Please enter a custom role name.'); setSubmitting(false); return; }
    try {
      await platformService.createTenantUser(tenantId, { ...form, role: resolvedRole });
      setShowModal(false);
      setForm({ username: '', password: '', role: 'author' });
      setCustomRole('');
      load();
    } catch (e) {
      setError(e.message || 'Failed to create user');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleToggle(user) {
    try {
      await platformService.updateTenantUser(tenantId, user.username, { is_active: !user.is_active });
      load();
    } catch (e) {
      setError(e.message || 'Failed to update user');
    }
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    try {
      await platformService.deleteTenantUser(tenantId, deleteTarget.username);
      setDeleteTarget(null);
      load();
    } catch (e) {
      setError(e.message || 'Failed to delete user');
    }
  }

  const total  = users.length;
  const active = users.filter(u => u.is_active).length;
  const pct    = usage ? Math.round((usage.active_users / usage.max_users) * 100) : 0;
  const barLevel = pct >= 90 ? 'crit' : pct >= 75 ? 'warn' : '';

  if (loading) {
    return <div className={styles.loading}>Loading users…</div>;
  }

  return (
    <div className={styles.page}>
      {/* Breadcrumb */}
      <nav className={styles.crumb} aria-label="Breadcrumb">
        <button onClick={() => navigate(ROUTES.PLATFORM_TENANTS)}>Organisations</button>
        <span className={styles.crumb__sep}>›</span>
        <span>Users</span>
      </nav>

      {/* Header */}
      <div className={styles.header}>
        <div>
          <h1 className={styles.header__title}>User Management</h1>
          {usage && (
            <span className={styles.header__slug}>
              {usage.active_users}/{usage.max_users} licensed seats used
            </span>
          )}
        </div>
        <button
          className={`${styles.btn} ${styles['btn--primary']}`}
          onClick={() => setShowModal(true)}
          disabled={usage && usage.active_users >= usage.max_users}
          title={usage && usage.active_users >= usage.max_users ? 'License limit reached' : undefined}
        >
          + Add User
        </button>
      </div>

      {error && <div className={styles.alert}>{error}</div>}

      {/* Stats */}
      <div className={styles.stats}>
        <div className={styles.stat}>
          <div className={styles.stat__value}>{total}</div>
          <div className={styles.stat__label}>Total Users</div>
        </div>
        <div className={`${styles.stat} ${styles['stat--active']}`}>
          <div className={styles.stat__value}>{active}</div>
          <div className={styles.stat__label}>Active</div>
        </div>
        <div className={`${styles.stat} ${styles['stat--license']}`}>
          <div className={styles.stat__value}>{pct}%</div>
          <div className={styles.stat__label}>License Used</div>
          {usage && (
            <div className={styles.licenseBar}>
              <div
                className={`${styles.licenseBarFill} ${barLevel ? styles[`licenseBarFill--${barLevel}`] : ''}`}
                style={{ width: `${pct}%` }}
              />
            </div>
          )}
        </div>
      </div>

      {/* User List */}
      <div className={styles.section}>
        <span className={styles.section__title}>{total} user{total !== 1 ? 's' : ''}</span>
      </div>

      {users.length === 0 ? (
        <div className={styles.empty}>
          <div className={styles.empty__icon}>👥</div>
          <p className={styles.empty__text}>No users yet — add the first one.</p>
        </div>
      ) : (
        <div className={styles.list}>
          {users.map(u => (
            <div
              key={u.username}
              className={`${styles.userRow} ${!u.is_active ? styles['userRow--inactive'] : ''}`}
            >
              <div className={styles.avatar}>{initials(u.username)}</div>

              <div className={styles.userInfo}>
                <div className={styles.username}>{u.username}</div>
                <div className={styles.userMeta}>{u.role_display || u.role}</div>
              </div>

              <div className={styles.userActions}>
                <span className={`${styles.badge} ${styles[`badge--${['admin', 'reviewer'].includes(u.role) ? u.role : 'author'}`]}`}>{u.role}</span>
                <span className={`${styles.badge} ${u.is_active ? styles['badge--active'] : styles['badge--inactive']}`}>
                  {u.is_active ? 'Active' : 'Inactive'}
                </span>
                <button
                  className={`${styles.btn} ${styles['btn--sm']} ${u.is_active ? styles['btn--warn'] : styles['btn--activate']}`}
                  onClick={() => handleToggle(u)}
                >
                  {u.is_active ? 'Deactivate' : 'Activate'}
                </button>
                <button
                  className={`${styles.btn} ${styles['btn--icon']}`}
                  onClick={() => setDeleteTarget(u)}
                  aria-label={`Delete ${u.username}`}
                >
                  🗑
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create User Modal */}
      {showModal && (
        <div className={styles.overlay} onClick={e => { if (e.target === e.currentTarget) setShowModal(false); }}>
          <div className={styles.modal} role="dialog" aria-modal="true" aria-labelledby="modal-create">
            <div className={styles.modal__header}>
              <h2 className={styles.modal__title} id="modal-create">Add User</h2>
              <button className={styles.modal__close} onClick={() => setShowModal(false)} aria-label="Close">✕</button>
            </div>
            <form onSubmit={handleCreate}>
              <div className={styles.modal__body}>
                <div className={styles.fieldRow}>
                  <div className={styles.field}>
                    <label className={styles.field__label} htmlFor="new-username">Username</label>
                    <input
                      id="new-username"
                      className={styles.input}
                      required
                      placeholder="john.doe"
                      value={form.username}
                      onChange={e => handleField('username', e.target.value)}
                    />
                  </div>
                  <div className={styles.field}>
                    <label className={styles.field__label} htmlFor="new-role">Role</label>
                    <select
                      id="new-role"
                      className={styles.input}
                      value={form.role}
                      onChange={e => handleField('role', e.target.value)}
                    >
                      {ROLE_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                    {form.role === '__custom__' && (
                      <input
                        className={styles.input}
                        style={{ marginTop: '8px' }}
                        placeholder="e.g. Instructional Designer"
                        value={customRole}
                        onChange={e => setCustomRole(e.target.value)}
                        required
                        autoFocus
                      />
                    )}
                  </div>
                </div>
                <div className={styles.field}>
                  <label className={styles.field__label} htmlFor="new-password">Password</label>
                  <input
                    id="new-password"
                    className={styles.input}
                    required
                    type="password"
                    placeholder="Min. 6 characters"
                    minLength={6}
                    value={form.password}
                    onChange={e => handleField('password', e.target.value)}
                  />
                </div>
              </div>
              <div className={styles.modal__footer}>
                <button type="button" className={`${styles.btn} ${styles['btn--ghost']}`} onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button type="submit" className={`${styles.btn} ${styles['btn--primary']}`} disabled={submitting}>
                  {submitting ? 'Adding…' : 'Add User'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Delete Confirm Modal */}
      {deleteTarget && (
        <div className={styles.overlay} onClick={e => { if (e.target === e.currentTarget) setDeleteTarget(null); }}>
          <div className={styles.modal} role="dialog" aria-modal="true" aria-labelledby="modal-delete">
            <div className={styles.modal__header}>
              <h2 className={styles.modal__title} id="modal-delete">Delete User</h2>
              <button className={styles.modal__close} onClick={() => setDeleteTarget(null)} aria-label="Close">✕</button>
            </div>
            <div className={styles.modal__body}>
              <p style={{ color: '#4b5563', fontSize: '0.875rem', margin: 0 }}>
                Permanently delete <strong>{deleteTarget.username}</strong>? This cannot be undone.
              </p>
            </div>
            <div className={styles.modal__footer}>
              <button className={`${styles.btn} ${styles['btn--ghost']}`} onClick={() => setDeleteTarget(null)}>
                Cancel
              </button>
              <button className={`${styles.btn} ${styles['btn--danger']}`} onClick={confirmDelete}>
                Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
