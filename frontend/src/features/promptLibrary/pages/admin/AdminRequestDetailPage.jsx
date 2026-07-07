import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { fetchPrompt } from '../../api/prompts';
import { fetchRequests, updateRequest } from '../../api/requests';
import { useToast } from '../../context/ToastContext';
import { APPLY_PROPOSAL_KEY, parseRequestDescription } from '../../utils/requestProposal';
import { plAdminRequests, plPrompt, plPromptEdit, plPromptNew } from '../../paths';

const STATUS_LABELS = { open: 'Open', in_progress: 'In progress', done: 'Done', rejected: 'Rejected' };

const PRE_STYLE = {
  fontSize: '.8rem',
  lineHeight: 1.5,
  whiteSpace: 'pre-wrap',
  // global.scss styles pre as light-on-dark; these blocks sit on a light
  // card, so both colors must be set together.
  background: 'var(--tag-bg)',
  color: 'var(--text)',
  padding: '10px 12px',
  borderRadius: 6,
  margin: 0,
  maxHeight: 340,
  overflowY: 'auto',
  flex: 1,
};

function CompareBlock({ label, current, proposed }) {
  if (proposed === null) return null;
  const unchanged = current !== null && proposed.trim() === current.trim();
  return (
    <div className="field">
      <label>
        {label}
        <span className={`badge ${unchanged ? 'badge-draft' : 'badge-team'}`} style={{ marginLeft: 8 }}>
          {unchanged ? '✓ already applied' : 'proposed change'}
        </span>
      </label>
      {unchanged ? (
        <pre style={PRE_STYLE}>{proposed}</pre>
      ) : (
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          {current !== null && (
            <div style={{ flex: '1 1 320px', display: 'flex', flexDirection: 'column', gap: 4 }}>
              <span style={{ fontSize: '.72rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.04em' }}>
                Current
              </span>
              <pre style={PRE_STYLE}>{current || '(empty)'}</pre>
            </div>
          )}
          <div style={{ flex: '1 1 320px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            <span style={{ fontSize: '.72rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '.04em' }}>
              Proposed
            </span>
            <pre style={{ ...PRE_STYLE, outline: '2px solid var(--primary)', outlineOffset: -2 }}>{proposed}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

export default function AdminRequestDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { show } = useToast();
  const [request, setRequest] = useState(null);
  const [linkedPrompt, setLinkedPrompt] = useState(null); // prompt | 'missing' | null
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
      setAdminNotes(r.admin_notes || '');
    });
  }, [id, navigate]);

  useEffect(() => {
    if (!request?.prompt_id) return;
    fetchPrompt(request.prompt_id)
      .then((p) => setLinkedPrompt(p || 'missing'))
      .catch(() => setLinkedPrompt('missing'));
  }, [request?.prompt_id]);

  async function applyStatus(nextStatus, successMessage) {
    if (nextStatus === 'rejected' && !adminNotes.trim()) {
      show('Add an admin note explaining the rejection — the requester will see it.');
      return;
    }
    setSaving(true);
    try {
      await updateRequest(id, { status: nextStatus, admin_notes: adminNotes.trim() });
      show(successMessage);
      navigate(plAdminRequests);
    } catch (err) {
      show(err instanceof Error ? err.message : 'Update failed');
    } finally {
      setSaving(false);
    }
  }

  async function saveNotesOnly() {
    setSaving(true);
    try {
      await updateRequest(id, { status: request.status, admin_notes: adminNotes.trim() });
      setRequest((r) => ({ ...r, admin_notes: adminNotes.trim() }));
      show('Notes saved.');
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
  // Current text for the side-by-side view: PL detail keeps the user template
  // in `content` and the system prompt on the pipeline sub-object.
  const currentSystem = promptLoaded ? (linkedPrompt.pipeline?.system_prompt || '') : null;
  const currentUser = promptLoaded ? (linkedPrompt.content || '') : null;
  const fullyApplied = hasProposedEdit && promptLoaded
    && (proposedSystem === null || proposedSystem.trim() === currentSystem.trim())
    && (proposedUser === null || proposedUser.trim() === currentUser.trim());
  const isResolved = request.status === 'done' || request.status === 'rejected';

  function openEditorWithProposal() {
    sessionStorage.setItem(APPLY_PROPOSAL_KEY, JSON.stringify({
      promptId: linkedPrompt.id,
      system: proposedSystem,
      user: proposedUser,
      note: `Applied from request #${request.id} by ${request.requested_by}: ${request.title}`,
    }));
    navigate(plPromptEdit(linkedPrompt.id));
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
            <span className={`req-status req-${request.status}`}>
              {STATUS_LABELS[request.status] || request.status}
            </span>
            {' · '}
            {request.type === 'new' ? 'New prompt request' : 'Update request'} by{' '}
            <strong>{request.requested_by}</strong>
            {request.created_at && ` · ${new Date(request.created_at).toLocaleDateString()}`}
            {request.updated_at && request.updated_at !== request.created_at
              && ` · updated ${new Date(request.updated_at).toLocaleDateString()}`}
          </p>
        </div>
        <div className="page-actions">
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

      {/* ── The request ─────────────────────────────────────────────── */}
      <div className="page-card">
        {request.prompt_id && (
          <div className="field">
            <label>Prompt</label>
            {linkedPrompt === null ? (
              <p style={{ fontSize: '.9rem', color: 'var(--muted)' }}>Loading…</p>
            ) : linkedPrompt === 'missing' ? (
              <p style={{ fontSize: '.9rem', color: 'var(--muted)' }}>
                Prompt #{request.prompt_id} is no longer available (deleted or inaccessible).
              </p>
            ) : (
              <p style={{ fontSize: '.9rem', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                <Link to={plPrompt(linkedPrompt.id)}>{linkedPrompt.title}</Link>
                {linkedPrompt.archived && <span className="badge badge-draft">🗄 Archived</span>}
                {!hasProposedEdit && (
                  <Link to={plPromptEdit(linkedPrompt.id)} className="btn btn-ghost" style={{ padding: '2px 10px', fontSize: '.75rem' }}>
                    ✏️ Edit
                  </Link>
                )}
              </p>
            )}
          </div>
        )}
        <div className="field" style={{ marginBottom: 0 }}>
          <label>{hasProposedEdit ? 'What should change, and why' : 'Description'}</label>
          <p style={{ fontSize: '.9rem', lineHeight: 1.5, whiteSpace: 'pre-wrap', margin: 0 }}>
            {rationale || '—'}
          </p>
        </div>
      </div>

      {/* ── The proposed change, next to what it changes ────────────── */}
      {hasProposedEdit && (
        <div className="page-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', marginBottom: 14 }}>
            <h2 style={{ fontSize: '1rem', margin: 0 }}>Proposed edit</h2>
            {promptLoaded && !fullyApplied && (
              <button type="button" className="btn btn-primary" onClick={openEditorWithProposal} style={{ marginLeft: 'auto' }}>
                ✏️ Review &amp; apply in the editor
              </button>
            )}
          </div>
          {fullyApplied && (
            <p className="var-tip" style={{ marginBottom: 14 }}>
              ✅ The prompt&apos;s current version already matches this proposal
              {isResolved ? '.' : ' — mark the request done below.'}
            </p>
          )}
          <CompareBlock label="System prompt" current={currentSystem} proposed={proposedSystem} />
          <CompareBlock label="User prompt template" current={currentUser} proposed={proposedUser} />
          {linkedPrompt === 'missing' && (
            <p style={{ fontSize: '.85rem', color: 'var(--muted)' }}>
              The prompt this proposal targets is no longer available, so it can&apos;t be applied.
            </p>
          )}
        </div>
      )}

      {/* ── Resolution ──────────────────────────────────────────────── */}
      <div className="page-card">
        <div className="field">
          <label>Response to the requester</label>
          <textarea
            value={adminNotes}
            onChange={(e) => setAdminNotes(e.target.value)}
            rows={3}
            placeholder="e.g. Applied in v4 — thanks!  /  Not taking this because…"
          />
          <p className="var-tip" style={{ marginTop: 6 }}>
            The requester sees the status and this response on their “My requests” page.
            Rejecting requires a response.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button type="button" className="btn btn-ghost" disabled={saving} onClick={() => void saveNotesOnly()}>
            Save response
          </button>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {request.status === 'open' && (
              <button
                type="button"
                className="btn btn-ghost"
                disabled={saving}
                onClick={() => void applyStatus('in_progress', 'Marked in progress.')}
              >
                ⏳ Mark in progress
              </button>
            )}
            {!isResolved && (
              <>
                <button
                  type="button"
                  className="btn btn-ghost"
                  disabled={saving}
                  style={{ color: 'var(--danger)' }}
                  onClick={() => void applyStatus('rejected', 'Request rejected — your response is visible to the requester.')}
                >
                  ✗ Reject
                </button>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={saving}
                  onClick={() => void applyStatus('done', 'Request marked done — the requester will see it on My requests.')}
                >
                  ✓ Mark done
                </button>
              </>
            )}
            {isResolved && (
              <button
                type="button"
                className="btn btn-ghost"
                disabled={saving}
                onClick={() => void applyStatus('open', 'Request reopened.')}
              >
                ↩️ Reopen
              </button>
            )}
          </span>
        </div>
      </div>
    </>
  );
}
