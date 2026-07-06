import { Link, useNavigate } from 'react-router-dom';
import { plPrompt, plPromptEdit } from '../../paths';
import { pipelineStageLabel } from '../../utils/prompt';
import VisibilityBadge from './VisibilityBadge';

export default function PromptListRow({
  prompt: p,
  isAdmin,
  isFollowUp = false,
  expandControl,
  onCopy,
  onDelete,
  onTagClick,
}) {
  const vars = p.variables || [];
  const copyLabel = vars.length ? 'Fill' : 'Copy';
  const navigate = useNavigate();

  function handleCopy() {
    if (vars.length) {
      navigate(plPrompt(p.id));
      return;
    }
    onCopy(p.id);
  }

  return (
    <div className={`list-row list-row-grid${isFollowUp ? ' list-row--followup' : ''}`}>
      <div className="list-expand-col">{expandControl ?? <span className="list-expand-placeholder" />}</div>
      <div className="list-title-cell">
        {isFollowUp && <span className="list-followup-badge">Follow-up</span>}
        <Link to={plPrompt(p.id)} className="list-title">
          {p.title}
        </Link>
        {p.description && <div className="list-desc">{p.description}</div>}
      </div>
      <div className="list-cat">{p.prompt_kind === 'pipeline' ? pipelineStageLabel(p) : p.category || ''}</div>
      <div className="list-tags">
        {(p.tags || []).slice(0, 3).map((t) => (
          <span
            key={t}
            className="tag"
            style={{ fontSize: '.66rem' }}
            onClick={() => onTagClick(t)}
            role="button"
            tabIndex={0}
          >
            {t}
          </span>
        ))}
      </div>
      <div>
        <VisibilityBadge prompt={p} />
      </div>
      <div className="list-actions">
        <button
          type="button"
          className={`btn-copy list-copy-btn${vars.length ? ' needs-vars' : ''}`}
          onClick={handleCopy}
        >
          <span className="list-copy-btn__icon" aria-hidden>
            {vars.length ? '✏' : '📋'}
          </span>
          {copyLabel}
        </button>
        <Link to={plPrompt(p.id)} className="icon-btn" title="View">
          👁
        </Link>
        {isAdmin && (
          <>
            <Link to={plPromptEdit(p.id)} className="icon-btn" title="Edit">
              ✏️
            </Link>
            {p.prompt_kind !== 'pipeline' && (
              <button type="button" className="icon-btn del" title="Delete" onClick={() => onDelete(p.id)}>
                🗑
              </button>
            )}
          </>
        )}
      </div>
    </div>
  );
}
