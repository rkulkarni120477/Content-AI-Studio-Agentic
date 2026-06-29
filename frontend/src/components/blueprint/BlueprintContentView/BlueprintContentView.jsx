import { useEffect, useMemo, useState } from 'react';
import { buildBlueprintUiSections } from '@utils/blueprintContent';
import { renderMarkdownPreview } from '@utils/markdownPreview';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import styles from './BlueprintContentView.module.scss';

export default function BlueprintContentView({
  fullContent,
  sections,
  editable = false,
  saving = false,
  onSaveSection,
}) {
  const uiSections = useMemo(
    () => buildBlueprintUiSections(fullContent, sections),
    [fullContent, sections],
  );

  const [expanded, setExpanded] = useState(() => new Set());
  const [drafts, setDrafts] = useState({});
  const [reasons, setReasons] = useState({});

  useEffect(() => {
    if (uiSections.length > 0) {
      setExpanded(new Set([uiSections[0].title]));
    } else {
      setExpanded(new Set());
    }
    setDrafts({});
    setReasons({});
  }, [uiSections]);

  function toggle(title) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title);
      else next.add(title);
      return next;
    });
  }

  function getDraft(sec) {
    return drafts[sec.title] ?? sec.content;
  }

  function isChanged(sec) {
    return getDraft(sec).trim() !== sec.content.trim();
  }

  async function handleSave(sec) {
    const reason = (reasons[sec.title] || '').trim();
    if (!reason) return;
    await onSaveSection?.({
      sectionTitle: sec.title,
      content: getDraft(sec),
      reason,
    });
    setDrafts((d) => {
      const next = { ...d };
      delete next[sec.title];
      return next;
    });
    setReasons((r) => {
      const next = { ...r };
      delete next[sec.title];
      return next;
    });
  }

  if (!fullContent?.trim() && uiSections.length === 0) {
    return <p className={styles.empty}>No Blueprint content yet.</p>;
  }

  if (uiSections.length === 0) {
    return (
      <div
        className={`${styles.fallback} markdown-content`}
        dangerouslySetInnerHTML={{ __html: renderMarkdownPreview(fullContent) }}
      />
    );
  }

  return (
    <div className={styles.wrap}>
      {uiSections.map((sec) => {
        const isOpen = expanded.has(sec.title);
        return (
          <div key={sec.title} className={styles.section}>
            <button
              type="button"
              className={styles.section__head}
              onClick={() => toggle(sec.title)}
              aria-expanded={isOpen}
            >
              <span>{sec.title}</span>
              <span aria-hidden="true">{isOpen ? '▾' : '▸'}</span>
            </button>
            {isOpen && (
              <div className={styles.section__body}>
                <div
                  className={`${styles.section__text} markdown-content`}
                  dangerouslySetInnerHTML={{ __html: renderMarkdownPreview(sec.content) }}
                />
                {editable && (
                  <>
                    <label className={styles.editLabel} htmlFor={`bp-edit-${sec.title}`}>
                      ✏️ Edit this section
                    </label>
                    <textarea
                      id={`bp-edit-${sec.title}`}
                      className={styles.editTextarea}
                      value={getDraft(sec)}
                      onChange={(e) => setDrafts((d) => ({ ...d, [sec.title]: e.target.value }))}
                    />
                    {isChanged(sec) && (
                      <p className={styles.editHint}>✏️ Edited. Add a reason before saving.</p>
                    )}
                    <Input
                      label="Edit reason"
                      placeholder="What changed?"
                      value={reasons[sec.title] || ''}
                      onChange={(e) => setReasons((r) => ({ ...r, [sec.title]: e.target.value }))}
                    />
                    <Button
                      variant="primary"
                      size="sm"
                      loading={saving}
                      disabled={!(reasons[sec.title] || '').trim()}
                      onClick={() => handleSave(sec)}
                    >
                      💾 Save Edit
                    </Button>
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
