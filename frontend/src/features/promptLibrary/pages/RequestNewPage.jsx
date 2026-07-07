import { useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { createRequest } from '../api/requests';
import { useToast } from '../context/ToastContext';
import { componentCategoryLabel } from '../utils/prompt';
import { plRequests } from '../paths';

export default function RequestNewPage() {
  const [searchParams] = useSearchParams();
  const promptId = searchParams.get('promptId') || '';
  // Deep-link prefill from the CAS generation tabs (Phase 12d): which
  // pipeline category the request is about, and the project/course the
  // requester was working in.
  const component = searchParams.get('component') || '';
  const context = searchParams.get('context') || '';
  const componentLabel = component ? componentCategoryLabel(component, null) : '';
  const navigate = useNavigate();
  const { show } = useToast();

  const [title, setTitle] = useState(componentLabel ? `${componentLabel} prompt change` : '');
  const [description, setDescription] = useState(
    componentLabel || context
      ? `Requested from the ${componentLabel || component} tab.${context ? `\nContext: ${context}` : ''}\n\n`
      : '',
  );
  const [type, setType] = useState(promptId ? 'update' : 'new');
  const [saving, setSaving] = useState(false);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!title.trim()) {
      show('Please enter a title.');
      return;
    }
    setSaving(true);
    try {
      await createRequest({
        title: title.trim(),
        description: description.trim(),
        type,
        prompt_id: promptId || null,
      });
      show('Request submitted! 📬');
      navigate(plRequests);
    } catch (err) {
      show(err instanceof Error ? err.message : 'Failed to submit');
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Link to={plRequests} className="back-link">
        ← Back to requests
      </Link>
      <div className="page-header">
        <h1>{promptId ? 'Request prompt update' : 'Submit a request'}</h1>
      </div>

      <form className="page-card" onSubmit={(e) => void handleSubmit(e)}>
        {promptId && (
          <p style={{ fontSize: '.85rem', color: 'var(--muted)', marginBottom: 14 }}>
            Linked to prompt ID: <code>{promptId}</code>
          </p>
        )}
        <div className="field">
          <label>Type</label>
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="new">New prompt</option>
            <option value="update">Update existing</option>
          </select>
        </div>
        <div className="field">
          <label>Title *</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)} required />
        </div>
        <div className="field">
          <label>Description</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={5} />
        </div>
        <div className="modal-footer">
          <Link to={plRequests} className="btn btn-ghost">
            Cancel
          </Link>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? 'Submitting…' : 'Submit request'}
          </button>
        </div>
      </form>
    </>
  );
}
