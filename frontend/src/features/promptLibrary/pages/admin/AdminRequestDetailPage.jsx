import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { fetchRequests, updateRequest } from '../../api/requests';
import { useToast } from '../../context/ToastContext';
import { plAdminRequests } from '../../paths';

export default function AdminRequestDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { show } = useToast();
  const [request, setRequest] = useState(null);
  const [status, setStatus] = useState('');
  const [adminNotes, setAdminNotes] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchRequests().then((list) => {
      // ids are integers since the native-table cutover; the route param is a string
      const r = list.find((x) => String(x.id) === String(id));
      if (!r) {
        navigate(plAdminRequests, { replace: true });
        return;
      }
      setRequest(r);
      setStatus(r.status);
      setAdminNotes(r.admin_notes || '');
    });
  }, [id, navigate]);

  async function handleSave(e) {
    e.preventDefault();
    if (!id) return;
    setSaving(true);
    try {
      await updateRequest(id, { status, admin_notes: adminNotes.trim() });
      show('Request updated!');
      navigate(plAdminRequests);
    } catch (err) {
      show(err instanceof Error ? err.message : 'Update failed');
    } finally {
      setSaving(false);
    }
  }

  if (!request) {
    return <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 48 }}>Loading…</p>;
  }

  return (
    <>
      <Link to={plAdminRequests} className="back-link">
        ← Back to requests
      </Link>
      <div className="page-header">
        <h1>{request.title}</h1>
      </div>

      <form className="page-card" onSubmit={(e) => void handleSave(e)}>
        <div className="field">
          <label>Description</label>
          <p style={{ fontSize: '.9rem', lineHeight: 1.5 }}>{request.description || '—'}</p>
        </div>
        <div className="inline-fields">
          <div className="field">
            <label>Type</label>
            <input value={request.type} disabled />
          </div>
          <div className="field">
            <label>Requested by</label>
            <input value={request.requested_by} disabled />
          </div>
        </div>
        <div className="field">
          <label>Status</label>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="open">Open</option>
            <option value="in_progress">In progress</option>
            <option value="done">Done</option>
            <option value="rejected">Rejected</option>
          </select>
        </div>
        <div className="field">
          <label>Admin notes</label>
          <textarea value={adminNotes} onChange={(e) => setAdminNotes(e.target.value)} rows={4} />
        </div>
        <div className="modal-footer">
          <Link to={plAdminRequests} className="btn btn-ghost">
            Cancel
          </Link>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? 'Saving…' : 'Save changes'}
          </button>
        </div>
      </form>
    </>
  );
}
