import { Link, useNavigate } from 'react-router-dom';
import { pipelineStageLabel, starsDisplay } from '../../utils/prompt';
import { useLabels } from '@hooks/useLabels';
import { plPrompt, plPromptEdit } from '../../paths';
import VisibilityBadge from './VisibilityBadge';

export default function PromptCard({
  prompt: p, isAdmin, canDelete = false, onCopy, onDuplicate, onDelete, onRestore, onTagClick,
}) {
  const navigate = useNavigate();
  const vars = p.variables || [];
  // `_version_count` is authoritative: the browse list ships the count without
  // the (potentially large) versions array, while the detail payload still
  // carries the array — read whichever is present.
  const verCount = (p._version_count ?? (p.versions || []).length) || 1;
  const rs = p._review_stats || { count: 0, avg: 0 };
  const L = useLabels();
  const category = p.prompt_kind === 'pipeline' ? pipelineStageLabel(p, L) : p.category;

  function handleCopy() {
    if (vars.length) {
      navigate(plPrompt(p.id));
      return;
    }
    onCopy(p.id);
  }

  return (
    <div className="card">
      <div className="card-header">
        <div className="card-title">{p.title}</div>
        {p.archived && (
          <span className="badge badge-draft" title="Archived — excluded from generation and all pickers until restored">
            🗄 Archived
          </span>
        )}
        <VisibilityBadge prompt={p} />
      </div>
      {p.description && <div className="card-desc">{p.description}</div>}
      <div className="card-content">{p.content}</div>
      {(p.tags || []).length > 0 && (
        <div className="tags">
          {(p.tags || []).map((t) => (
            <span key={t} className="tag" onClick={() => onTagClick(t)} role="button" tabIndex={0}>
              {t}
            </span>
          ))}
        </div>
      )}
      <div className="card-footer">
        {category && <span className="card-meta">📁 {category}</span>}
        {vars.length > 0 && (
          <span className="var-chip">
            ⚙ {vars.length} var{vars.length > 1 ? 's' : ''}
          </span>
        )}
        {verCount > 1 && <span className="ver-chip">v{verCount}</span>}
        {(p._child_count ?? 0) > 0 && (
          <span className="ver-chip" title="Follow-up prompts">
            ↳ {p._child_count}
          </span>
        )}
        {rs.count > 0 && (
          <span style={{ display: 'flex', alignItems: 'center', gap: 3 }}>
            <span className="stars">{starsDisplay(rs.avg)}</span>
            <span className="rating-text">{rs.avg}</span>
          </span>
        )}
        <div className="card-actions">
          <button
            type="button"
            className={`btn-copy${vars.length ? ' needs-vars' : ''}`}
            onClick={handleCopy}
          >
            {vars.length ? '✏ Fill & Copy' : '📋 Copy'}
          </button>
          <Link to={plPrompt(p.id)} className="icon-btn" title="View">
            👁
          </Link>
          {isAdmin && p.archived && (
            <button type="button" className="icon-btn" title="Restore from archive" onClick={() => onRestore?.(p.id)}>
              ♻
            </button>
          )}
          {isAdmin && !p.archived && (
            <>
              <Link to={plPromptEdit(p.id)} className="icon-btn" title="Edit">
                ✏️
              </Link>
              {/* Duplicate stays library-only: it forks a `library`-kind copy,
                  which is a meaningless shape for a CAS pipeline row. */}
              {p.prompt_kind !== 'pipeline' && (
                <button type="button" className="icon-btn" title="Duplicate" onClick={() => onDuplicate(p.id)}>
                  ⧉
                </button>
              )}
            </>
          )}
          {/* Delete spans both kinds — the console lists only pipeline rows, so
              a library-only guard here would hide the action from every row on
              screen. The prompt object (not just its id) goes up so the
              confirmation can name it and check what it is bound to. */}
          {canDelete && !p.archived && (
            <button type="button" className="icon-btn del" title="Delete" onClick={() => onDelete(p)}>
              🗑
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
