import { useEffect, useMemo, useState } from 'react';
import { buildBlueprintUiSections } from '@utils/blueprintContent';
import { renderMarkdownPreview, renderInlineMarkdown } from '@utils/markdownPreview';
import { parseItemsFromSection } from '@utils/blockItems';
import { useLabels } from '@hooks/useLabels';
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

  function toggle(title) {
    setExpanded((prev) => (prev === title ? null : title));
  }

  function isOpen(sec) {
    return sec.whole ? expanded !== sec.title : expanded === sec.title;
  }

  /**
   * How the page must splice this section back in. Reported by the parser rather
   * than re-derived downstream, so the save path can never disagree with what
   * was displayed.
   */
  function shapeOf(sec) {
    return { isWholeDocument: Boolean(sec.whole), isDluSection: Boolean(sec.dlu) };
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
      ...shapeOf(sec),
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
    const instruction = (reasons[sec.title] || '').trim();
    // Section regeneration is not given the current text — it drafts from the
    // section title, the module and the CDD summary. On a whole-document
    // section that means one click would replace the entire document with an
    // ungrounded draft, so the instruction is the only steer there is and the
    // blast radius is spelled out before anything is spent.
    if (sec.whole) {
      if (!instruction) return;
      const ok = window.confirm(
        'Regenerate the WHOLE document?\n\n'
        + 'Every part is rewritten from your instruction — the current text is '
        + 'not sent to the model. The result is saved as a new version, so the '
        + 'current one stays in the version history.',
      );
      if (!ok) return;
    }
    setRegenSection(sec.title);
    try {
      await onRegenerateSection({
        sectionTitle: sec.title,
        instruction,
        ...shapeOf(sec),
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
          ⚠️ This {L.blueprint}&apos;s structure wasn&apos;t recognised, so it is edited as one
          document instead of part by part. Editing, single-item regeneration and versioning
          all work as usual.
        </p>
      )}
      {uiSections.map((sec) => {
        const open = isOpen(sec);
        const items = editable ? parseItemsFromSection(sec.content) : [];
        const sectionBusy = regenSection === sec.title;
        const instruction = (reasons[sec.title] || '').trim();
        return (
          <div key={sec.title} className={styles.section}>
            <button
              type="button"
              className={styles.section__head}
              onClick={() => toggle(sec.title)}
              aria-expanded={open}
            >
              <span>{sec.title}</span>
              <span aria-hidden="true">{open ? '▾' : '▸'}</span>
            </button>
            {open && (
              <div className={styles.section__body}>
                <div
                  className={`${styles.section__text} markdown-content`}
                  dangerouslySetInnerHTML={{ __html: renderMarkdownPreview(sec.content) }}
                />
                {editable && (
                  <>
                    <label className={styles.editLabel} htmlFor={`bp-edit-${sec.title}`}>
                      ✏️ Edit this {sec.whole ? 'document' : 'section'}
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
                            ? 'Rewrite the whole document from the instruction above. '
                              + 'The current text is not sent to the model.'
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
