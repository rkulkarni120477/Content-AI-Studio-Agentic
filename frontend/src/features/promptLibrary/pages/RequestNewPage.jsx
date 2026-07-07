import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { listAllPrompts } from '../api/flow';
import { createRequest } from '../api/requests';
import { useToast } from '../context/ToastContext';
import { componentCategoryLabel } from '../utils/prompt';
import { plRequests } from '../paths';

export default function RequestNewPage() {
  const [searchParams] = useSearchParams();
  const linkedPromptId = searchParams.get('promptId') || '';
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
  const [type, setType] = useState(linkedPromptId ? 'update' : 'new');
  const [promptId, setPromptId] = useState(linkedPromptId);
  const [prompts, setPrompts] = useState(null); // null = not loaded yet
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (type !== 'update' || prompts !== null) return;
    listAllPrompts()
      .then((list) =>
        setPrompts(
          (Array.isArray(list) ? list : [])
            .slice()
            .sort((a, b) => (a.name || '').localeCompare(b.name || '')),
        ),
      )
      .catch(() => setPrompts([]));
  }, [type, prompts]);

  async function handleSubmit(e) {
    e.preventDefault();
    if (!title.trim()) {
      show('Please enter a title.');
      return;
    }
    if (type === 'update' && !promptId) {
      show('Choose the prompt this request is about.');
      return;
    }
    setSaving(true);
    try {
      await createRequest({
        title: title.trim(),
        description: description.trim(),
        type,
        prompt_id: type === 'update' ? promptId : null,
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
        <h1>{linkedPromptId ? 'Request prompt update' : 'Submit a request'}</h1>
      </div>

      <form className="page-card" onSubmit={(e) => void handleSubmit(e)}>
        <div className="field">
          <label>Type</label>
          <select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="new">New prompt</option>
            <option value="update">Update existing</option>
          </select>
        </div>
        {type === 'update' && (
          <div className="field">
            <label>Prompt to update *</label>
            {prompts === null ? (
              <p style={{ fontSize: '.85rem', color: 'var(--muted)' }}>Loading prompts…</p>
            ) : (
              <select value={promptId} onChange={(e) => setPromptId(e.target.value)}>
                <option value="">— Choose a prompt —</option>
                {prompts.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                    {p.component_type
                      ? ` — ${componentCategoryLabel(p.component_type, p.variant)}`
                      : ''}
                    {p.is_default ? ' ★' : ''}
                  </option>
                ))}
              </select>
            )}
            <p className="var-tip" style={{ marginTop: 6 }}>
              Tells the admin exactly which prompt you want changed.
            </p>
          </div>
        )}
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
