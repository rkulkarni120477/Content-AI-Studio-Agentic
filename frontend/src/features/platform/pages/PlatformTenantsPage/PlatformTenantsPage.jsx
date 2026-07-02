import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { platformService } from '@features/platform/services/platformService';
import { ROUTES } from '@utils/constants';
import styles from './PlatformTenantsPage.module.scss';

function LicenseBar({ used, max }) {
  const pct = max > 0 ? Math.min(100, Math.round((used / max) * 100)) : 0;
  const level = pct >= 90 ? 'crit' : pct >= 75 ? 'warn' : '';
  return (
    <div className={styles.card__bar}>
      <div
        className={`${styles.card__barFill} ${level ? styles[`card__barFill--${level}`] : ''}`}
        style={{ width: `${pct}%` }}
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      />
    </div>
  );
}

export default function PlatformTenantsPage() {
  const navigate = useNavigate();
  const [tenants, setTenants]       = useState([]);
  const [loading, setLoading]       = useState(true);
  const [error, setError]           = useState(null);
  const [showModal, setShowModal]   = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [form, setForm] = useState({
    slug: '', name: '', max_users: 50,
    admin_username: '', admin_password: '',
  });

  useEffect(() => { load(); }, []);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await platformService.listTenants();
      setTenants(data);
    } catch (e) {
      setError(e.message || 'Failed to load organisations');
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
    try {
      await platformService.createTenant({ ...form, max_users: Number(form.max_users) });
      setShowModal(false);
      setForm({ slug: '', name: '', max_users: 50, admin_username: '', admin_password: '' });
      load();
    } catch (e) {
      setError(e.message || 'Failed to create organisation');
    } finally {
      setSubmitting(false);
    }
  }

  async function handleToggleStatus(tenant) {
    const newStatus = tenant.status === 'active' ? 'suspended' : 'active';
    try {
      await platformService.updateTenant(tenant.id, { status: newStatus });
      load();
    } catch (e) {
      setError(e.message || 'Failed to update organisation');
    }
  }

  // Derived stats
  const total     = tenants.length;
  const active    = tenants.filter(t => t.status === 'active').length;
  const suspended = tenants.filter(t => t.status === 'suspended').length;
  const totalUsers = tenants.reduce((s, t) => s + (t.active_users || 0), 0);

  if (loading) {
    return <div className={styles.loading}><span>Loading organisations…</span></div>;
  }

  return (
    <div className={styles.page}>
      {/* Header */}
      <div className={styles.header}>
        <div>
          <p className={styles.header__eyebrow}>Platform Admin</p>
          <h1 className={styles.header__title}>Organisations</h1>
          <p className={styles.header__sub}>Manage client organisations, licensing, and access.</p>
        </div>
        <button className={`${styles.btn} ${styles['btn--primary']}`} onClick={() => setShowModal(true)}>
          + New Organisation
        </button>
      </div>

      {error && <div className={styles.alert}>{error}</div>}

      {/* Stats */}
      <div className={styles.stats}>
        <div className={styles.stat}>
          <div className={styles.stat__value}>{total}</div>
          <div className={styles.stat__label}>Total Orgs</div>
        </div>
        <div className={`${styles.stat} ${styles['stat--active']}`}>
          <div className={styles.stat__value}>{active}</div>
          <div className={styles.stat__label}>Active</div>
        </div>
        <div className={`${styles.stat} ${styles['stat--suspended']}`}>
          <div className={styles.stat__value}>{suspended}</div>
          <div className={styles.stat__label}>Suspended</div>
        </div>
        <div className={`${styles.stat} ${styles['stat--users']}`}>
          <div className={styles.stat__value}>{totalUsers}</div>
          <div className={styles.stat__label}>Total Users</div>
        </div>
      </div>

      {/* Grid */}
      <div className={styles.section}>
        <span className={styles.section__title}>{total} organisation{total !== 1 ? 's' : ''}</span>
      </div>

      {tenants.length === 0 ? (
        <div className={styles.empty}>
          <div className={styles.empty__icon}>🏢</div>
          <p className={styles.empty__text}>No organisations yet — create the first one.</p>
        </div>
      ) : (
        <div className={styles.grid}>
          {tenants.map(t => {
            const pct = t.max_users > 0 ? Math.round((t.active_users / t.max_users) * 100) : 0;
            return (
              <div key={t.id} className={styles.card}>
                <div className={`${styles.card__stripe} ${t.status === 'active' ? styles['card__stripe--active'] : styles['card__stripe--suspended']}`} />
                <div className={styles.card__body}>
                  <div className={styles.card__top}>
                    <div>
                      <div className={styles.card__name}>{t.name}</div>
                      <span className={styles.card__slug}>{t.slug}</span>
                    </div>
                    <span className={`${styles.badge} ${t.status === 'active' ? styles['badge--active'] : styles['badge--suspended']}`}>
                      {t.status}
                    </span>
                  </div>

                  <div className={styles.card__license}>
                    <div className={styles.card__licenseRow}>
                      <span className={styles.card__licenseLabel}>License Usage</span>
                      <span className={styles.card__licenseCount}>{t.active_users} / {t.max_users} users · {pct}%</span>
                    </div>
                    <LicenseBar used={t.active_users} max={t.max_users} />
                  </div>

                  <div className={styles.card__meta}>
                    Created {t.created_at ? new Date(t.created_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '—'}
                    {t.created_by && ` by ${t.created_by}`}
                  </div>
                </div>

                <div className={styles.card__footer}>
                  <button
                    className={`${styles.btn} ${styles['btn--ghost']} ${styles['btn--sm']}`}
                    onClick={() => navigate(ROUTES.PLATFORM_USERS(t.id))}
                  >
                    👥 Users
                  </button>
                  <button
                    className={`${styles.btn} ${styles['btn--sm']} ${t.status === 'active' ? styles['btn--warn'] : styles['btn--activate']}`}
                    onClick={() => handleToggleStatus(t)}
                  >
                    {t.status === 'active' ? 'Suspend' : 'Activate'}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Create Modal */}
      {showModal && (
        <div className={styles.overlay} onClick={e => { if (e.target === e.currentTarget) setShowModal(false); }}>
          <div className={styles.modal} role="dialog" aria-modal="true" aria-labelledby="modal-title">
            <div className={styles.modal__header}>
              <h2 className={styles.modal__title} id="modal-title">New Organisation</h2>
              <button className={styles.modal__close} onClick={() => setShowModal(false)} aria-label="Close">✕</button>
            </div>
            <form onSubmit={handleCreate}>
              <div className={styles.modal__body}>
                <div className={styles.fieldRow}>
                  <div className={styles.field}>
                    <label className={styles.field__label} htmlFor="slug">Organisation Code</label>
                    <p className={styles.field__hint}>Lowercase, used to log in (e.g. acme-corp)</p>
                    <input
                      id="slug"
                      className={styles.input}
                      required
                      placeholder="acme-corp"
                      value={form.slug}
                      onChange={e => handleField('slug', e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ''))}
                    />
                  </div>
                  <div className={styles.field}>
                    <label className={styles.field__label} htmlFor="name">Display Name</label>
                    <p className={styles.field__hint}>Shown in the dashboard</p>
                    <input
                      id="name"
                      className={styles.input}
                      required
                      placeholder="Acme Corporation"
                      value={form.name}
                      onChange={e => handleField('name', e.target.value)}
                    />
                  </div>
                </div>

                <div className={styles.field}>
                  <label className={styles.field__label} htmlFor="max_users">Max Licensed Users</label>
                  <input
                    id="max_users"
                    className={styles.input}
                    type="number"
                    required
                    min={1}
                    placeholder="50"
                    value={form.max_users}
                    onChange={e => handleField('max_users', e.target.value)}
                  />
                </div>

                <div className={styles.fieldRow}>
                  <div className={styles.field}>
                    <label className={styles.field__label} htmlFor="admin_username">Admin Username</label>
                    <input
                      id="admin_username"
                      className={styles.input}
                      required
                      placeholder="admin"
                      value={form.admin_username}
                      onChange={e => handleField('admin_username', e.target.value)}
                    />
                  </div>
                  <div className={styles.field}>
                    <label className={styles.field__label} htmlFor="admin_password">Admin Password</label>
                    <input
                      id="admin_password"
                      className={styles.input}
                      required
                      type="password"
                      placeholder="Min. 6 characters"
                      minLength={6}
                      value={form.admin_password}
                      onChange={e => handleField('admin_password', e.target.value)}
                    />
                  </div>
                </div>
              </div>
              <div className={styles.modal__footer}>
                <button type="button" className={`${styles.btn} ${styles['btn--ghost']}`} onClick={() => setShowModal(false)}>
                  Cancel
                </button>
                <button type="submit" className={`${styles.btn} ${styles['btn--primary']}`} disabled={submitting}>
                  {submitting ? 'Creating…' : 'Create Organisation'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
