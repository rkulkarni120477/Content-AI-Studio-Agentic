import { Link } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { fetchRequests } from '../api/requests';
import { plRequestNew } from '../paths';

function statusClass(status) {
  return `req-status req-${status}`;
}

export default function RequestsPage() {
  const [requests, setRequests] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchRequests()
      .then(setRequests)
      .finally(() => setLoading(false));
  }, []);

  return (
    <>
      <div className="page-header">
        <div>
          <h1>My requests</h1>
          <p className="subtitle">Submit new prompt ideas or request updates to existing prompts.</p>
        </div>
        <Link to={plRequestNew} className="btn btn-primary">
          ＋ New request
        </Link>
      </div>

      <div className="page-card">
        {loading ? (
          <p style={{ color: 'var(--muted)' }}>Loading…</p>
        ) : requests.length === 0 ? (
          <p style={{ color: 'var(--muted)' }}>No requests yet.</p>
        ) : (
          <div className="table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Created</th>
                </tr>
              </thead>
              <tbody>
                {requests.map((r) => (
                  <tr key={r.id}>
                    <td>
                      <strong>{r.title}</strong>
                      {r.description && (
                        <div style={{ fontSize: '.78rem', color: 'var(--muted)', marginTop: 4 }}>
                          {r.description}
                        </div>
                      )}
                    </td>
                    <td>{r.type}</td>
                    <td>
                      <span className={statusClass(r.status)}>{r.status.replace('_', ' ')}</span>
                    </td>
                    <td style={{ whiteSpace: 'nowrap', fontSize: '.85rem' }}>
                      {r.created_at ? new Date(r.created_at).toLocaleDateString() : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
