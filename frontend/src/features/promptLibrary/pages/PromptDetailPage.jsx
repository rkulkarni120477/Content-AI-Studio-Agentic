import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import {
  deleteAttachment,
  downloadAttachment,
  fetchPrompt,
  fetchReviews,
  markPromptUsed,
  promotePrompt,
  restorePrompt,
  submitReview,
  uploadAttachment,
} from '../api/prompts';
import { setPipelineDefault, setPipelineVersionState } from '../api/pipeline';
import { listPromptFixings } from '../api/flow';
import { useAuth } from '../context/AuthContext';
import { useToast } from '../context/ToastContext';
import { canManagePipelinePrompts, canManagePrompts } from '../utils/permissions';
import { CAS_SLOT_OPTIONS, componentCategoryLabel, fillPromptContent, starsDisplay } from '../utils/prompt';
import { plHome, plPrompt, plPromptEdit, plRequestNew } from '../paths';

// Mirrors the backend's _STATE_TRANSITIONS (illegal moves 409 server-side).
const STATE_ACTIONS = {
  draft: [{ to: 'in_review', label: 'Submit for review' }],
  in_review: [
    { to: 'approved', label: 'Approve' },
    { to: 'draft', label: 'Reject to draft' },
  ],
  approved: [
    { to: 'active', label: 'Deploy (activate)' },
    { to: 'draft', label: 'Reject to draft' },
  ],
  active: [],
};

const STATE_BADGE = {
  draft: 'badge-draft',
  in_review: 'badge-team',
  approved: 'badge-global',
  active: 'badge-global',
};

function StateBadge({ state }) {
  if (!state) return null;
  return (
    <span className={`badge ${STATE_BADGE[state] || 'badge-draft'}`}>
      {state.replace('_', ' ')}
    </span>
  );
}

