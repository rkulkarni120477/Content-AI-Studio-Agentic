import { visibilityLabel } from '../../utils/prompt';

// For pipeline rows (admin-only in any list response) the visibility concept
// doesn't apply — this is a STATUS badge: ★ Default (what the stage resolves
// to absent a lock) + the active version's workflow state. The stage itself
// is NOT repeated here — the Category column / card footer already shows it
// as the CAS label. Library rows (fallback) keep the visibility badge.
export default function VisibilityBadge({ prompt }) {
  if (prompt.prompt_kind === 'pipeline') {
    const pipe = prompt.pipeline || {};
    const state = pipe.workflow_state;
    const stateLabel = state && state.charAt(0).toUpperCase() + state.slice(1).replace('_', ' ');
    return (
      <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
        {pipe.is_default && <span className="badge badge-global">★ Default</span>}
        {state && (
          <span className={`badge ${state === 'active' ? 'badge-global' : 'badge-draft'}`}>
            {stateLabel}
          </span>
        )}
      </span>
    );
  }
  const { className, text } = visibilityLabel(prompt);
  return <span className={`badge ${className}`}>{text}</span>;
}
