import { useCallback, useEffect, useState } from 'react';
import { fetchChildPrompts } from '../../api/prompts';
import PromptListRow from './PromptListRow';

export default function PromptListTable({
  prompts, isAdmin, canDelete = false, refreshToken = 0,
  onCopy, onDelete, onRestore, onTagClick,
}) {
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

  // Refetch any open follow-up panel after a mutation, so a deleted child
  // disappears (and a restored one returns) without a second click. Deletion is
  // confirmed asynchronously now, so the optimistic splice this replaces would
  // have lied whenever the user cancelled or the server refused.
  //
  // Keyed on the page's mutation counter rather than on `prompts`: that array
  // is rebuilt on every filter and search keystroke too, which would turn each
  // one into a child fetch per open panel.
  useEffect(() => {
    const openIds = Object.keys(expanded).filter((id) => expanded[id]);
    if (!refreshToken || !openIds.length) return undefined;
    let cancelled = false;
    void Promise.all(
      openIds.map((id) =>
        fetchChildPrompts(Number(id)).then((kids) => [id, kids]).catch(() => null),
      ),
    ).then((pairs) => {
      if (cancelled) return;
      setChildrenByParent((prev) => {
        const next = { ...prev };
        for (const pair of pairs) {
          if (pair) next[pair[0]] = pair[1];
        }
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
    // `expanded` is deliberately not a dependency — re-running on it would
    // duplicate the fetch toggleExpand already performs when a panel opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshToken]);

  return (
    <div className="list-view">
      <div className="list-header list-row-grid">
        <div className="list-expand-col" aria-hidden />
        <div>Title / Description</div>
        <div>Category</div>
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
              canDelete={canDelete}
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
                      canDelete={canDelete}
                      isFollowUp
                      onCopy={(id) => onCopy(id, child.content)}
                      onDelete={onDelete}
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
