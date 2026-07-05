import { visibilityLabel } from '../../utils/prompt';

// For pipeline rows (admin-only in any list response) the visibility concept
// doesn't apply — show the resolution keys + workflow state instead.
export default function VisibilityBadge({ prompt }) {
  if (prompt.prompt_kind === 'pipeline') {
    const pipe = prompt.pipeline || {};
    const state = pipe.workflow_state;
    return (
      <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
        <span className="badge badge-team">
          ⚙ {pipe.component_type || 'pipeline'}
          {pipe.variant ? ` / ${pipe.variant}` : ''}
        </span>
        {pipe.is_default && <span className="badge badge-global">Default</span>}
        {state && (
          <span className={`badge ${state === 'active' ? 'badge-global' : 'badge-draft'}`}>
            {state.replace('_', ' ')}
          </span>
        )}
      </span>
    );
  }
  const { className, text } = visibilityLabel(prompt);
  return <span className={`badge ${className}`}>{text}</span>;
}
