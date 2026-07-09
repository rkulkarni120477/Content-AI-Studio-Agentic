import { useEffect, useMemo, useState } from 'react';
import { buildBlueprintUiSections } from '@utils/blueprintContent';
import { renderMarkdownPreview, renderInlineMarkdown } from '@utils/markdownPreview';
import { parseItemsFromSection } from '@utils/blockItems';
import Button from '@components/common/Button/Button';
import Input from '@components/common/Input/Input';
import styles from './BlueprintContentView.module.scss';

export default function BlueprintContentView({
  fullContent,
  sections,
  editable = false,
  saving = false,
  onSaveSection,
  onRegenerateSection,
  onRegenerateItem,
}) {
  const uiSections = useMemo(
    () => buildBlueprintUiSections(fullContent, sections),
    [fullContent, sections],
  );

  const [expanded, setExpanded] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [reasons, setReasons] = useState({});
  const [regenSection, setRegenSection] = useState(null);
  const [regenItem, setRegenItem] = useState(null);

  useEffect(() => {
    // Keep all sections closed by default. Users open only the section they need.
    setExpanded(null);
    setDrafts({});
    setReasons({});
  }, [uiSections]);

  function toggle(title) {
    setExpanded((prev) => (prev === title ? null : title));
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

  async function handleRegenSection(sec) {
    if (!onRegenerateSection) return;
    setRegenSection(sec.title);
    try {
      await onRegenerateSection({
        sectionTitle: sec.title,
        instruction: (reasons[sec.title] || '').trim(),
      });
    } finally {
      setRegenSection(null);
    }
  }

  async function handleRegenItem(sec, idx) {
    if (!onRegenerateItem) return;
    setRegenItem(`${sec.title}:${idx}`);
    try {
      await onRegenerateItem({
        sectionTitle: sec.title,
        sectionContent: sec.content,
        itemIndex: idx,
        instruction: (reasons[sec.title] || '').trim(),
      });
    } finally {
      setRegenItem(null);
    }
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
        const isOpen = expanded === sec.title;
        const items = editable ? parseItemsFromSection(sec.content) : [];
        const sectionBusy = regenSection === sec.title;
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
                      label="Edit reason / Regen instruction"
                      placeholder="What changed? (also used as the AI regeneration instruction)"
                      value={reasons[sec.title] || ''}
                      onChange={(e) => setReasons((r) => ({ ...r, [sec.title]: e.target.value }))}
                    />
                    <div className={styles.saveRow}>
                      <Button
                        variant="primary"
                        size="sm"
                        loading={saving}
                        disabled={!(reasons[sec.title] || '').trim()}
                        onClick={() => handleSave(sec)}
                      >
                        💾 Save Edit
                      </Button>
                      {onRegenerateSection && (
                        <Button
                          variant="secondary"
                          size="sm"
                          loading={sectionBusy}
                          disabled={saving || sectionBusy}
                          onClick={() => handleRegenSection(sec)}
                          title="Regenerate this whole section with AI using the instruction above"
                        >
                          🔄 Regenerate Section
                        </Button>
                      )}
                    </div>

                    {onRegenerateItem && items.length > 0 && (
                      <div className={styles.itemRegen}>
                        <p className={styles.itemRegen__head}>
                          🔄 Regenerate a single item — click ⟳ next to any item
                        </p>
                        <ul className={styles.itemList}>
                          {items.map((item, idx) => {
                            const busy = regenItem === `${sec.title}:${idx}`;
                            return (
                              <li key={idx} className={styles.itemList__row}>
                                <span className={styles.itemList__text}>
                                  <code className={styles.itemList__num}>{idx + 1}</code>
                                  <span
                                    className={styles.itemList__md}
                                    dangerouslySetInnerHTML={{ __html: renderInlineMarkdown(item.text) }}
                                  />
                                </span>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  loading={busy}
                                  disabled={Boolean(regenItem) || sectionBusy}
                                  onClick={() => handleRegenItem(sec, idx)}
                                  title={`Regenerate only item ${idx + 1}. Others stay unchanged.`}
                                >
                                  ⟳
                                </Button>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    )}
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