export default function PromptDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { show } = useToast();
  const canManage = canManagePrompts(user);
  const canViewReviewDetails = canManage;

  const [prompt, setPrompt] = useState(null);
  const [fixings, setFixings] = useState([]);
  const [reviews, setReviews] = useState([]);
  const [varValues, setVarValues] = useState({});
  const [rating, setRating] = useState(0);
  const [feedback, setFeedback] = useState('');
  const [loading, setLoading] = useState(true);
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoteSlot, setPromoteSlot] = useState('');

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    Promise.all([fetchPrompt(id), canViewReviewDetails ? fetchReviews(id) : Promise.resolve([])])
      .then(([p, revs]) => {
        if (!p) {
          navigate(plHome, { replace: true });
          return;
        }
        setPrompt(p);
        setReviews(revs);
        if (p.prompt_kind === 'pipeline') {
          void listPromptFixings(p.id).then(setFixings).catch(() => setFixings([]));
        } else {
          setFixings([]);
        }
        const vars = p.variables || [];
        const init = {};
        vars.forEach((v) => {
          init[v.name] = '';
        });
        setVarValues(init);
      })
      .finally(() => setLoading(false));
  }, [id, navigate, canViewReviewDetails]);

  const content = useMemo(() => prompt?.content ?? '', [prompt]);

  const filledPreview = useMemo(() => {
    if (!prompt) return '';
    const vars = prompt.variables || [];
    return fillPromptContent(content, vars, varValues);
  }, [content, prompt, varValues]);

  const allVarsFilled = useMemo(() => {
    const defs = prompt?.variables || [];
    if (defs.length === 0) return true;
    return defs.every((v) => (varValues[v.name] ?? '').trim() !== '');
  }, [prompt, varValues]);

  function renderPreviewHtml() {
    return content.split(/(\{\{\w+\}\})/g).map((part, i) => {
      const m = part.match(/^\{\{(\w+)\}\}$/);
      if (m) {
        const v = (varValues[m[1]] || '').trim();
        return v ? (
          <mark key={i} className="var-filled">
            {v}
          </mark>
        ) : (
          <span key={i} className="var-unfilled">
            {`{{${m[1]}}}`}
          </span>
        );
      }
      return <span key={i}>{part}</span>;
    });
  }

  async function handleCopy() {
    if (!id) return;
    const defs = prompt?.variables || [];
    if (defs.length > 0 && !defs.every((v) => (varValues[v.name] ?? '').trim() !== '')) {
      show('Fill in all variables before copying.');
      return;
    }
    await navigator.clipboard.writeText(filledPreview);
    await markPromptUsed(id);
    show('Prompt copied to clipboard ✓');
  }

  async function handleUpload(e) {
    if (!id || !e.target.files?.[0]) return;
    try {
      await uploadAttachment(id, e.target.files[0]);
      const p = await fetchPrompt(id);
      if (p) setPrompt(p);
      show('File uploaded!');
    } catch (err) {
      show(err instanceof Error ? err.message : 'Upload failed');
    }
    e.target.value = '';
  }

  async function handleDeleteAtt(aid) {
    if (!id) return;
    await deleteAttachment(id, aid);
    const p = await fetchPrompt(id);
    if (p) setPrompt(p);
    show('Attachment deleted.');
  }

  async function handleDownloadAtt(aid, name) {
    try {
      await downloadAttachment(prompt.id, aid, name);
    } catch (err) {
      show(err instanceof Error ? err.message : 'Download failed');
    }
  }

  async function handleSubmitReview(e) {
    e.preventDefault();
    if (!id || rating < 1) {
      show('Please select a star rating.');
      return;
    }
    await submitReview(id, rating, feedback);
    setRating(0);
    setFeedback('');
    if (canViewReviewDetails) {
      const revs = await fetchReviews(id);
      setReviews(revs);
    }
    const p = await fetchPrompt(id);
    if (p) setPrompt(p);
    show('Review submitted! ⭐');
  }

  async function refetch() {
    const p = await fetchPrompt(id);
    if (p) setPrompt(p);
  }

  async function handleTransition(versionLabel, to) {
    try {
      await setPipelineVersionState(prompt.id, versionLabel, to);
      await refetch();
      show(to === 'active' ? `Version ${versionLabel} deployed ✓` : `Version ${versionLabel} is now ${to.replace('_', ' ')}.`);
    } catch (err) {
      show(err instanceof Error ? err.message : 'Transition failed');
    }
  }

  async function handlePromote() {
    const slot = CAS_SLOT_OPTIONS.find((s) => s.label === promoteSlot);
    if (!slot) return;
    // Declared variables survive promotion and arm strict enforcement: a
    // generation call that fails to supply one errors loudly. Warn up front
    // so nobody promotes a {{var}}-heavy prompt into a stage whose builder
    // doesn't provide those names.
    const varNames = (prompt.variables || []).map((v) => v.name);
    const varWarning = varNames.length
      ? `\n\n⚠ This prompt declares ${varNames.length} variable(s): ${varNames
          .map((n) => `{{${n}}}`)
          .join(', ')}. After promotion, ${slot.label} generation MUST supply them or it will ` +
        'fail with an error — only promote if that stage provides these values.'
      : '';
    const ok = window.confirm(
      `Promote "${prompt.title}" to an admin-managed ${slot.label} pipeline prompt?\n\n` +
        'It keeps its ID and version history. ' +
        'Generation will NOT use it until you make it a component default or lock it to a scope on the Titles tab.' +
        varWarning,
    );
    if (!ok) return;
    try {
      await promotePrompt(prompt.id, { component_type: slot.component, variant: slot.variant });
      setPromoteOpen(false);
      setPromoteSlot('');
      await refetch();
      void listPromptFixings(prompt.id).then(setFixings).catch(() => setFixings([]));
      show('Promoted to pipeline ✓ — make it a default or bind it in Titles to put it in use.');
    } catch (err) {
      show(err instanceof Error ? err.message : 'Promote failed');
    }
  }

  async function handleToggleDefault() {
    try {
      await setPipelineDefault(prompt.id, !prompt.pipeline?.is_default);
      await refetch();
      show(prompt.pipeline?.is_default ? 'Default flag cleared.' : 'This prompt is now the component default ✓');
    } catch (err) {
      show(err instanceof Error ? err.message : 'Update failed');
    }
  }

  if (loading || !prompt) {
    return <p style={{ color: 'var(--muted)', textAlign: 'center', padding: 48 }}>Loading…</p>;
  }

  const isPipeline = prompt.prompt_kind === 'pipeline';
  const pipe = prompt.pipeline || null;
  const canPipeline = canManagePipelinePrompts(user);
  const vars = prompt.variables || [];
  const atts = prompt.attachments || [];
  const reviewStats = prompt._review_stats || { count: 0, avg: 0 };
  const hasReviews = reviewStats.count > 0;
  const avg = hasReviews ? reviewStats.avg.toFixed(1) : null;

  return (
    <>
      <Link to={prompt.parent ? plPrompt(prompt.parent.id) : plHome} className="back-link">
        ← {prompt.parent ? `Back to ${prompt.parent.title}` : 'Back to library'}
      </Link>
      <div className="page-header">
        <div>
          <h1>
            {prompt.title}
            {prompt.parent_id && (
              <span className="badge badge-team" style={{ marginLeft: 8, verticalAlign: 'middle' }}>
                Follow-up
              </span>
            )}
            {prompt.archived && (
              <span
                className="badge badge-draft"
                style={{ marginLeft: 8, verticalAlign: 'middle' }}
                title="Archived — excluded from generation and all pickers until restored"
              >
                🗄 Archived
              </span>
            )}
          </h1>
          {prompt.parent && (
            <p className="view-meta" style={{ marginBottom: 4 }}>
              Parent: <Link to={plPrompt(prompt.parent.id)}>{prompt.parent.title}</Link>
            </p>
          )}
          {isPipeline && pipe ? (
            <>
              <p className="view-meta">
                <span className="badge badge-team" style={{ marginRight: 6 }}>
                  Pipeline · {componentCategoryLabel(pipe.component_type, pipe.variant)}
                  {pipe.component_type ? ` (${pipe.component_type}${pipe.variant ? `/${pipe.variant}` : ''})` : ''}
                </span>
                {pipe.is_default && (
                  <span className="badge badge-global" style={{ marginRight: 6 }}>
                    Default
                  </span>
                )}
                <StateBadge state={pipe.workflow_state} />
                {' '}· Registry name: <code>{pipe.name}</code>
                {' '}· Active: {pipe.active_version || '—'}
                {' '}· Updated: {prompt.updated_at ? new Date(prompt.updated_at).toLocaleDateString() : '—'}
              </p>
              {/* Scope facets (Phase 11, doc §4): where this prompt is in use —
                  relational facts rendered read-only, never freeform tags. */}
              <p className="view-meta" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                <span style={{ color: 'var(--muted)' }}>Used by:</span>
                {pipe.is_default && (
                  <span className="badge badge-team" title="Resolves for every scope without a lock for this component">
                    ★ All titles (component default)
                  </span>
                )}
                {fixings.map((f) => (
                  <span
                    key={f.id}
                    className="badge badge-global"
                    title={`Locked by ${f.fixed_by || '—'} for component "${f.component}"`}
                  >
                    🔒 {f.scope_level === 'global'
                      ? 'Global'
                      : `${f.scope_level[0].toUpperCase()}${f.scope_level.slice(1)}: ${f.scope_name || `#${f[`${f.scope_level}_id`]}`}`}
                  </span>
                ))}
                {!pipe.is_default && fixings.length === 0 && (
                  <span style={{ color: 'var(--muted)' }}>
                    nowhere yet — not a default and no scope locks (bind one on the Titles tab)
                  </span>
                )}
              </p>
            </>
          ) : (
            <p className="view-meta">
              Category: {prompt.category || '—'} ·{' '}
              {prompt.visibility === 'team' && (prompt.teams || []).length
                ? `Teams: ${(prompt.teams || []).join(', ')}`
                : `Visibility: ${prompt.visibility}`}{' '}
              · Updated: {prompt.updated_at ? new Date(prompt.updated_at).toLocaleDateString() : '—'}
            </p>
          )}
        </div>
        <div className="page-actions">
          <button
            type="button"
            className="btn btn-primary btn-copy"
            onClick={() => void handleCopy()}
            disabled={vars.length > 0 && !allVarsFilled}
            title={vars.length > 0 && !allVarsFilled ? 'Fill in all variables first' : undefined}
          >
            {vars.length ? '📋 Copy Filled Prompt' : '📋 Copy Prompt'}
          </button>
          {!canManage && !isPipeline && (
            <Link to={`${plRequestNew}?promptId=${prompt.id}`} className="btn btn-ghost">
              Request update
            </Link>
          )}
          {(isPipeline ? canPipeline : canManage) && (
            <Link to={plPromptEdit(prompt.id)} className="btn btn-ghost">
              Edit
            </Link>
          )}
          {isPipeline && canPipeline && pipe?.component_type && (
            <button type="button" className="btn btn-ghost" onClick={() => void handleToggleDefault()}>
              {pipe.is_default ? 'Clear default' : `Make default for ${componentCategoryLabel(pipe.component_type, pipe.variant)}`}
            </button>
          )}
          {prompt.archived && (isPipeline ? canPipeline : canManage) && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                void restorePrompt(prompt.id)
                  .then(() => refetch())
                  .then(() => show('Prompt restored ♻ — available again everywhere.'))
                  .catch((err) => show(err instanceof Error ? err.message : 'Restore failed'));
              }}
            >
              ♻ Restore from archive
            </button>
          )}
          {!isPipeline && canPipeline && !prompt.parent_id && (prompt.children || []).length === 0 && (
            <button
              type="button"
              className="btn btn-ghost"
              title="Convert this library prompt into an admin-managed CAS pipeline prompt"
              onClick={() => setPromoteOpen((v) => !v)}
            >
              ⚙ Promote to pipeline…
            </button>
          )}
        </div>
      </div>

      {promoteOpen && !isPipeline && (
        <div className="page-card" style={{ marginBottom: 16 }}>
          <div className="section-hdr" style={{ marginTop: 0, paddingTop: 0, border: 'none' }}>
            Promote to pipeline
          </div>
          <p className="view-meta">
            The prompt keeps its ID and version history but becomes an
            admin-managed pipeline prompt classified under the chosen CAS
            category. Promotion alone changes nothing for generation: the
            prompt is used only once it is made a component default or locked
            to a scope on the Titles tab.
          </p>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <select value={promoteSlot} onChange={(e) => setPromoteSlot(e.target.value)}>
              <option value="">CAS category…</option>
              {CAS_SLOT_OPTIONS.map((s) => (
                <option key={s.label} value={s.label}>
                  {s.label} ({s.component}
                  {s.variant ? `/${s.variant}` : ''})
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn btn-primary"
              disabled={!promoteSlot}
              onClick={() => void handlePromote()}
            >
              Promote
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setPromoteOpen(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      <div className="page-card">
        {prompt.description && <p className="view-desc">{prompt.description}</p>}
        {(prompt.tags || []).length > 0 && (
          <div className="tags" style={{ marginBottom: 12 }}>
            {(prompt.tags || []).map((t) => (
              <span key={t} className="tag">
                {t}
              </span>
            ))}
          </div>
        )}

        {vars.length > 0 && (
          <div className="var-input-section">
            <div className="section-hdr" style={{ border: 'none', paddingTop: 0, marginTop: 0 }}>
              {isPipeline ? 'Declared variables' : 'Fill variables'}
            </div>
            {isPipeline && (
              <p className="var-tip" style={{ marginTop: 0 }}>
                ⚡ Strict enforcement applies to these: a generation call that
                does not supply one fails with an error instead of silently
                falling back. Fill them below to copy a rendered prompt.
              </p>
            )}
            <div className="var-inputs-grid">
              {vars.map((v) => (
                <div key={v.name} className="view-var-field">
                  <label>{v.label || v.name}</label>
                  <input
                    type="text"
                    placeholder={v.hint || 'Enter value…'}
                    value={varValues[v.name] || ''}
                    onChange={(e) => setVarValues((prev) => ({ ...prev, [v.name]: e.target.value }))}
                  />
                </div>
              ))}
            </div>
          </div>
        )}

        {isPipeline && pipe && (
          <>
            <div className="section-hdr">System prompt</div>
            <div className="view-content" style={{ whiteSpace: 'pre-wrap' }}>
              {pipe.system_prompt || <span style={{ color: 'var(--muted)' }}>(none)</span>}
            </div>
          </>
        )}
        <div className="section-hdr">{isPipeline ? 'User prompt template' : 'Prompt content'}</div>
        <div className="view-content">{renderPreviewHtml()}</div>

        {isPipeline && (
          <div className="detail-section">
            <div className="section-hdr">Version history</div>
            {(prompt.versions || []).length === 0 ? (
              <div style={{ fontSize: '.8rem', color: 'var(--muted)' }}>
                No versions yet — commit one from the edit page.
              </div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {[...(prompt.versions || [])].reverse().map((v) => (
                  <div
                    key={v.version}
                    style={{
                      padding: '10px 12px',
                      border: '1px solid var(--border)',
                      borderRadius: 8,
                      background: v.is_active ? 'var(--card-highlight, rgba(64,120,255,.06))' : undefined,
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <strong>{v.label || `v${v.version}`}</strong>
                      <StateBadge state={v.workflow_state} />
                      {v.is_active && <span className="badge badge-global">live</span>}
                      <span style={{ fontSize: '.75rem', color: 'var(--muted)' }}>
                        {v.created_by || '—'}
                        {v.created_at ? ` · ${new Date(v.created_at).toLocaleDateString()}` : ''}
                      </span>
                      {canPipeline && (
                        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
                          {(STATE_ACTIONS[v.workflow_state] || []).map((a) => (
                            <button
                              key={a.to}
                              type="button"
                              className={a.to === 'active' ? 'btn btn-primary' : 'btn btn-ghost'}
                              style={{ padding: '2px 10px', fontSize: '.75rem' }}
                              onClick={() => void handleTransition(v.label || `v${v.version}`, a.to)}
                            >
                              {a.label}
                            </button>
                          ))}
                        </span>
                      )}
                    </div>
                    {v.note && (
                      <div style={{ fontSize: '.78rem', color: 'var(--muted)', marginTop: 4 }}>{v.note}</div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {!isPipeline && (atts.length > 0 || canManage) && (
          <div className="detail-section">
            <div className="section-hdr">Attachments</div>
            <div className="attach-list">
              {atts.length === 0 ? (
                <div style={{ fontSize: '.8rem', color: 'var(--muted)' }}>No attachments yet.</div>
              ) : (
                atts.map((a) => (
                  <div key={a.id} className="attach-item">
                    <span>📎</span>
                    <span className="attach-name">{a.original_name}</span>
                    <span className="attach-size">{(a.size / 1024).toFixed(1)} KB</span>
                    <button
                      type="button"
                      className="attach-dl"
                      onClick={() => void handleDownloadAtt(a.id, a.original_name)}
                    >
                      ⬇ Download
                    </button>
                    {canManage && (
                      <button type="button" className="attach-del" onClick={() => void handleDeleteAtt(a.id)}>
                        🗑
                      </button>
                    )}
                  </div>
                ))
              )}
            </div>
            {canManage && (
              <div className="upload-row">
                <input type="file" onChange={(e) => void handleUpload(e)} />
              </div>
            )}
          </div>
        )}

        {!isPipeline && prompt.children && prompt.children.length > 0 && (
          <div className="detail-section">
            <div className="section-hdr">Follow-up prompts</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {prompt.children.map((c) => (
                <Link
                  key={c.id}
                  to={plPrompt(c.id)}
                  style={{
                    display: 'block',
                    padding: '10px 12px',
                    border: '1px solid var(--border)',
                    borderRadius: 8,
                    textDecoration: 'none',
                    color: 'inherit',
                  }}
                >
                  <strong>{c.title}</strong>
                  {c.description && (
                    <div style={{ fontSize: '.8rem', color: 'var(--muted)', marginTop: 4 }}>{c.description}</div>
                  )}
                </Link>
              ))}
            </div>
          </div>
        )}

        {!isPipeline && (
        <div className="detail-section">
          <div className="section-hdr">Reviews</div>
          {!hasReviews ? (
            <div style={{ fontSize: '.82rem', color: 'var(--muted)', marginBottom: 12 }}>No reviews yet.</div>
          ) : (
            <div className="review-summary">
              <div className="review-avg">{avg}</div>
              <div>
                <div className="review-stars-lg">{starsDisplay(parseFloat(avg))}</div>
                <div className="review-count">
                  {reviewStats.count} review{reviewStats.count !== 1 ? 's' : ''}
                </div>
              </div>
            </div>
          )}
          {canViewReviewDetails &&
            reviews.map((r) => (
              <div key={r.id} className="review-item">
                <div className="review-header">
                  <span className="review-author">{r.username}</span>
                  <span className="review-stars-sm">
                    {'★'.repeat(r.rating)}
                    {'☆'.repeat(5 - r.rating)}
                  </span>
                  <span className="review-date">
                    {r.created_at ? new Date(r.created_at).toLocaleDateString() : ''}
                  </span>
                </div>
                {r.feedback && <div className="review-text">{r.feedback}</div>}
              </div>
            ))}

          <form onSubmit={(e) => void handleSubmitReview(e)} style={{ marginTop: 16 }}>
            <div className="section-hdr" style={{ border: 'none', paddingTop: 8 }}>
              Rate this prompt
            </div>
            <div className="star-picker">
              {[1, 2, 3, 4, 5].map((n) => (
                <span
                  key={n}
                  role="button"
                  tabIndex={0}
                  className={n <= rating ? 'active' : ''}
                  onClick={() => setRating(n)}
                  onKeyDown={(e) => e.key === 'Enter' && setRating(n)}
                >
                  ★
                </span>
              ))}
            </div>
            <div className="field">
              <label>Feedback (optional)</label>
              <textarea value={feedback} onChange={(e) => setFeedback(e.target.value)} rows={3} />
            </div>
            <button type="submit" className="btn btn-primary">
              Submit review
            </button>
          </form>
        </div>
        )}
      </div>
    </>
  );
}
