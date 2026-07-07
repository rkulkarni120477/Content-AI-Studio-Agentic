import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { fetchPrompt } from '../../api/prompts';
import { fetchRequests, updateRequest } from '../../api/requests';
import { useToast } from '../../context/ToastContext';
import { APPLY_PROPOSAL_KEY, parseRequestDescription } from '../../utils/requestProposal';
import { plAdminRequests, plPrompt, plPromptEdit, plPromptNew } from '../../paths';

export default function AdminRequestDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { show } = useToast();
  const [request, setRequest] = useState(null);
  const [linkedPrompt, setLinkedPrompt] = useState(null); // prompt | 'missing' | null
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

  useEffect(() => {
    if (!request?.prompt_id) return;
    fetchPrompt(request.prompt_id)
      .then((p) => setLinkedPrompt(p || 'missing'))
      .catch(() => setLinkedPrompt('missing'));
  }, [request?.prompt_id]);

  async function handleSave(e) {
    e.preventDefault();
    if (!id) return;
    if (status === 'rejected' && !adminNotes.trim()) {
      show('Add an admin note explaining the rejection — the requester will see it.');
      return;
    }
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

  const { rationale, proposedSystem, proposedUser } = parseRequestDescription(request.description);
  const hasProposedEdit = proposedSystem !== null || proposedUser !== null;
  const promptLoaded = linkedPrompt && linkedPrompt !== 'missing';
  // Current text for side-by-side context: PL detail keeps the user template in
  // `content` and the system prompt on the pipeline sub-object.
  const currentSystem = promptLoaded ? (linkedPrompt.pipeline?.system_prompt || '') : null;
  const currentUser = promptLoaded ? (linkedPrompt.content || '') : null;

  function openEditorWithProposal() {
    sessionStorage.setItem(APPLY_PROPOSAL_KEY, JSON.stringify({
      promptId: linkedPrompt.id,
      system: proposedSystem,
      user: proposedUser,
      note: `Applied from request #${request.id} by ${request.requested_by}: ${request.title}`,
    }));
    navigate(plPromptEdit(linkedPrompt.id));
  }

  function proposalBlock(label, proposed, current) {
    if (proposed === null) return null;
    const unchanged = current !== null && proposed.trim() === current.trim();
    return (
      <div className="field" key={label}>
        <label>
          Proposed {label}
          {current !== null && (
            <span className={`badge ${unchanged ? 'badge-draft' : 'badge-team'}`} style={{ marginLeft: 8 }}>
              {unchanged ? 'matches the current version' : 'differs from the current version'}
            </span>
          )}
        </label>
        <pre
          style={{
            fontSize: '.8rem',
            lineHeight: 1.5,
            whiteSpace: 'pre-wrap',
            // global.scss styles pre as light-on-dark; this block is on a
            // light card, so both colors must be set together.
            background: 'var(--tag-bg)',
            color: 'var(--text)',
            padding: '10px 12px',
            borderRadius: 6,
            margin: 0,
            maxHeight: 340,
            overflowY: 'auto',
          }}
        >
          {proposed}
        </pre>
      </div>
    );
  }

  return (
    <>
      <Link to={plAdminRequests} className="back-link">
        ← Back to requests
      </Link>
      <div className="page-header">
        <div>
          <h1>{request.title}</h1>
          <p className="view-meta">
            Requested by <strong>{request.requested_by}</strong>
            {request.created_at && ` · ${new Date(request.created_at).toLocaleDateString()}`}
            {request.updated_at && request.updated_at !== request.created_at
              && ` · updated ${new Date(request.updated_at).toLocaleDateString()}`}
          </p>
        </div>
        <div className="page-actions">
          {promptLoaded && hasProposedEdit && (
            <button type="button" className="btn btn-primary" onClick={openEditorWithProposal}>
              ✅ Apply this proposal in the editor
            </button>
          )}
          {promptLoaded && !hasProposedEdit && (
            <Link to={plPromptEdit(linkedPrompt.id)} className="btn btn-primary">
              ✏️ Edit the prompt
            </Link>
          )}
          {request.type === 'new' && !request.prompt_id && (
            <Link
              to={`${plPromptNew}?${new URLSearchParams({
                description:
                  `Requested by ${request.requested_by} (request #${request.id}): ${request.title}`
                  + (request.description ? `\n\n${request.description}` : ''),
              })}`}
              className="btn btn-primary"
            >
              ＋ Draft this prompt
            </Link>
          )}
        </div>
      </div>

      <form className="page-card" onSubmit={(e) => void handleSave(e)}>
        <div className="field">
          <label>{hasProposedEdit ? 'What should change, and why' : 'Description'}</label>
          <p style={{ fontSize: '.9rem', lineHeight: 1.5, whiteSpace: 'pre-wrap' }}>
            {rationale || '—'}
          </p>
        </div>
        {hasProposedEdit && (
          <>
            {proposalBlock('system prompt', proposedSystem, currentSystem)}
            {proposalBlock('user prompt template', proposedUser, currentUser)}
          </>
        )}
        {request.prompt_id && (
          <div className="field">
            <label>Linked prompt</label>
            {linkedPrompt === null ? (
              <p style={{ fontSize: '.9rem', color: 'var(--muted)' }}>Loading…</p>
            ) : linkedPrompt === 'missing' ? (
              <p style={{ fontSize: '.9rem', color: 'var(--muted)' }}>
                Prompt #{request.prompt_id} is no longer available (deleted or inaccessible).
              </p>
            ) : (
              <p style={{ fontSize: '.9rem' }}>
                <Link to={plPrompt(linkedPrompt.id)}>{linkedPrompt.title}</Link>
                {linkedPrompt.archived && (
                  <span className="badge badge-draft" style={{ marginLeft: 8 }}>🗄 Archived</span>
                )}
              </p>
            )}
          </div>
        )}
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
          <p className="var-tip" style={{ marginTop: 6 }}>
            The requester sees the status and these notes on their “My requests” page.
          </p>
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
