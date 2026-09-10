import { useEffect, useMemo, useState } from 'react';
import { buildBlueprintUiSections } from '@utils/blueprintContent';
import { renderMarkdownPreview, renderInlineMarkdown } from '@utils/markdownPreview';
import { parseItemsFromSection } from '@utils/blockItems';
import { useLabels } from '@hooks/useLabels';
import { applyTerminology } from '@config/tenantLabels';
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
  const L = useLabels();
  const uiSections = useMemo(
    () => buildBlueprintUiSections(fullContent, sections),
    [fullContent, sections],
  );

  // A document whose structure no parser recognised comes back as a single
  // whole-document section. It is the entire reading surface, so it opens
  // expanded — collapsing it would hide the document behind a toggle.
  const wholeDoc = uiSections.length === 1 && uiSections[0].whole ? uiSections[0] : null;

  const [expanded, setExpanded] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [reasons, setReasons] = useState({});
  const [regenSection, setRegenSection] = useState(null);
  const [regenItem, setRegenItem] = useState(null);

  useEffect(() => {
    // Content/version changed — clear any in-progress edits. Keep all sections
    // closed by default; users open only the section they need.
    setExpanded(null);
    setDrafts({});
    setReasons({});
  }, [fullContent, sections]);

  /**
   * Stable identity for a section's UI state (open/closed, draft, reason).
   *
   * Titles are not unique — a generator can emit two `### Screen: Quick Check`
   * headings in one document, and keying on the title would collapse them into
   * one accordion sharing one draft while the range-based splice edited them
   * separately. Parsers that can produce duplicates supply `key`; the others
   * fall back to the title they have always used.
   */
  function idOf(sec) {
    return sec.key || sec.title;
  }

  /** `id`/`htmlFor` safe: section keys and titles carry `:`, `#`, spaces. */
  function domId(sec) {
    return `bp-edit-${idOf(sec).replace(/[^a-zA-Z0-9_-]+/g, '-')}`;
  }

  function toggle(id) {
    setExpanded((prev) => (prev === id ? null : id));
  }

  function isOpen(sec) {
    return sec.whole ? expanded !== idOf(sec) : expanded === idOf(sec);
  }

  /**
   * How the page must splice this section back in. Reported by the parser rather
   * than re-derived downstream, so the save path can never disagree with what
   * was displayed.
   */
  function shapeOf(sec) {
    return {
      isWholeDocument: Boolean(sec.whole),
      isDluSection: Boolean(sec.dlu),
      // The line range this section occupies in the document that was rendered.
      // Passed along so the save splices exactly the lines the reader edited.
      headingLocator: sec.heading ? sec.locator : undefined,
    };
  }

  function getDraft(sec) {
    return drafts[idOf(sec)] ?? sec.content;
  }

  function isChanged(sec) {
    return getDraft(sec).trim() !== sec.content.trim();
  }

  async function handleSave(sec) {
    const id = idOf(sec);
    const reason = (reasons[id] || '').trim();
    if (!reason) return;
    await onSaveSection?.({
      sectionTitle: sec.title,
      content: getDraft(sec),
      reason,
      ...shapeOf(sec),
    });
    setDrafts((d) => {
      const next = { ...d };
      delete next[id];
      return next;
    });
    setReasons((r) => {
      const next = { ...r };
      delete next[id];
      return next;
    });
  }

  async function handleRegenSection(sec) {
    if (!onRegenerateSection) return;
    const instruction = (reasons[idOf(sec)] || '').trim();
    // The current text IS sent now, so this is a revision rather than a blind
    // redraft. It still replaces everything in one shot, so a whole-document
    // regeneration keeps its instruction requirement and its confirmation.
    if (sec.whole) {
      if (!instruction) return;
      const ok = window.confirm(
        'Regenerate the WHOLE document?\n\n'
        + 'The model is given the current text and your instruction, and rewrites '
        + 'the document in one pass — so every part can change, not only the part '
        + 'you asked about. The result is saved as a new version, so the current '
        + 'one stays in the version history and can be restored.',
      );
      if (!ok) return;
    }
    setRegenSection(idOf(sec));
    try {
      await onRegenerateSection({
        sectionTitle: sec.title,
        sectionContent: sec.content,
        instruction,
        ...shapeOf(sec),
      });
    } finally {
      setRegenSection(null);
    }
  }

  async function handleRegenItem(sec, idx) {
    if (!onRegenerateItem) return;
    setRegenItem(`${idOf(sec)}\u0000${idx}`);
    try {
      await onRegenerateItem({
        sectionTitle: sec.title,
        sectionContent: sec.content,
        itemIndex: idx,
        instruction: (reasons[idOf(sec)] || '').trim(),
        ...shapeOf(sec),
      });
    } finally {
      setRegenItem(null);
    }
  }

  if (uiSections.length === 0) {
    return <p className={styles.empty}>No {L.blueprint} content yet.</p>;
  }

  return (
    <div className={styles.wrap}>
      {wholeDoc && editable && (
        <p className={styles.structureNotice}>
          ℹ️ This {L.blueprint} has no section headings, so it is edited as a single document
          rather than section by section. Editing, single-item regeneration and version
          history all work as usual.
        </p>
      )}
      {uiSections.map((sec) => {
        const id = idOf(sec);
        const open = isOpen(sec);
        const items = editable ? parseItemsFromSection(sec.content) : [];
        const sectionBusy = regenSection === id;
        const instruction = (reasons[id] || '').trim();
        return (
          <div key={id} className={styles.section}>
            <button
              type="button"
              className={styles.section__head}
              onClick={() => toggle(id)}
              aria-expanded={open}
            >
              <span>{applyTerminology(sec.title, L)}</span>
              <span aria-hidden="true">{open ? '▾' : '▸'}</span>
            </button>
            {open && (
              <div className={styles.section__body}>
                <div
                  className={`${styles.section__text} markdown-content`}
                  dangerouslySetInnerHTML={{
                    __html: renderMarkdownPreview(applyTerminology(sec.content, L)),
                  }}
                />
                {editable && (
                  <>
                    <label className={styles.editLabel} htmlFor={domId(sec)}>
                      ✏️ Edit this {sec.whole ? 'document' : 'section'}
                    </label>
                    <textarea
                      id={domId(sec)}
                      className={styles.editTextarea}
                      value={getDraft(sec)}
                      onChange={(e) => setDrafts((d) => ({ ...d, [id]: e.target.value }))}
                    />
                    {isChanged(sec) && (
                      <p className={styles.editHint}>✏️ Edited. Add a reason before saving.</p>
                    )}
                    <Input
                      label="Edit reason / Regen instruction"
                      placeholder="What changed? (also used as the AI regeneration instruction)"
                      value={reasons[id] || ''}
                      onChange={(e) => setReasons((r) => ({ ...r, [id]: e.target.value }))}
                    />
                    <div className={styles.saveRow}>
                      <Button
                        variant="primary"
                        size="sm"
                        loading={saving}
                        disabled={!instruction}
                        onClick={() => handleSave(sec)}
                      >
                        💾 Save Edit
                      </Button>
                      {onRegenerateSection && (
                        <Button
                          variant="secondary"
                          size="sm"
                          loading={sectionBusy}
                          disabled={saving || sectionBusy || (sec.whole && !instruction)}
                          onClick={() => handleRegenSection(sec)}
                          title={sec.whole
                            ? 'Rewrite the whole document from the current text plus the '
                              + 'instruction above. Every part can change.'
                            : 'Regenerate this whole section with AI using the instruction above'}
                        >
                          {sec.whole ? '🔄 Regenerate Whole Document' : '🔄 Regenerate Section'}
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
                            const busy = regenItem === `${id}\u0000${idx}`;
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
