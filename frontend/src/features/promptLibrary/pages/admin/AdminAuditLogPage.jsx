import { useCallback, useEffect, useState } from 'react';
import { exportAuditCsv, fetchAuditEvents } from '../../api/audit';
import { useAuth } from '../../context/AuthContext';
import { useToast } from '../../context/ToastContext';
import { canExportAudit } from '../../utils/permissions';

export default function AdminAuditLogPage() {
  const { user } = useAuth();
  const { show } = useToast();
  const canExport = canExportAudit(user);
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pages, setPages] = useState(1);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(null);
  const [filters, setFilters] = useState({ limit: 25, page: 1 });
  const [draft, setDraft] = useState({ actor: '', event_type: '', entity_type: '', from: '', to: '' });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchAuditEvents({ ...filters, page });
      setItems(data.items);
      setTotal(data.total);
      setPages(data.pages);
    } finally {
      setLoading(false);
    }
  }, [filters, page]);

  useEffect(() => {
    void load();
  }, [load]);

  function applyFilters() {
    setPage(1);
    setFilters({
      limit: 25,
      page: 1,
      actor: draft.actor || undefined,
      event_type: draft.event_type || undefined,
      entity_type: draft.entity_type || undefined,
      from: draft.from || undefined,
      to: draft.to || undefined,
    });
  }

  function clearFilters() {
    setDraft({ actor: '', event_type: '', entity_type: '', from: '', to: '' });
    setPage(1);
    setFilters({ limit: 25, page: 1 });
  }

  async function handleExport() {
    try {
      await exportAuditCsv({ ...filters, page: undefined, limit: undefined });
    } catch (err) {
      show(err instanceof Error ? err.message : 'Export failed');
    }
  }

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Audit log</h1>
          <p className="subtitle">Security and compliance activity trail.</p>
        </div>
        {canExport && (
          <button type="button" className="btn btn-ghost" onClick={handleExport}>
            ⬇ Export CSV
          </button>
        )}
      </div>

      <div className="page-card" style={{ marginBottom: 16 }}>
        <div className="audit-filters">
          <div className="audit-filters__field">
            <label htmlFor="audit-actor">Actor</label>
            <input
              id="audit-actor"
              type="text"
              placeholder="Username"
              value={draft.actor}
              onChange={(e) => setDraft((d) => ({ ...d, actor: e.target.value }))}
            />
          </div>
          <div className="audit-filters__field">
            <label htmlFor="audit-event">Event type</label>
            <input
              id="audit-event"
              type="text"
              placeholder="e.g. prompt.create"
              value={draft.event_type}
              onChange={(e) => setDraft((d) => ({ ...d, event_type: e.target.value }))}
            />
          </div>
          <div className="audit-filters__field">
            <label htmlFor="audit-entity">Entity type</label>
            <input
              id="audit-entity"
              type="text"
              placeholder="e.g. prompt"
              value={draft.entity_type}
              onChange={(e) => setDraft((d) => ({ ...d, entity_type: e.target.value }))}
            />
          </div>
          <div className="audit-filters__field">
            <label htmlFor="audit-from">From</label>
            <input
              id="audit-from"
              type="date"
              value={draft.from}
              onChange={(e) => setDraft((d) => ({ ...d, from: e.target.value }))}
            />
          </div>
          <div className="audit-filters__field">
            <label htmlFor="audit-to">To</label>
            <input
              id="audit-to"
              type="date"
              value={draft.to}
              onChange={(e) => setDraft((d) => ({ ...d, to: e.target.value }))}
            />
          </div>
          <div className="audit-filters__actions">
            <button type="button" className="btn btn-primary btn-sm" onClick={applyFilters}>
              Apply
            </button>
            <button type="button" className="btn btn-ghost btn-sm" onClick={clearFilters}>
              Clear
            </button>
            <span className="count" style={{ marginLeft: 0 }}>
              {total} event{total !== 1 ? 's' : ''}
            </span>
          </div>
        </div>
      </div>

      <div className="page-card">
        {loading ? (
          <p style={{ color: 'var(--muted)', textAlign: 'center', padding: '32px 0' }}>Loading…</p>
        ) : !items.length ? (
          <div className="audit-empty">
            <div className="audit-empty__title">No matching events</div>
            <p>No audit events match your filters. Try clearing filters or widening the date range.</p>
          </div>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Actor</th>
                  <th>Event</th>
                  <th>Action</th>
                  <th>Summary</th>
                  <th>IP</th>
                </tr>
              </thead>
              <tbody>
                {items.map((e) => (
                  <tr key={e.id} style={{ cursor: 'pointer' }} onClick={() => setSelected(e)}>
                    <td style={{ whiteSpace: 'nowrap' }}>{e.created_at}</td>
                    <td>{e.actor_username || '—'}</td>
                    <td>
                      <code>{e.event_type}</code>
                    </td>
                    <td>{e.action}</td>
                    <td>{e.summary}</td>
                    <td style={{ fontSize: '.8rem', color: 'var(--muted)' }}>{e.ip_address || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {pages > 1 && (
          <div className="audit-pager">
            <button type="button" className="btn btn-ghost btn-sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </button>
            <span style={{ fontSize: '.85rem', color: 'var(--muted)' }}>
              Page {page} of {pages}
            </span>
            <button type="button" className="btn btn-ghost btn-sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
              Next
            </button>
          </div>
        )}
      </div>

      {selected && (
        <div className="backdrop open" role="dialog" aria-modal="true" onClick={() => setSelected(null)}>
          <div className="modal" onClick={(ev) => ev.stopPropagation()}>
            <h2 style={{ marginBottom: 12 }}>Event detail</h2>
            <dl className="audit-detail">
              <dt>Summary</dt>
              <dd>{selected.summary}</dd>
              <dt>Event</dt>
              <dd>
                {selected.event_type} / {selected.action}
              </dd>
              <dt>Actor</dt>
              <dd>
                {selected.actor_username} ({selected.actor_role})
              </dd>
              <dt>Entity</dt>
              <dd>
                {selected.entity_type} {selected.entity_id}
              </dd>
              <dt>IP / User-Agent</dt>
              <dd style={{ wordBreak: 'break-all' }}>
                {selected.ip_address || '—'}
                <br />
                <span style={{ color: 'var(--muted)', fontSize: '.8rem' }}>{selected.user_agent || '—'}</span>
              </dd>
              {selected.changes && (
                <>
                  <dt>Changes</dt>
                  <dd>
                    <pre>{JSON.stringify(selected.changes, null, 2)}</pre>
                  </dd>
                </>
              )}
            </dl>
            <button type="button" className="btn btn-primary" onClick={() => setSelected(null)}>
              Close
            </button>
          </div>
        </div>
      )}
    </>
  );
}
