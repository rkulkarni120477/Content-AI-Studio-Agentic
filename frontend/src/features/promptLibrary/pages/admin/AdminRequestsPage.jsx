import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { apiFetch } from '../../api/client';
import { hasProposal } from '../../utils/requestProposal';
import { plAdminRequest, plPrompt } from '../../paths';

function statusClass(status) {
  return `req-status req-${status}`;
}

export default function AdminRequestsPage() {
  const [statusFilter, setStatusFilter] = useState('');
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      const qs = statusFilter ? `?status=${statusFilter}` : '';
      const res = await apiFetch(`/api/requests${qs}`);
      const data = res.ok ? await res.json() : [];
      if (active) {
        setRequests(data);
        setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [statusFilter]);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Manage requests</h1>
          <p className="subtitle">Review and update user-submitted prompt requests.</p>
        </div>
        <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          <option value="open">Open</option>
          <option value="in_progress">In progress</option>
          <option value="done">Done</option>
          <option value="rejected">Rejected</option>
        </select>
      </div>

      <div className="page-card">
        {loading ? (
          <p style={{ color: 'var(--muted)' }}>Loading…</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Type</th>
                  <th>Requested by</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {requests.length === 0 ? (
                  <tr>
                    <td colSpan={6} style={{ textAlign: 'center', color: 'var(--muted)', padding: 18 }}>
                      No requests.
                    </td>
                  </tr>
                ) : (
                  requests.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <strong>{r.title}</strong>
                        {r.prompt_id && (
                          <Link
                            to={plPrompt(r.prompt_id)}
                            style={{ marginLeft: 8, fontSize: '.75rem', whiteSpace: 'nowrap' }}
                            title="Open the prompt this request is about"
                          >
                            🔗 prompt #{r.prompt_id}
                          </Link>
                        )}
                        {hasProposal(r.description) && (
                          <span
                            className="badge badge-team"
                            style={{ marginLeft: 8 }}
                            title="The requester wrote the exact prompt text they want — open the request to review and apply it"
                          >
                            ✏️ proposal
                          </span>
                        )}
                        {r.admin_notes && (
                          <div style={{ fontSize: '.73rem', color: 'var(--muted)' }}>{r.admin_notes}</div>
                        )}
                      </td>
                      <td>{r.type}</td>
                      <td>{r.requested_by}</td>
                      <td>
                        <span className={statusClass(r.status)}>{r.status.replace('_', ' ')}</span>
                      </td>
                      <td style={{ whiteSpace: 'nowrap', fontSize: '.78rem' }}>
                        {r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}
                      </td>
                      <td>
                        <Link to={plAdminRequest(r.id)} className="btn btn-ghost btn-sm">
                          Edit
                        </Link>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
