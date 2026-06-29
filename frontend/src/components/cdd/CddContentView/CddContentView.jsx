import { useEffect, useMemo, useState } from 'react';
import { buildCddUiBlocks } from '@utils/cddContent';
import { renderMarkdownPreview } from '@utils/markdownPreview';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import styles from './CddContentView.module.scss';

export default function CddContentView({
  fullContent,
  sections,
  editable = false,
  saving = false,
  onSaveBlock,
}) {
  const uiBlocks = useMemo(
    () => buildCddUiBlocks(fullContent, sections),
    [fullContent, sections],
  );

  const [expanded, setExpanded] = useState(() => new Set());
  const [drafts, setDrafts] = useState({});
  const [reasons, setReasons] = useState({});
  const [scopes, setScopes] = useState({});

  useEffect(() => {
    if (uiBlocks.length > 0) {
      setExpanded(new Set([uiBlocks[0].key]));
    } else {
      setExpanded(new Set());
    }
    setDrafts({});
    setReasons({});
    setScopes({});
  }, [fullContent, sections]);

  function toggle(key) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function getDraft(block) {
    return drafts[block.key] ?? block.content;
  }

  function isChanged(block) {
    return getDraft(block).trim() !== block.content.trim();
  }

  async function handleSave(block) {
    const reason = (reasons[block.key] || '').trim();
    if (!reason) return;
    const scope = scopes[block.key] === 'learning' ? 'learning' : 'one_time';
    await onSaveBlock?.({
      blockKey: block.key,
      content: getDraft(block),
      reason,
      scope,
    });
    setDrafts((d) => {
      const next = { ...d };
      delete next[block.key];
      return next;
    });
    setReasons((r) => {
      const next = { ...r };
      delete next[block.key];
      return next;
    });
  }

  if (!fullContent?.trim() && uiBlocks.length === 0) {
    return <p className={styles.empty}>No CDD content yet.</p>;
  }

  if (uiBlocks.length === 0) {
    return (
      <div
        className={`${styles.fallback} markdown-content`}
        dangerouslySetInnerHTML={{ __html: renderMarkdownPreview(fullContent) }}
      />
    );
  }

  return (
    <div className={styles.wrap}>
      {uiBlocks.map((block) => {
        const isOpen = expanded.has(block.key);
        const draft = getDraft(block);
        const changed = isChanged(block);
        const reason = reasons[block.key] || '';
        const scope = scopes[block.key] || 'one_time';
        const canSave = Boolean(reason.trim());

        return (
          <div key={block.key} className={styles.section}>
            <button
              type="button"
              className={styles.section__head}
              onClick={() => toggle(block.key)}
              aria-expanded={isOpen}
            >
              <span>{block.label}</span>
              <span aria-hidden="true">{isOpen ? '▾' : '▸'}</span>
            </button>
            {isOpen && (
              <div className={styles.section__body}>
                <div
                  className={`${styles.section__preview} markdown-content`}
                  dangerouslySetInnerHTML={{ __html: renderMarkdownPreview(block.content) }}
                />

                {editable && (
                  <>
                    <label className={styles.editLabel} htmlFor={`cdd-edit-${block.key}`}>
                      ✏️ Edit this block
                    </label>
                    <textarea
                      id={`cdd-edit-${block.key}`}
                      className={styles.editTextarea}
                      value={draft}
                      onChange={(e) => setDrafts((d) => ({ ...d, [block.key]: e.target.value }))}
                    />
                    {changed && (
                      <p className={styles.editHint}>
                        ✏️ Edited. Add a reason before saving.
                      </p>
                    )}
                    <Input
                      label="Edit reason"
                      placeholder="e.g. Updated module duration, add a Bloom's verb"
                      value={reason}
                      onChange={(e) => setReasons((r) => ({ ...r, [block.key]: e.target.value }))}
                    />
                    <div className={styles.scopeRow} role="radiogroup" aria-label="Edit scope">
                      <label className={styles.scopeLabel}>
                        <input
                          type="radio"
                          name={`scope-${block.key}`}
                          checked={scope === 'one_time'}
                          onChange={() => setScopes((s) => ({ ...s, [block.key]: 'one_time' }))}
                        />
                        ⚡ Apply Once
                      </label>
                      <label className={styles.scopeLabel}>
                        <input
                          type="radio"
                          name={`scope-${block.key}`}
                          checked={scope === 'learning'}
                          onChange={() => setScopes((s) => ({ ...s, [block.key]: 'learning' }))}
                        />
                        🧠 Use as Learning
                      </label>
                    </div>
                    <div className={styles.saveRow}>
                      <Button
                        variant="primary"
                        size="sm"
                        loading={saving}
                        disabled={!canSave}
                        onClick={() => handleSave(block)}
                      >
                        💾 Save Edit
                      </Button>
                      {!canSave && changed && (
                        <span className={styles.editHint} style={{ margin: 0, padding: '4px 8px' }}>
                          Enter a reason to enable saving.
                        </span>
                      )}
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
