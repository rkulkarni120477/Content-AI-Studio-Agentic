import { Link, useNavigate } from 'react-router-dom';
import { pipelineStageLabel, starsDisplay } from '../../utils/prompt';
import { plPrompt, plPromptEdit } from '../../paths';
import VisibilityBadge from './VisibilityBadge';

export default function PromptCard({ prompt: p, isAdmin, onCopy, onDuplicate, onDelete, onTagClick }) {
  const navigate = useNavigate();
  const vars = p.variables || [];
  const verCount = (p.versions || []).length || 1;
  const rs = p._review_stats || { count: 0, avg: 0 };
  const category = p.prompt_kind === 'pipeline' ? pipelineStageLabel(p) : p.category;

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
          {isAdmin && (
            <>
              <Link to={plPromptEdit(p.id)} className="icon-btn" title="Edit">
                ✏️
              </Link>
              {p.prompt_kind !== 'pipeline' && (
                <>
                  <button type="button" className="icon-btn" title="Duplicate" onClick={() => onDuplicate(p.id)}>
                    ⧉
                  </button>
                  <button type="button" className="icon-btn del" title="Delete" onClick={() => onDelete(p.id)}>
                    🗑
                  </button>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
