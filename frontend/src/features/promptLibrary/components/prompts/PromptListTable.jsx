import { useCallback, useState } from 'react';
import { fetchChildPrompts } from '../../api/prompts';
import PromptListRow from './PromptListRow';

export default function PromptListTable({ prompts, isAdmin, onCopy, onDelete, onRestore, onTagClick }) {
  const [expanded, setExpanded] = useState({});
  const [childrenByParent, setChildrenByParent] = useState({});
  const [loadingChildren, setLoadingChildren] = useState({});

  const toggleExpand = useCallback(
    async (parent) => {
      const id = parent.id;
      const isOpen = expanded[id];
      if (isOpen) {
        setExpanded((prev) => ({ ...prev, [id]: false }));
        return;
      }
      setExpanded((prev) => ({ ...prev, [id]: true }));
      if (childrenByParent[id]) return;

      setLoadingChildren((prev) => ({ ...prev, [id]: true }));
      try {
        const kids = await fetchChildPrompts(id);
        setChildrenByParent((prev) => ({ ...prev, [id]: kids }));
      } finally {
        setLoadingChildren((prev) => ({ ...prev, [id]: false }));
      }
    },
    [expanded, childrenByParent],
  );

  function handleDeleteChild(parentId, childId) {
    onDelete(childId);
    setChildrenByParent((prev) => ({
      ...prev,
      [parentId]: (prev[parentId] || []).filter((c) => c.id !== childId),
    }));
  }

  return (
    <div className="list-view">
      <div className="list-header list-row-grid">
        <div className="list-expand-col" aria-hidden />
        <div>Title / Description</div>
        <div>Workflow</div>
        <div>Tags</div>
        <div>Status</div>
        <div className="list-actions-col">Actions</div>
      </div>

      {prompts.map((p) => {
        const childCount = p._child_count ?? 0;
        const isOpen = !!expanded[p.id];
        const children = childrenByParent[p.id];
        const loadingKids = !!loadingChildren[p.id];

        return (
          <div key={p.id} className="list-parent-group">
            <PromptListRow
              prompt={p}
              isAdmin={isAdmin}
              onCopy={(id) => onCopy(id, p.content)}
              onDelete={onDelete}
              onRestore={onRestore}
              onTagClick={onTagClick}
              expandControl={
                childCount > 0 ? (
                  <button
                    type="button"
                    className="list-expand-btn"
                    onClick={() => void toggleExpand(p)}
                    aria-expanded={isOpen}
                    aria-label={
                      isOpen
                        ? `Collapse ${childCount} follow-up prompt${childCount !== 1 ? 's' : ''}`
                        : `Expand ${childCount} follow-up prompt${childCount !== 1 ? 's' : ''}`
                    }
                    title={
                      isOpen ? 'Hide follow-ups' : `Show ${childCount} follow-up${childCount !== 1 ? 's' : ''}`
                    }
                  >
                    {isOpen ? '−' : '+'}
                  </button>
                ) : (
                  <span className="list-expand-placeholder" />
                )
              }
            />

            {isOpen && (
              <div className="list-children-panel">
                <div className="list-children-label">Follow-up prompts</div>
                {loadingKids ? (
                  <p className="list-children-loading">Loading follow-ups…</p>
                ) : !children?.length ? (
                  <p className="list-children-empty">No follow-up prompts.</p>
                ) : (
                  children.map((child) => (
                    <PromptListRow
                      key={child.id}
                      prompt={child}
                      isAdmin={isAdmin}
                      isFollowUp
                      onCopy={(id) => onCopy(id, child.content)}
                      onDelete={() => handleDeleteChild(p.id, child.id)}
                      onTagClick={onTagClick}
                    />
                  ))
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
