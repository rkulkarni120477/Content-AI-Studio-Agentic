import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { getHostPromptDetail, listAllPrompts } from '../api/flow';
import { createRequest } from '../api/requests';
import { useToast } from '../context/ToastContext';
import { componentCategoryLabel } from '../utils/prompt';
import { buildRequestDescription } from '../utils/requestProposal';
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

  // Current content of the chosen prompt, and the requester's editable copy.
  // detail: null = nothing to load, 'loading', 'unavailable', or {system, user}.
  const [detail, setDetail] = useState(null);
  // Name/category of the deep-linked prompt, shown as a fixed fact instead of
  // a picker — the requester already chose it by clicking "Request a Change"
  // next to it.
  const [detailMeta, setDetailMeta] = useState(null);
  const [proposedSystem, setProposedSystem] = useState('');
  const [proposedUser, setProposedUser] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    // The picker is only for requests started cold from "My requests" —
    // deep-linked requests arrive knowing their prompt.
    if (type !== 'update' || linkedPromptId || prompts !== null) return;
    listAllPrompts()
      .then((list) =>
        setPrompts(
          (Array.isArray(list) ? list : [])
            .slice()
            .sort((a, b) => (a.name || '').localeCompare(b.name || '')),
        ),
      )
      .catch(() => setPrompts([]));
  }, [type, prompts, linkedPromptId]);

  // Load the chosen prompt's active text so the requester can edit it in
  // place. Some carried-over library rows have no system/user split — for
  // those the form falls back to a plain written request.
  useEffect(() => {
    if (type !== 'update' || !promptId) {
      setDetail(null);
      return;
    }
    let active = true;
    setDetail('loading');
    getHostPromptDetail(promptId)
      .then((d) => {
        if (!active) return;
        setDetailMeta({
          name: d?.name || `prompt #${promptId}`,
          component: d?.component_type || '',
          variant: d?.variant || '',
        });
        const system = d?.system_prompt || '';
        const user = d?.user_prompt_template || '';
        if (!system && !user) {
          setDetail('unavailable');
          return;
        }
        setDetail({ system, user });
        setProposedSystem(system);
        setProposedUser(user);
      })
      .catch(() => {
        if (active) setDetail('unavailable');
      });
    return () => {
      active = false;
    };
  }, [type, promptId]);

  const systemChanged = detail && typeof detail === 'object' && proposedSystem.trim() !== detail.system.trim();
  const userChanged = detail && typeof detail === 'object' && proposedUser.trim() !== detail.user.trim();

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
    if (type === 'update' && !description.trim() && !systemChanged && !userChanged) {
      show('Describe the change you want, or edit the prompt text below to propose it.');
      return;
    }
    setSaving(true);
    try {
      await createRequest({
        title: title.trim(),
        description: buildRequestDescription({
          rationale: description,
          proposedSystem: systemChanged ? proposedSystem : null,
          proposedUser: userChanged ? proposedUser : null,
        }),
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
        {type === 'update' && linkedPromptId && (
          <div className="field">
            <label>Prompt to update</label>
            <p style={{ fontSize: '.9rem', margin: 0 }}>
              <strong>{detailMeta?.name || `prompt #${linkedPromptId}`}</strong>
              {detailMeta?.component && (
                <span style={{ color: 'var(--muted)' }}>
                  {' '}— {componentCategoryLabel(detailMeta.component, detailMeta.variant || null)}
                </span>
              )}
            </p>
          </div>
        )}
        {type === 'update' && !linkedPromptId && (
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
              Easier route: open the tab or page where you use the prompt and click
              “📬 Request a Change” there — it fills this form in for you.
            </p>
          </div>
        )}
        <div className="field">
          <label>Title *</label>
          <input value={title} onChange={(e) => setTitle(e.target.value)} required />
        </div>
        <div className="field">
          <label>{type === 'update' ? 'What should change, and why?' : 'Description'}</label>
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={4} />
        </div>

        {type === 'update' && detail === 'loading' && (
          <p style={{ fontSize: '.85rem', color: 'var(--muted)' }}>Loading the current prompt text…</p>
        )}
        {type === 'update' && detail === 'unavailable' && promptId && (
          <p style={{ fontSize: '.85rem', color: 'var(--muted)' }}>
            This prompt&apos;s text can&apos;t be loaded here — describe the change you want above.
          </p>
        )}
        {type === 'update' && detail && typeof detail === 'object' && (
          <>
            <p className="var-tip">
              ✏️ Below is the prompt&apos;s current text. Edit it to propose the exact change —
              or leave it untouched and just describe the change above. Only the parts you
              edit are sent with the request.
            </p>
            <div className="field">
              <label>
                System prompt
                {systemChanged && <span className="badge badge-team" style={{ marginLeft: 8 }}>edited</span>}
              </label>
              <textarea
                value={proposedSystem}
                onChange={(e) => setProposedSystem(e.target.value)}
                rows={8}
                style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '.82rem' }}
              />
            </div>
            <div className="field">
              <label>
                User prompt template
                {userChanged && <span className="badge badge-team" style={{ marginLeft: 8 }}>edited</span>}
              </label>
              <textarea
                value={proposedUser}
                onChange={(e) => setProposedUser(e.target.value)}
                rows={8}
                style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: '.82rem' }}
              />
            </div>
          </>
        )}

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
